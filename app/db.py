# app/db.py
import aiosqlite
import asyncio
import logging
from pathlib import Path

log = logging.getLogger("db")

DB_PATH = Path("data/app.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS crm_customers (
  id TEXT PRIMARY KEY,
  tg_link TEXT,
  phone TEXT,
  address TEXT,
  ref_code TEXT,
  invited_by TEXT,
  order_ids_json TEXT,
  updated_at TEXT,
  rev INTEGER
);

CREATE TABLE IF NOT EXISTS crm_orders (
  id TEXT PRIMARY KEY,
  customer_id TEXT,
  source_type TEXT, -- stock|poizon_link
  poizon_or_stock_ref TEXT,
  size TEXT,
  carrier TEXT,
  tracking TEXT,
  status TEXT,
  poizon_price REAL,
  order_cost_var REAL,
  order_cost_fixed REAL,
  margin REAL,
  final_price REAL,
  payment_url TEXT,
  payment_status TEXT,
  updated_at TEXT,
  rev INTEGER
);

CREATE TABLE IF NOT EXISTS crm_fixed_costs (
  id TEXT PRIMARY KEY,
  name TEXT,
  kind TEXT,            -- fixed|percent
  value REAL,
  scope TEXT,           -- all|stock|poizon_link
  active INTEGER,
  comment TEXT,
  updated_at TEXT,
  rev INTEGER
);

CREATE TABLE IF NOT EXISTS admins (
  tg_user_id INTEGER PRIMARY KEY,
  role TEXT,
  is_active INTEGER,
  added_at TEXT
);

CREATE TABLE IF NOT EXISTS referrals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  referrer_id TEXT,
  referred_customer_id TEXT,
  order_id TEXT,
  amount INTEGER,
  is_paid INTEGER,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS order_status_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id TEXT,
  old_status TEXT,
  new_status TEXT,
  reason TEXT,
  actor TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS action_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor TEXT,
  scope TEXT,
  payload_json TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT,
  payload_json TEXT,
  status TEXT,
  due_at TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS exchange_rates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  base TEXT,
  quote TEXT,
  rate REAL,
  as_of TEXT
);

CREATE TABLE IF NOT EXISTS payments (
  id TEXT PRIMARY KEY,
  order_id TEXT,
  provider TEXT,
  amount REAL,
  status TEXT,
  idempotence_key TEXT,
  created_at TEXT,
  updated_at TEXT
);
"""

async def open_db():
    db = await aiosqlite.connect(DB_PATH.as_posix())
    db.row_factory = aiosqlite.Row
    await db.executescript(DDL)
    await db.commit()
    log.info("SQLite initialized at %s", DB_PATH)
    return db

async def ensure_owner(db, owner_id: int):
    await db.execute(
        "INSERT OR IGNORE INTO admins(tg_user_id, role, is_active, added_at) "
        "VALUES (?, 'owner', 1, datetime('now'))", (owner_id,)
    )
    await db.commit()
