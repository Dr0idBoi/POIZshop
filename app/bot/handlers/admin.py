# app/bot/handlers/admin.py
import logging
from datetime import datetime
from aiogram.filters import Command
from aiogram import Router, F, Bot
import re
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from app.bot.middlewares import AdminOnly
from app.db import get_db, log_action, add_status_history
from app.services.pricing import calc_final_price, format_price_breakdown, calc_total_rub
from decimal import Decimal
from app.services.exchange import fetch_cbr_rate
from app.services.payments import create_payment_with_yookassa, succeed_payment
from app.services.links import carrier_link
from app.config import settings
from app.services.notifications import NotificationService
from app.crm.sheets import append_order_row, read_fin_settings, partial_update_by_ext_id
from app.constants import ORDER_STATUSES, ORDER_COLUMNS, SHEET_NAMES
from app.bot.keyboards import (
    ikb_admin_order_actions,
    ikb_confirm_calculation,
    ikb_payment_test_button, ikb_payment_button
)
import csv
from io import StringIO
log = logging.getLogger("admin")
router = Router(name="admin")
router.message.middleware(AdminOnly())
router.callback_query.middleware(AdminOnly())
def CMD(name: str):
    return F.text.regexp(fr"^/{name}(?:@\w+)?(?:\s|$)")
# === DATA EXPORT ===

@router.message(Command("export_csv"))
async def export_csv(m: Message):
    """Экспорт всех клиентов и заказов (включая удалённые) в два CSV-файла"""
    try:
        async with get_db() as db:
            # Клиенты
            cur = await db.execute("SELECT * FROM crm_customers")
            customers = [dict(r) for r in await cur.fetchall()]
            # Заказы
            cur = await db.execute("SELECT * FROM crm_orders")
            orders = [dict(r) for r in await cur.fetchall()]

        # Готовим CSV для клиентов
        cust_buf = StringIO()
        if customers:
            cust_writer = csv.DictWriter(cust_buf, fieldnames=list(customers[0].keys()))
            cust_writer.writeheader()
            cust_writer.writerows(customers)
        else:
            cust_buf.write("id\n")

        # Готовим CSV для заказов
        ord_buf = StringIO()
        if orders:
            ord_writer = csv.DictWriter(ord_buf, fieldnames=list(orders[0].keys()))
            ord_writer.writeheader()
            ord_writer.writerows(orders)
        else:
            ord_buf.write("id\n")

        # Отправляем файлы администратору
        cust_buf.seek(0)
        ord_buf.seek(0)
        await m.answer_document(document=("customers.csv", cust_buf.getvalue()))
        await m.answer_document(document=("orders.csv", ord_buf.getvalue()))

    except Exception as e:
        log.error(f"Error in export_csv: {e}")
        await m.answer("❌ Ошибка при экспорте CSV")
# === FSM STATES ===

class OrderApprovalFSM(StatesGroup):
    """FSM для процесса одобрения заказа"""
    WaitingPoizonPrice = State()
    WaitingVariableCosts = State()
    ConfirmingCalculation = State()
    WaitingRejectReason = State()

class MessageClientFSM(StatesGroup):
    WaitingText = State()

# === ADMIN COMMANDS ===

@router.message(Command("admin_start"))
async def admin_start(m: Message):
    """Главная команда для админов"""
    await m.answer(
        "🛠️ <b>Админ-панель ZakazPOIZ</b>\n\n"
        "<b>📋 Управление заказами:</b>\n"
        "• /orders_waiting — заявки на одобрение/отклонение\n"
        "• /order ID — открыть заказ\n\n"
        "<b>👥 Управление админами:</b>\n"
        "• /add_admin ID — добавить админа\n"
        "• /rm_admin ID — удалить админа\n\n"
        "<b>📊 Данные и синхронизация:</b>\n"
        "• /sync_now — синхронизация с Google Sheets\n"
        "• /refresh_cny — обновить курс CNY\n"
        "• /stats — статистика\n\n"
        "<b>💬 Коммуникация:</b>\n"
        "• /message USER_ID — написать клиенту\n\n"
        "<b>ℹ️ Справка:</b>\n"
        "• /admin_help — подробная справка",
        reply_markup=ReplyKeyboardRemove()
    )

