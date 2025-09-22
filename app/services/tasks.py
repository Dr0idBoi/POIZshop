# app/services/tasks.py  (добавлен синк Sheets→SQLite + уведомления)
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from aiogram import Bot

from .exchange import fetch_cbr_rate
from ..db import open_db
from .exports import export_table
from ..crm.sheets import pull_orders, pull_customers, pull_costs
from ..services.links import carrier_link

log = logging.getLogger("scheduler")

def create_scheduler() -> AsyncIOScheduler:
    return AsyncIOScheduler(timezone="UTC")

async def job_update_rates():
    db = await open_db()
    rate = await fetch_cbr_rate("CNY")
    if rate:
        await db.execute(
            "INSERT INTO exchange_rates(base,quote,rate,as_of) VALUES('CNY','RUB',?, datetime('now'))",
            (rate,)
        )
        await db.commit()
        log.info("CNY rate updated: %.4f", rate)
    await db.close()

async def job_export_logs():
    db = await open_db()
    cur = await db.execute("SELECT * FROM action_logs ORDER BY created_at DESC LIMIT 5000")
    rows = [dict(r) for r in await cur.fetchall()]
    if rows:
        export_table(rows, headers=list(rows[0].keys()), fname_prefix="logs")
    await db.close()

def _intval(v, default=0):
    try:
        return int(v)
    except Exception:
        return default

def _float(v, default=0.0):
    try:
        return float(str(v).replace(",", "."))
    except Exception:
        return default

async def job_sync_crm_pull(bot: Bot):
    """
    Тянем данные из Google Sheets и сливаем в SQLite.
    Если по заказу поменялся статус/трек — уведомляем клиента.
    """
    # --- PULL ---
    try:
        orders = pull_orders()
    except Exception as e:
        log.exception("Sheets pull_orders failed")
        return
    try:
        customers = pull_customers()
    except Exception:
        customers = []
    try:
        costs = pull_costs()
    except Exception:
        costs = []

    db = await open_db()

    # --- customers ---
    for c in customers:
        await db.execute(
            "INSERT OR REPLACE INTO crm_customers(id, tg_link, phone, address, ref_code, invited_by, order_ids_json, updated_at, rev) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                c.get("ID Клиента",""),
                c.get("Ссылка на тг",""),
                c.get("Контактный номер",""),
                c.get("Адрес",""),
                c.get("Реф Код",""),
                c.get("приглашен кем",""),
                c.get("список заказов",""),
                c.get("updated_at",""),
                _intval(c.get("rev", 0)),
            )
        )

    # --- costs ---
    for x in costs:
        await db.execute(
            "INSERT OR REPLACE INTO crm_fixed_costs(id, name, kind, value, scope, active, comment, updated_at, rev) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                x.get("ID",""),
                x.get("Название",""),
                x.get("Тип",""),
                _float(x.get("Значение",0)),
                x.get("Применимость",""),
                1 if str(x.get("Активна","")).lower() in ("1","true","yes","да") else 0,
                x.get("Комментарий",""),
                x.get("updated_at",""),
                _intval(x.get("rev",0))
            )
        )

    # --- orders (с уведомлениями) ---
    for o in orders:
        oid = o.get("ID Заказа","")
        cid = o.get("ID клиента","")
        new_status = o.get("статус","")
        new_carrier = o.get("перевозчик","")
        new_track = o.get("трекинговый код","")
        new_rev = _intval(o.get("rev", 0))

        # текущее в БД
        cur = await db.execute("SELECT rev, status, carrier, tracking FROM crm_orders WHERE id=?", (oid,))
        old = await cur.fetchone()

        # апсерт кэша
        await db.execute(
            "INSERT OR REPLACE INTO crm_orders("
            "id, customer_id, source_type, poizon_or_stock_ref, size, carrier, tracking, status, "
            "poizon_price, order_cost_var, order_cost_fixed, margin, final_price, payment_url, payment_status, updated_at, rev"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                oid,
                cid,
                o.get("откуда",""),
                o.get("ссылка на поизон / номер из стока",""),
                o.get("размер",""),
                new_carrier,
                new_track,
                new_status,
                _float(o.get("цена на поизоне", 0.0)),
                _float(o.get("цена на издержки данного заказа", 0.0)),
                _float(o.get("издержки постоянные", 0.0)),
                _float(o.get("маржа", 0.0)),
                _float(o.get("финальная цена", 0.0)),
                o.get("ссылка на оплату",""),
                o.get("статус оплаты",""),
                o.get("updated_at",""),
                new_rev
            )
        )

        # если был старый ряд и rev вырос — проверим изменения для уведомлений
        if old and new_rev > int(old["rev"] or 0):
            try:
                chat_id = int(cid)
            except Exception:
                chat_id = None

            if chat_id:
                # статус изменился
                if new_status and new_status != (old["status"] or ""):
                    try:
                        await bot.send_message(chat_id, f"📦 Ваш заказ #{oid}: статус → <b>{new_status}</b>")
                    except Exception:
                        pass
                # трек/перевозчик изменились
                if new_track and (new_track != (old["tracking"] or "") or new_carrier != (old["carrier"] or "")):
                    url = carrier_link(new_carrier, new_track)
                    try:
                        txt = f"🚚 Трек-код для заказа #{oid}: <code>{new_track}</code>"
                        if url:
                            txt += f"\nПроверить: {url}"
                        await bot.send_message(chat_id, txt)
                    except Exception:
                        pass

    await db.commit()
    await db.close()
    log.info("Sheets → SQLite sync done (%d orders, %d customers, %d costs)", len(orders), len(customers), len(costs))

def wire_jobs(s: AsyncIOScheduler, bot: Bot):
    # ежедневные
    s.add_job(job_update_rates, CronTrigger(hour=5, minute=5))   # UTC≈MSK-3
    s.add_job(job_export_logs, CronTrigger(hour=5, minute=15))
    # синк каждые 2 минуты
    s.add_job(job_sync_crm_pull, IntervalTrigger(minutes=2), args=[bot])
