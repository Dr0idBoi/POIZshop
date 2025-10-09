"""
Скрипт для очистки ошибочных записей с None/пустыми ID из БД и Google Sheets
"""
import asyncio
import sys
from pathlib import Path

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

import aiosqlite
import gspread
from app.config import settings
from app.constants import SHEET_NAMES, CUSTOMER_COLUMNS, ORDER_COLUMNS


async def cleanup_database():
    """Удаляет записи с невалидными ID из базы данных"""
    print("🔍 Проверка базы данных...")
    
    db = await aiosqlite.connect(str(settings.db_path))
    db.row_factory = aiosqlite.Row
    
    try:
        # Находим клиентов с None или пустыми ID
        cur = await db.execute(
            """SELECT rowid, id FROM crm_customers 
            WHERE id IS NULL OR id = '' OR id = 'None' OR TRIM(id) = ''"""
        )
        invalid_customers = await cur.fetchall()
        
        if invalid_customers:
            print(f"❌ Найдено {len(invalid_customers)} клиентов с невалидными ID:")
            for customer in invalid_customers:
                print(f"   - rowid: {customer['rowid']}, id: {customer['id']}")
            
            # Удаляем
            await db.execute(
                """DELETE FROM crm_customers 
                WHERE id IS NULL OR id = '' OR id = 'None' OR TRIM(id) = ''"""
            )
            print(f"✅ Удалено {len(invalid_customers)} клиентов из БД")
        else:
            print("✅ В БД нет клиентов с невалидными ID")
        
        # Находим заказы с None в ID (критическая ошибка - удаляем)
        cur = await db.execute(
            """SELECT rowid, id, customer_id FROM crm_orders 
            WHERE id IS NULL OR id = '' OR id = 'None' OR TRIM(id) = ''"""
        )
        invalid_orders_id = await cur.fetchall()
        
        if invalid_orders_id:
            print(f"❌ Найдено {len(invalid_orders_id)} заказов с невалидными ID:")
            for order in invalid_orders_id:
                print(f"   - rowid: {order['rowid']}, id: {order['id']}, customer: {order['customer_id']}")
            
            # Удаляем заказы с невалидными ID
            await db.execute(
                """DELETE FROM crm_orders 
                WHERE id IS NULL OR id = '' OR id = 'None' OR TRIM(id) = ''"""
            )
            print(f"✅ Удалено {len(invalid_orders_id)} заказов с невалидными ID из БД")
        else:
            print("✅ В БД нет заказов с невалидными ID")
        
        # Находим заказы с None в статусе (исправляем)
        cur = await db.execute(
            """SELECT id, status FROM crm_orders 
            WHERE status IS NULL OR status = 'None' OR status = ''"""
        )
        invalid_orders_status = await cur.fetchall()
        
        if invalid_orders_status:
            print(f"❌ Найдено {len(invalid_orders_status)} заказов с невалидным статусом:")
            for order in invalid_orders_status:
                print(f"   - id: {order['id']}, status: {order['status']}")
            
            # Обновляем статус на дефолтный
            await db.execute(
                """UPDATE crm_orders 
                SET status = 'Ожидает решения' 
                WHERE status IS NULL OR status = 'None' OR status = ''"""
            )
            print(f"✅ Исправлено {len(invalid_orders_status)} заказов в БД")
        else:
            print("✅ В БД нет заказов с невалидным статусом")
        
        await db.commit()
        
    finally:
        await db.close()


