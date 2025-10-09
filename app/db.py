# app/db.py
import aiosqlite
import asyncio
import logging
import json
from pathlib import Path
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager
from datetime import datetime

from .config import settings

log = logging.getLogger("db")

DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS crm_customers (
    id TEXT PRIMARY KEY,
    tg_link TEXT NOT NULL,
    phone TEXT DEFAULT '',
    address TEXT DEFAULT '',
    ref_code TEXT DEFAULT '',
    invited_by TEXT DEFAULT '',
    order_ids_json TEXT DEFAULT '[]',
    ext_id TEXT DEFAULT '',
    sheet_row_id INTEGER DEFAULT NULL,
    deleted_in_sheets_at TEXT DEFAULT NULL,
    updated_at TEXT DEFAULT (datetime('now')),
    rev INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS crm_orders (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('stock', 'poizon_link')),
    poizon_or_stock_ref TEXT DEFAULT '',
    size TEXT DEFAULT '',
    carrier TEXT DEFAULT '',
    tracking TEXT DEFAULT '',
    status TEXT DEFAULT 'Ожидает оплату' CHECK (status IN ('Ожидает оплату', 'Оплачен', 'В пути по Китаю', 'На пути на склад', 'На пути к получателю', 'Выдано', 'Закрыт', 'Отмена')),
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
);

