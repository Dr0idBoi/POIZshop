async def push_customers_orders():
    """
    Выгружает всех клиентов и заказы из SQLite в Google Sheets (батч).
    """
    import aiosqlite
    db = await aiosqlite.connect("data/app.db")
    db.row_factory = aiosqlite.Row
    
    try:
        # Клиенты
        cur = await db.execute("SELECT id, tg_link, phone, address, ref_code, invited_by, rev, updated_at FROM crm_customers")
        customers_from_db = [dict(r) for r in await cur.fetchall()]
        
        # Заказы (только не отмененные для списка заказов клиента)
        cur = await db.execute("SELECT id, customer_id FROM crm_orders WHERE status != 'Отмена'")
        active_orders_from_db = [dict(r) for r in await cur.fetchall()]
        
        # Группируем заказы по клиентам
        customer_orders_map = {}
        for order in active_orders_from_db:
            customer_id = order['customer_id']
            if customer_id not in customer_orders_map:
                customer_orders_map[customer_id] = []
            customer_orders_map[customer_id].append(order['id'])

        # Преобразуем клиентов к формату Sheets, добавляя список заказов
        customers_to_sheets = []
        for c in customers_from_db:
            customer_id = c.get("id", "")
            order_ids = customer_orders_map.get(customer_id, [])
            customers_to_sheets.append({
                "ID Клиента": c.get("id",""),
                "Ссылка на тг": c.get("tg_link",""),
                "Контактный номер": c.get("phone",""),
                "Адрес": c.get("address",""),
                "Реф Код": c.get("ref_code",""),
                "приглашен кем": c.get("invited_by",""),
                "список заказов": json.dumps(order_ids, ensure_ascii=False), # Обновляем список заказов
                "rev": c.get("rev",1),
                "updated_at": c.get("updated_at",""),
            })

        # Заказы (все)
        cur = await db.execute("SELECT * FROM crm_orders")
        orders_from_db = [dict(r) for r in await cur.fetchall()]
        
        # Преобразуем заказы к формату Sheets
        orders_to_sheets = []
        for o in orders_from_db:
            orders_to_sheets.append({
                "ID Заказа": o.get("id",""),
                "ID клиента": o.get("customer_id",""),
                "откуда": o.get("source_type",""),
                "ссылка на поизон / номер из стока": o.get("poizon_or_stock_ref",""),
                "размер": o.get("size",""),
                "перевозчик": o.get("carrier",""),
                "трекинговый код": o.get("tracking",""),
                "статус": o.get("status",""),
                "цена на поизоне": o.get("poizon_price",0.0),
                "цена на издержки данного заказа": o.get("order_cost_var",0.0),
                "издержки постоянные": o.get("order_cost_fixed",0.0),
                "маржа": o.get("margin",0.0),
                "финальная цена": o.get("final_price",0.0),
                "ссылка на оплату": o.get("payment_url",""),
                "статус оплаты": o.get("payment_status",""),
                "rev": o.get("rev",1),
                "updated_at": o.get("updated_at",""),
            })
        
        # Финансовые настройки
        cur = await db.execute("SELECT id, name, value, rev, updated_at FROM crm_financial_settings")
        financial_settings_from_db = [dict(r) for r in await cur.fetchall()]
        
        # Преобразуем финансовые настройки к формату Sheets
        financial_settings_to_sheets = []
        for setting in financial_settings_from_db:
            financial_settings_to_sheets.append({
                "ID": setting.get("id", ""),
                "Название пост. траты": setting.get("name", ""),
                "Значение Руб": setting.get("value", 0.0),
                "rev": setting.get("rev", 1),
                "updated_at": setting.get("updated_at", ""),
            })
        
        # Синхронизация с Google Sheets
        upsert_rows("Клиенты", C_CUSTOMERS, customers_to_sheets)
        upsert_rows("Заказы", C_ORDERS, orders_to_sheets)
        upsert_rows("Финансовые настройки", C_FINANCIAL_SETTINGS, financial_settings_to_sheets)
        
    except Exception as e:
        log.error(f"Ошибка синхронизации с Google Sheets: {e}")
    finally:
        await db.close()

import gspread
import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
from decimal import Decimal

from app.config import settings
from app.constants import (
    SHEET_NAMES, CUSTOMER_COLUMNS, ORDER_COLUMNS,
    FINANCIAL_COLUMNS, FINANCIAL_CELLS, FINANCIAL_SETTINGS,
    ORDER_STATUSES
)

log = logging.getLogger("crm.sheets")

def _to_int(v, default=0):
    try:
        return int(v) if v is not None and str(v).strip() else default
    except (ValueError, TypeError):
        return default

def _to_float(v, default=0.0):
    try:
        return float(str(v).replace(",", ".")) if v is not None and str(v).strip() else default
    except (ValueError, TypeError):
        return default


def _client() -> gspread.Client:
    if not Path(settings.gcp_sa_json_path).exists():
        log.error("GCP service account JSON not found at %s", settings.gcp_sa_json_path)
        raise FileNotFoundError(f"GCP service account JSON not found: {settings.gcp_sa_json_path}")
    try:
        return gspread.service_account(filename=settings.gcp_sa_json_path)
    except Exception as e:
        log.error(f"Ошибка авторизации GCP: {e}")
        raise

