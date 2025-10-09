# app/bot/middlewares.py
import time
import logging
from aiogram import BaseMiddleware
from typing import Callable, Dict, Any, Awaitable
from aiogram.types import Message, CallbackQuery
from ..config import settings
from ..db import get_db

log = logging.getLogger("middlewares")

class AdminOnly(BaseMiddleware):
    """
    Пускает владельца (OWNER_ID) и активных админов из таблицы admins.
    Иначе отвечает "Нет прав" и прерывает обработку.
    """
    async def __call__(
        self,
        handler: Callable[[Dict[str, Any], Any], Awaitable[Any]],
        event,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return

        if user.id == settings.owner_id:
            return await handler(event, data)

        async with get_db() as db:
            cur = await db.execute(
                "SELECT 1 FROM admins WHERE tg_user_id=? AND is_active=1", (user.id,)
            )
            row = await cur.fetchone()

        if row:
            return await handler(event, data)

        if isinstance(event, Message):
            await event.answer("⛔ У вас нет прав для этой команды.")
        elif isinstance(event, CallbackQuery):
            try:
                await event.answer("⛔ Нет прав", show_alert=True)
            except Exception:
                pass
        return

class ClientOnly(BaseMiddleware):
    """
    Пропускает ТОЛЬКО не-админов. Владельца и активных админов перенаправляет к админским командам.
    """
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)
        
        # Проверяем является ли пользователь админом
        is_admin = False
        if user.id == settings.owner_id:
            is_admin = True
        else:
            async with get_db() as db:
                cur = await db.execute("SELECT 1 FROM admins WHERE tg_user_id=? AND is_active=1", (user.id,))
                row = await cur.fetchone()
                if row:
                    is_admin = True
        
        # Если админ - отвечаем что это админская панель, не вызываем клиентский handler
        if is_admin:
            if isinstance(event, Message):
                # Для команды /start пропускаем (она обрабатывается в самом handler)
                if event.text and event.text.startswith("/start"):
                    return await handler(event, data)
                # Для остальных команд показываем что это админ
                await event.answer(
                    "⚙️ Вы администратор. Используйте админские команды:\n"
                    "/start - Админ-панель\n"
                    "/orders_waiting - Заявки на обработку\n"
                    "/stats - Статистика"
                )
            return  # Не вызываем клиентский handler для админов
        
        # Обычный клиент - пропускаем к клиентским handlers
        return await handler(event, data)


class RateLimitMiddleware(BaseMiddleware):
    """
    Middleware для защиты от флуда
    Ограничивает количество запросов от одного пользователя
    """
    def __init__(self, rate_limit: int = 5, time_window: int = 60):
        """
        Args:
            rate_limit: Максимальное количество запросов
            time_window: Временное окно в секундах
        """
        self.rate_limit = rate_limit
        self.time_window = time_window
        self.user_requests: Dict[int, list] = {}
        super().__init__()
    
    async def __call__(
        self,
        handler: Callable[[Dict[str, Any], Any], Awaitable[Any]],
        event,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)
        
        user_id = user.id
        current_time = time.time()
        
        # Инициализируем список запросов для пользователя
        if user_id not in self.user_requests:
            self.user_requests[user_id] = []
        
        # Очищаем старые запросы
        self.user_requests[user_id] = [
            req_time for req_time in self.user_requests[user_id]
            if current_time - req_time < self.time_window
        ]
        
        # Проверяем лимит
        if len(self.user_requests[user_id]) >= self.rate_limit:
            log.warning(f"Rate limit exceeded for user {user_id}")
            
            if isinstance(event, Message):
                await event.answer(
                    "⏱️ Слишком много запросов. Пожалуйста, подождите немного.",
                    show_quote=False
                )
            elif isinstance(event, CallbackQuery):
                await event.answer(
                    "⏱️ Слишком много запросов. Подождите.",
                    show_alert=True
                )
            return
        
        # Добавляем текущий запрос
        self.user_requests[user_id].append(current_time)
        
        # Продолжаем обработку
        return await handler(event, data)
