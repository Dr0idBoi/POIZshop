#!/usr/bin/env python3
"""
Скрипт для принудительной проверки статуса платежей через YooKassa API
"""

import asyncio
import sys
import os

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.payments import check_all_pending_payments
from app.logger import setup_logging

async def main():
    """Принудительная проверка всех ожидающих платежей"""
    setup_logging()
    
    print("🔍 Запуск принудительной проверки платежей...")
    
    try:
        updated_count = await check_all_pending_payments()
        
        if updated_count > 0:
            print(f"✅ Обновлено {updated_count} платежей")
        else:
            print("ℹ️ Нет платежей для обновления")
            
    except Exception as e:
        print(f"❌ Ошибка при проверке платежей: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