def _safe_retry(func):
    """Декоратор для повторных попыток при ошибках API с экспоненциальным бэкоффом"""
    import functools
    import time
    import random
    
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        max_retries = 5
        retry_delay = 1.0
        
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except gspread.exceptions.APIError as e:
                # Проверяем код ошибки
                if hasattr(e, 'response') and e.response.status_code in (429, 500, 502, 503, 504):
                    if attempt < max_retries - 1:
                        # Добавляем случайность к задержке (джиттер)
                        sleep_time = retry_delay * (1 + random.random() * 0.1)
                        log.warning(f"API error {e.response.status_code}, retrying in {sleep_time:.2f}s (attempt {attempt+1}/{max_retries})")
                        time.sleep(sleep_time)
                        # Увеличиваем задержку экспоненциально
                        retry_delay *= 2
                    else:
                        log.error(f"API error after {max_retries} retries: {e}")
                        raise
                else:
                    # Другие ошибки API пробрасываем сразу
                    log.error(f"API error: {e}")
                    raise
            except Exception as e:
                log.error(f"Unexpected error: {e}")
                raise
    
    return wrapper

@_safe_retry
def _open():
    try:
        gc = _client()
        return gc.open_by_key(settings.spreadsheet_id)
    except Exception as e:
        log.error(f"Ошибка открытия Google Sheets: {e}")
        raise

@_safe_retry
def _sheet(name: str):
    try:
        return _open().worksheet(name)
    except Exception as e:
        log.error(f"Ошибка доступа к листу '{name}': {e}")
        raise

# Получаем списки колонок из констант
C_CUSTOMERS = list(CUSTOMER_COLUMNS.values())
C_ORDERS = list(ORDER_COLUMNS.values())
C_FINANCIAL_SETTINGS = list(FINANCIAL_COLUMNS.values())

def _rows_to_dicts(rows: List[List[str]], header: List[str], columns: List[str]) -> List[Dict[str, Any]]:
    # карта "имя колонки в шите" -> индекс
    idx = {col: (header.index(col) if col in header else None) for col in columns}

    items = []
    for r in rows:
        if not any(str(x).strip() for x in r):
            continue
        item = {}
        for col in columns:
            i = idx.get(col)
            val = r[i] if i is not None and i < len(r) else ""
            # нормализуем числа
            if col in [ORDER_COLUMNS["POIZON_PRICE"], ORDER_COLUMNS["VAR_COSTS"],
                       ORDER_COLUMNS["FIXED_COSTS"], ORDER_COLUMNS["MARGIN"],
                       ORDER_COLUMNS["FINAL_PRICE"], ORDER_COLUMNS["REV"],
                       FINANCIAL_COLUMNS["VALUE"]]:
                val = _to_float(val, 0.0)
            item[col] = val
        items.append(item)
    return items


@_safe_retry
def pull_customers() -> List[Dict[str, Any]]:
    try:
        ws = _sheet(SHEET_NAMES["CUSTOMERS"])
        rows = ws.get_all_values()
        if not rows:
            return []
        header, data = rows[0], rows[1:]
        return _rows_to_dicts(data, header, C_CUSTOMERS)
    except Exception as e:
        log.error(f"Ошибка чтения клиентов из Sheets: {e}")
        return []

@_safe_retry
def pull_orders() -> List[Dict[str, Any]]:
    try:
        ws = _sheet(SHEET_NAMES["ORDERS"])
        rows = ws.get_all_values()
        if not rows:
            return []
        header, data = rows[0], rows[1:]
        return _rows_to_dicts(data, header, C_ORDERS)
    except Exception as e:
        log.error(f"Ошибка чтения заказов из Sheets: {e}")
        return []

def read_fin_settings() -> Tuple[int, int]:
    """
    Читает финансовые настройки из ячеек L5 (маржа) и L6 (постоянные траты)
    
    Returns:
        Кортеж (margin_pct:int, fixed_costs:int)
    """
    try:
        ws = _sheet(SHEET_NAMES["FINANCIAL"])
        margin_pct = ws.acell(FINANCIAL_CELLS["MARGIN_PERCENT"]).value
        fixed_costs = ws.acell(FINANCIAL_CELLS["FIXED_COSTS"]).value
        
        # Преобразуем в целые числа
        margin_pct = int(float(margin_pct)) if margin_pct else 0
        fixed_costs = int(float(fixed_costs)) if fixed_costs else 0
        
        return margin_pct, fixed_costs
    except Exception as e:
        log.error(f"Ошибка чтения финансовых настроек из ячеек: {e}")
        return 0, 0

def get_financial_settings() -> Dict[str, Optional[float]]:
    """
    Получает финансовые настройки (постоянные траты, процент маржи) из Google Sheets.
    Использует новую функцию read_fin_settings для чтения из ячеек L5 и L6.
    """
    try:
        margin_pct, fixed_costs = read_fin_settings()
        
        return {
            "fixed_costs": float(fixed_costs),
            "margin_percent": float(margin_pct)
        }
    except Exception as e:
        log.error(f"Ошибка чтения финансовых настроек из Sheets: {e}")
        return {"fixed_costs": 0.0, "margin_percent": 0.0}

@_safe_retry
def pull_financial_settings() -> List[Dict[str, Any]]:
    """
    Получает все записи из таблицы финансовых настроек.
    """
    try:
        ws = _sheet(SHEET_NAMES["FINANCIAL"])
        rows = ws.get_all_values()
        if not rows:
            return []
        header, data = rows[0], rows[1:]
        return _rows_to_dicts(data, header, C_FINANCIAL_SETTINGS)
    except Exception as e:
        log.error(f"Ошибка чтения финансовых настроек из Sheets: {e}")
        return []

