# app/bot/handlers/client.py  (добавлены обработчики отмены + мелкие правки)
from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, ReplyKeyboardRemove
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from ...db import open_db
from ..keyboards import kb_consent, kb_share_phone, kb_order_type
import uuid

router = Router(name="client")

class NewOrderFSM(StatesGroup):
    Consent = State()
    Phone = State()
    Ref = State()
    OrderType = State()
    Details = State()

@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(
        "Привет! Я помогу заказать кроссовки из Poizon. Начнём — согласны на обработку данных?",
        reply_markup=kb_consent()
    )
    await state.set_state(NewOrderFSM.Consent)

# универсальная отмена
@router.message(F.text.casefold() == "отмена")
@router.message(Command("cancel"))
async def cancel_any(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("❎ Оформление отменено.", reply_markup=ReplyKeyboardRemove())

@router.message(NewOrderFSM.Consent, F.text.casefold() == "согласен")
async def consent_ok(m: Message, state: FSMContext):
    await m.answer("Отправьте контактный номер", reply_markup=kb_share_phone())
    await state.set_state(NewOrderFSM.Phone)

@router.message(NewOrderFSM.Consent)
async def consent_any(m: Message, state: FSMContext):
    await m.answer("Нажмите «Согласен» или «Отмена».")

@router.message(NewOrderFSM.Phone, F.contact)
async def got_phone_contact(m: Message, state: FSMContext):
    await state.update_data(phone=m.contact.phone_number)
    await m.answer("Есть реф-код? Введите или напишите «нет».", reply_markup=ReplyKeyboardRemove())
    await state.set_state(NewOrderFSM.Ref)

@router.message(NewOrderFSM.Phone, F.text)
async def got_phone_text(m: Message, state: FSMContext):
    await state.update_data(phone=m.text.strip())
    await m.answer("Есть реф-код? Введите или напишите «нет».", reply_markup=ReplyKeyboardRemove())
    await state.set_state(NewOrderFSM.Ref)

@router.message(NewOrderFSM.Ref)
async def got_ref(m: Message, state: FSMContext):
    ref = None if m.text.lower() == "нет" else m.text.strip()
    await state.update_data(ref_code=ref)
    await m.answer("Выберите тип заказа:", reply_markup=kb_order_type())
    await state.set_state(NewOrderFSM.OrderType)

@router.message(NewOrderFSM.OrderType, F.text.in_(["Из стока","По ссылке Poizon"]))
async def order_type_selected(m: Message, state: FSMContext):
    await state.update_data(order_type=("stock" if m.text == "Из стока" else "poizon_link"))
    if m.text == "Из стока":
        await m.answer("Пришлите номер из стока (ID публикации).")
    else:
        await m.answer("Пришлите ссылку на Poizon.")
    await state.set_state(NewOrderFSM.Details)

@router.message(NewOrderFSM.Details)
async def order_details(m: Message, state: FSMContext):
    data = await state.get_data()
    db = await open_db()
    order_id = str(uuid.uuid4())[:8].upper()
    customer_id = str(m.from_user.id)

    # клиент
    await db.execute(
        "INSERT OR IGNORE INTO crm_customers(id, tg_link, phone, ref_code, invited_by, updated_at, rev) "
        "VALUES (?, ?, ?, ?, NULL, datetime('now'), 1)",
        (customer_id, f"@{m.from_user.username}" if m.from_user.username else str(m.from_user.id),
         data.get("phone",""), data.get("ref_code"))
    )

    # черновик заказа
    await db.execute(
        "INSERT OR REPLACE INTO crm_orders(id, customer_id, source_type, poizon_or_stock_ref, size, status, "
        "poizon_price, order_cost_var, order_cost_fixed, margin, final_price, payment_status, updated_at, rev) "
        "VALUES (?, ?, ?, ?, '', 'Ожидает оплату', 0, 0, 0, 0, 'не оплачено', datetime('now'), 1)",
        (order_id, customer_id, data["order_type"], m.text.strip())
    )
    await db.commit()
    await db.close()

    await m.answer(
        f"Заявка #{order_id} создана и отправлена администратору. Вы получите ссылку на оплату после проверки.",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.clear()

@router.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer(
        "Помощь:\n— Оформить заказ: /start\n— Отмена текущего: /cancel\n"
        "— Мои заказы: скоро"
    )
