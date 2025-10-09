"""
Скрипт миграции базы данных SQLite для проекта ZakazPOIZ Bot
Добавляет необходимые поля для синхронизации с Google Sheets
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

# SQL для миграции
MIGRATION_SQL = """
-- Добавление полей в crm_orders
ALTER TABLE crm_orders ADD COLUMN ext_id TEXT DEFAULT NULL;
ALTER TABLE crm_orders ADD COLUMN rev INTEGER DEFAULT 0;
ALTER TABLE crm_orders ADD COLUMN sheet_row_id INTEGER DEFAULT NULL;
ALTER TABLE crm_orders ADD COLUMN deleted_in_sheets_at TEXT DEFAULT NULL;
ALTER TABLE crm_orders ADD COLUMN awaiting_decision INTEGER DEFAULT 1;

-- Заполнение ext_id для существующих записей (используем id)
UPDATE crm_orders SET ext_id = id WHERE ext_id IS NULL;

-- Добавление полей в crm_customers
ALTER TABLE crm_customers ADD COLUMN ext_id TEXT DEFAULT NULL;
ALTER TABLE crm_customers ADD COLUMN sheet_row_id INTEGER DEFAULT NULL;
ALTER TABLE crm_customers ADD COLUMN deleted_in_sheets_at TEXT DEFAULT NULL;

-- Заполнение ext_id для существующих записей (используем id)
UPDATE crm_customers SET ext_id = id WHERE ext_id IS NULL;

-- Добавление metadata_json в payments
ALTER TABLE payments ADD COLUMN metadata_json TEXT DEFAULT '{}';

-- Создание индексов для оптимизации
CREATE INDEX IF NOT EXISTS idx_orders_ext_id ON crm_orders(ext_id);
CREATE INDEX IF NOT EXISTS idx_orders_sheet_row_id ON crm_orders(sheet_row_id);
CREATE INDEX IF NOT EXISTS idx_orders_awaiting_decision ON crm_orders(awaiting_decision);
CREATE INDEX IF NOT EXISTS idx_customers_ext_id ON crm_customers(ext_id);
CREATE INDEX IF NOT EXISTS idx_customers_sheet_row_id ON crm_customers(sheet_row_id);
"""

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
        
        # Выполняем миграцию
        await db.executescript(MIGRATION_SQL)
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