def fetch_sheet_index_by_ext_id(sheet_name: str, ext_id: str) -> Optional[int]:
    """
    Находит номер строки в таблице по ext_id
    
    Args:
        sheet_name: Название листа
        ext_id: Идентификатор записи
        
    Returns:
        Номер строки (1-based) или None если не найдено
    """
    try:
        ws = _sheet(sheet_name)
        
        # Определяем колонку с ext_id
        id_col = 1  # По умолчанию первая колонка
        if sheet_name == SHEET_NAMES["CUSTOMERS"]:
            header = ws.row_values(1)
            id_col = header.index(CUSTOMER_COLUMNS["ID"]) + 1
        elif sheet_name == SHEET_NAMES["ORDERS"]:
            header = ws.row_values(1)
            id_col = header.index(ORDER_COLUMNS["ID"]) + 1
        
        # Ищем строку с нужным ext_id
        cell = ws.find(ext_id, in_column=id_col)
        if cell:
            return cell.row
        return None
    except Exception as e:
        log.error(f"Ошибка поиска строки по ext_id {ext_id} в {sheet_name}: {e}")
        return None

@_safe_retry
def append_order_row(order: Dict[str, Any]) -> Optional[int]:
    """
    Добавляет новую строку заказа в конец таблицы
    
    Args:
        order: Словарь с данными заказа (ключи = названия колонок из ORDER_COLUMNS)
        
    Returns:
        Номер добавленной строки или None при ошибке
    """
    try:
        # Валидация ID заказа перед добавлением
        order_id = order.get(ORDER_COLUMNS["ID"], "")
        if not order_id or order_id == "None" or str(order_id).strip() == "":
            log.error(f"Попытка добавить заказ с невалидным ID: {order_id}")
            return None
        
        ws = _sheet(SHEET_NAMES["ORDERS"])
        
        # Подготовка данных для вставки
        row_data = []
        for col in ORDER_COLUMNS.values():
            # Напрямую берем значение по имени колонки
            value = order.get(col, "")
            
            # Заменяем None на пустую строку
            if value is None or value == "None":
                value = ""
                
            row_data.append(str(value))
        
        # Добавляем строку
        result = ws.append_row(row_data, value_input_option='RAW')
        
        # Получаем номер добавленной строки
        sheet_row_id = ws.row_count
        
        log.info(f"Добавлен заказ {order_id} в строку {sheet_row_id}")
        return sheet_row_id
        
    except Exception as e:
        log.error(f"Ошибка добавления строки заказа: {e}")
        return None

@_safe_retry
def append_customer_row(customer: Dict[str, Any]) -> Optional[int]:
    """
    Добавляет новую строку клиента в конец таблицы
    
    Args:
        customer: Словарь с данными клиента
        
    Returns:
        Номер добавленной строки или None при ошибке
    """
    try:
        # Валидация ID клиента перед добавлением
        customer_id = customer.get(CUSTOMER_COLUMNS["ID"], "")
        if not customer_id or customer_id == "None" or str(customer_id).strip() == "":
            log.error(f"Попытка добавить клиента с невалидным ID: {customer_id}")
            return None
        
        ws = _sheet(SHEET_NAMES["CUSTOMERS"])
        
        # Подготовка данных для вставки
        row_data = []
        for col in CUSTOMER_COLUMNS.values():
            # Напрямую берем значение по имени колонки
            value = customer.get(col, "")
            
            # Заменяем None на пустую строку
            if value is None or value == "None":
                value = ""
                
            row_data.append(str(value))
        
        # Добавляем строку
        result = ws.append_row(row_data, value_input_option='RAW')
        
        # Получаем номер добавленной строки
        sheet_row_id = ws.row_count
        
        log.info(f"Добавлен клиент {customer_id} в строку {sheet_row_id}")
        return sheet_row_id
        
    except Exception as e:
        log.error(f"Ошибка добавления строки клиента: {e}")
        return None

@_safe_retry
def partial_update_by_ext_id(sheet_name: str, ext_id: str, changes: Dict[str, Any]) -> bool:
    """
    Частично обновляет строку по ext_id
    
    Args:
        sheet_name: Название листа
        ext_id: Идентификатор записи
        changes: Словарь изменений {column_name: new_value}
        
    Returns:
        True если обновление успешно
    """
    try:
        # Находим строку
        row_index = fetch_sheet_index_by_ext_id(sheet_name, ext_id)
        if not row_index:
            log.error(f"Строка с ext_id={ext_id} не найдена в {sheet_name}")
            return False
            
        ws = _sheet(sheet_name)
        
        # Определяем соответствие колонок
        columns_map = {}
        if sheet_name == SHEET_NAMES["CUSTOMERS"]:
            columns_map = CUSTOMER_COLUMNS
        elif sheet_name == SHEET_NAMES["ORDERS"]:
            columns_map = ORDER_COLUMNS
        elif sheet_name == SHEET_NAMES["FINANCIAL"]:
            columns_map = FINANCIAL_COLUMNS
        
        # Получаем заголовки
        header_row = ws.row_values(1)
        
        # Подготавливаем batch-запрос
        batch_updates = []
        
        for field, value in changes.items():
            # Получаем имя колонки в таблице
            column_name = columns_map.get(field.upper())
            if not column_name:
                continue
                
            # Находим индекс колонки
            try:
                col_index = header_row.index(column_name) + 1
            except ValueError:
                log.warning(f"Колонка {column_name} не найдена в {sheet_name}")
                continue
                
            # Заменяем None на пустую строку
            if value is None:
                value = ""
                
            # Добавляем в batch
            cell_range = gspread.utils.rowcol_to_a1(row_index, col_index)
            batch_updates.append({
                'range': cell_range,
                'values': [[str(value)]]
            })
        
        if batch_updates:
            # Выполняем batch-обновление
            ws.batch_update(batch_updates, value_input_option='RAW')
            log.info(f"Обновлено {len(batch_updates)} ячеек для {ext_id} в {sheet_name}")
            return True
        
        return False
        
    except Exception as e:
        log.error(f"Ошибка обновления строки по ext_id {ext_id} в {sheet_name}: {e}")
        return False

