"""
Скрипт миграции базы данных SQLite для проекта ZakazPOIZ Bot
Добавляет необходимые поля для синхронизации с Google Sheets
Безопасная версия - проверяет наличие колонок перед их добавлением
"""
import asyncio
import aiosqlite
import logging
import sys
import shutil
from pathlib import Path
from datetime import datetime

# Добавляем корневую директорию в путь для импортов
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
from app.logger import setup_logging

# Функция для проверки наличия колонки в таблице
async def column_exists(db, table, column):
    """Проверяет, существует ли колонка в таблице"""
    cur = await db.execute(f"PRAGMA table_info({table})")
    columns = await cur.fetchall()
    return any(col[1] == column for col in columns)

async def run_migration():
    """Выполнение миграции базы данных"""
    setup_logging()
    log = logging.getLogger("migration")
    
    log.info(f"Starting database migration for {settings.db_path}")
    
    # Создаем бэкап
    backup_path = settings.db_path.with_suffix(f".bak.{int(datetime.now().timestamp())}")
    try:
        shutil.copy2(settings.db_path, backup_path)
        log.info(f"Created database backup at {backup_path}")
    except Exception as e:
        log.error(f"Failed to create backup: {e}")
        return
    
    try:
        # Подключаемся к БД
        db = await aiosqlite.connect(str(settings.db_path))
        
        # Проверяем и добавляем колонки в crm_orders
        if not await column_exists(db, "crm_orders", "ext_id"):
            await db.execute("ALTER TABLE crm_orders ADD COLUMN ext_id TEXT DEFAULT NULL")
            log.info("Added ext_id column to crm_orders")
        
        if not await column_exists(db, "crm_orders", "rev"):
            await db.execute("ALTER TABLE crm_orders ADD COLUMN rev INTEGER DEFAULT 0")
            log.info("Added rev column to crm_orders")
        
        if not await column_exists(db, "crm_orders", "sheet_row_id"):
            await db.execute("ALTER TABLE crm_orders ADD COLUMN sheet_row_id INTEGER DEFAULT NULL")
            log.info("Added sheet_row_id column to crm_orders")
        
        if not await column_exists(db, "crm_orders", "deleted_in_sheets_at"):
            await db.execute("ALTER TABLE crm_orders ADD COLUMN deleted_in_sheets_at TEXT DEFAULT NULL")
            log.info("Added deleted_in_sheets_at column to crm_orders")
        
        if not await column_exists(db, "crm_orders", "awaiting_decision"):
            await db.execute("ALTER TABLE crm_orders ADD COLUMN awaiting_decision INTEGER DEFAULT 1")
            log.info("Added awaiting_decision column to crm_orders")
        
        # Заполнение ext_id для существующих записей (используем id)
        await db.execute("UPDATE crm_orders SET ext_id = id WHERE ext_id IS NULL")
        log.info("Updated ext_id for existing orders")
        
        # Проверяем и добавляем колонки в crm_customers
        if not await column_exists(db, "crm_customers", "ext_id"):
            await db.execute("ALTER TABLE crm_customers ADD COLUMN ext_id TEXT DEFAULT NULL")
            log.info("Added ext_id column to crm_customers")
        
        if not await column_exists(db, "crm_customers", "rev"):
            await db.execute("ALTER TABLE crm_customers ADD COLUMN rev INTEGER DEFAULT 0")
            log.info("Added rev column to crm_customers")
        
        if not await column_exists(db, "crm_customers", "sheet_row_id"):
            await db.execute("ALTER TABLE crm_customers ADD COLUMN sheet_row_id INTEGER DEFAULT NULL")
            log.info("Added sheet_row_id column to crm_customers")
        
        if not await column_exists(db, "crm_customers", "deleted_in_sheets_at"):
            await db.execute("ALTER TABLE crm_customers ADD COLUMN deleted_in_sheets_at TEXT DEFAULT NULL")
            log.info("Added deleted_in_sheets_at column to crm_customers")
        
        # Заполнение ext_id для существующих записей (используем id)
        await db.execute("UPDATE crm_customers SET ext_id = id WHERE ext_id IS NULL")
        log.info("Updated ext_id for existing customers")
        
        # Проверяем и добавляем колонки в payments
        if await column_exists(db, "payments", "id"):  # Проверяем, существует ли таблица payments
            if not await column_exists(db, "payments", "metadata_json"):
                await db.execute("ALTER TABLE payments ADD COLUMN metadata_json TEXT DEFAULT '{}'")
                log.info("Added metadata_json column to payments")
        
        # Создание индексов для оптимизации
        await db.execute("CREATE INDEX IF NOT EXISTS idx_orders_ext_id ON crm_orders(ext_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_orders_sheet_row_id ON crm_orders(sheet_row_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_orders_awaiting_decision ON crm_orders(awaiting_decision)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_customers_ext_id ON crm_customers(ext_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_customers_sheet_row_id ON crm_customers(sheet_row_id)")
        log.info("Created indexes for optimization")
        
        # Фиксируем изменения
        await db.commit()
        
        log.info("Migration completed successfully")
        
    except Exception as e:
        log.error(f"Migration failed: {e}")
        log.info(f"You can restore from backup at {backup_path}")
    finally:
        if 'db' in locals():
            await db.close()

if __name__ == "__main__":
    asyncio.run(run_migration())
