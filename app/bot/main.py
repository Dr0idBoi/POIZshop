# app/bot/main.py
import logging
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery
from aiogram import F
import re
from .handlers import client, admin
from ..config import settings
from ..db import open_db, get_db, ensure_owner, create_customer_if_not_exists, init_db # Добавляем init_db
from ..logger import setup_logging
from ..services.tasks import create_scheduler, wire_jobs

# Глобальная переменная для бота (для использования в других модулях)
bot: Bot = None

# Глобальный обработчик ошибок
async def global_error_handler(event, bot: Bot):
    """Глобальный обработчик ошибок для всех роутеров"""
    exc = getattr(event, "exception", None)
    upd = getattr(event, "update", None)
    
    # Логируем ошибку
    logging.exception("Unhandled exception in bot", exc_info=exc)
    
    # Пытаемся ответить пользователю
    try:
        if upd:
            msg = getattr(upd, "message", None)
            if msg:
                await bot.send_message(
                    msg.chat.id, 
                    "⚠️ Произошла ошибка. Мы уже разбираемся с проблемой."
                )
            
            cq = getattr(upd, "callback_query", None)
            if cq:
                try:
                    await bot.answer_callback_query(
                        cq.id, 
                        "⚠️ Ошибка. Попробуйте ещё раз.", 
                        show_alert=True
                    )
                except Exception:
                    pass
    except Exception as e:
        logging.error(f"Failed to send error message to user: {e}")
    
    return True  # Прекращаем дальнейшую обработку ошибки

async def start_scheduler(bot: Bot):
    """Запуск планировщика задач"""
    try:
        scheduler = create_scheduler()
        wire_jobs(scheduler, bot)
        scheduler.start()
        logging.getLogger("scheduler").info("APScheduler started successfully")
        return scheduler
    except Exception as e:
        logging.error(f"Failed to start scheduler: {e}")
        raise

async def setup_bot_data(bot: Bot):
    """Настройка начальных данных бота"""
    try:
        # Инициализируем БД (создаем таблицы, если их нет)
        await init_db()
        
        # Обеспечиваем наличие владельца в админах
        async with get_db() as db:
            await ensure_owner(db, settings.owner_id)
        
        # Создаем владельца как клиента, если его нет
        owner_id = str(settings.owner_id)
        tg_link = f"tg://user?id={settings.owner_id}"
        await create_customer_if_not_exists(owner_id, tg_link)
        
        logging.info("Bot data setup completed")
        
    except Exception as e:
        logging.error(f"Failed to setup bot data: {e}")
        raise

async def create_bot() -> Bot:
    """Создание и настройка бота"""
    global bot
    try:
        bot = Bot(
            token=settings.bot_token.get_secret_value(),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        
        # Проверяем токен
        bot_info = await bot.get_me()
        logging.info(f"Bot initialized: @{bot_info.username} ({bot_info.first_name})")
        
        return bot
    except Exception as e:
        logging.error(f"Failed to create bot: {e}")
        raise

async def create_dispatcher() -> Dispatcher:
    """Создание и настройка диспетчера"""
    try:
        # Используем MemoryStorage для FSM
        storage = MemoryStorage()
        dp = Dispatcher(storage=storage)
        
        # Подключаем роутеры в правильном порядке
        dp.include_router(admin.router)  # Админ роутер первым (с middleware)
        dp.include_router(client.router)  # Клиентский роутер вторым
        
        # Подключаем глобальный обработчик ошибок
        dp.errors.register(global_error_handler)
        
        # Добавляем логирование необработанных обновлений
        #@dp.update.outer_middleware()
        #async def log_unhandled_updates(handler, event, data):
         #   try:
        #        result = await handler(event, data)
        #        if result is False:
        #            logging.warning(f"Unhandled update: {event}")
        #        return result
        #    except Exception as e:
        #        logging.error(f"Error in update handling: {e}")
        #        return False
        
        logging.info("Dispatcher configured successfully")
        return dp
        
    except Exception as e:
        logging.error(f"Failed to create dispatcher: {e}")
        raise

async def run_bot():
    """Основная функция запуска бота"""
    try:
        # Настройка логирования
        setup_logging()
        logging.info("Starting bot application...")
        
        # Создание бота и диспетчера
        bot = await create_bot()
        dp = await create_dispatcher()
        
        # Настройка данных бота
        await setup_bot_data(bot)
        
        # Запуск планировщика
        scheduler = await start_scheduler(bot)
        
        try:
            # Запуск polling
            logging.info("Starting polling...")
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
            
        finally:
            # Остановка планировщика при завершении
            if scheduler and scheduler.running:
                scheduler.shutdown()
                logging.info("Scheduler stopped")
                
    except KeyboardInterrupt:
        logging.info("Bot stopped by user")
    except Exception as e:
        logging.error(f"Critical error in bot: {e}")
        raise
    finally:
        logging.info("Bot application finished")

async def main():
    """Точка входа приложения"""
    try:
        await run_bot()
    except Exception as e:
        logging.critical(f"Failed to start bot application: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
