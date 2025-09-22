# app/bot/keyboards.py
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

def kb_consent():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Согласен")],[KeyboardButton(text="Отмена")]],
        resize_keyboard=True, one_time_keyboard=True
    )

def kb_share_phone():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Поделиться телефоном", request_contact=True)],
                  [KeyboardButton(text="Отмена")]],
        resize_keyboard=True
    )

def kb_order_type():
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Из стока"), KeyboardButton(text="По ссылке Poizon")],
            [KeyboardButton(text="Отмена")]
        ],
        resize_keyboard=True
    )
    return kb

def ikb_admin_order(order_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Одобрить", callback_data=f"ORDER:APPROVE:{order_id}"),
         InlineKeyboardButton(text="Отклонить", callback_data=f"ORDER:REJECT:{order_id}")],
        [InlineKeyboardButton(text="Сменить статус", callback_data=f"ORDER:STATUS_MENU:{order_id}")],
        [InlineKeyboardButton(text="Добавить трек", callback_data=f"ORDER:TRACK:{order_id}")],
        [InlineKeyboardButton(text="Инд. траты", callback_data=f"ORDER:COSTVAR:{order_id}")],
        [InlineKeyboardButton(text="Смоделировать оплату", callback_data=f"ORDER:MOCKPAY:{order_id}")]
    ])

def ikb_status_menu(order_id: str):
    statuses = [
        "Ожидает оплату","Оплачен","В пути по Китаю","на пути на склад","на складе",
        "На пути к получателю","выдано","закрыт","отмена"
    ]
    rows = []
    row = []
    for s in statuses:
        row.append(InlineKeyboardButton(text=s, callback_data=f"ORDER:STATUS:{order_id}:{s}"))
        if len(row)==2:
            rows.append(row); row=[]
    if row: rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)
