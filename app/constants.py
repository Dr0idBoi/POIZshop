"""
Модуль констант для проекта ZakazPOIZ Bot
Содержит все строковые константы, используемые в проекте
"""

# Статусы заказов
ORDER_STATUSES = {
    "AWAITING_PAYMENT": "Ожидает оплату",
    "AWAITING_DECISION": "Ожидает решения",
    "REJECTED": "Отклонён",
    "PAID": "Оплачен",
    "IN_CHINA": "В пути по Китаю",
    "TO_WAREHOUSE": "На пути на склад",
    "TO_CUSTOMER": "На пути к получателю",
    "DELIVERED": "Выдано",
    "CLOSED": "Закрыт",
    "CANCELLED": "Отмена"
}

# Статусы оплаты
PAYMENT_STATUSES = {
    "PENDING": "не оплачено",
    "PAID": "оплачено",
    "FAILED": "ошибка оплаты",
    "REFUNDED": "возврат"
}

# Типы источников заказа
ORDER_SOURCE_TYPES = {
    "STOCK": "stock",
    "POIZON_LINK": "poizon_link"
}

# Названия листов
SHEET_NAMES = {
    "CUSTOMERS": "Клиенты",
    "ORDERS": "Заказы",
    "FINANCIAL": "Финансовые настройки"
}

# Колонки в листе Клиенты
CUSTOMER_COLUMNS = {
    "ID": "ID Клиента",
    "TG_LINK": "Ссылка на тг",
    "PHONE": "Контактный номер",
    "ADDRESS": "Адрес",
    "REF_CODE": "Реф Код",
    "INVITED_BY": "приглашен кем",
    "ORDER_LIST": "список заказов",
    "REV": "rev",
    "UPDATED_AT": "updated_at"
}

# Колонки в листе Заказы
ORDER_COLUMNS = {
    "ID": "ID Заказа",
    "CUSTOMER_ID": "ID клиента",
    "SOURCE_TYPE": "откуда",
    "POIZON_REF": "ссылка на поизон / номер из стока",
    "SIZE": "размер",
    "CARRIER": "перевозчик",
    "TRACKING": "трекинговый код",
    "STATUS": "статус",
    "POIZON_PRICE": "цена на поизоне",
    "VAR_COSTS": "цена на издержки данного заказа",
    "FIXED_COSTS": "издержки постоянные",
    "MARGIN": "маржа",
    "FINAL_PRICE": "финальная цена",
    "PAYMENT_URL": "ссылка на оплату",
    "PAYMENT_STATUS": "статус оплаты",
    "REV": "rev",
    "UPDATED_AT": "updated_at"
}

# Колонки в листе Финансовые настройки
FINANCIAL_COLUMNS = {
    "ID": "ID",
    "NAME": "Название пост. траты",
    "VALUE": "Значение Руб",
    "REV": "rev",
    "UPDATED_AT": "updated_at"
}

# Ячейки финансовых настроек
FINANCIAL_CELLS = {
    "MARGIN_PERCENT": "L5",
    "FIXED_COSTS": "L6"
}

# Названия финансовых настроек
FINANCIAL_SETTINGS = {
    "FIXED_COSTS": "Постоянные траты",
    "MARGIN_PERCENT": "Маржа"
}
