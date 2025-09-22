# app/bot/handlers/admin.py
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram import Bot

from ..middlewares import AdminOnly
from ...db import open_db
from ...services.pricing import calc_final_price
from ...services.exchange import fetch_cbr_rate
from ...services.payments import create_mock_payment, succeed_payment
from ...services.links import carrier_link
from ...config import settings

router = Router(name="admin")
router.message.middleware(AdminOnly())
router.callback_query.middleware(AdminOnly())

@router.message(Command("admin"))
async def admin_menu(m: Message):
    await m.answer(
        "🛠️ Админ-панель\n"
        "Команды:\n"
        "• /admin_help — справка\n"
        "• /orders_waiting — последние заказы\n"
        "• /order ID — открыть заказ\n"
        "• /add_admin ID — добавить админа (только владелец)\n"
        "• /rm_admin ID — удалить админа (только владелец)"
    )

@router.message(Command("admin_help"))
async def admin_help(m: Message):
    await m.answer(
        "Справка по админ-панели:\n"
        "• /orders_waiting — список последних заказов\n"
        "• /order ID — открыть карточку заказа\n\n"
        "В карточке: Одобрить / Отклонить / Сменить статус / Добавить трек / Инд. траты / Смоделировать оплату.\n\n"
        "Примеры:\n"
        "<code>/order 6F9A6656</code>\n"
        "<code>track 6F9A6656 boxberry BBX123456</code>\n"
        "<code>pay 123e4567-e89b-12d3-a456-426614174000</code>"
    )

