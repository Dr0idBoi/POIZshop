# app/bot/keyboards.py
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# === REPLY KEYBOARDS ===

def kb_consent():
    """Клавиатура согласия"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Согласен")],
            [KeyboardButton(text="Отмена")]
        ],
        resize_keyboard=True, 
        one_time_keyboard=True
    )

def kb_share_phone():
    """Клавиатура для отправки контакта"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Поделиться телефоном", request_contact=True)],
            [KeyboardButton(text="Отмена")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def kb_order_type():
    """Клавиатура выбора типа заказа"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Из стока"), KeyboardButton(text="По ссылке Poizon")],
            [KeyboardButton(text="Отмена")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def kb_main_menu():
    """Главное меню клиента"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛍️ Сделать заказ")],
            [KeyboardButton(text="📦 Мои заказы"), KeyboardButton(text="👤 Мой аккаунт")],
            [KeyboardButton(text="📍 Адрес доставки")]
        ],
        resize_keyboard=True,
        one_time_keyboard=False
    )

def kb_address_menu():
    """Меню управления адресом"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Изменить адрес")],
            [KeyboardButton(text="Назад")]
        ],
        resize_keyboard=True,
        one_time_keyboard=False
    )

def kb_account_menu():
    """Меню управления аккаунтом"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Удалить аккаунт")],
            [KeyboardButton(text="Назад")]
        ],
        resize_keyboard=True,
        one_time_keyboard=False
    )

def kb_cancel():
    """Клавиатура отмены заказа"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Отменить заказ")],
            [KeyboardButton(text="Продолжить")]
        ],
        resize_keyboard=True,
        one_time_keyboard=False
    )

def kb_sizes():
    """Клавиатура для выбора размера обуви"""
    sizes = [
        ["35", "36", "37", "38", "39"],
        ["40", "41", "42", "43", "44"],
        ["45", "46", "47", "48", "49"],
        ["Другой размер"],
        ["Отменить заказ"]
    ]
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=size) for size in row] for row in sizes],
        resize_keyboard=True,
        one_time_keyboard=False
    )

# === INLINE KEYBOARDS ===

def ikb_payment_button(order_id: str, amount: float):
    """Inline-клавиатура для оплаты заказа через YooKassa"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Оплатить {amount:.0f} ₽", callback_data=f"ORDER:PAY_YOOKASSA:{order_id}")]
    ])

def ikb_payment_test_button(order_id: str):
    """Inline-клавиатура для тестовой оплаты заказа клиентом (deprecated)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Оплачено (тест)", callback_data=f"ORDER:PAID_TEST:{order_id}")]
    ])

def ikb_admin_order_actions(order_id: str, is_approved: bool = False):
    """
    Inline-клавиатура для админа для управления заказом.
    Показывается только для неодобренных заказов.
    """
    # Кнопки только для неодобренных заказов
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"ORDER:APPROVE:{order_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"ORDER:REJECT:{order_id}")
        ]
    ])

def ikb_confirm_calculation():
    """Inline-клавиатура для подтверждения расчета админом"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data="CALC:CONFIRM"),
            InlineKeyboardButton(text="❌ Отменить", callback_data="CALC:CANCEL")
        ]
    ])