@_safe_retry
def upsert_rows(worksheet_name: str, columns: List[str], data: List[Dict[str, Any]]):
    """
    Обновляет или добавляет строки в Google Sheets.
    Если строка уже существует (по ID), она обновляется. Новые строки добавляются снизу.
    
    Обновлено для работы с ext_id и rev.
    """
    try:
        ws = _sheet(worksheet_name)
        existing_rows = ws.get_all_values()
        header = existing_rows[0] if existing_rows else columns
        
        # Определяем колонки ID и rev
        id_column = ""
        rev_column = ""
        
        if worksheet_name == SHEET_NAMES["CUSTOMERS"]:
            id_column = CUSTOMER_COLUMNS["ID"]
            rev_column = CUSTOMER_COLUMNS["REV"]
        elif worksheet_name == SHEET_NAMES["ORDERS"]:
            id_column = ORDER_COLUMNS["ID"]
            rev_column = ORDER_COLUMNS["REV"]
        elif worksheet_name == SHEET_NAMES["FINANCIAL"]:
            id_column = FINANCIAL_COLUMNS["ID"]
            rev_column = FINANCIAL_COLUMNS["REV"]
        
        # Находим индексы колонок
        id_column_index = -1
        rev_column_index = -1
        
        if id_column in header:
            id_column_index = header.index(id_column)
        
        if rev_column in header:
            rev_column_index = header.index(rev_column)
        
        # Создаем карту существующих строк по ID для быстрого поиска
        existing_data_map = {}
        if id_column_index != -1 and len(existing_rows) > 1:
            for r_idx, row in enumerate(existing_rows[1:], start=1):
                if id_column_index < len(row) and row[id_column_index]:
                    existing_data_map[row[id_column_index]] = {
                        "row_index": r_idx, 
                        "data": row,
                        "rev": int(row[rev_column_index]) if rev_column_index != -1 and rev_column_index < len(row) and row[rev_column_index] else 0
                    }
        
        updates = []
        new_rows = []
        
        for item in data:
            # Получаем ID и rev
            item_id = item.get(id_column, "")
            item_rev = int(item.get(rev_column, 0)) if rev_column in item else 0
            
            # Подготавливаем строку для записи
            row_to_write = []
            for col in columns:
                value = item.get(col, "")
                # Заменяем None на пустую строку
                if value is None:
                    value = ""
                row_to_write.append(str(value))
            
            if item_id and item_id in existing_data_map:
                # Проверяем rev для обновления
                existing_row_info = existing_data_map[item_id]
                existing_rev = existing_row_info["rev"]
                
                # Обновляем только если rev больше или равен существующему
                if item_rev >= existing_rev:
                    row_index = existing_row_info["row_index"]
                    
                    # Сравниваем и обновляем только измененные ячейки
                    for col_idx, new_value in enumerate(row_to_write):
                        if col_idx < len(existing_row_info["data"]) and existing_row_info["data"][col_idx] != new_value:
                            updates.append({
                                'range': gspread.utils.rowcol_to_a1(row_index + 1, col_idx + 1),
                                'values': [[new_value]]
                            })
            else:
                # Добавляем новую строку
                new_rows.append(row_to_write)
        
        if updates:
            ws.batch_update(updates, value_input_option='RAW')
            log.info("Updated %d cells in %s", len(updates), worksheet_name)
            
        if new_rows:
            ws.append_rows(new_rows, value_input_option='RAW')
            log.info("Appended %d new rows to %s", len(new_rows), worksheet_name)
            
    except Exception as e:
        log.error(f"Ошибка записи в лист '{worksheet_name}': {e}")

