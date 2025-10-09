"""
Модуль валидации данных для проекта ZakazPOIZ Bot
"""
import re
from typing import Optional, Tuple


def validate_order_id(order_id: str) -> bool:
    """
    Проверяет корректность ID заказа
    
    Args:
        order_id: ID заказа для проверки
        
    Returns:
        True если ID корректный
    """
    if not order_id:
        return False
    # Формат: ORD_XXXXXXXX (где X - hex символы)
    pattern = r'^ORD_[A-F0-9]{8}$'
    return bool(re.match(pattern, order_id))


def validate_customer_id(customer_id: str) -> bool:
    """
    Проверяет корректность ID клиента (Telegram ID)
    
    Args:
        customer_id: ID клиента для проверки
        
    Returns:
        True если ID корректный
    """
    if not customer_id:
        return False
    # Telegram ID - это число
    return customer_id.isdigit() and len(customer_id) > 5


def validate_phone(phone: str) -> Tuple[bool, Optional[str]]:
    """
    Проверяет корректность номера телефона
    
    Args:
        phone: Номер телефона для проверки
        
    Returns:
        Кортеж (валидность, сообщение об ошибке)
    """
    if not phone:
        return False, "Номер телефона не может быть пустым"
    
    # Убираем все символы кроме цифр и +
    clean_phone = re.sub(r'[^\d+]', '', phone)
    
    # Проверяем длину (минимум 10 цифр)
    if len(clean_phone) < 10:
        return False, "Номер телефона должен содержать минимум 10 цифр"
    
    # Проверяем формат (начинается с + или цифры)
    if not re.match(r'^[\+]?[\d]{10,15}$', clean_phone):
        return False, "Некорректный формат номера телефона"
    
    return True, None


def validate_poizon_link(link: str) -> Tuple[bool, Optional[str]]:
    """
    Проверяет корректность ссылки на товар
    Принимает любые ссылки (http/https), не только POIZON/DEWU
    
    Args:
        link: Ссылка для проверки
        
    Returns:
        Кортеж (валидность, сообщение об ошибке)
    """
    if not link:
        return False, "Ссылка не может быть пустой"
    
    # Проверяем, что это действительно ссылка (содержит http:// или https://)
    url_pattern = r'https?://.+'
    if not re.match(url_pattern, link.strip(), re.IGNORECASE):
        return False, "Некорректный формат ссылки (должна начинаться с http:// или https://)"
    
    # Проверяем минимальную длину ссылки (http://a.b = минимум 10 символов)
    if len(link.strip()) < 10:
        return False, "Ссылка слишком короткая"
    
    return True, None


def validate_stock_ref(ref: str) -> Tuple[bool, Optional[str]]:
    """
    Проверяет корректность номера из стока
    
    Args:
        ref: Номер для проверки
        
    Returns:
        Кортеж (валидность, сообщение об ошибке)
    """
    if not ref:
        return False, "Номер товара не может быть пустым"
    
    # Номер должен содержать буквы или цифры
    if not re.match(r'^[A-Za-z0-9\-_]+$', ref):
        return False, "Номер товара может содержать только буквы, цифры, дефисы и подчеркивания"
    
    if len(ref) < 3:
        return False, "Номер товара слишком короткий (минимум 3 символа)"
    
    return True, None


def validate_price(price: float) -> Tuple[bool, Optional[str]]:
    """
    Проверяет корректность цены
    
    Args:
        price: Цена для проверки
        
    Returns:
        Кортеж (валидность, сообщение об ошибке)
    """
    if price is None:
        return False, "Цена не может быть пустой"
    
    if price < 0:
        return False, "Цена не может быть отрицательной"
    
    if price == 0:
        return False, "Цена не может быть равна нулю"
    
    # Проверка на разумность (максимум 1 миллион рублей)
    if price > 1_000_000:
        return False, "Цена слишком большая (максимум 1 000 000 ₽)"
    
    return True, None


def validate_address(address: str) -> Tuple[bool, Optional[str]]:
    """
    Проверяет корректность адреса доставки
    
    Args:
        address: Адрес для проверки
        
    Returns:
        Кортеж (валидность, сообщение об ошибке)
    """
    if not address:
        return False, "Адрес не может быть пустым"
    
    # Минимальная длина адреса
    if len(address.strip()) < 10:
        return False, "Адрес слишком короткий (минимум 10 символов)"
    
    # Максимальная длина адреса
    if len(address) > 500:
        return False, "Адрес слишком длинный (максимум 500 символов)"
    
    return True, None


def sanitize_string(text: str, max_length: int = 1000) -> str:
    """
    Очищает строку от потенциально опасных символов
    
    Args:
        text: Строка для очистки
        max_length: Максимальная длина
        
    Returns:
        Очищенная строка
    """
    if not text:
        return ""
    
    # Удаляем управляющие символы
    cleaned = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)
    
    # Обрезаем до максимальной длины
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length]
    
    return cleaned.strip()

