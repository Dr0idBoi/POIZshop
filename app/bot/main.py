# app/bot/main.py
import asyncio
import logging
from aiogram import Bot, Dispatcher, Router
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from .handlers import client, admin
from ..config import settings
from ..db import open_db, ensure_owner
from ..logger import setup_logging
from ..services.tasks import create_scheduler, wire_jobs

# Глобальный обработчик ошибок: бот отвечает пользователю и логирует
errors_router = Router(name="errors")

@errors_router.errors()
async def global_error_handler(event, bot: Bot):
    """
    event: ErrorEvent (структура с .exception и .update)
    """
    exc = getattr(event, "exception", None)
    upd = getattr(event, "update", None)

    # постараемся ответить пользователю
    try:
        # message
        msg = getattr(upd, "message", None) if upd else None
        if msg:
            await bot.send_message(
                chat_id=msg.chat.id,
                text="⚠️ Произошла ошибка при обработке запроса. Мы уже разбираемся."
            )
        # callback
        cq = getattr(upd, "callback_query", None) if upd else None
        if cq:
            try:
                await bot.answer_callback_query(cq.id, "⚠️ Ошибка. Попробуйте ещё раз.", show_alert=True)
            except Exception:
                pass
    finally:
        logging.exception("Unhandled exception", exc_info=exc)
    # вернуть True — чтобы прекратить дальнейшую обработку этой ошибки
    return True

async def start_scheduler(bot: Bot):
    s = create_scheduler()
    wire_jobs(s, bot)  # ← передаём bot внутрь джобов (для нотификаций из синка)
    s.start()
    logging.getLogger("scheduler").info("APScheduler started")

async def run_bot():
    setup_logging()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher()
    # ошибки — первым роутером, чтобы ловить всё
    dp.include_router(errors_router)
    dp.include_routers(client.router, admin.router)

    db = await open_db()
    await ensure_owner(db, settings.owner_id)
    await db.close()

    await start_scheduler(bot)
    await dp.start_polling(bot)

async def main():
    await run_bot()