@router.message(Command("admin_help"))
async def admin_help(m: Message):
    """Справка по админским командам"""
    await m.answer(
        "📖 <b>Справка по командам администратора</b>\n\n"
        "<b>Управление заказами:</b>\n"
        "• /orders_waiting — Список заявок на одобрение/отклонение\n"
        "• /order ID — Открыть карточку заказа по ID\n\n"
        "<b>Управление админами:</b>\n"
        "• /add_admin USER_ID — Добавить администратора\n"
        "• /rm_admin USER_ID — Удалить администратора\n\n"
        "<b>Синхронизация и данные:</b>\n"
        "• /sync_now — Принудительная синхронизация с Google Sheets\n"
        "• /refresh_cny — Принудительно обновить курс CNY\n"
        "• /stats — Статистика по заказам и клиентам\n\n"
        "<b>Коммуникация:</b>\n"
        "• /message USER_ID — Отправить сообщение клиенту\n\n"
        "<b>Статусы заказов:</b>\n"
        "• Ожидает решения — Новый заказ, требует одобрения\n"
        "• Ожидает оплату — Одобрен, ждет оплаты клиента\n"
        "• Оплачен — Клиент оплатил заказ\n"
        "• В пути по Китаю — Заказ едет по Китаю\n"
        "• На пути на склад — Заказ едет на склад\n"
        "• На пути к получателю — Заказ едет к клиенту\n"
        "• Выдано — Заказ получен клиентом\n"
        "• Закрыт — Заказ завершен\n"
        "• Отмена — Заказ отменен"
    )

@router.message(CMD("sync_now"))
async def sync_now(m: Message, bot: Bot):
    """Полная синхронизация всех компонентов"""
    try:
        status_msg = await m.answer(
            "🔄 <b>Запускаю полную синхронизацию...</b>\n\n"
            "Это может занять некоторое время. Пожалуйста, подождите..."
        )
        
        # Импортируем менеджер полной синхронизации
        from app.services.sync_manager import run_full_sync, get_last_sync_summary
        
        # Запускаем полную синхронизацию
        results = await run_full_sync(bot)
        
        # Получаем форматированное сообщение с результатами
        summary = get_last_sync_summary()
        
        # Отправляем результаты
        await status_msg.edit_text(summary)
        
    except Exception as e:
        log.error(f"Full sync error: {e}", exc_info=True)
        try:
            await status_msg.edit_text(
                "❌ <b>Ошибка при выполнении синхронизации</b>\n\n"
                f"Детали: {str(e)}\n\n"
                "Проверьте логи для подробной информации."
            )
        except:
            await m.answer(
                "❌ <b>Критическая ошибка синхронизации</b>\n\n"
                "Проверьте логи для подробной информации."
            )

@router.message(CMD("refresh_cny"))
async def refresh_cny_rate(m: Message):
    """Принудительно обновить курс CNY"""
    try:
        from app.services.exchange import force_refresh_cny_rate
        
        await m.answer("🔄 Обновляю курс CNY...")
        
        rate = await force_refresh_cny_rate()
        if rate:
            await m.answer(
                f"✅ Курс CNY обновлен!\n\n"
                f"💱 Новый курс: {rate:.4f} ₽\n"
                f"📊 Включает +1.0 ₽ к официальному курсу"
            )
        else:
            await m.answer("❌ Не удалось обновить курс CNY")
            
    except Exception as e:
        log.error(f"Error refreshing CNY rate: {e}")
        await m.answer("❌ Ошибка при обновлении курса")

@router.message(Command("stats"))
async def show_stats(m: Message):
    """Показать статистику"""
    try:
        async with get_db() as db:
            # Статистика по клиентам
            cur = await db.execute("SELECT COUNT(*) as count FROM crm_customers")
            total_customers = (await cur.fetchone())["count"]
            
            # Статистика по заказам
            cur = await db.execute("SELECT COUNT(*) as count FROM crm_orders")
            total_orders = (await cur.fetchone())["count"]
            
            # Статистика по статусам
            cur = await db.execute(
                "SELECT status, COUNT(*) as count FROM crm_orders GROUP BY status ORDER BY count DESC"
            )
            status_stats = await cur.fetchall()
            
            # Заказы за последние 7 дней
            cur = await db.execute(
                "SELECT COUNT(*) as count FROM crm_orders WHERE created_at >= datetime('now', '-7 days')"
            )
            orders_last_7_days = (await cur.fetchone())["count"]
            
            # Заказы ожидающие одобрения
            cur = await db.execute(
                "SELECT COUNT(*) as count FROM crm_orders WHERE awaiting_decision = 1"
            )
            awaiting_approval = (await cur.fetchone())["count"]
            
            # Общая сумма заказов
            cur = await db.execute(
                "SELECT SUM(final_price) as total FROM crm_orders WHERE status != 'Отмена'"
            )
            total_revenue = (await cur.fetchone())["total"] or 0
            
        # Формируем сообщение
        msg = [
            "📊 <b>Статистика бота</b>\n",
            f"👥 <b>Всего клиентов:</b> {total_customers}",
            f"📦 <b>Всего заказов:</b> {total_orders}",
            f"🆕 <b>Заказов за 7 дней:</b> {orders_last_7_days}",
            f"⏳ <b>Ожидают одобрения:</b> {awaiting_approval}",
            f"💰 <b>Общая сумма заказов:</b> {total_revenue:,.0f} ₽\n",
            "<b>Распределение по статусам:</b>"
        ]
        
        for stat in status_stats:
            msg.append(f"  • {stat['status']}: {stat['count']}")
        
        await m.answer("\n".join(msg))
        
    except Exception as e:
        log.error(f"Error in stats: {e}")
        await m.answer("❌ Ошибка при загрузке статистики.")

