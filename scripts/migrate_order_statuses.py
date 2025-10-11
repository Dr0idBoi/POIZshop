"""
Миграция: добавление новых статусов заказов
Добавляет 'Ожидает решения' и 'Отклонён' в список допустимых статусов
"""
import asyncio
import aiosqlite
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "app.db"

async def migrate():
    """Пересоздаёт таблицу crm_orders с новыми статусами"""
    print(f"🔄 Начинаем миграцию БД: {DB_PATH}")
    
    async with aiosqlite.connect(DB_PATH) as db:
        # Включаем поддержку foreign keys
        await db.execute("PRAGMA foreign_keys=OFF")
        
        # Начинаем транзакцию
        await db.execute("BEGIN TRANSACTION")
        
        try:
            # 1. Переименовываем старую таблицу
            print("  📦 Сохраняем старые данные...")
            await db.execute("ALTER TABLE crm_orders RENAME TO crm_orders_old")
            
            # 2. Создаём новую таблицу с обновлённым CHECK constraint
            print("  🔨 Создаём новую таблицу...")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS crm_orders (
                    id TEXT PRIMARY KEY,
                    customer_id TEXT NOT NULL,
                    source_type TEXT NOT NULL CHECK (source_type IN ('stock', 'poizon_link')),
                    poizon_or_stock_ref TEXT DEFAULT '',
                    size TEXT DEFAULT '',
                    carrier TEXT DEFAULT '',
                    tracking TEXT DEFAULT '',
                    status TEXT DEFAULT 'Ожидает оплату' CHECK (status IN ('Ожидает решения', 'Ожидает оплату', 'Отклонён', 'Оплачен', 'В пути по Китаю', 'На пути на склад', 'На пути к получателю', 'Выдано', 'Закрыт', 'Отмена')),
                    poizon_price REAL DEFAULT 0.0,
                    order_cost_var REAL DEFAULT 0.0,
                    order_cost_fixed REAL DEFAULT 0.0,
                    margin REAL DEFAULT 0.0,
                    final_price REAL DEFAULT 0.0,
                    payment_url TEXT DEFAULT '',
                    payment_status TEXT DEFAULT 'не оплачено',
                    awaiting_decision INTEGER DEFAULT 0,
                    ext_id TEXT DEFAULT '',
                    sheet_row_id INTEGER DEFAULT NULL,
                    deleted_in_sheets_at TEXT DEFAULT NULL,
                    updated_at TEXT DEFAULT (datetime('now')),
                    rev INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (customer_id) REFERENCES crm_customers(id) ON DELETE CASCADE
                )
            """)
            
            # 3. Копируем данные из старой таблицы
            print("  📋 Копируем данные...")
            await db.execute("""
                INSERT INTO crm_orders 
                SELECT * FROM crm_orders_old
            """)
            
            # 4. Удаляем старую таблицу
            print("  🗑️  Удаляем старую таблицу...")
            await db.execute("DROP TABLE crm_orders_old")
            
            # 5. Пересоздаём индексы
            print("  🔗 Восстанавливаем индексы...")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_orders_customer_id ON crm_orders(customer_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_orders_ext_id ON crm_orders(ext_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON crm_orders(status)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_orders_deleted ON crm_orders(deleted_in_sheets_at)")
            
            # Коммитим транзакцию
            await db.commit()
            await db.execute("PRAGMA foreign_keys=ON")
            
            print("✅ Миграция завершена успешно!")
            
        except Exception as e:
            print(f"❌ Ошибка миграции: {e}")
            await db.execute("ROLLBACK")
            await db.execute("PRAGMA foreign_keys=ON")
            raise

if __name__ == "__main__":
    asyncio.run(migrate())


