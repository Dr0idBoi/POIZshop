# app/services/tasks.py
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from aiogram import Bot

from app.config import settings
from app.db import get_db
from app.services.exports import export_table

log = logging.getLogger("scheduler")

def create_scheduler() -> AsyncIOScheduler:
    """Создание планировщика задач"""
    return AsyncIOScheduler(timezone=settings.tz)

async def job_export_logs():
    """Экспорт логов действий"""
    try:
        async with get_db() as db:
            cur = await db.execute("SELECT * FROM action_logs ORDER BY created_at DESC LIMIT 5000")
            rows = [dict(r) for r in await cur.fetchall()]
        
        if rows:
            export_table(rows, headers=list(rows[0].keys()), fname_prefix="logs")
            log.info(f"Exported {len(rows)} log entries")
    except Exception as e:
        log.error(f"Error exporting logs: {e}")

async def job_full_sync(bot: Bot):
    """
    Полная синхронизация всех компонентов системы (5 этапов):
    1. Pull из Google Sheets → SQLite
    2. Push из SQLite → Google Sheets
    3. Проверка статусов платежей YooKassa
    4. Обновление курсов валют
    5. Обновление списков заказов клиентов
    """
    try:
        log.info("=" * 60)
        log.info("Запуск автоматической полной синхронизации")
        log.info("=" * 60)
        
        from app.services.sync_manager import run_full_sync
        
        results = await run_full_sync(bot)
        
        if results.get("success"):
            log.info(f"✅ Автоматическая синхронизация завершена успешно за {results.get('duration_seconds', 0):.2f}с")
        else:
            log.warning(f"⚠️ Автоматическая синхронизация завершена с ошибками: {len(results.get('errors', []))} ошибок")
            
    except Exception as e:
        log.error(f"❌ Критическая ошибка в автоматической синхронизации: {e}", exc_info=True)

def wire_jobs(scheduler: AsyncIOScheduler, bot: Bot):
    """Подключение задач к планировщику"""
    try:
        # Ежедневные задачи
        scheduler.add_job(
            job_export_logs, 
            CronTrigger(hour=5, minute=15, timezone=settings.tz),
            id="export_logs",
            replace_existing=True
        )
        
        # Полная синхронизация каждые 5 минут (включает все 5 этапов)
        # Этап 1: Pull из Sheets → SQLite
        # Этап 2: Push из SQLite → Sheets
        # Этап 3: Проверка платежей YooKassa
        # Этап 4: Обновление курсов валют
        # Этап 5: Обновление списков заказов клиентов
        scheduler.add_job(
            job_full_sync,
            IntervalTrigger(minutes=5),
            args=[bot],
            id="full_sync",
            replace_existing=True
        )
        
        log.info("All scheduled jobs configured successfully")
        log.info("  - Export logs: daily at 05:15")
        log.info("  - Full sync (5 steps): every 5 minutes")
        
    except Exception as e:
        log.error(f"Error configuring scheduled jobs: {e}")
        raise