@router.message(Command("add_admin"))
async def add_admin(m: Message):
    """Добавить администратора"""
    try:
        parts = (m.text or "").split()
        if len(parts) < 2:
            await m.answer(
                "❌ <b>Неверный формат</b>\n\n"
                "Использование: /add_admin USER_ID\n\n"
                "Пример: /add_admin 123456789"
            )
            return
        
        try:
            new_admin_id = int(parts[1])
        except ValueError:
            await m.answer("❌ USER_ID должен быть числом")
            return
        
        # Проверяем, не является ли уже админом
        async with get_db() as db:
            cur = await db.execute(
                "SELECT * FROM admins WHERE tg_user_id=?", 
                (new_admin_id,)
            )
            existing = await cur.fetchone()
            
            if existing:
                if existing["is_active"] == 1:
                    await m.answer(f"ℹ️ Пользователь {new_admin_id} уже является администратором")
                else:
                    # Реактивируем деактивированного админа
                    await db.execute(
                        "UPDATE admins SET is_active=1 WHERE tg_user_id=?",
                        (new_admin_id,)
                    )
                    await db.commit()
                    await m.answer(f"✅ Администратор {new_admin_id} реактивирован")
            else:
                # Добавляем нового админа
                await db.execute(
                    "INSERT INTO admins (tg_user_id, role, is_active, added_at) "
                    "VALUES (?, 'admin', 1, datetime('now'))",
                    (new_admin_id,)
                )
                await db.commit()
                await m.answer(f"✅ Пользователь {new_admin_id} добавлен как администратор")
                
                # Логируем действие
                await log_action(str(m.from_user.id), "admin_added", {
                    "new_admin_id": new_admin_id
                })
        
    except Exception as e:
        log.error(f"Error in add_admin: {e}")
        await m.answer("❌ Ошибка при добавлении администратора")

@router.message(Command("rm_admin"))
async def remove_admin(m: Message):
    """Удалить администратора"""
    try:
        parts = (m.text or "").split()
        if len(parts) < 2:
            await m.answer(
                "❌ <b>Неверный формат</b>\n\n"
                "Использование: /rm_admin USER_ID\n\n"
                "Пример: /rm_admin 123456789"
            )
            return
        
        try:
            admin_id = int(parts[1])
        except ValueError:
            await m.answer("❌ USER_ID должен быть числом")
            return
        
        # Проверяем, что это не владелец
        if admin_id == settings.owner_id:
            await m.answer("❌ Нельзя удалить владельца бота")
            return
        
        async with get_db() as db:
            cur = await db.execute(
                "SELECT * FROM admins WHERE tg_user_id=?", 
                (admin_id,)
            )
            existing = await cur.fetchone()
            
            if not existing:
                await m.answer(f"❌ Пользователь {admin_id} не является администратором")
                return
            
            # Деактивируем админа (не удаляем полностью)
            await db.execute(
                "UPDATE admins SET is_active=0 WHERE tg_user_id=?",
                (admin_id,)
            )
            await db.commit()
            await m.answer(f"✅ Администратор {admin_id} удален")
            
            # Логируем действие
            await log_action(str(m.from_user.id), "admin_removed", {
                "removed_admin_id": admin_id
            })
        
    except Exception as e:
        log.error(f"Error in rm_admin: {e}")
        await m.answer("❌ Ошибка при удалении администратора")