async def _sync_order_from_sheets(db, sheets_order: Dict[str, Any], db_order: Optional[Dict[str, Any]]) -> Tuple[bool, Optional[Dict[str, str]]]:
    """
    Синхронизирует один заказ из Sheets в БД
    
    Args:
        db: Соединение с БД
        sheets_order: Данные заказа из Sheets
        db_order: Данные заказа из БД (если существует)
        
    Returns:
        Tuple[обновлено, изменение_статуса]
    """
    order_id = sheets_order.get(ORDER_COLUMNS["ID"])
    if not order_id:
        return False, None
    
    # Получаем rev из Sheets
    sheets_rev = _to_int(sheets_order.get(ORDER_COLUMNS["REV"]), 0)
    
    if db_order:
        # Обновляем существующий заказ
        db_rev = _to_int(db_order.get("rev"), 0)
        
        # Проверяем изменились ли важные поля (для ручных изменений в Sheets)
        sheets_status = sheets_order.get(ORDER_COLUMNS["STATUS"], "")
        sheets_carrier = sheets_order.get(ORDER_COLUMNS["CARRIER"], "")
        sheets_tracking = sheets_order.get(ORDER_COLUMNS["TRACKING"], "")
        
        db_status = db_order.get("status", "")
        db_carrier = db_order.get("carrier", "")
        db_tracking = db_order.get("tracking", "")
        
        # Обновляем если rev больше ИЛИ изменились важные поля
        important_fields_changed = (
            sheets_status != db_status or 
            sheets_carrier != db_carrier or 
            sheets_tracking != db_tracking
        )
        
        if sheets_rev > db_rev or important_fields_changed:
            # Получаем остальные данные из Sheets (статус, перевозчик, трекинг уже получены выше)
            sheets_customer_id = sheets_order.get(ORDER_COLUMNS["CUSTOMER_ID"], "")
            sheets_source_type = sheets_order.get(ORDER_COLUMNS["SOURCE_TYPE"], "")
            sheets_poizon_ref = sheets_order.get(ORDER_COLUMNS["POIZON_REF"], "")
            sheets_size = sheets_order.get(ORDER_COLUMNS["SIZE"], "")
            sheets_poizon_price = _to_float(sheets_order.get(ORDER_COLUMNS["POIZON_PRICE"]), 0.0)
            sheets_var_costs = _to_float(sheets_order.get(ORDER_COLUMNS["VAR_COSTS"]), 0.0)
            sheets_fixed_costs = _to_float(sheets_order.get(ORDER_COLUMNS["FIXED_COSTS"]), 0.0)
            sheets_margin = _to_float(sheets_order.get(ORDER_COLUMNS["MARGIN"]), 0.0)
            sheets_final_price = _to_float(sheets_order.get(ORDER_COLUMNS["FINAL_PRICE"]), 0.0)
            sheets_payment_url = sheets_order.get(ORDER_COLUMNS["PAYMENT_URL"], "")
            sheets_payment_status = sheets_order.get(ORDER_COLUMNS["PAYMENT_STATUS"], "")
            sheets_updated_at = sheets_order.get(ORDER_COLUMNS["UPDATED_AT"], datetime.now().isoformat())
            
            # Проверяем изменение статуса для уведомлений (db_status уже получен выше)
            status_change = None
            if sheets_status and sheets_status != db_status:
                status_change = {
                    "order_id": order_id,
                    "customer_id": sheets_customer_id,
                    "old_status": db_status,
                    "new_status": sheets_status
                }
            
            # Обновляем запись в БД
            await db.execute(
                """UPDATE crm_orders SET 
                customer_id=?, source_type=?, poizon_or_stock_ref=?, size=?,
                carrier=?, tracking=?, status=?, poizon_price=?, order_cost_var=?,
                order_cost_fixed=?, margin=?, final_price=?, payment_url=?,
                payment_status=?, rev=?, updated_at=?, sheet_row_id=?
                WHERE id=?""",
                (
                    sheets_customer_id, sheets_source_type, sheets_poizon_ref, sheets_size,
                    sheets_carrier, sheets_tracking, sheets_status, sheets_poizon_price, sheets_var_costs,
                    sheets_fixed_costs, sheets_margin, sheets_final_price, sheets_payment_url,
                    sheets_payment_status, sheets_rev, sheets_updated_at, 
                    db_order.get("sheet_row_id"), order_id
                )
            )
            
            return True, status_change
    
    return False, None

async def _sync_customer_from_sheets(db, sheets_customer: Dict[str, Any], db_customer: Optional[Dict[str, Any]]) -> bool:
    """
    Синхронизирует одного клиента из Sheets в БД
    
    Args:
        db: Соединение с БД
        sheets_customer: Данные клиента из Sheets
        db_customer: Данные клиента из БД (если существует)
        
    Returns:
        True если обновлено
    """
    customer_id = sheets_customer.get(CUSTOMER_COLUMNS["ID"])
    if not customer_id:
        return False
    
    # Получаем rev из Sheets
    sheets_rev = int(sheets_customer.get(CUSTOMER_COLUMNS["REV"], 0))
    
    if db_customer:
        # Обновляем существующего клиента, только если rev в Sheets больше
        db_rev = int(db_customer.get("rev", 0))
        
        if sheets_rev > db_rev:
            # Получаем данные из Sheets
            sheets_tg_link = sheets_customer.get(CUSTOMER_COLUMNS["TG_LINK"], "")
            sheets_phone = sheets_customer.get(CUSTOMER_COLUMNS["PHONE"], "")
            sheets_address = sheets_customer.get(CUSTOMER_COLUMNS["ADDRESS"], "")
            sheets_ref_code = sheets_customer.get(CUSTOMER_COLUMNS["REF_CODE"], "")
            sheets_invited_by = sheets_customer.get(CUSTOMER_COLUMNS["INVITED_BY"], "")
            sheets_order_list = sheets_customer.get(CUSTOMER_COLUMNS["ORDER_LIST"], "[]")
            sheets_updated_at = sheets_customer.get(CUSTOMER_COLUMNS["UPDATED_AT"], datetime.now().isoformat())
            
            # Обновляем запись в БД
            await db.execute(
                """UPDATE crm_customers SET 
                tg_link=?, phone=?, address=?, ref_code=?, invited_by=?,
                order_ids_json=?, rev=?, updated_at=?, sheet_row_id=?
                WHERE id=?""",
                (
                    sheets_tg_link, sheets_phone, sheets_address, sheets_ref_code, sheets_invited_by,
                    sheets_order_list, sheets_rev, sheets_updated_at, 
                    db_customer.get("sheet_row_id"), customer_id
                )
            )
            
            return True
    
    return False

