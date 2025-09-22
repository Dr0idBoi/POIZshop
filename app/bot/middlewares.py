# app/bot/middlewares.py
from aiogram import BaseMiddleware
from typing import Callable, Dict, Any, Awaitable
from aiogram.types import Message, CallbackQuery
from ..config import settings
from ..db import open_db

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

        # проверим таблицу admins
        db = await open_db()
        cur = await db.execute(
            "SELECT 1 FROM admins WHERE tg_user_id=? AND is_active=1", (user.id,)
        )
        row = await cur.fetchone()
        await db.close()

        if row:
            return await handler(event, data)

        # нет прав
        if isinstance(event, Message):
            await event.answer("⛔ У вас нет прав для этой команды.")
        elif isinstance(event, CallbackQuery):
            try:
                await event.answer("⛔ Нет прав", show_alert=True)
            except Exception:
                pass
        return