@router.message(Command("message"))
async def message_client_start(m: Message, state: FSMContext):
    """Начать отправку сообщения клиенту"""
    try:
        parts = (m.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await m.answer(
                "❌ <b>Неверный формат</b>\n\n"
                "Использование: /message USER_ID\n\n"
                "Пример: /message 123456789\n\n"
                "После отправки команды напишите текст сообщения."
            )
            return
        
        try:
            client_id = int(parts[1])
        except ValueError:
            await m.answer("❌ USER_ID должен быть числом")
            return
        
        # Проверяем существование клиента
        async with get_db() as db:
            cur = await db.execute(
                "SELECT id FROM crm_customers WHERE id=?",
                (str(client_id),)
            )
            customer = await cur.fetchone()
            
            if not customer:
                await m.answer(f"❌ Клиент {client_id} не найден в базе данных")
                return
        
        # Сохраняем ID клиента в FSM и ждем текст сообщения
        await state.set_state(MessageClientFSM.WaitingText)
        await state.update_data(client_id=client_id)
        
        await m.answer(
            f"💬 <b>Отправка сообщения клиенту {client_id}</b>\n\n"
            "Напишите текст сообщения, которое хотите отправить:"
        )
        
    except Exception as e:
        log.error(f"Error in message_client_start: {e}")
        await m.answer("❌ Ошибка при начале отправки сообщения")

@router.message(MessageClientFSM.WaitingText)
async def message_client_send(m: Message, state: FSMContext, bot: Bot):
    """Отправить сообщение клиенту"""
    try:
        data = await state.get_data()
        client_id = data.get("client_id")
        
        if not client_id:
            await m.answer("❌ Ошибка: ID клиента не найден. Начните заново с /message")
            await state.clear()
            return
        
        message_text = m.text
        if not message_text or len(message_text.strip()) == 0:
            await m.answer("❌ Сообщение не может быть пустым. Попробуйте снова:")
            return
        
        # Отправляем сообщение клиенту
        try:
            await bot.send_message(
                client_id,
                f"💬 <b>Сообщение от администратора:</b>\n\n{message_text}"
            )
            
            await m.answer(
                f"✅ Сообщение успешно отправлено клиенту {client_id}"
            )
            
            # Логируем действие
            await log_action(str(m.from_user.id), "message_sent", {
                "client_id": client_id,
                "message_preview": message_text[:100]
            })
            
        except Exception as e:
            log.error(f"Failed to send message to client {client_id}: {e}")
            await m.answer(
                f"❌ Не удалось отправить сообщение клиенту {client_id}\n"
                f"Возможно, пользователь заблокировал бота."
            )
        
        await state.clear()
        
    except Exception as e:
        log.error(f"Error in message_client_send: {e}")
        await m.answer("❌ Ошибка при отправке сообщения")
        await state.clear()

@router.message(Command("orders_waiting"))
async def orders_waiting(m: Message):
    """Список заказов, ожидающих одобрения/отклонения"""
    try:
        async with get_db() as db:
            cur = await db.execute(
                "SELECT o.id, COALESCE(o.updated_at, o.created_at) AS ts, c.tg_link, c.phone "
                "FROM crm_orders o "
                "LEFT JOIN crm_customers c ON o.customer_id = c.id "
                "WHERE o.awaiting_decision = 1 AND o.id IS NOT NULL "
                "ORDER BY ts DESC LIMIT 20"
            )
            rows = await cur.fetchall()

        if not rows:
            await m.answer("📋 <b>Заявки на одобрение/отклонение</b>\n\n🤷‍♂️ Нет заказов, ожидающих обработки.")
            return

        msg = [f"📋 <b>Заявки на одобрение/отклонение ({len(rows)} шт.)</b>\n"]
        for r in rows:
            oid = r["id"]
            oid_short = oid[:8] if isinstance(oid, str) else (str(oid) if oid is not None else "—")
            client_info = r["phone"] or r["tg_link"] or f"ID:{oid_short}"
            msg.append(f"  • <code>{oid}</code> | Клиент: {client_info}")

        msg.append("\n💡 <i>Используйте</i> <code>/order ID</code> <i>для подробной информации и обработки</i>")
        await m.answer("\n".join(msg))

    except Exception as e:
        logging.getLogger("admin").error(f"Error in orders_waiting: {e}", exc_info=True)
        await m.answer("❌ Ошибка при загрузке заявок.")


@router.message(CMD("order"))
async def order_open(m: Message):
    """Открыть карточку заказа"""
    parts = (m.text or "").split()
    if len(parts) < 2:
        await m.answer("Использование: /order ID")
        return
        
    order_id = parts[1]
    
    try:
        async with get_db() as db:
            cur = await db.execute(
                "SELECT o.*, c.tg_link, c.address, c.phone FROM crm_orders o "
                "LEFT JOIN crm_customers c ON o.customer_id = c.id WHERE o.id=?", 
                (order_id,)
            )
            r = await cur.fetchone()
        
        if not r:
            await m.answer("❌ Заказ не найден")
            return
            
        # Формируем карточку
        msg = [f"<b>Заказ #{r['id']}</b>"]
        msg.append(f"👤 <b>Клиент:</b> {r['tg_link'] or r['phone'] or r['customer_id']}")
        msg.append(f"📍 <b>Адрес:</b> {r['address'] or '-'}")
        msg.append(f"📦 <b>Источник:</b> {r['source_type']}")
        msg.append(f"🔗 <b>Ref:</b> {r['poizon_or_stock_ref']}")
        if r['size']:
            msg.append(f"👟 <b>Размер:</b> {r['size']}")
        
        # Если заказ ожидает решения - показываем кнопки для одобрения/отклонения
        if r['awaiting_decision'] == 1:
            msg.append(f"⏳ <b>Статус:</b> Ожидает решения администратора")
            keyboard = ikb_admin_order_actions(order_id, is_approved=False)
            await m.answer("\n".join(msg), reply_markup=keyboard)
        else:
            # Если заказ уже одобрен - просто показываем информацию без кнопок
            msg.append(f"📊 <b>Статус:</b> {r['status']}")
            msg.append(f"💳 <b>Оплата:</b> {r['payment_status']}")
            msg.append(f"💰 <b>Суммы:</b> poizon={r['poizon_price']} var={r['order_cost_var']} fix={r['order_cost_fixed']} margin={r['margin']} final={r['final_price']}")
            await m.answer("\n".join(msg))
        
    except Exception as e:
        log.error(f"Error in order_open: {e}")
        await m.answer("❌ Ошибка при загрузке заказа.")

@router.callback_query(F.data.startswith("ORDER:APPROVE:"))
async def cb_approve_order(cq: CallbackQuery, state: FSMContext):
    """Начало процесса одобрения заказа"""
    order_id = cq.data.split(":", 2)[2]
    log.info(f"Callback APPROVE received for order {order_id} from user {cq.from_user.id}")
    
    try:
        async with get_db() as db:
            cur = await db.execute(
                "SELECT * FROM crm_orders WHERE id=?", 
                (order_id,)
            )
            order = await cur.fetchone()
            
            if not order:
                await cq.answer("Заказ не найден", show_alert=True)
                return
                
            # Проверяем, что заказ ожидает решения
            if order["awaiting_decision"] != 1:
                await cq.answer("Этот заказ уже обработан", show_alert=True)
                return
                
            # Сохраняем данные заказа в FSM
            await state.set_state(OrderApprovalFSM.WaitingPoizonPrice)
            await state.update_data(order_id=order_id)
            
            await cq.message.answer(
                f"💰 <b>Одобрение заказа #{order_id}</b>\n\n"
                f"Введите цену на POIZON в CNY (целое число):"
            )
            await cq.answer()
            
    except Exception as e:
        log.error(f"Error in approve order: {e}")
        await cq.answer("Ошибка при обработке заказа", show_alert=True)

@router.callback_query(F.data.startswith("ORDER:REJECT:"))
async def cb_reject_order(cq: CallbackQuery, state: FSMContext):
    """Отклонение заказа"""
    order_id = cq.data.split(":", 2)[2]
    log.info(f"Callback REJECT received for order {order_id} from user {cq.from_user.id}")
    
    try:
        async with get_db() as db:
            cur = await db.execute(
                "SELECT * FROM crm_orders WHERE id=?", 
                (order_id,)
            )
            order = await cur.fetchone()
            
            if not order:
                await cq.answer("Заказ не найден", show_alert=True)
                return
                
            # Проверяем, что заказ ожидает решения
            if order["awaiting_decision"] != 1:
                await cq.answer("Этот заказ уже обработан", show_alert=True)
                return
                
            # Сохраняем данные заказа в FSM
            await state.set_state(OrderApprovalFSM.WaitingRejectReason)
            await state.update_data(order_id=order_id)
            
            await cq.message.answer(
                f"❌ <b>Отклонение заказа #{order_id}</b>\n\n"
                f"Укажите причину отклонения:"
            )
            await cq.answer()
            
    except Exception as e:
        log.error(f"Error in reject order: {e}")
        await cq.answer("Ошибка при обработке заказа", show_alert=True)

@router.message(OrderApprovalFSM.WaitingPoizonPrice)
async def process_poizon_price(m: Message, state: FSMContext):
    """Обработка цены на POIZON"""
    try:
        # Валидация ввода (целое число)
        price_text = m.text.strip()
        try:
            price = int(price_text)
            if price <= 0:
                raise ValueError("Цена должна быть положительным числом")
        except ValueError:
            await m.answer("❌ Пожалуйста, введите корректное целое число.")
            return
            
        # Сохраняем цену и переходим к следующему шагу
        await state.update_data(poizon_price=price)
        await state.set_state(OrderApprovalFSM.WaitingVariableCosts)
        
        await m.answer(
            f"💰 Цена на POIZON: {price} CNY\n\n"
            f"Введите индивидуальные траты в рублях (целое число):"
        )
        
    except Exception as e:
        log.error(f"Error processing POIZON price: {e}")
        await m.answer("❌ Произошла ошибка. Попробуйте еще раз.")
        await state.clear()

@router.message(OrderApprovalFSM.WaitingVariableCosts)
async def process_variable_costs(m: Message, state: FSMContext, bot: Bot):
    """Обработка индивидуальных трат"""
    try:
        # Валидация ввода (целое число)
        costs_text = m.text.strip()
        try:
            costs = int(costs_text)
            if costs < 0:
                raise ValueError("Траты не могут быть отрицательными")
        except ValueError:
            await m.answer("❌ Пожалуйста, введите корректное целое число.")
            return
            
        # Сохраняем траты
        state_data = await state.get_data()
        poizon_price = state_data["poizon_price"]
        order_id = state_data["order_id"]
        
        # Получаем курс CNY (принудительно с +1 рублем)
        from app.services.exchange import force_refresh_cny_rate
        cny_rate = await force_refresh_cny_rate()
        if not cny_rate:
            await m.answer("❌ Не удалось получить актуальный курс CNY. Попробуйте позже.")
            return
            
        # Получаем финансовые настройки из Sheets
        margin_pct, fixed_costs = read_fin_settings()
        
        # Рассчитываем итоговую цену
        total_rub = calc_total_rub(
            price_cny=poizon_price,
            rate=Decimal(str(cny_rate)),
            indiv=costs,
            fixed=fixed_costs,
            margin_pct=margin_pct
        )
        
        # Сохраняем все данные
        await state.update_data(
            var_costs=costs,
            cny_rate=cny_rate,
            fixed_costs=fixed_costs,
            margin_pct=margin_pct,
            total_rub=total_rub
        )
        
        # Формируем сводку для подтверждения
        summary = [
            f"📊 <b>Расчет стоимости заказа #{order_id}</b>\n",
            f"🏷️ Цена на POIZON: {poizon_price} CNY",
            f"💱 Курс CNY→RUB: {cny_rate:.2f}",
            f"💰 В рублях: {poizon_price * cny_rate:.2f} ₽",
            f"📌 Индивидуальные траты: {costs} ₽",
            f"📈 Постоянные траты: {fixed_costs} ₽",
            f"📊 Маржа {margin_pct}%: {(poizon_price * cny_rate + costs + fixed_costs) * (margin_pct / 100):.2f} ₽",
            f"\n💵 <b>Итоговая цена: {total_rub} ₽</b>"
        ]
        
        # Переходим к подтверждению
        await state.set_state(OrderApprovalFSM.ConfirmingCalculation)
        
        await m.answer(
            "\n".join(summary),
            reply_markup=ikb_confirm_calculation()
        )
        
    except Exception as e:
        log.error(f"Error processing variable costs: {e}")
        await m.answer("❌ Произошла ошибка. Попробуйте еще раз.")
        await state.clear()

@router.callback_query(F.data == "CALC:CONFIRM", OrderApprovalFSM.ConfirmingCalculation)
async def confirm_calculation(cq: CallbackQuery, state: FSMContext, bot: Bot):
    """Подтверждение расчета и публикация заказа"""
    try:
        # Получаем все данные
        data = await state.get_data()
        order_id = data["order_id"]
        poizon_price = data["poizon_price"]
        var_costs = data["var_costs"]
        fixed_costs = data["fixed_costs"]
        margin_pct = data["margin_pct"]
        total_rub = data["total_rub"]
        cny_rate = data["cny_rate"]
        
        # Обновляем заказ в БД
        async with get_db() as db:
            # Получаем текущие данные заказа
            cur = await db.execute(
                "SELECT customer_id, source_type, poizon_or_stock_ref, size FROM crm_orders WHERE id=?",
                (order_id,)
            )
            order = await cur.fetchone()
            
            if not order:
                await cq.answer("Заказ не найден", show_alert=True)
                return
                
            # Обновляем заказ
            await db.execute(
                """UPDATE crm_orders SET 
                poizon_price=?, order_cost_var=?, order_cost_fixed=?,
                margin=?, final_price=?, status=?, awaiting_decision=0,
                rev=rev+1, updated_at=datetime('now'), ext_id=?
                WHERE id=?""",
                (
                    poizon_price, var_costs, fixed_costs,
                    margin_pct, total_rub, ORDER_STATUSES["AWAITING_PAYMENT"],
                    order_id, order_id
                )
            )
            await db.commit()
            
            # Добавляем в историю статусов
            await add_status_history(
                order_id, 
                "", 
                ORDER_STATUSES["AWAITING_PAYMENT"], 
                f"admin_{cq.from_user.id}", 
                "order_approved"
            )
            
            # Логируем действие
            await log_action(str(cq.from_user.id), "order_approved", {
                "order_id": order_id,
                "poizon_price": poizon_price,
                "var_costs": var_costs,
                "fixed_costs": fixed_costs,
                "margin_pct": margin_pct,
                "total_rub": total_rub
            })
        
        # Публикуем заказ в Google Sheets (используем полные названия колонок)
        order_data = {
            ORDER_COLUMNS["ID"]: order_id,
            ORDER_COLUMNS["CUSTOMER_ID"]: order["customer_id"],
            ORDER_COLUMNS["SOURCE_TYPE"]: order["source_type"],
            ORDER_COLUMNS["POIZON_REF"]: order["poizon_or_stock_ref"],
            ORDER_COLUMNS["SIZE"]: order["size"],
            ORDER_COLUMNS["CARRIER"]: "",
            ORDER_COLUMNS["TRACKING"]: "",
            ORDER_COLUMNS["STATUS"]: ORDER_STATUSES["AWAITING_PAYMENT"],
            ORDER_COLUMNS["POIZON_PRICE"]: poizon_price,
            ORDER_COLUMNS["VAR_COSTS"]: var_costs,
            ORDER_COLUMNS["FIXED_COSTS"]: fixed_costs,
            ORDER_COLUMNS["MARGIN"]: margin_pct,
            ORDER_COLUMNS["FINAL_PRICE"]: total_rub,
            ORDER_COLUMNS["PAYMENT_URL"]: "",
            ORDER_COLUMNS["PAYMENT_STATUS"]: "unpaid",
            ORDER_COLUMNS["REV"]: 1,
            ORDER_COLUMNS["UPDATED_AT"]: datetime.now().isoformat()
        }
        
        # Добавляем строку в Sheets
        sheet_row_id = append_order_row(order_data)
        
        if sheet_row_id:
            # Обновляем sheet_row_id в БД
            async with get_db() as db:
                await db.execute(
                    "UPDATE crm_orders SET sheet_row_id=? WHERE id=?",
                    (sheet_row_id, order_id)
                )
                await db.commit()
        
        # Создаем платеж через YooKassa
        payment_data = await create_payment_with_yookassa(
            order_id=order_id,
            amount=float(total_rub),
            description=f"Оплата заказа #{order_id}"
        )
        
        # Обновляем payment_url в БД и Google Sheets сразу после создания платежа
        if payment_data and payment_data.get('url'):
            # Сохраняем payment_url в БД
            try:
                async with get_db() as db:
                    await db.execute(
                        "UPDATE crm_orders SET payment_url=?, updated_at=datetime('now'), rev=rev+1 WHERE id=?",
                        (payment_data['url'], order_id)
                    )
                    await db.commit()
                log.info(f"Updated payment_url in DB for order {order_id}")
            except Exception as e:
                log.error(f"Failed to update payment_url in DB: {e}")
            
            # Обновляем payment_url в Google Sheets
            try:
                partial_update_by_ext_id(
                    SHEET_NAMES["ORDERS"],
                    order_id,
                    {
                        "PAYMENT_URL": payment_data['url'],
                        "PAYMENT_STATUS": "unpaid",
                        "UPDATED_AT": datetime.now().isoformat()
                    }
                )
                log.info(f"Updated payment_url in Google Sheets for order {order_id}")
            except Exception as e:
                log.error(f"Failed to update payment_url in Sheets: {e}")
        
        # Уведомляем клиента
        try:
            customer_id = order["customer_id"]
            if payment_data:
                # Уведомляем клиента с реальной ссылкой на оплату
                await bot.send_message(
                    int(customer_id),
                    f"🎉 <b>Ваш заказ #{order_id} одобрен!</b>\n\n"
                    f"💰 <b>Сумма к оплате:</b> {total_rub} ₽\n\n"
                    f"Для оплаты нажмите кнопку ниже:\n\n"
                    f"ℹ️ <i>После оплаты статус обновится автоматически в течение 2-3 минут</i>",
                    reply_markup=ikb_payment_button(order_id, float(total_rub))
                )
            else:
                # Fallback на тестовую кнопку если YooKassa недоступна
                await bot.send_message(
                    int(customer_id),
                    f"🎉 <b>Ваш заказ #{order_id} одобрен!</b>\n\n"
                    f"💰 <b>Сумма к оплате:</b> {total_rub} ₽\n\n"
                    f"Для подтверждения оплаты нажмите кнопку ниже:",
                    reply_markup=ikb_payment_test_button(order_id)
                )
        except Exception as e:
            log.error(f"Failed to notify customer: {e}")
        
        # Завершаем FSM
        await state.clear()
        
        await cq.message.answer(f"✅ Заказ #{order_id} одобрен и опубликован в Google Sheets")
        await cq.answer()
        
    except Exception as e:
        log.error(f"Error confirming calculation: {e}")
        await cq.answer("Ошибка при обработке заказа", show_alert=True)
        await state.clear()

@router.callback_query(F.data == "CALC:CANCEL", OrderApprovalFSM.ConfirmingCalculation)
async def cancel_calculation(cq: CallbackQuery, state: FSMContext):
    """Отмена расчета"""
    await state.clear()
    await cq.message.answer("❌ Расчет отменен. Начните процесс заново.")
    await cq.answer()

@router.message(OrderApprovalFSM.WaitingRejectReason)
async def process_reject_reason(m: Message, state: FSMContext, bot: Bot):
    """Обработка причины отклонения заказа"""
    try:
        # Получаем данные из FSM
        data = await state.get_data()
        order_id = data["order_id"]
        reason = m.text.strip()
        
        if not reason:
            await m.answer("❌ Пожалуйста, укажите причину отклонения.")
            return
        
        # Обновляем заказ в БД
        async with get_db() as db:
            # Получаем данные о заказе
            cur = await db.execute(
                "SELECT customer_id FROM crm_orders WHERE id=?",
                (order_id,)
            )
            order = await cur.fetchone()
            
            if not order:
                await m.answer("❌ Заказ не найден")
                await state.clear()
                return
                
            customer_id = order["customer_id"]
            
            # Помечаем заказ как отклоненный
            await db.execute(
                """UPDATE crm_orders SET 
                awaiting_decision=0, 
                status=?, 
                updated_at=datetime('now')
                WHERE id=?""",
                (ORDER_STATUSES["REJECTED"], order_id)
            )
            await db.commit()
            
            # Добавляем в историю статусов
            await add_status_history(
                order_id, 
                "", 
                ORDER_STATUSES["REJECTED"], 
                f"admin_{m.from_user.id}", 
                f"order_rejected: {reason}"
            )
            
            # Логируем действие
            await log_action(str(m.from_user.id), "order_rejected", {
                "order_id": order_id,
                "reason": reason
            })
        
        # Уведомляем клиента
        try:
            await bot.send_message(
                int(customer_id),
                f"❌ <b>Ваш заказ #{order_id} отклонен</b>\n\n"
                f"<b>Причина:</b> {reason}\n\n"
                f"По всем вопросам обращайтесь к <a href='tg://user?id={settings.owner_id}'>администратору</a>"
            )
        except Exception as e:
            log.error(f"Failed to notify customer about rejection: {e}")
        
        # Завершаем FSM
        await state.clear()
        
        await m.answer(f"✅ Заказ #{order_id} отклонен. Клиент уведомлен.")
        
    except Exception as e:
        log.error(f"Error processing reject reason: {e}")
        await m.answer("❌ Произошла ошибка при отклонении заказа.")
        await state.clear()
