# app/bot/fsm_utils.py
"""
Утилиты для работы с FSM состояниями
"""
import time
import logging
from functools import wraps
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from typing import Optional, Callable, Any

log = logging.getLogger("fsm_utils")

# Константы
FSM_TIMEOUT = 1800  # 30 минут
FSM_TIMESTAMP_KEY = "_fsm_last_update"
FSM_PROCESSING_KEY = "_fsm_processing"


async def check_fsm_timeout(state: FSMContext) -> bool:
    """
    Проверяет, истек ли таймаут FSM состояния
    
    Returns:
        True если таймаут истек и состояние было очищено
    """
    try:
        data = await state.get_data()
        last_update = data.get(FSM_TIMESTAMP_KEY)
        
        if last_update and (time.time() - last_update) > FSM_TIMEOUT:
            log.info(f"FSM timeout expired, clearing state")
            await state.clear()
            return True
            
        return False
    except Exception as e:
        log.error(f"Error checking FSM timeout: {e}")
        return False


async def update_fsm_timestamp(state: FSMContext):
    """Обновляет timestamp последнего обновления FSM"""
    try:
        await state.update_data(**{FSM_TIMESTAMP_KEY: time.time()})
    except Exception as e:
        log.error(f"Error updating FSM timestamp: {e}")


async def is_fsm_processing(state: FSMContext) -> bool:
    """
    Проверяет, обрабатывается ли сейчас FSM процесс
    Защита от race conditions
    """
    try:
        data = await state.get_data()
        return data.get(FSM_PROCESSING_KEY, False)
    except Exception as e:
        log.error(f"Error checking FSM processing: {e}")
        return False


async def set_fsm_processing(state: FSMContext, processing: bool = True):
    """Устанавливает флаг обработки FSM"""
    try:
        await state.update_data(**{FSM_PROCESSING_KEY: processing})
    except Exception as e:
        log.error(f"Error setting FSM processing: {e}")


def auto_clear_on_error(error_message: str = "❌ Произошла ошибка. Попробуйте позже."):
    """
    Декоратор для автоматической очистки FSM при ошибках
    
    Usage:
        @auto_clear_on_error("Ошибка создания заказа")
        async def my_handler(m: Message, state: FSMContext):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(message: Message, state: FSMContext, *args, **kwargs):
            try:
                # Проверяем таймаут перед выполнением
                if await check_fsm_timeout(state):
                    await message.answer(
                        "⏱️ Время ожидания истекло. Пожалуйста, начните заново.",
                        reply_markup=None
                    )
                    return
                
                # Обновляем timestamp
                await update_fsm_timestamp(state)
                
                # Выполняем функцию
                return await func(message, state, *args, **kwargs)
                
            except Exception as e:
                log.error(f"Error in {func.__name__}: {e}", exc_info=True)
                await state.clear()
                await message.answer(error_message)
                
        return wrapper
    return decorator


async def safe_clear_state(state: FSMContext, message: Optional[Message] = None):
    """
    Безопасная очистка FSM состояния с логированием
    
    Args:
        state: FSM контекст
        message: Опциональное сообщение для логирования
    """
    try:
        current_state = await state.get_state()
        if current_state:
            log.info(f"Clearing FSM state: {current_state}")
        await state.clear()
    except Exception as e:
        log.error(f"Error clearing FSM state: {e}")


def validate_message_length(text: str, max_length: int = 4000) -> tuple[bool, str]:
    """
    Валидация длины сообщения для Telegram
    
    Args:
        text: Текст сообщения
        max_length: Максимальная длина (по умолчанию 4000, оставляем запас)
    
    Returns:
        (is_valid, error_message or truncated_text)
    """
    if not text:
        return False, "Сообщение не может быть пустым"
    
    if len(text) > max_length:
        truncated = text[:max_length - 50] + "\n\n... (сообщение обрезано)"
        return False, truncated
    
    return True, text


async def handle_cancel_command(message: Message, state: FSMContext, return_markup=None):
    """
    Универсальный обработчик команды отмены
    
    Args:
        message: Сообщение пользователя
        state: FSM контекст
        return_markup: Клавиатура для возврата в главное меню
    """
    await safe_clear_state(state, message)
    await message.answer(
        "❌ Операция отменена.",
        reply_markup=return_markup
    )