def cleanup_google_sheets():
    """Удаляет строки с None в ID из Google Sheets"""
    print("\n🔍 Проверка Google Sheets...")
    
    try:
        gc = gspread.service_account(filename=settings.gcp_sa_json_path)
        spreadsheet = gc.open_by_key(settings.spreadsheet_id)
        
        # Очистка листа "Клиенты"
        try:
            ws_customers = spreadsheet.worksheet(SHEET_NAMES["CUSTOMERS"])
            all_values = ws_customers.get_all_values()
            
            if len(all_values) > 1:
                header = all_values[0]
                
                # Находим колонку с ID
                try:
                    id_col_idx = header.index(CUSTOMER_COLUMNS["ID"])
                except ValueError:
                    print(f"⚠️ Не найдена колонка '{CUSTOMER_COLUMNS['ID']}' в листе Клиенты")
                    return
                
                # Находим строки с None/пустым ID
                rows_to_delete = []
                for idx, row in enumerate(all_values[1:], start=2):  # Начинаем со 2 (пропускаем заголовок)
                    if id_col_idx < len(row):
                        cell_value = row[id_col_idx]
                        if not cell_value or cell_value == "None" or str(cell_value).strip() == "":
                            rows_to_delete.append(idx)
                
                if rows_to_delete:
                    print(f"❌ Найдено {len(rows_to_delete)} строк с невалидными ID в листе Клиенты:")
                    print(f"   Строки: {rows_to_delete}")
                    
                    # Удаляем строки (в обратном порядке, чтобы индексы не сбивались)
                    for row_idx in reversed(rows_to_delete):
                        ws_customers.delete_rows(row_idx)
                        print(f"   ✓ Удалена строка {row_idx}")
                    
                    print(f"✅ Удалено {len(rows_to_delete)} строк из Google Sheets")
                else:
                    print("✅ В Google Sheets нет строк с невалидными ID")
            else:
                print("✅ Лист Клиенты пуст или содержит только заголовок")
                
        except Exception as e:
            print(f"⚠️ Ошибка при обработке листа Клиенты: {e}")
        
        # Проверка листа "Заказы"
        try:
            ws_orders = spreadsheet.worksheet(SHEET_NAMES["ORDERS"])
            all_values = ws_orders.get_all_values()
            
            if len(all_values) > 1:
                header = all_values[0]
                
                # Находим нужные колонки
                try:
                    status_col_idx = header.index(ORDER_COLUMNS["STATUS"])
                    id_col_idx = header.index(ORDER_COLUMNS["ID"])
                except ValueError:
                    print(f"⚠️ Не найдены нужные колонки в листе Заказы")
                    return
                
                # Находим строки с None в ID заказа (удаляем)
                rows_to_delete = []
                for idx, row in enumerate(all_values[1:], start=2):
                    if id_col_idx < len(row):
                        id_value = row[id_col_idx]
                        if not id_value or id_value == "None" or str(id_value).strip() == "":
                            rows_to_delete.append(idx)
                
                if rows_to_delete:
                    print(f"❌ Найдено {len(rows_to_delete)} заказов с невалидными ID в листе Заказы:")
                    print(f"   Строки: {rows_to_delete}")
                    
                    # Удаляем строки (в обратном порядке)
                    for row_idx in reversed(rows_to_delete):
                        ws_orders.delete_rows(row_idx)
                        print(f"   ✓ Удалена строка {row_idx}")
                    
                    print(f"✅ Удалено {len(rows_to_delete)} заказов с невалидными ID из Google Sheets")
                else:
                    print("✅ В Google Sheets нет заказов с невалидными ID")
                
                # Перечитываем данные после удаления
                all_values = ws_orders.get_all_values()
                
                # Исправляем None в статусах (у оставшихся заказов)
                updates = []
                for idx, row in enumerate(all_values[1:], start=2):
                    if status_col_idx < len(row):
                        status_value = row[status_col_idx]
                        if not status_value or status_value == "None" or str(status_value).strip() == "":
                            # Обновляем ячейку статуса
                            cell = gspread.utils.rowcol_to_a1(idx, status_col_idx + 1)
                            updates.append({'range': cell, 'values': [["Ожидает решения"]]})
                
                if updates:
                    print(f"❌ Найдено {len(updates)} заказов с невалидным статусом")
                    ws_orders.batch_update(updates, value_input_option='RAW')
                    print(f"✅ Исправлено {len(updates)} статусов в Google Sheets")
                else:
                    print("✅ В Google Sheets нет заказов с невалидным статусом")
                    
        except Exception as e:
            print(f"⚠️ Ошибка при обработке листа Заказы: {e}")
            
    except Exception as e:
        print(f"❌ Ошибка при подключении к Google Sheets: {e}")


async def main():
    """Главная функция"""
    print("=" * 60)
    print("🧹 ОЧИСТКА ОШИБОЧНЫХ ЗАПИСЕЙ")
    print("=" * 60)
    
    # Очистка БД
    await cleanup_database()
    
    # Очистка Google Sheets
    cleanup_google_sheets()
    
    print("\n" + "=" * 60)
    print("✅ ОЧИСТКА ЗАВЕРШЕНА!")
    print("=" * 60)
    print("\n📝 Рекомендации:")
    print("   1. Перезапустите бота для применения изменений")
    print("   2. Проверьте Google Sheets вручную")
    print("   3. Проверьте статистику командой /stats")


if __name__ == "__main__":
    asyncio.run(main())