CREATE TABLE IF NOT EXISTS crm_financial_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    value REAL NOT NULL,
    rev INTEGER DEFAULT 1,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS crm_order_status_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    changed_by TEXT DEFAULT 'system',
    reason TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (order_id) REFERENCES crm_orders(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS admins (
    tg_user_id INTEGER PRIMARY KEY,
    role TEXT NOT NULL DEFAULT 'admin',
    is_active INTEGER DEFAULT 1,
    added_at TEXT DEFAULT (datetime('now')),
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS referrals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_id TEXT NOT NULL,
    referred_customer_id TEXT NOT NULL,
    order_id TEXT,
    amount INTEGER DEFAULT 0,
    is_paid INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (referred_customer_id) REFERENCES crm_customers(id) ON DELETE CASCADE,
    FOREIGN KEY (order_id) REFERENCES crm_orders(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS order_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    old_status TEXT DEFAULT '',
    new_status TEXT NOT NULL,
    reason TEXT DEFAULT '',
    actor TEXT DEFAULT 'system',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (order_id) REFERENCES crm_orders(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS action_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    scope TEXT NOT NULL,
    payload_json TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    payload_json TEXT DEFAULT '{}',
    status TEXT DEFAULT 'pending',
    due_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS exchange_rates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    base TEXT NOT NULL,
    quote TEXT NOT NULL,
    rate REAL NOT NULL,
    as_of TEXT DEFAULT (datetime('now')),
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(base, quote, as_of)
);

CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'mock',
    amount REAL NOT NULL DEFAULT 0.0,
    status TEXT DEFAULT 'pending',
    idempotence_key TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (order_id) REFERENCES crm_orders(id) ON DELETE CASCADE
);

-- Индексы для оптимизации
CREATE INDEX IF NOT EXISTS idx_orders_customer_id ON crm_orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON crm_orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_updated_at ON crm_orders(updated_at);
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON crm_orders(created_at);
CREATE INDEX IF NOT EXISTS idx_payments_order_id ON payments(order_id);
CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id);
CREATE INDEX IF NOT EXISTS idx_status_history_order ON order_status_history(order_id);
CREATE INDEX IF NOT EXISTS idx_customers_ref_code ON crm_customers(ref_code);
CREATE INDEX IF NOT EXISTS idx_customers_invited_by ON crm_customers(invited_by);
CREATE INDEX IF NOT EXISTS idx_customers_created_at ON crm_customers(created_at);
"""

@asynccontextmanager
async def get_db():
    """Context manager for database connections with automatic retries"""
    db = None
    retries = 3
    retry_delay = 1.0
    
    while retries > 0:
        try:
            db = await aiosqlite.connect(str(settings.db_path))
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("PRAGMA busy_timeout=5000")
            yield db
            return
        except Exception as e:
            retries -= 1
            if retries == 0:
                log.error(f"Database connection failed after all retries: {e}")
                if db:
                    await db.rollback()
                raise
            log.warning(f"Database connection attempt failed, retrying in {retry_delay}s: {e}")
            await asyncio.sleep(retry_delay)
            retry_delay *= 2
        finally:
            if db:
                try:
                    await db.close()
                except Exception as e:
                    log.error(f"Error closing database connection: {e}")

async def init_db() -> None:
    """Initialize database with schema"""
    try:
        async with get_db() as db:
            await db.executescript(DDL)
            await db.commit()
            log.info("Database schema initialized at %s", settings.db_path)
    except Exception as e:
        log.error(f"Failed to initialize database: {e}")
        raise

async def ensure_owner(db: aiosqlite.Connection, owner_id: int) -> None:
    """Ensure owner exists in admins table"""
    try:
        await db.execute(
            "INSERT OR IGNORE INTO admins(tg_user_id, role, is_active, added_at) "
            "VALUES (?, 'owner', 1, datetime('now'))", 
            (owner_id,)
        )
        await db.commit()
        log.info(f"Owner {owner_id} ensured in admins table")
    except Exception as e:
        log.error(f"Failed to ensure owner: {e}")
        raise

async def create_customer_if_not_exists(customer_id: str, tg_link: str) -> bool:
    """Create customer if not exists, returns True if created"""
    try:
        async with get_db() as db:
            cur = await db.execute("SELECT id FROM crm_customers WHERE id=?", (customer_id,))
            exists = await cur.fetchone()
            
            if not exists:
                await db.execute(
                    "INSERT INTO crm_customers (id, tg_link, updated_at) "
                    "VALUES (?, ?, datetime('now'))",
                    (customer_id, tg_link)
                )
                await db.commit()
                log.info(f"Created customer {customer_id}")
                return True
            return False
    except Exception as e:
        log.error(f"Failed to create customer {customer_id}: {e}")
        raise

async def log_action(actor: str, scope: str, payload: Dict[str, Any]) -> None:
    """Log user action with error handling and validation"""
    try:
        if not actor or not scope:
            raise ValueError("Actor and scope are required")
            
        # Ensure payload is JSON serializable
        try:
            payload_json = json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            log.error(f"Invalid payload for action log: {e}")
            payload_json = json.dumps({"error": "Invalid payload", "original": str(payload)})
        
        async with get_db() as db:
            await db.execute(
                "INSERT INTO action_logs(actor, scope, payload_json, created_at) "
                "VALUES (?, ?, ?, datetime('now'))",
                (actor, scope, payload_json)
            )
            await db.commit()
            
    except Exception as e:
        log.error(f"Failed to log action: {e}")
        # Don't raise the exception to avoid disrupting the main flow
        # but make sure it's properly logged

async def add_status_history(
    order_id: str, 
    old_status: str, 
    new_status: str, 
    actor: str = "system", 
    reason: str = ""
) -> None:
    """Add order status change to history with validation"""
    try:
        if not order_id or not new_status:
            raise ValueError("Order ID and new status are required")
            
        async with get_db() as db:
            # Verify order exists
            cur = await db.execute("SELECT id FROM crm_orders WHERE id=?", (order_id,))
            if not await cur.fetchone():
                raise ValueError(f"Order {order_id} not found")
                
            await db.execute(
                "INSERT INTO order_status_history(order_id, old_status, new_status, reason, actor, created_at) "
                "VALUES (?, ?, ?, ?, ?, datetime('now'))",
                (order_id, old_status, new_status, reason, actor)
            )
            await db.commit()
            
    except Exception as e:
        log.error(f"Failed to add status history: {e}")
        raise

async def get_customer_orders(customer_id: str) -> List[Dict[str, Any]]:
    """Get all orders for a customer with error handling"""
    try:
        async with get_db() as db:
            cur = await db.execute(
                "SELECT * FROM crm_orders WHERE customer_id=? ORDER BY created_at DESC",
                (customer_id,)
            )
            return [dict(row) for row in await cur.fetchall()]
    except Exception as e:
        log.error(f"Failed to get orders for customer {customer_id}: {e}")
        raise

async def get_order_history(order_id: str) -> List[Dict[str, Any]]:
    """Get status history for an order"""
    try:
        async with get_db() as db:
            cur = await db.execute(
                "SELECT * FROM order_status_history WHERE order_id=? ORDER BY created_at DESC",
                (order_id,)
            )
            return [dict(row) for row in await cur.fetchall()]
    except Exception as e:
        log.error(f"Failed to get history for order {order_id}: {e}")
        raise

# Backward compatibility
async def open_db() -> aiosqlite.Connection:
    """Legacy function for backward compatibility - prefer get_db() context manager"""
    try:
        db = await aiosqlite.connect(str(settings.db_path))
        db.row_factory = aiosqlite.Row
        await db.executescript(DDL)
        await db.commit()
        return db
    except Exception as e:
        log.error(f"Failed to open database: {e}")
        raise
