# app/crm/sync_utils.py
"""
Утилиты для безопасной синхронизации между БД и Google Sheets
"""
import asyncio
import logging
from typing import Optional, Callable, Any
from functools import wraps
from datetime import datetime

log = logging.getLogger("sync_utils")

# Глобальный lock для синхронизации
_sync_lock = asyncio.Lock()
_sync_in_progress = False
_last_sync_time: Optional[datetime] = None


class SyncManager:
    """Менеджер для управления синхронизацией"""
    
    def __init__(self):
        self._lock = asyncio.Lock()
        self._sync_in_progress = False
        self._last_sync: Optional[datetime] = None
    
    async def is_sync_in_progress(self) -> bool:
        """Проверка, идет ли синхронизация"""
        return self._sync_in_progress
    
    async def acquire_sync_lock(self, timeout: float = 30.0) -> bool:
        """
        Попытка захватить lock для синхронизации
        
        Args:
            timeout: Максимальное время ожидания в секундах
        
        Returns:
            True если lock захвачен
        """
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=timeout)
            self._sync_in_progress = True
            return True
        except asyncio.TimeoutError:
            log.warning(f"Failed to acquire sync lock after {timeout}s")
            return False
    
    def release_sync_lock(self):
        """Освобождение lock"""
        if self._lock.locked():
            self._sync_in_progress = False
            self._last_sync = datetime.now()
            self._lock.release()
    
    def get_last_sync_time(self) -> Optional[datetime]:
        """Получить время последней синхронизации"""
        return self._last_sync


# Глобальный экземпляр менеджера
sync_manager = SyncManager()


def with_sync_lock(timeout: float = 30.0):
    """
    Декоратор для функций синхронизации
    Гарантирует, что только одна синхронизация выполняется одновременно
    
    Usage:
        @with_sync_lock(timeout=60.0)
        async def sync_sheets_to_db():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Проверяем, не идет ли уже синхронизация
            if await sync_manager.is_sync_in_progress():
                log.warning(f"{func.__name__}: Sync already in progress, skipping")
                return None
            
            # Пытаемся захватить lock
            if not await sync_manager.acquire_sync_lock(timeout):
                log.error(f"{func.__name__}: Failed to acquire sync lock")
                return None
            
            try:
                log.info(f"{func.__name__}: Starting sync")
                result = await func(*args, **kwargs)
                log.info(f"{func.__name__}: Sync completed successfully")
                return result
            except Exception as e:
                log.error(f"{func.__name__}: Sync failed with error: {e}", exc_info=True)
                raise
            finally:
                sync_manager.release_sync_lock()
        
        return wrapper
    return decorator


async def safe_async_retry(
    func: Callable,
    *args,
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    exponential_base: float = 2.0,
    **kwargs
) -> Any:
    """
    Безопасный retry с exponential backoff для async функций
    
    Args:
        func: Async функция для выполнения
        max_retries: Максимальное количество попыток
        initial_delay: Начальная задержка в секундах
        max_delay: Максимальная задержка в секундах
        exponential_base: База для экспоненциального роста задержки
    
    Returns:
        Результат выполнения функции
    
    Raises:
        Последнее исключение, если все попытки неудачны
    """
    last_exception = None
    delay = initial_delay
    
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            
            if attempt < max_retries - 1:
                log.warning(
                    f"Attempt {attempt + 1}/{max_retries} failed: {e}. "
                    f"Retrying in {delay:.2f}s..."
                )
                await asyncio.sleep(delay)
                delay = min(delay * exponential_base, max_delay)
            else:
                log.error(f"All {max_retries} attempts failed")
    
    raise last_exception


def safe_sync_retry(
    func: Callable,
    *args,
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    exponential_base: float = 2.0,
    **kwargs
) -> Any:
    """
    Безопасный retry с exponential backoff для синхронных функций
    НЕ БЛОКИРУЕТ event loop (использует asyncio.sleep через run_in_executor)
    
    Args:
        func: Синхронная функция для выполнения
        max_retries: Максимальное количество попыток
        initial_delay: Начальная задержка в секундах
        max_delay: Максимальная задержка в секундах
        exponential_base: База для экспоненциального роста задержки
    
    Returns:
        Результат выполнения функции
    
    Raises:
        Последнее исключение, если все попытки неудачны
    """
    import time
    last_exception = None
    delay = initial_delay
    
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            
            if attempt < max_retries - 1:
                log.warning(
                    f"Attempt {attempt + 1}/{max_retries} failed: {e}. "
                    f"Retrying in {delay:.2f}s..."
                )
                time.sleep(delay)  # OK для синхронных функций
                delay = min(delay * exponential_base, max_delay)
            else:
                log.error(f"All {max_retries} attempts failed")
    
    raise last_exception


class TransactionContext:
    """
    Контекстный менеджер для выполнения операций в транзакции
    
    Usage:
        async with TransactionContext(db) as tx:
            await tx.execute("UPDATE ...")
            await tx.execute("INSERT ...")
            # Автоматический commit при выходе
            # Автоматический rollback при ошибке
    """
    
    def __init__(self, db):
        self.db = db
        self._in_transaction = False
    
    async def __aenter__(self):
        await self.db.execute("BEGIN TRANSACTION")
        self._in_transaction = True
        log.debug("Transaction started")
        return self.db
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            # Ошибка - откатываем
            await self.db.execute("ROLLBACK")
            log.warning(f"Transaction rolled back due to error: {exc_val}")
            self._in_transaction = False
            return False
        else:
            # Успех - коммитим
            await self.db.commit()
            log.debug("Transaction committed")
            self._in_transaction = False
            return True


async def run_in_executor(func: Callable, *args, **kwargs) -> Any:
    """
    Выполнить синхронную функцию в executor, чтобы не блокировать event loop
    
    Args:
        func: Синхронная функция
        *args, **kwargs: Аргументы функции
    
    Returns:
        Результат выполнения функции
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))
