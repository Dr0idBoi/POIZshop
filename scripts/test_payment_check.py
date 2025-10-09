#!/usr/bin/env python3
"""
Тестовый скрипт для проверки статуса платежей
"""

import asyncio
import sys
import os

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.payments import check_all_pending_payments
from app.services.yookassa_service import yookassa_service
from app.logger import setup_logging
from app.db import get_db

async def test_payment_check():
    """Тестирование проверки платежей"""
    setup_logging()
    
    print("🔍 Тестирование проверки платежей...")
    
    try:
        # Проверяем настройки YooKassa
        print(f"YooKassa Shop ID: {yookassa_service.shop_id if hasattr(yookassa_service, 'shop_id') else 'Not set'}")
        print(f"Mock mode: {yookassa_service._mock_mode}")
        
        # Проверяем заказы с ожидающими платежами
        async with get_db() as db:
            cur = await db.execute(
                """SELECT o.id, o.status, o.payment_url, p.metadata_json, p.status as payment_status
                FROM crm_orders o
                LEFT JOIN payments p ON o.id = p.order_id
                WHERE o.payment_url IS NOT NULL 
                AND o.payment_url != ''
                ORDER BY o.updated_at DESC
                LIMIT 5"""
            )
            orders = await cur.fetchall()
            
            print(f"\n📋 Найдено {len(orders)} заказов с payment_url:")
            for order in orders:
                print(f"  • Order {order['id']}: status={order['status']}, payment_status={order['payment_status']}")
        
        # Запускаем проверку
        print("\n🔄 Запуск проверки платежей...")
        updated_count = await check_all_pending_payments()
        
        if updated_count > 0:
            print(f"✅ Обновлено {updated_count} платежей")
        else:
            print("ℹ️ Нет платежей для обновления")
            
    except Exception as e:
        print(f"❌ Ошибка при тестировании: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0

if __name__ == "__main__":
    exit_code = asyncio.run(test_payment_check())
    sys.exit(exit_code)