async def sync_sheets_to_db(bot=None):
    """
    Синхронизация данных из Google Sheets в SQLite с учетом rev.
    Обновляет только записи с большим rev, чем в БД.
    Помечает удаленные записи.
    """
    from ..db import get_db, add_status_history
    from datetime import datetime
    
    try:
        log.info("Начинаем синхронизацию Sheets → SQLite")
        
        # Получаем данные из Google Sheets
        sheets_orders = pull_orders()
        sheets_customers = pull_customers()
        
        log.info(f"Получено из Sheets: {len(sheets_orders)} заказов, {len(sheets_customers)} клиентов")
        
        async with get_db() as db:
            # Получаем текущие данные из БД для сравнения
            cur = await db.execute("SELECT id, ext_id, rev, sheet_row_id, status FROM crm_orders")
            db_orders = {row['ext_id'] if row['ext_id'] else row['id']: dict(row) for row in await cur.fetchall()}
            
            cur = await db.execute("SELECT id, ext_id, rev, sheet_row_id FROM crm_customers")
            db_customers = {row['ext_id'] if row['ext_id'] else row['id']: dict(row) for row in await cur.fetchall()}
            
            # Создаем множества ID для проверки удаленных записей
            sheets_order_ids = {order.get(ORDER_COLUMNS["ID"]) for order in sheets_orders if order.get(ORDER_COLUMNS["ID"])}
            sheets_customer_ids = {customer.get(CUSTOMER_COLUMNS["ID"]) for customer in sheets_customers if customer.get(CUSTOMER_COLUMNS["ID"])}
            
            # Обработка заказов
            orders_updated = 0
            orders_deleted = 0
            status_changes = []
            
            for sheets_order in sheets_orders:
                order_id = sheets_order.get(ORDER_COLUMNS["ID"])
                if not order_id:
                    continue
                
                # Используем вспомогательную функцию для синхронизации
                db_order = db_orders.get(order_id)
                updated, status_change = await _sync_order_from_sheets(db, sheets_order, db_order)
                
                if updated:
                    orders_updated += 1
                    if status_change:
                        status_changes.append(status_change)
                elif not db_order:
                    # Новый заказ из Sheets - не добавляем, только логируем
                    log.info(f"Найден новый заказ в Sheets: {order_id}, но не добавляем его в БД")
            
            # Обработка удаленных заказов
            # Проверяем только записи, которые еще не помечены как удаленные
            cur = await db.execute(
                "SELECT id, ext_id FROM crm_orders WHERE sheet_row_id IS NOT NULL AND deleted_in_sheets_at IS NULL"
            )
            active_db_orders = {row['ext_id'] if row['ext_id'] else row['id']: dict(row) for row in await cur.fetchall()}
            
            for db_order_id, db_order in active_db_orders.items():
                if db_order_id not in sheets_order_ids:
                    # Заказ был удален из Sheets - помечаем в БД
                    await db.execute(
                        "UPDATE crm_orders SET deleted_in_sheets_at=datetime('now') WHERE id=?",
                        (db_order.get("id"),)
                    )
                    orders_deleted += 1
            
            # Обработка клиентов
            customers_updated = 0
            customers_deleted = 0
            
            for sheets_customer in sheets_customers:
                customer_id = sheets_customer.get(CUSTOMER_COLUMNS["ID"])
                if not customer_id:
                    continue
                
                # Используем вспомогательную функцию для синхронизации
                db_customer = db_customers.get(customer_id)
                updated = await _sync_customer_from_sheets(db, sheets_customer, db_customer)
                
                if updated:
                    customers_updated += 1
                elif not db_customer:
                    # Новый клиент из Sheets - не добавляем, только логируем
                    log.info(f"Найден новый клиент в Sheets: {customer_id}, но не добавляем его в БД")
            
            # Обработка удаленных клиентов
            # Проверяем только записи, которые еще не помечены как удаленные
            cur = await db.execute(
                "SELECT id, ext_id FROM crm_customers WHERE sheet_row_id IS NOT NULL AND deleted_in_sheets_at IS NULL"
            )
            active_db_customers = {row['ext_id'] if row['ext_id'] else row['id']: dict(row) for row in await cur.fetchall()}
            
            for db_customer_id, db_customer in active_db_customers.items():
                if db_customer_id not in sheets_customer_ids:
                    # Клиент был удален из Sheets - помечаем в БД
                    await db.execute(
                        "UPDATE crm_customers SET deleted_in_sheets_at=datetime('now') WHERE id=?",
                        (db_customer.get("id"),)
                    )
                    customers_deleted += 1
            
            # Фиксируем изменения
            await db.commit()
            
            # Логируем результаты
            log.info(
                f"Синхронизация Sheets → SQLite завершена: "
                f"обновлено {orders_updated} заказов, {customers_updated} клиентов; "
                f"помечено как удаленные {orders_deleted} заказов, {customers_deleted} клиентов"
            )
            
            # Отправляем уведомления об изменении статусов
            if bot and status_changes:
                for change in status_changes:
                    try:
                        # Добавляем в историю статусов
                        await add_status_history(
                            change["order_id"],
                            change["old_status"],
                            change["new_status"],
                            "sheets_sync",
                            "status_changed_in_sheets"
                        )
                        
                        # Отправляем уведомление клиенту
                        if change["customer_id"].isdigit():
                            await bot.send_message(
                                int(change["customer_id"]),
                                f"📦 <b>Обновление заказа #{change['order_id']}</b>\n\n"
                                f"📊 <b>Новый статус:</b> {change['new_status']}\n\n"
                                f"По всем вопросам обращайтесь к <a href='tg://user?id={settings.owner_id}'>администратору</a>"
                            )
                    except Exception as e:
                        log.error(f"Ошибка отправки уведомления о смене статуса: {e}")
            
    except Exception as e:
        log.error(f"Ошибка синхронизации Sheets → SQLite: {e}")

