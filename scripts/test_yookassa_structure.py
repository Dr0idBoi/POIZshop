#!/usr/bin/env python3
"""
Тестовый скрипт для проверки структуры YooKassaService
"""

import sys
import os

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.yookassa_service import yookassa_service

def test_service_structure():
    """Тестирование структуры сервиса"""
    
    print("🔍 Проверка структуры YooKassaService...")
    
    # Проверяем наличие методов
    methods = ['create_payment', 'get_payment_status', 'check_payment_status']
    
    for method in methods:
        if hasattr(yookassa_service, method):
            print(f"  ✅ Метод {method} существует")
        else:
            print(f"  ❌ Метод {method} НЕ НАЙДЕН")
    
    # Проверяем режим работы
    print(f"\n📊 Режим работы:")
    print(f"  Mock mode: {yookassa_service._mock_mode}")
    if hasattr(yookassa_service, 'shop_id'):
        print(f"  Shop ID: {yookassa_service.shop_id}")
    else:
        print(f"  Shop ID: не настроен (mock mode)")
    
    print("\n✅ Проверка структуры завершена")

if __name__ == "__main__":
    test_service_structure()
