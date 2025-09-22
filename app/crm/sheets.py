# app/crm/sheets.py
import gspread
import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Tuple
from ..config import settings

log = logging.getLogger("crm.sheets")

def _client() -> gspread.Client:
    if not Path(settings.gcp_sa_json_path).exists():
        log.warning("GCP service account JSON not found at %s", settings.gcp_sa_json_path)
    return gspread.service_account(filename=settings.gcp_sa_json_path)

def _open():
    gc = _client()
    return gc.open_by_key(settings.spreadsheet_id)

def _sheet(name: str):
    return _open().worksheet(name)

# Column order helpers (must match your Sheets)
C_CUSTOMERS = [
    "ID Клиента","Ссылка на тг","Контактный номер","Адрес","Реф Код","приглашен кем","список заказов","rev","updated_at"
]
C_ORDERS = [
    "ID Заказа","ID клиента","откуда","ссылка на поизон / номер из стока","размер",
    "перевозчик","трекинговый код","статус","цена на поизоне","цена на издержки данного заказа",
    "издержки постоянные","маржа","финальная цена","ссылка на оплату","статус оплаты","rev","updated_at"
]
C_COSTS = [
    "ID","Название","Тип","Значение","Применимость","Активна","Комментарий","rev","updated_at"
]

def _rows_to_dicts(rows: List[List[str]], columns: List[str]) -> List[Dict[str, Any]]:
    items = []
    for r in rows:
        if not any(str(x).strip() for x in r):
            continue
        item = {}
        for i, col in enumerate(columns):
            item[col] = r[i] if i < len(r) else ""
        items.append(item)
    return items

def pull_customers() -> List[Dict[str, Any]]:
    ws = _sheet("Клиенты")
    rows = ws.get_all_values()
    header, data = rows[0], rows[1:]
    # enforce expected order
    return _rows_to_dicts(data, C_CUSTOMERS)

def pull_orders() -> List[Dict[str, Any]]:
    ws = _sheet("Заказы")
    rows = ws.get_all_values()
    header, data = rows[0], rows[1:]
    return _rows_to_dicts(data, C_ORDERS)

def pull_costs() -> List[Dict[str, Any]]:
    ws = _sheet("Постоянные затраты")
    rows = ws.get_all_values()
    header, data = rows[0], rows[1:]
    return _rows_to_dicts(data, C_COSTS)

def upsert_rows(worksheet_name: str, columns: List[str], data: List[Dict[str, Any]]):
    """
    Простая перезапись блоком (батч). Ожидает, что первая строка — заголовки (уже в таблице).
    """
    ws = _sheet(worksheet_name)
    write_rows = []
    for item in data:
        row = [str(item.get(col,"")) for col in columns]
        write_rows.append(row)
    start_cell = "A2"
    end_col = chr(ord('A') + len(columns) - 1)
    end_row = 1 + max(len(write_rows),1)
    ws.resize(end_row)  # безопасно
    ws.update(f"{start_cell}:{end_col}{end_row}", write_rows)
    log.info("Upserted %d rows into %s", len(write_rows), worksheet_name)
