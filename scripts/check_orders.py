"""Проверка заказов в БД"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import aiosqlite

async def main():
    db = await aiosqlite.connect('data/app.db')
    db.row_factory = aiosqlite.Row
    
    print("\n📦 ЗАКАЗЫ В БД:\n")
    cur = await db.execute("""
        SELECT id, customer_id, source_type, poizon_or_stock_ref, 
               status, awaiting_decision, final_price, created_at 
        FROM crm_orders 
        ORDER BY created_at DESC
    """)
    orders = await cur.fetchall()
    
    for o in orders:
        print(f"ID: {o['id']}")
        print(f"  Customer: {o['customer_id']}")
        print(f"  Type: {o['source_type']}")
        print(f"  Ref: {o['poizon_or_stock_ref']}")
        print(f"  Status: {o['status']}")
        print(f"  Awaiting: {o['awaiting_decision']}")
        print(f"  Price: {o['final_price']}")
        print(f"  Created: {o['created_at']}")
        print()
    
    print(f"\n👥 КЛИЕНТЫ В БД:\n")
    cur = await db.execute("SELECT id, tg_link, phone FROM crm_customers")
    customers = await cur.fetchall()
    
    for c in customers:
        print(f"ID: {c['id']}, Phone: {c['phone']}, TG: {c['tg_link']}")
    
    await db.close()

if __name__ == "__main__":
    asyncio.run(main())