@_safe_retry
def delete_row_by_ext_id(worksheet_name: str, ext_id: str) -> bool:
    """
    Удаляет строку из Google Sheets по ext_id
    
    Args:
        worksheet_name: Название листа
        ext_id: Внешний ID записи
        
    Returns:
        True если строка была удалена, False если не найдена
    """
    try:
        gc = _client()
        sh = gc.open_by_key(settings.spreadsheet_id)
        ws = sh.worksheet(worksheet_name)
        
        # Получаем все данные
        all_values = ws.get_all_values()
        if not all_values:
            return False
        
        # Находим индекс колонки ID
        headers = all_values[0]
        id_col_name = CUSTOMER_COLUMNS["ID"] if worksheet_name == SHEET_NAMES["CUSTOMERS"] else ORDER_COLUMNS["ID"]
        
        try:
            id_col_idx = headers.index(id_col_name)
        except ValueError:
            log.error(f"Колонка '{id_col_name}' не найдена в листе '{worksheet_name}'")
            return False
        
        # Ищем строку с нужным ext_id
        for row_idx, row in enumerate(all_values[1:], start=2):  # Начинаем с 2 (пропускаем заголовок)
            if row_idx <= len(row) and id_col_idx < len(row):
                if str(row[id_col_idx]).strip() == str(ext_id).strip():
                    # Удаляем строку
                    ws.delete_rows(row_idx)
                    log.info(f"Удалена строка {row_idx} с ext_id={ext_id} из листа '{worksheet_name}'")
                    return True
        
        log.warning(f"Строка с ext_id={ext_id} не найдена в листе '{worksheet_name}'")
        return False
        
    except Exception as e:
        log.error(f"Ошибка удаления строки с ext_id={ext_id} из '{worksheet_name}': {e}")
        return False

