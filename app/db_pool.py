# app/db_pool.py
"""
Connection pool для SQLite с защитой от перегрузки
"""
import aiosqlite
import asyncio
import logging
from typing import Optional
from contextlib import asynccontextmanager
from datetime import datetime

from .config import settings

log = logging.getLogger("db_pool")


class DatabasePool:
    """
    Connection pool для SQLite
    
    Особенности:
    - Ограниченное количество соединений
    - Переиспользование соединений
    - Автоматическая очистка
    - Graceful shutdown
    """
    
    def __init__(self, db_path: str, pool_size: int = 5, timeout: float = 30.0):
        """
        Args:
            db_path: Путь к файлу БД
            pool_size: Максимальное количество соединений
            timeout: Таймаут ожидания свободного соединения
        """
        self.db_path = db_path
        self.pool_size = pool_size
        self.timeout = timeout
        self._pool: asyncio.Queue = asyncio.Queue(maxsize=pool_size)
        self._initialized = False
        self._closed = False
        self._active_connections = 0
        self._total_acquired = 0
        self._total_released = 0
    
    async def initialize(self):
        """Инициализация пула соединений"""
        if self._initialized:
            log.warning("Pool already initialized")
            return
        
        log.info(f"Initializing connection pool with {self.pool_size} connections")
        
        for i in range(self.pool_size):
            try:
                conn = await aiosqlite.connect(
                    self.db_path,
                    timeout=10.0,
                    check_same_thread=False
                )
                conn.row_factory = aiosqlite.Row
                await conn.execute("PRAGMA foreign_keys=ON")
                await conn.execute("PRAGMA busy_timeout=5000")
                await conn.execute("PRAGMA journal_mode=WAL")
                await self._pool.put(conn)
                log.debug(f"Created connection {i+1}/{self.pool_size}")
            except Exception as e:
                log.error(f"Failed to create connection {i+1}: {e}")
                # Закрываем уже созданные соединения
                await self.close()
                raise
        
        self._initialized = True
        log.info(f"Connection pool initialized successfully")
    
    @asynccontextmanager
    async def acquire(self):
        """
        Получить соединение из пула
        
        Usage:
            async with pool.acquire() as conn:
                await conn.execute("SELECT ...")
        """
        if not self._initialized:
            raise RuntimeError("Pool not initialized. Call initialize() first.")
        
        if self._closed:
            raise RuntimeError("Pool is closed")
        
        conn = None
        try:
            # Ждем свободное соединение с таймаутом
            conn = await asyncio.wait_for(
                self._pool.get(),
                timeout=self.timeout
            )
            
            self._active_connections += 1
            self._total_acquired += 1
            
            log.debug(f"Connection acquired (active: {self._active_connections}/{self.pool_size})")
            
            yield conn
            
        except asyncio.TimeoutError:
            log.error(f"Timeout waiting for connection (waited {self.timeout}s)")
            raise RuntimeError(f"Failed to acquire connection within {self.timeout}s")
        
        finally:
            if conn:
                try:
                    # Возвращаем соединение в пул
                    await self._pool.put(conn)
                    self._active_connections -= 1
                    self._total_released += 1
                    log.debug(f"Connection released (active: {self._active_connections}/{self.pool_size})")
                except Exception as e:
                    log.error(f"Error releasing connection: {e}")
    
    async def close(self):
        """Закрыть все соединения в пуле"""
        if self._closed:
            return
        
        log.info("Closing connection pool...")
        self._closed = True
        
        # Закрываем все соединения
        closed_count = 0
        while not self._pool.empty():
            try:
                conn = await asyncio.wait_for(self._pool.get(), timeout=1.0)
                await conn.close()
                closed_count += 1
            except asyncio.TimeoutError:
                break
            except Exception as e:
                log.error(f"Error closing connection: {e}")
        
        log.info(f"Closed {closed_count} connections")
        self._initialized = False
    
    def get_stats(self) -> dict:
        """Получить статистику пула"""
        return {
            "pool_size": self.pool_size,
            "active_connections": self._active_connections,
            "available_connections": self._pool.qsize(),
            "total_acquired": self._total_acquired,
            "total_released": self._total_released,
            "initialized": self._initialized,
            "closed": self._closed
        }


# Глобальный пул соединений
_db_pool: Optional[DatabasePool] = None


async def init_pool(pool_size: int = 5):
    """Инициализировать глобальный пул соединений"""
    global _db_pool
    
    if _db_pool is not None:
        log.warning("Pool already exists, closing old pool")
        await _db_pool.close()
    
    _db_pool = DatabasePool(
        db_path=str(settings.db_path),
        pool_size=pool_size,
        timeout=30.0
    )
    
    await _db_pool.initialize()
    log.info("Global database pool initialized")


async def close_pool():
    """Закрыть глобальный пул соединений"""
    global _db_pool
    
    if _db_pool:
        await _db_pool.close()
        _db_pool = None
        log.info("Global database pool closed")


@asynccontextmanager
async def get_db_from_pool():
    """
    Получить соединение из глобального пула
    
    Usage:
        async with get_db_from_pool() as db:
            await db.execute("SELECT ...")
    """
    if _db_pool is None:
        raise RuntimeError("Database pool not initialized. Call init_pool() first.")
    
    async with _db_pool.acquire() as conn:
        yield conn


def get_pool_stats() -> dict:
    """Получить статистику глобального пула"""
    if _db_pool is None:
        return {"error": "Pool not initialized"}
    
    return _db_pool.get_stats()