@router.message(Command("add_admin"))
async def add_admin(m: Message):
    # только владелец
    if m.from_user.id != settings.owner_id:
        await m.answer("⛔ Только владелец может добавлять админов.")
        return
    parts = (m.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await m.answer("Использование: /add_admin ID")
        return
    uid = int(parts[1])
    db = await open_db()
    await db.execute(
        "INSERT OR REPLACE INTO admins(tg_user_id, role, is_active, added_at) VALUES (?, 'admin', 1, datetime('now'))",
        (uid,)
    )
    await db.commit()
    await db.close()
    await m.answer(f"✅ Добавлен админ {uid}")

@router.message(Command("rm_admin"))
async def rm_admin(m: Message):
    if m.from_user.id != settings.owner_id:
        await m.answer("⛔ Только владелец может удалять админов.")
        return
    parts = (m.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await m.answer("Использование: /rm_admin ID")
        return
    uid = int(parts[1])
    db = await open_db()
    await db.execute("UPDATE admins SET is_active=0 WHERE tg_user_id=?", (uid,))
    await db.commit()
    await db.close()
    await m.answer(f"✅ Админ {uid} деактивирован")

@router.message(Command("orders_waiting"))
async def orders_waiting(m: Message):
    db = await open_db()
    cur = await db.execute(
        "SELECT id, customer_id, source_type, poizon_or_stock_ref, status FROM crm_orders "
        "ORDER BY updated_at DESC LIMIT 20"
    )
    rows = await cur.fetchall()
    await db.close()
    if not rows:
        await m.answer("Заявок нет.")
        return
    from ..keyboards import ikb_admin_order
    for r in rows:
        await m.answer(
            f"Заказ #{r['id']} (клиент {r['customer_id']}): {r['source_type']} — {r['poizon_or_stock_ref']}\nСтатус: {r['status']}",
            reply_markup=ikb_admin_order(r["id"])
        )

@router.message(Command("order"))
async def order_open(m: Message):
    parts = (m.text or "").split()
    if len(parts) < 2:
        await m.answer("Использование: /order ID")
        return
    order_id = parts[1]
    db = await open_db()
    cur = await db.execute("SELECT * FROM crm_orders WHERE id=?", (order_id,))
    r = await cur.fetchone()
    await db.close()
    if not r:
        await m.answer("Не найдено")
        return
    from ..keyboards import ikb_admin_order
    await m.answer(
        f"Заказ #{r['id']}\nКлиент: {r['customer_id']}\nИсточник: {r['source_type']}\n"
        f"Ref: {r['poizon_or_stock_ref']}\nСтатус: {r['status']}\nОплата: {r['payment_status']}\n"
        f"Суммы: poizon={r['poizon_price']} var={r['order_cost_var']} fix={r['order_cost_fixed']} "
        f"margin={r['margin']} final={r['final_price']}",
        reply_markup=ikb_admin_order(order_id)
    )

@router.callback_query(F.data.startswith("ORDER:APPROVE:"))
async def cb_approve(cq: CallbackQuery):
    order_id = cq.data.split(":")[2]
    db = await open_db()
    cur = await db.execute(
        "SELECT poizon_price, order_cost_var, order_cost_fixed FROM crm_orders WHERE id=?",
        (order_id,)
    )
    r = await cur.fetchone()
    cny = await fetch_cbr_rate("CNY") or 12.0
    base, margin, final = calc_final_price(
        float(r["poizon_price"] or 0.0),
        float(cny),
        float(r["order_cost_var"] or 0.0),
        float(r["order_cost_fixed"] or 0.0)
    )
    await db.execute(
        "UPDATE crm_orders SET margin=?, final_price=?, updated_at=datetime('now') WHERE id=?",
        (margin, final, order_id)
    )
    await db.commit()
    await db.close()
    await cq.message.answer(f"✅ Заказ #{order_id} одобрен. Финальная цена: {final} ₽. Ссылка на оплату будет сгенерирована.")
    await cq.answer("OK")

@router.callback_query(F.data.startswith("ORDER:REJECT:"))
async def cb_reject(cq: CallbackQuery):
    order_id = cq.data.split(":")[2]
    db = await open_db()
    await db.execute(
        "UPDATE crm_orders SET status='отмена', updated_at=datetime('now') WHERE id=?",
        (order_id,)
    )
    await db.execute(
        "INSERT INTO order_status_history(order_id, old_status, new_status, reason, actor, created_at) "
        "VALUES (?, (SELECT status FROM crm_orders WHERE id=?), 'отмена', 'by admin', 'admin', datetime('now'))",
        (order_id, order_id)
    )
    await db.commit()
    await db.close()
    await cq.message.answer(f"❌ Заказ #{order_id} отклонён.")
    await cq.answer("OK")

@router.callback_query(F.data.startswith("ORDER:STATUS_MENU:"))
async def cb_status_menu(cq: CallbackQuery):
    order_id = cq.data.split(":")[2]
    from ..keyboards import ikb_status_menu
    await cq.message.answer("Выберите новый статус:", reply_markup=ikb_status_menu(order_id))
    await cq.answer()

@router.callback_query(F.data.startswith("ORDER:STATUS:"))
async def cb_status(cq: CallbackQuery, bot: Bot):
    _, _, order_id, new_status = cq.data.split(":", 3)
    db = await open_db()
    cur = await db.execute("SELECT status, customer_id FROM crm_orders WHERE id=?", (order_id,))
    r = await cur.fetchone()
    old_status = r["status"] if r else ""
    await db.execute(
        "UPDATE crm_orders SET status=?, updated_at=datetime('now') WHERE id=?",
        (new_status, order_id)
    )
    await db.execute(
        "INSERT INTO order_status_history(order_id, old_status, new_status, reason, actor, created_at) "
        "VALUES (?, ?, ?, '', 'admin', datetime('now'))",
        (order_id, old_status, new_status)
    )
    await db.commit()
    await db.close()

    await cq.message.answer(f"Статус заказа #{order_id} → {new_status}")
    # уведомим клиента
    try:
        chat_id = int(r["customer_id"])
        await bot.send_message(chat_id, f"📦 Ваш заказ #{order_id}: статус → <b>{new_status}</b>")
    except Exception:
        pass
    await cq.answer("OK")

@router.callback_query(F.data.startswith("ORDER:MOCKPAY:"))
async def cb_mockpay(cq: CallbackQuery):
    order_id = cq.data.split(":")[2]
    db = await open_db()
    cur = await db.execute("SELECT final_price FROM crm_orders WHERE id=?", (order_id,))
    r = await cur.fetchone()
    amount = float(r["final_price"] or 0)
    payment = create_mock_payment(order_id, amount)
    await db.execute(
        "INSERT OR REPLACE INTO payments(id, order_id, provider, amount, status, idempotence_key, created_at, updated_at) "
        "VALUES (?, ?, 'mock', ?, 'pending', ?, datetime('now'), datetime('now'))",
        (payment.id, order_id, amount, payment.id)
    )
    await db.execute(
        "UPDATE crm_orders SET payment_url=?, updated_at=datetime('now') WHERE id=?",
        (payment.url, order_id)
    )
    await db.commit()
    await db.close()
    await cq.message.answer(
        f"🧪 Тест-ссылка на оплату: {payment.url}\n"
        "Чтобы завершить — нажмите кнопку ещё раз или отправьте команду:\n"
        f"<code>pay {payment.id}</code>"
    )
    await cq.answer("Ссылка выдана")

@router.callback_query(F.data.startswith("ORDER:TRACK:"))
async def cb_track(cq: CallbackQuery):
    await cq.message.answer(
        "Пришлите сообщением:\n"
        "<code>track ORDER_ID CARRIER TRACKCODE</code>\n"
        "Где CARRIER: <code>boxberry</code> | <code>cdek</code> | <code>pochta</code>"
    )
    await cq.answer()

@router.message(F.text.regexp(r"^track\s+(\S+)\s+(\S+)\s+(\S+)$"))
async def set_track(m: Message, regexp_command):
    order_id, carrier, code = (
        regexp_command.group(1),
        regexp_command.group(2),
        regexp_command.group(3),
    )
    db = await open_db()
    await db.execute(
        "UPDATE crm_orders SET carrier=?, tracking=?, updated_at=datetime('now') WHERE id=?",
        (carrier, code, order_id)
    )
    await db.commit()
    await db.close()
    await m.answer(f"✅ Трек обновлён для #{order_id}: {carrier} {code}")

@router.message(F.text.regexp(r"^pay\s+(\S+)$"))
async def pay_ok(m: Message, regexp_command, bot: Bot):
    pid = regexp_command.group(1)
    if succeed_payment(pid):
        db = await open_db()
        cur = await db.execute("SELECT order_id FROM payments WHERE id=?", (pid,))
        r = await cur.fetchone()
        if r:
            await db.execute("UPDATE payments SET status='succeeded', updated_at=datetime('now') WHERE id=?", (pid,))
            await db.execute("UPDATE crm_orders SET payment_status='оплачено', status='Оплачен', updated_at=datetime('now') WHERE id=?", (r["order_id"],))
            await db.commit()
            # уведомим клиента
            cur2 = await db.execute("SELECT customer_id FROM crm_orders WHERE id=?", (r["order_id"],))
            rr = await cur2.fetchone()
            if rr:
                try:
                    await bot.send_message(int(rr["customer_id"]), f"💳 Оплата заказа #{r['order_id']} получена. Статус → <b>Оплачен</b>.")
                except Exception:
                    pass
        await db.close()
        await m.answer(f"✅ Оплата {pid} помечена как успешная.")
    else:
        await m.answer("Не найден такой payment_id.")