async def sync_db_to_sheets():
    """
    Синхронизация данных из SQLite в Google Sheets.
    Экспортирует только новые записи, не помеченные как удаленные.
    Удаляет из Sheets записи, помеченные как удаленные в БД.
    """
    from ..db import get_db
    
    try:
        log.info("Начинаем синхронизацию SQLite → Sheets")
        
        async with get_db() as db:
            # Сначала удаляем записи, помеченные как удаленные
            # Получаем заказы для удаления
            cur = await db.execute(
                """SELECT ext_id, id FROM crm_orders 
                WHERE deleted_in_sheets_at IS NOT NULL 
                AND sheet_row_id IS NOT NULL"""
            )
            orders_to_delete = [dict(r) for r in await cur.fetchall()]
            
            # Получаем клиентов для удаления
            cur = await db.execute(
                """SELECT ext_id, id FROM crm_customers 
                WHERE deleted_in_sheets_at IS NOT NULL 
                AND sheet_row_id IS NOT NULL"""
            )
            customers_to_delete = [dict(r) for r in await cur.fetchall()]
            
            log.info(f"Найдено для удаления: {len(orders_to_delete)} заказов, {len(customers_to_delete)} клиентов")
            
            # Удаляем заказы из Sheets
            orders_deleted = 0
            for order in orders_to_delete:
                ext_id = order.get("ext_id") or order.get("id")
                if ext_id and delete_row_by_ext_id(SHEET_NAMES["ORDERS"], str(ext_id)):
                    # Очищаем sheet_row_id в БД после успешного удаления
                    await db.execute(
                        "UPDATE crm_orders SET sheet_row_id=NULL WHERE id=?",
                        (order.get("id"),)
                    )
                    orders_deleted += 1
            
            # Удаляем клиентов из Sheets
            customers_deleted = 0
            for customer in customers_to_delete:
                ext_id = customer.get("ext_id") or customer.get("id")
                if ext_id and delete_row_by_ext_id(SHEET_NAMES["CUSTOMERS"], str(ext_id)):
                    # Очищаем sheet_row_id в БД после успешного удаления
                    await db.execute(
                        "UPDATE crm_customers SET sheet_row_id=NULL WHERE id=?",
                        (customer.get("id"),)
                    )
                    customers_deleted += 1
            
            if orders_deleted > 0 or customers_deleted > 0:
                log.info(f"Удалено из Sheets: {orders_deleted} заказов, {customers_deleted} клиентов")
            
            # Фиксируем удаления
            await db.commit()
            # Получаем заказы для экспорта (новые, не удаленные, не ожидающие решения, не отклоненные)
            # Экспортируем только заказы с payment_url (т.е. одобренные админом)
            cur = await db.execute(
                """SELECT * FROM crm_orders 
                WHERE sheet_row_id IS NULL 
                AND deleted_in_sheets_at IS NULL
                AND awaiting_decision = 0
                AND status != ?
                AND payment_url IS NOT NULL
                AND payment_url != ''""",
                (ORDER_STATUSES["REJECTED"],)
            )
            orders_to_export = [dict(r) for r in await cur.fetchall()]
            
            # Получаем клиентов для экспорта (новые, не удаленные)
            cur = await db.execute(
                """SELECT * FROM crm_customers 
                WHERE sheet_row_id IS NULL 
                AND deleted_in_sheets_at IS NULL"""
            )
            customers_to_export = [dict(r) for r in await cur.fetchall()]
            
            log.info(f"Найдено для экспорта: {len(orders_to_export)} заказов, {len(customers_to_export)} клиентов")
            
            # Экспорт заказов
            orders_exported = 0
            for order in orders_to_export:
                # Преобразуем к формату Sheets
                order_data = {
                    ORDER_COLUMNS["ID"]: order.get("ext_id") or order.get("id", ""),
                    ORDER_COLUMNS["CUSTOMER_ID"]: order.get("customer_id", ""),
                    ORDER_COLUMNS["SOURCE_TYPE"]: order.get("source_type", ""),
                    ORDER_COLUMNS["POIZON_REF"]: order.get("poizon_or_stock_ref", ""),
                    ORDER_COLUMNS["SIZE"]: order.get("size", ""),
                    ORDER_COLUMNS["CARRIER"]: order.get("carrier", ""),
                    ORDER_COLUMNS["TRACKING"]: order.get("tracking", ""),
                    ORDER_COLUMNS["STATUS"]: order.get("status", ""),
                    ORDER_COLUMNS["POIZON_PRICE"]: order.get("poizon_price", 0),
                    ORDER_COLUMNS["VAR_COSTS"]: order.get("order_cost_var", 0),
                    ORDER_COLUMNS["FIXED_COSTS"]: order.get("order_cost_fixed", 0),
                    ORDER_COLUMNS["MARGIN"]: order.get("margin", 0),
                    ORDER_COLUMNS["FINAL_PRICE"]: order.get("final_price", 0),
                    ORDER_COLUMNS["PAYMENT_URL"]: order.get("payment_url", ""),
                    ORDER_COLUMNS["PAYMENT_STATUS"]: order.get("payment_status", ""),
                    ORDER_COLUMNS["REV"]: order.get("rev", 1),
                    ORDER_COLUMNS["UPDATED_AT"]: order.get("updated_at", "")
                }
                
                # Добавляем строку в Sheets
                sheet_row_id = append_order_row(order_data)
                
                if sheet_row_id:
                    # Обновляем sheet_row_id в БД
                    await db.execute(
                        "UPDATE crm_orders SET sheet_row_id=? WHERE id=?",
                        (sheet_row_id, order.get("id"))
                    )
                    orders_exported += 1
            
            # Экспорт клиентов
            customers_exported = 0
            for customer in customers_to_export:
                # используем ext_id если есть, иначе id
                cid = customer.get("ext_id") or customer.get("id")
                
                # ВАЖНО: Пропускаем записи с None, пустыми или строкой "None" в ID
                if not cid or cid == "None" or str(cid).strip() == "":
                    log.warning(f"Пропущена запись клиента с невалидным ID: {cid}")
                    continue

                # Список НЕотменённых заказов → JSON-строка
                cur = await db.execute(
                    "SELECT id FROM crm_orders WHERE customer_id=? AND status != 'Отмена'",
                    (customer.get("id"),)
                )
                order_ids = [row["id"] for row in await cur.fetchall()]
                orders_list_json = json.dumps(order_ids, ensure_ascii=False)

                # Формируем данные строго по именам колонок листа
                customer_data = {
                    CUSTOMER_COLUMNS["ID"]: str(cid),
                    CUSTOMER_COLUMNS["TG_LINK"]: customer.get("tg_link") or "",
                    CUSTOMER_COLUMNS["PHONE"]: customer.get("phone") or "",
                    CUSTOMER_COLUMNS["ADDRESS"]: customer.get("address") or "",
                    CUSTOMER_COLUMNS["REF_CODE"]: customer.get("ref_code") or "",
                    CUSTOMER_COLUMNS["INVITED_BY"]: customer.get("invited_by") or "",
                    CUSTOMER_COLUMNS["ORDER_LIST"]: orders_list_json,
                    CUSTOMER_COLUMNS["REV"]: customer.get("rev") or 0,
                    CUSTOMER_COLUMNS["UPDATED_AT"]: customer.get("updated_at") or "",
                }

                # Пишем через helper, чтобы не было «None»-строк и разъезда колонок
                row_id = append_customer_row(customer_data)
                if row_id:
                    await db.execute(
                        "UPDATE crm_customers SET sheet_row_id=? WHERE id=?",
                        (row_id, customer.get("id"))
                    )
                    customers_exported += 1
            
            # Фиксируем изменения
            await db.commit()
            
            log.info(
                f"Синхронизация SQLite → Sheets завершена: "
                f"удалено {orders_deleted} заказов, {customers_deleted} клиентов; "
                f"экспортировано {orders_exported} заказов, {customers_exported} клиентов"
            )
            
    except Exception as e:
        log.error(f"Ошибка синхронизации SQLite → Sheets: {e}")

async def update_customer_order_lists():
    """
    Обновляет списки заказов клиентов в Sheets.
    """
    from ..db import get_db
    
    try:
        log.info("Начинаем обновление списков заказов клиентов")
        
        async with get_db() as db:
            # Получаем всех клиентов с sheet_row_id
            cur = await db.execute(
                "SELECT id, ext_id, sheet_row_id FROM crm_customers WHERE sheet_row_id IS NOT NULL"
            )
            customers = [dict(r) for r in await cur.fetchall()]
            
            updates_count = 0
            
            for customer in customers:
                # Получаем список активных заказов клиента
                cur = await db.execute(
                    "SELECT id FROM crm_orders WHERE customer_id=? AND status != 'Отмена'",
                    (customer.get("id"),)
                )
                order_ids = [row["id"] for row in await cur.fetchall()]
                
                # Обновляем список заказов в Sheets
                ext_id = customer.get("ext_id") or customer.get("id")
                if ext_id:
                    result = partial_update_by_ext_id(
                        SHEET_NAMES["CUSTOMERS"],
                        ext_id,
                        {"ORDER_LIST": json.dumps(order_ids, ensure_ascii=False)}
                    )
                    
                    if result:
                        updates_count += 1
            
            log.info(f"Обновлено списков заказов: {updates_count} из {len(customers)}")
            
    except Exception as e:
        log.error(f"Ошибка обновления списков заказов: {e}")
