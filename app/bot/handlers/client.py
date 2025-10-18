# app/bot/handlers/client.py
import uuid
import logging
import re
from aiogram import Router, F, Bot
from aiogram.types import Message, ReplyKeyboardRemove, CallbackQuery
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from datetime import datetime
from app.db import get_db, log_action, add_status_history
from app.config import settings
from app.services.notifications import NotificationService
from app.constants import ORDER_STATUSES, SHEET_NAMES, ORDER_SOURCE_TYPES, PAYMENT_STATUSES
from app.validators import (
    validate_poizon_link, validate_stock_ref, validate_address, sanitize_string
)
from app.bot.keyboards import (
    kb_consent, kb_share_phone, kb_order_type, kb_main_menu,
    kb_address_menu, kb_account_menu, kb_cancel, kb_sizes,
    ikb_admin_order_actions, ikb_payment_test_button, ikb_payment_button, ikb_open_payment_bot
)
from app.crm.sheets import partial_update_by_ext_id, sync_db_to_sheets
from app.bot.middlewares import ClientOnly
from app.bot.fsm_utils import (
    auto_clear_on_error, check_fsm_timeout, update_fsm_timestamp,
    is_fsm_processing, set_fsm_processing, handle_cancel_command,
    validate_message_length
)
from app.config import settings
log = logging.getLogger("client")
router = Router(name="client")
# Убрали ClientOnly middleware - проверка будет в самих handlers
def CMD(name: str):
    return F.text.regexp(fr"^/{name}(?:@\w+)?(?:\s|$)")
# === FSM STATES ===

def _btn(*variants: str):
    # матч по началу строки, игнорим вариации эмодзи и лишние пробелы
    patterns = [rf"^\s*{re.escape(v).replace('️','')}.*$" for v in variants]
    return F.text.func(lambda t: isinstance(t, str) and any(re.match(p.replace('️',''), t.replace('️','')) for p in patterns))

class RegistrationFSM(StatesGroup):
    Address = State()
    Phone = State()
    RefCode = State()
    Consent = State()

class OrderFSM(StatesGroup):
    OrderType = State()
    Details = State()
    Size = State()
    CancelConfirm = State()

class AddressFSM(StatesGroup):
    NewAddress = State()

class AccountFSM(StatesGroup):
    DeleteConfirm = State()

# === UTILITY FUNCTIONS ===

@router.message(RegistrationFSM.Consent)
async def process_consent(m: Message, state: FSMContext):
    """Обработка согласия на обработку персональных данных"""
    text = (m.text or "").strip()
    if "согласиться" in text.lower() or text == "✅ Согласиться с условиями":
        # Переходим к шагу адреса
        await state.set_state(RegistrationFSM.Address)
        await m.answer(
            "🎉 <b>Добро пожаловать в ZakazPOIZ!</b>\n\n"
            "📍 <b>Шаг 1/3:</b> Укажите ваш адрес доставки:\n"
            " Формат: Город, Улица, Дом, Корпус(опционально)",
            reply_markup=ReplyKeyboardRemove()
        )
        return
    
    # Если пользователь прислал что-то другое
    await m.answer(
        "Пожалуйста, нажмите кнопку \"✅ Согласиться с условиями\" для продолжения регистрации.\n\n"
        "Если вы не хотите продолжать, просто закройте бота.",
        reply_markup=kb_consent()
    )

async def notify_admins_new_order(bot: Bot, order_id: str, customer_id: str, order_type: str, reference: str, size: str = ""):
    """Уведомляет всех админов о новом заказе с inline-кнопками"""
    try:
        async with get_db() as db:
            # Получаем информацию о клиенте
            cur = await db.execute("SELECT tg_link, phone, address FROM crm_customers WHERE id=?", (customer_id,))
            customer = await cur.fetchone()
            
            # Получаем всех активных админов
            cur = await db.execute("SELECT tg_user_id FROM admins WHERE is_active=1")
            admins = await cur.fetchall()
        
        # Формируем список админов (включая владельца)
        # Используем set для исключения дубликатов
        admin_ids = set([settings.owner_id])
        admin_ids.update(admin['tg_user_id'] for admin in admins)
        admin_ids = list(admin_ids)
        
        # Формируем сообщение
        order_type_text = "Из стока" if order_type == ORDER_SOURCE_TYPES["STOCK"] else "По ссылке Poizon"
        customer_link = customer['tg_link'] if customer else f"ID: {customer_id}"
        customer_phone = customer['phone'] if customer and customer['phone'] else "Не указан"
        customer_address = customer['address'] if customer and customer['address'] else "Не указан"
        
        message_text = (
            f"🆕 <b>НОВЫЙ ЗАКАЗ #{order_id}</b>\n\n"
            f"👤 <b>Клиент:</b> {customer_link}\n"
            f"📱 <b>Телефон:</b> {customer_phone}\n"
            f"📍 <b>Адрес:</b> {customer_address}\n\n"
            f"📦 <b>Тип заказа:</b> {order_type_text}\n"
            f"🔗 <b>Референс:</b> {reference}\n"
        )
        
        if size:
            message_text += f"👟 <b>Размер:</b> {size}\n"
            
        message_text += (
            f"📊 <b>Статус:</b> Ждет одобрения\n\n"
            f"⚡ <b>Требуется действие администратора!</b>"
        )
        
        # Отправляем уведомления всем админам через сервис уведомлений
        await NotificationService.send_admin_notification(
            bot, 
            message_text, 
            admin_ids
        )
                
    except Exception as e:
        log.error(f"Failed to notify admins about new order {order_id}: {e}")

async def sync_with_sheets():
    try:
        await sync_db_to_sheets()
    except Exception as e:
        log.error(f"Sheets sync error: {e}")

@router.message(CMD("start"))
async def start_command(m: Message, state: FSMContext):
    """Команда /start для клиентов. Админы должны использовать /admin_start"""
    log.info(f"Received /start command from user {m.from_user.id}")
    try:
        await state.clear()
        user_id = m.from_user.id

        # Проверка: является ли пользователь админом
        is_admin = False
        try:
            async with get_db() as db:
                cur = await db.execute("SELECT 1 FROM admins WHERE tg_user_id=? AND is_active=1", (user_id,))
                row = await cur.fetchone()
            is_admin = (user_id == settings.owner_id) or (row is not None)
        except Exception as e:
            log.error(f"Admin check failed: {e}")

        # Если админ - направляем к admin_start
        if is_admin:
            await m.answer(
                "⚙️ <b>Вы администратор!</b>\n\n"
                "Для доступа к админ-панели используйте команду:\n"
                "/admin_start\n\n"
                "Или другие админские команды:\n"
                "• /orders_waiting — заявки на обработку\n"
                "• /stats — статистика\n"
                "• /admin_help — справка"
            )
            return

        # Дальше только для клиентов
        async with get_db() as db:
            cur = await db.execute(
                "SELECT id, address, phone, ref_code, deleted_in_sheets_at FROM crm_customers WHERE id=?",
                (str(user_id),)
            )
            user_data = await cur.fetchone()

        # Проверяем: если пользователь существует И не удален — показываем меню
        if user_data and not user_data['deleted_in_sheets_at']:
            await m.answer("👟 Добро пожаловать обратно! Выберите действие:", reply_markup=kb_main_menu())
            return

        # Новый пользователь — сперва запрос согласия на обработку персональных данных
        await state.set_state(RegistrationFSM.Consent)
        
        # Отправляем пользовательское соглашение
        try:
            from pathlib import Path
            from aiogram.types import FSInputFile
            
            agreement_path = Path(__file__).parent.parent.parent / "Пользовательское соглашение PoizShop.pdf"
            if agreement_path.exists():
                document = FSInputFile(agreement_path)
                await m.answer_document(
                    document=document,
                    caption=(
                        "Нажимая \"Согласиться с условиями\", вы подтверждаете, что:\n"
                        "• Ознакомились с Пользовательским соглашением и принимаете его условия"
                    )
                )
            else:
                log.warning(f"User agreement file not found at {agreement_path}")
                await m.answer(
                    "Нажимая \"Согласиться с условиями\", вы подтверждаете, что:\n"
                    "• Ознакомились с Пользовательским соглашением и принимаете его условия",
                    reply_markup=kb_consent()
                )
                return
        except Exception as e:
            log.error(f"Failed to send user agreement: {e}", exc_info=True)
            await m.answer(
                "Нажимая \"Согласиться с условиями\", вы подтверждаете, что:\n"
                "• Ознакомились с Пользовательским соглашением и принимаете его условия",
                reply_markup=kb_consent()
            )
            return
        
        # Показываем клавиатуру согласия
        await m.answer(
            "Выберите действие:",
            reply_markup=kb_consent()
        )
        # Далее обработка ответа в отдельном хэндлере

    except Exception as e:
        log.error(f"Error in start handler for user {m.from_user.id}: {e}", exc_info=True)
        await m.answer("⚠️ Произошла ошибка. Попробуйте позже.")

@router.message(RegistrationFSM.Address)
async def process_registration_address(m: Message, state: FSMContext):
    """Обработка адреса при регистрации"""
    try:
        address = sanitize_string(m.text.strip(), max_length=500)
        
        # Валидация адреса
        is_valid, error_msg = validate_address(address)
        if not is_valid:
            await m.answer(f"❌ {error_msg}\n\nПожалуйста, введите корректный адрес:")
            return
        
        # Сохраняем адрес и переходим к телефону
        await state.update_data(address=address)
        await state.set_state(RegistrationFSM.Phone)
        
        await m.answer(
            "📱 <b>Шаг 2/3:</b> Поделитесь номером телефона:",
            reply_markup=kb_share_phone()
        )
        
    except Exception as e:
        log.error(f"Error in process_registration_address: {e}")
        await m.answer("⚠️ Произошла ошибка. Попробуйте позже.")
        await state.clear()

@router.message(RegistrationFSM.Phone)
async def process_registration_phone(m: Message, state: FSMContext):
    """Обработка телефона при регистрации (контакт или текст)"""
    try:
        user_id = str(m.from_user.id)
        tg_link = f"tg://user?id={m.from_user.id}"
        
        # Получаем номер телефона из контакта или текста
        phone = ""
        if m.contact and m.contact.phone_number:
            # Если отправлен контакт
            phone = m.contact.phone_number
            log.info(f"Received phone via contact: {phone}")
        elif m.text:
            # Если отправлен текст
            phone = sanitize_string(m.text.strip(), max_length=20)
            log.info(f"Received phone via text: {phone}")
        
        if not phone:
            await m.answer("❌ Пожалуйста, поделитесь номером телефона или введите его вручную.")
            return
        
        # Получаем сохраненный адрес
        data = await state.get_data()
        address = data.get("address", "")
        
        # Сохраняем клиента в БД
        async with get_db() as db:
            # Проверяем, существует ли уже запись (возможно, удаленная)
            cur = await db.execute(
                "SELECT id, deleted_in_sheets_at FROM crm_customers WHERE id=?",
                (user_id,)
            )
            existing = await cur.fetchone()
            
            if existing and existing['deleted_in_sheets_at']:
                # Если запись существует и помечена как удаленная - обновляем её
                await db.execute(
                    """UPDATE crm_customers 
                    SET tg_link=?, phone=?, address=?, 
                        deleted_in_sheets_at=NULL, sheet_row_id=NULL,
                        updated_at=datetime('now'), rev=rev+1 
                    WHERE id=?""",
                    (tg_link, phone, address, user_id)
                )
                log.info(f"Restored deleted customer {user_id}")
            elif not existing:
                # Если записи нет - создаем новую
                await db.execute(
                    """INSERT INTO crm_customers (id, tg_link, phone, address, updated_at, rev) 
                    VALUES (?, ?, ?, ?, datetime('now'), 1)""",
                    (user_id, tg_link, phone, address)
                )
                log.info(f"Created new customer {user_id}")
            else:
                # Если запись существует и не удалена - это ошибка
                log.error(f"Customer {user_id} already exists and is not deleted")
                await m.answer("❌ Ваш аккаунт уже существует. Используйте /start для входа.")
                await state.clear()
                return
            
            await db.commit()
        
        # Завершаем регистрацию
        await state.clear()
        await m.answer(
            "✅ <b>Регистрация завершена!</b>\n\n"
            "Добро пожаловать в ZakazPOIZ! Теперь вы можете создавать заказы.",
            reply_markup=kb_main_menu()
        )
        
        # Логируем действие
        await log_action(user_id, "customer_registered", {
            "phone": phone,
            "address": address
        })
        
        log.info(f"Customer {user_id} registered successfully with phone {phone}")
        
    except Exception as e:
        log.error(f"Error in process_registration_phone: {e}", exc_info=True)
        await m.answer("⚠️ Произошла ошибка. Попробуйте позже.")
        await state.clear()

async def check_order_block(customer_id: str) -> tuple[bool, str]:
    """
    Проверка блокировки заказов для пользователя
    
    Блокировка на 24 часа если за последние 24 часа было 3+ отказа
    
    Returns:
        (is_blocked, reason_message)
    """
    from datetime import datetime, timedelta
    
    try:
        async with get_db() as db:
            # Проверяем отклоненные заказы за последние 24 часа
            twenty_four_hours_ago = (datetime.now() - timedelta(hours=24)).isoformat()
            
            cur = await db.execute(
                """SELECT COUNT(*) as reject_count, MAX(updated_at) as last_reject
                FROM crm_orders
                WHERE customer_id=? AND status=? 
                AND updated_at > ?""",
                (customer_id, ORDER_STATUSES["REJECTED"], twenty_four_hours_ago)
            )
            result = await cur.fetchone()
            
            reject_count = result["reject_count"]
            last_reject = result["last_reject"]
            
            # Если 3 или более отказов за 24 часа - блокировка
            if reject_count >= 3:
                # Вычисляем когда истекает блокировка
                if last_reject:
                    last_reject_time = datetime.fromisoformat(last_reject)
                    unblock_time = last_reject_time + timedelta(hours=24)
                    time_left = unblock_time - datetime.now()
                    
                    hours_left = int(time_left.total_seconds() // 3600)
                    minutes_left = int((time_left.total_seconds() % 3600) // 60)
                    
                    reason = (
                        f"❌ <b>Заказы временно заблокированы</b>\n\n"
                        f"За последние 24 часа у вас было {reject_count} отклоненных заказов.\n\n"
                        f"⏳ Блокировка будет снята через: {hours_left}ч {minutes_left}мин"
                    )
                    return True, reason
            
            return False, ""
            
    except Exception as e:
        log.error(f"Error in check_order_block: {e}")
        return False, ""

@router.message(_btn("🛍️ Сделать заказ", "🛍 Сделать заказ"))
async def start_order(m: Message, state: FSMContext):
    """Начало процесса создания заказа"""
    try:
        customer_id = str(m.from_user.id)
        
        # Проверка на активный FSM процесс (защита от race conditions)
        if await is_fsm_processing(state):
            await m.answer(
                "⏳ У вас уже есть активный процесс создания заказа. "
                "Завершите его или отмените командой /cancel",
                reply_markup=kb_main_menu()
            )
            return
        
        # Проверка блокировки
        is_blocked, block_message = await check_order_block(customer_id)
        if is_blocked:
            await m.answer(block_message, reply_markup=kb_main_menu())
            return
        
        # Устанавливаем флаг обработки
        await set_fsm_processing(state, True)
        await update_fsm_timestamp(state)
        
        await state.set_state(OrderFSM.OrderType)
        await m.answer(
            "📦 <b>Новый заказ</b>\n\n"
            "Выберите тип заказа:",
            reply_markup=kb_order_type()
        )
    except Exception as e:
        log.error(f"Error in start_order: {e}")
        await state.clear()
        await m.answer("❌ Произошла ошибка. Попробуйте позже.", reply_markup=kb_main_menu())

@router.message(_btn("📦 Мои заказы"))
async def my_orders(m: Message):
    """Просмотр списка заказов пользователя"""
    try:
        user_id = str(m.from_user.id)
        
        async with get_db() as db:
            # Проверяем, что аккаунт не удален
            cur = await db.execute(
                "SELECT deleted_in_sheets_at FROM crm_customers WHERE id=?",
                (user_id,)
            )
            customer = await cur.fetchone()
            
            if not customer or customer['deleted_in_sheets_at']:
                await m.answer("❌ Ваш аккаунт не найден. Попробуйте перезапустить бота командой /start")
                return
            
            # Получаем заказы пользователя (исключаем удаленные)
            cur = await db.execute(
                """SELECT id, status, updated_at, poizon_or_stock_ref, final_price 
                FROM crm_orders 
                WHERE customer_id=? AND deleted_in_sheets_at IS NULL
                ORDER BY updated_at DESC 
                LIMIT 10""",
                (user_id,)
            )
            orders = await cur.fetchall()
        
        if not orders:
            await m.answer(
                "📦 <b>Мои заказы</b>\n\n"
                "У вас пока нет заказов. Нажмите '🛍️ Сделать заказ', чтобы создать новый.",
                reply_markup=kb_main_menu()
            )
            return
        
        # Формируем сообщение со списком заказов
        msg = ["📦 <b>Мои заказы</b>\n"]
        
        for order in orders:
            # Форматируем дату
            date_str = order["updated_at"].split()[0] if order["updated_at"] else "Н/Д"
            
            # Форматируем ссылку/номер
            ref = order["poizon_or_stock_ref"]
            if len(ref) > 20:
                ref = ref[:17] + "..."
            
            # Форматируем цену
            price_str = f"{order['final_price']} ₽" if order["final_price"] else "Не указана"
            
            msg.append(
                f"• <b>#{order['id']}</b> | {date_str}\n"
                f"  <b>Статус:</b> {order['status']}\n"
                f"  <b>Ссылка/номер:</b> {ref}\n"
                f"  <b>Цена:</b> {price_str}\n"
            )
        
        await m.answer("\n".join(msg), reply_markup=kb_main_menu())
        
    except Exception as e:
        log.error(f"Error in my_orders: {e}")
        await m.answer("❌ Произошла ошибка при загрузке заказов. Попробуйте позже.")

@router.message(_btn("👤 Мой аккаунт"))
async def my_account(m: Message):
    """Просмотр информации об аккаунте"""
    try:
        user_id = str(m.from_user.id)
        
        async with get_db() as db:
            # Получаем данные пользователя
            cur = await db.execute(
                "SELECT * FROM crm_customers WHERE id=?",
                (user_id,)
            )
            user = await cur.fetchone()
            
            if not user or user['deleted_in_sheets_at']:
                await m.answer("❌ Ваш аккаунт не найден. Попробуйте перезапустить бота командой /start")
                return
            
            # Получаем количество НЕ удалённых заказов
            cur = await db.execute(
                "SELECT COUNT(*) as count FROM crm_orders WHERE customer_id=? AND deleted_in_sheets_at IS NULL",
                (user_id,)
            )
            orders_count = (await cur.fetchone())["count"]
        
        # Формируем сообщение с информацией об аккаунте
        msg = [
            "👤 <b>Мой аккаунт</b>\n",
            f"<b>ID:</b> {user['id']}",
            f"<b>Телефон:</b> {user['phone'] or 'Не указан'}",
            f"<b>Адрес:</b> {user['address'] or 'Не указан'}",
            f"<b>Заказов:</b> {orders_count}",
            f"<b>Создан:</b> {user['created_at'].split()[0] if user['created_at'] else 'Н/Д'}"
        ]
        
        await m.answer("\n".join(msg), reply_markup=kb_account_menu())
        
    except Exception as e:
        log.error(f"Error in my_account: {e}")
        await m.answer("❌ Произошла ошибка при загрузке данных аккаунта. Попробуйте позже.")

@router.message(_btn("📍 Адрес доставки"))
async def delivery_address(m: Message):
    """Просмотр и управление адресом доставки"""
    try:
        user_id = str(m.from_user.id)
        
        async with get_db() as db:
            # Получаем адрес пользователя
            cur = await db.execute(
                "SELECT address, deleted_in_sheets_at FROM crm_customers WHERE id=?",
                (user_id,)
            )
            user = await cur.fetchone()
            
            if not user or user['deleted_in_sheets_at']:
                await m.answer("❌ Ваш аккаунт не найден. Попробуйте перезапустить бота командой /start")
                return
        
        # Формируем сообщение с адресом
        address = user["address"] or "Не указан"
        
        await m.answer(
            f"📍 <b>Адрес доставки</b>\n\n"
            f"{address}\n\n"
            f"Вы можете изменить адрес доставки, нажав кнопку ниже:",
            reply_markup=kb_address_menu()
        )
        
    except Exception as e:
        log.error(f"Error in delivery_address: {e}")
        await m.answer("❌ Произошла ошибка при загрузке адреса. Попробуйте позже.")

@router.message(F.text == "Назад")
async def back_to_main_menu(m: Message, state: FSMContext):
    """Возврат в главное меню"""
    await state.clear()
    await m.answer("Выберите действие:", reply_markup=kb_main_menu())


@router.message(F.text.in_(["❌ Отмена", "/cancel"]))
async def universal_cancel(m: Message, state: FSMContext):
    """Универсальный обработчик отмены для всех FSM состояний"""
    current_state = await state.get_state()
    
    if current_state:
        log.info(f"User {m.from_user.id} cancelled FSM state: {current_state}")
        await handle_cancel_command(m, state, kb_main_menu())
    else:
        await m.answer("Нечего отменять. Выберите действие:", reply_markup=kb_main_menu())

@router.message(OrderFSM.OrderType)
async def process_order_type(m: Message, state: FSMContext):
    """Обработка типа заказа"""
    try:
        # Валидация ввода
        order_type = None
        if m.text == "Из стока":
            order_type = ORDER_SOURCE_TYPES["STOCK"]
        elif m.text == "По ссылке Poizon":
            order_type = ORDER_SOURCE_TYPES["POIZON_LINK"]
        elif m.text == "Отмена":
            await m.answer("❌ Заказ отменен.")
            await state.clear()
            await m.answer("Выберите действие:", reply_markup=kb_main_menu())
            return
        else:
            await m.answer("❌ Пожалуйста, выберите тип заказа из предложенных вариантов.")
            return
            
        # Сохраняем тип заказа
        await state.update_data(order_type=order_type)
        
        # Запрашиваем детали
        prompt = "📝 <b>Шаг 2/3:</b> " + (
            "Введите номер товара из стока:" if order_type == ORDER_SOURCE_TYPES["STOCK"]
            else "Вставьте ссылку на товар из приложения Poizon:"
        )
        
        await state.set_state(OrderFSM.Details)
        await m.answer(prompt, reply_markup=kb_cancel())
        
    except Exception as e:
        log.error(f"Error in process_order_type: {e}")
        await m.answer("❌ Произошла ошибка. Попробуйте позже.")
        await state.clear()

@router.message(OrderFSM.Details)
async def process_order_details(m: Message, state: FSMContext):
    """Обработка деталей заказа"""
    try:
        # Получаем данные из FSM
        data = await state.get_data()
        order_type = data.get("order_type")
        
        if not order_type:
            await m.answer("❌ Произошла ошибка. Начните заказ заново.")
            await state.clear()
            return
            
        # Валидация ввода
        details = sanitize_string(m.text.strip(), max_length=1000)
        if not details:
            await m.answer("❌ Пожалуйста, введите корректные данные.")
            return
        
        # Специфичная валидация в зависимости от типа
        if order_type == ORDER_SOURCE_TYPES["POIZON_LINK"]:
            is_valid, error_msg = validate_poizon_link(details)
            if not is_valid:
                await m.answer(f"❌ {error_msg}\n\nПожалуйста, введите корректную ссылку:")
                return
        elif order_type == ORDER_SOURCE_TYPES["STOCK"]:
            is_valid, error_msg = validate_stock_ref(details)
            if not is_valid:
                await m.answer(f"❌ {error_msg}\n\nПожалуйста, введите корректный номер товара:")
                return
            
        # Сохраняем детали
        await state.update_data(details=details)
        
        # Запрашиваем размер (для всех типов заказов)
        await state.set_state(OrderFSM.Size)
        await m.answer(
            "👟 <b>Шаг 3/3:</b> Укажите размер:\n\n"
            "Введите размер обуви (например: 42, 43.5, US 10)",
            reply_markup=kb_cancel()
        )
            
    except Exception as e:
        log.error(f"Error in process_order_details: {e}")
        await m.answer("❌ Произошла ошибка. Попробуйте позже.")
        await state.clear()

@router.message(OrderFSM.Size)
async def process_size(m: Message, state: FSMContext, bot: Bot):
    """Обработка размера"""
    try:
        # Валидация ввода
        size = sanitize_string(m.text.strip(), max_length=20)
        
        if not size or size == "❌ Отмена":
            await m.answer("❌ Заказ отменен.")
            await state.clear()
            await m.answer("Выберите действие:", reply_markup=kb_main_menu())
            return
        
        # Простая валидация размера (должен содержать цифры)
        if not any(char.isdigit() for char in size):
            await m.answer(
                "❌ Размер должен содержать цифры.\n\n"
                "Введите размер (например: 42, 43.5, US 10):"
            )
            return
            
        # Сохраняем размер
        await state.update_data(size=size)
        
        # Создаем заказ
        await create_order(m, state, bot)
        
    except Exception as e:
        log.error(f"Error in process_size: {e}")
        await m.answer("❌ Произошла ошибка. Попробуйте позже.")
        await state.clear()

async def create_order(m: Message, state: FSMContext, bot: Bot):
    """Создание заказа"""
    try:
        # Получаем данные из FSM
        data = await state.get_data()
        order_type = data.get("order_type")
        details = data.get("details")
        size = data.get("size", "")
        
        # Генерируем ID заказа
        order_id = f"ORD_{uuid.uuid4().hex[:8].upper()}"
        customer_id = str(m.from_user.id)
        
        # Проверяем, что аккаунт не удален
        async with get_db() as db:
            cur = await db.execute(
                "SELECT deleted_in_sheets_at FROM crm_customers WHERE id=?",
                (customer_id,)
            )
            customer = await cur.fetchone()
            
            if not customer or customer['deleted_in_sheets_at']:
                await m.answer("❌ Ваш аккаунт не найден. Попробуйте перезапустить бота командой /start", reply_markup=kb_main_menu())
                await state.clear()
                return
        
        # Сохраняем заказ в БД
        async with get_db() as db:
            await db.execute(
                """INSERT INTO crm_orders (
                id, customer_id, source_type, poizon_or_stock_ref, size, 
                status, awaiting_decision, created_at, updated_at, ext_id, rev
                ) VALUES (?, ?, ?, ?, ?, ?, 1, datetime('now'), datetime('now'), ?, 1)""",
                (
                    order_id, customer_id, order_type, details, size, 
                    ORDER_STATUSES["AWAITING_DECISION"], order_id, 
                )
            )
            await db.commit()
            
            # Логируем действие
            await log_action(customer_id, "order_created", {
                "order_id": order_id,
                "source_type": order_type,
                "reference": details,
                "size": size
            })
        
        # Уведомляем админов о новом заказе
        await notify_admins_new_order(bot, order_id, customer_id, order_type, details, size)
        
        # Уведомляем клиента
        await m.answer(
            f"✅ <b>Заказ #{order_id} успешно создан!</b>\n\n"
            f"Ваша заявка передана администратору. "
            f"После одобрения вы получите уведомление с суммой к оплате.\n\n"
            f"Спасибо за заказ!",
            reply_markup=kb_main_menu()
        )
        
        # Очищаем FSM
        await state.clear()
        
    except Exception as e:
        log.error(f"Error creating order: {e}")
        await m.answer("❌ Произошла ошибка при создании заказа. Попробуйте позже.")
        await state.clear()
        await m.answer("Выберите действие:", reply_markup=kb_main_menu())

@router.callback_query(F.data.startswith("ORDER:PAID_TEST:"))
async def cb_order_paid_test(cq: CallbackQuery, bot: Bot):
    pay_url = settings.payment_bot_url or "https://t.me/YourPaymentsBot"
    await cq.message.answer(
        "✅ Подтверждение оплаты в этом боте отключено.\n"
        "Перейдите во второго бота и произведите оплату.",
        reply_markup=ikb_open_payment_bot(pay_url, "Открыть второго бота")
    )
    await cq.answer()

@router.callback_query(F.data.startswith("ORDER:PAY_YOOKASSA:"))
async def cb_order_pay_yookassa(cq: CallbackQuery, bot: Bot):
    try:
        order_id = cq.data.split(":", 2)[2]
    except Exception:
        order_id = "UNKNOWN"

    pay_url = settings.payment_bot_url or "https://t.me/YourPaymentsBot"
    await cq.message.answer(
        f"💳 Оплата для заказа #{order_id} теперь принимается во <b>втором боте</b>.",
        reply_markup=ikb_open_payment_bot(pay_url, "Открыть второго бота")
    )
    await cq.answer()
# === УПРАВЛЕНИЕ АДРЕСОМ ===

@router.message(F.text == "Изменить адрес")
async def change_address_start(m: Message, state: FSMContext):
    """Начало процесса смены адреса"""
    try:
        await state.set_state(AddressFSM.NewAddress)
        await update_fsm_timestamp(state)
        
        await m.answer(
            "📍 <b>Изменение адреса доставки</b>\n\n"
            "Введите новый адрес доставки:\n\n"
            "<i>Пример: г. Москва, ул. Ленина, д. 10, кв. 5</i>",
            reply_markup=ReplyKeyboardRemove()
        )
    except Exception as e:
        log.error(f"Error in change_address_start: {e}")
        await m.answer("❌ Произошла ошибка. Попробуйте позже.", reply_markup=kb_main_menu())

@router.message(AddressFSM.NewAddress)
async def process_new_address(m: Message, state: FSMContext, bot: Bot):
    """Обработка нового адреса"""
    try:
        # Проверка таймаута
        if await check_fsm_timeout(state):
            await m.answer(
                "⏱ Время ожидания истекло. Начните процесс заново.",
                reply_markup=kb_main_menu()
            )
            await state.clear()
            return
        
        new_address = m.text.strip()
        customer_id = str(m.from_user.id)
        
        # Валидация адреса
        is_valid, error_msg = validate_address(new_address)
        if not is_valid:
            await m.answer(
                f"❌ {error_msg}\n\n"
                "Пожалуйста, введите корректный адрес."
            )
            return
        
        # Сохраняем старый адрес для логирования
        async with get_db() as db:
            cur = await db.execute(
                "SELECT address, deleted_in_sheets_at FROM crm_customers WHERE id=?",
                (customer_id,)
            )
            old_data = await cur.fetchone()
            
            # Проверяем, что аккаунт не удален
            if not old_data or old_data['deleted_in_sheets_at']:
                await m.answer("❌ Ваш аккаунт не найден. Попробуйте перезапустить бота командой /start", reply_markup=kb_main_menu())
                await state.clear()
                return
            
            old_address = old_data['address'] if old_data else ""
            
            # Обновляем адрес в БД
            await db.execute(
                "UPDATE crm_customers SET address=?, updated_at=datetime('now'), rev=rev+1 WHERE id=?",
                (new_address, customer_id)
            )
            await db.commit()
            
            # Логируем действие
            await log_action(
                customer_id,
                "address_changed",
                {
                    "old_address": old_address,
                    "new_address": new_address
                }
            )
        
        # Обновляем в Google Sheets
        try:
            from app.constants import CUSTOMER_COLUMNS
            
            partial_update_by_ext_id(
                SHEET_NAMES["CUSTOMERS"],
                customer_id,
                {
                    CUSTOMER_COLUMNS["ADDRESS"]: new_address,
                    CUSTOMER_COLUMNS["UPDATED_AT"]: datetime.now().isoformat()
                }
            )
            log.info(f"Updated address in Sheets for customer {customer_id}")
        except Exception as e:
            log.error(f"Failed to update address in Sheets: {e}")
        
        await state.clear()
        await m.answer(
            "✅ <b>Адрес успешно изменен!</b>\n\n"
            f"📍 <b>Новый адрес:</b>\n{new_address}",
            reply_markup=kb_main_menu()
        )
        
    except Exception as e:
        log.error(f"Error in process_new_address: {e}", exc_info=True)
        await state.clear()
        await m.answer("❌ Произошла ошибка при изменении адреса. Попробуйте позже.", reply_markup=kb_main_menu())

# === УПРАВЛЕНИЕ АККАУНТОМ ===

@router.message(_btn("👤 Мой аккаунт"))
async def my_account(m: Message):
    """Показать информацию об аккаунте"""
    try:
        customer_id = str(m.from_user.id)
        
        async with get_db() as db:
            cur = await db.execute(
                "SELECT phone, address, created_at, deleted_in_sheets_at FROM crm_customers WHERE id=?",
                (customer_id,)
            )
            customer = await cur.fetchone()
            
            # Проверяем, что аккаунт не удален
            if not customer or customer['deleted_in_sheets_at']:
                await m.answer("❌ Ваш аккаунт не найден. Попробуйте перезапустить бота командой /start")
                return
            
            # Получаем количество заказов
            cur = await db.execute(
                "SELECT COUNT(*) as count FROM crm_orders WHERE customer_id=? AND deleted_in_sheets_at IS NULL",
                (customer_id,)
            )
            orders_count = (await cur.fetchone())['count']
        
        if customer and not customer['deleted_in_sheets_at']:
            created_date = datetime.fromisoformat(customer['created_at']).strftime("%d.%m.%Y")
            
            await m.answer(
                f"👤 <b>Информация об аккаунте</b>\n\n"
                f"📱 <b>Телефон:</b> {customer['phone']}\n"
                f"📍 <b>Адрес:</b> {customer['address']}\n"
                f"📦 <b>Всего заказов:</b> {orders_count}\n"
                f"📅 <b>Дата регистрации:</b> {created_date}\n\n"
                f"Вы можете удалить свой аккаунт, выбрав соответствующую опцию.",
                reply_markup=kb_account_menu()
            )
        else:
            await m.answer("❌ Информация об аккаунте не найдена.", reply_markup=kb_main_menu())
    
    except Exception as e:
        log.error(f"Error in my_account: {e}")
        await m.answer("❌ Произошла ошибка при загрузке информации. Попробуйте позже.", reply_markup=kb_main_menu())

@router.message(F.text == "Удалить аккаунт")
async def delete_account_confirm(m: Message, state: FSMContext):
    """Подтверждение удаления аккаунта"""
    try:
        await state.set_state(AccountFSM.DeleteConfirm)
        await update_fsm_timestamp(state)
        
        await m.answer(
            "⚠️ <b>ВНИМАНИЕ!</b>\n\n"
            "Вы уверены, что хотите удалить свой аккаунт?\n\n"
            "❗️ <b>Это действие необратимо!</b>\n\n"
            "При удалении аккаунта:\n"
            "• Будут удалены все ваши данные\n"
            "• История заказов будет недоступна\n"
            "• Вам придется зарегистрироваться заново\n\n"
            "Напишите <b>УДАЛИТЬ</b> (заглавными буквами), чтобы подтвердить удаление, "
            "или нажмите 'Назад' для отмены.",
            reply_markup=kb_account_menu()
        )
    except Exception as e:
        log.error(f"Error in delete_account_confirm: {e}")
        await m.answer("❌ Произошла ошибка. Попробуйте позже.", reply_markup=kb_main_menu())

@router.message(AccountFSM.DeleteConfirm)
async def process_delete_account(m: Message, state: FSMContext, bot: Bot):
    """Обработка удаления аккаунта"""
    try:
        # Проверка таймаута
        if await check_fsm_timeout(state):
            await m.answer(
                "⏱ Время ожидания истекло. Удаление отменено.",
                reply_markup=kb_main_menu()
            )
            await state.clear()
            return
        
        confirmation = m.text.strip()
        customer_id = str(m.from_user.id)
        
        # Проверка подтверждения
        if confirmation != "УДАЛИТЬ":
            await m.answer(
                "❌ Неверное подтверждение. Удаление отменено.\n\n"
                "Для удаления аккаунта напишите <b>УДАЛИТЬ</b> (заглавными буквами)."
            )
            return
        
        # Отправляем сообщение о начале процесса
        status_msg = await m.answer(
            "🔄 <b>Удаление аккаунта...</b>\n\n"
            "Пожалуйста, подождите. Это может занять некоторое время.",
            reply_markup=ReplyKeyboardRemove()
        )
        
        try:
            # Архивируем и помечаем клиента как удаленного в БД
            async with get_db() as db:
                # Получаем информацию о клиенте для логирования
                cur = await db.execute(
                    "SELECT * FROM crm_customers WHERE id=?",
                    (customer_id,)
                )
                customer_data = await cur.fetchone()
                
                # Архивируем клиента
                try:
                    from json import dumps
                    await db.execute(
                        "INSERT INTO archives(entity_type, entity_id, payload_json, archived_reason) VALUES(?, ?, ?, ?)",
                        ("customer", customer_id, dumps(dict(customer_data) if customer_data else {} , ensure_ascii=False), "account_deleted")
                    )
                except Exception as e:
                    log.error(f"Failed to archive customer {customer_id}: {e}")

                # Помечаем клиента как удаленного
                await db.execute(
                    "UPDATE crm_customers SET "
                    "deleted_in_sheets_at=datetime('now'), "
                    "updated_at=datetime('now'), "
                    "rev=rev+1 "
                    "WHERE id=?",
                    (customer_id,)
                )
                
                # Получаем заказы клиента для архива
                cur = await db.execute(
                    "SELECT * FROM crm_orders WHERE customer_id=?",
                    (customer_id,)
                )
                orders_to_archive = [dict(r) for r in await cur.fetchall()]

                # Архивируем заказы
                try:
                    from json import dumps
                    for order in orders_to_archive:
                        await db.execute(
                            "INSERT INTO archives(entity_type, entity_id, payload_json, archived_reason) VALUES(?, ?, ?, ?)",
                            ("order", order.get("id"), dumps(order, ensure_ascii=False), "account_deleted")
                        )
                except Exception as e:
                    log.error(f"Failed to archive orders for customer {customer_id}: {e}")

                # Помечаем все заказы клиента как удаленные
                await db.execute(
                    "UPDATE crm_orders SET "
                    "deleted_in_sheets_at=datetime('now'), "
                    "updated_at=datetime('now'), "
                    "rev=rev+1 "
                    "WHERE customer_id=?",
                    (customer_id,)
                )
                
                # Архивируем историю статусов заказов клиента
                try:
                    cur = await db.execute(
                        "SELECT * FROM order_status_history WHERE order_id IN (SELECT id FROM crm_orders WHERE customer_id=?)",
                        (customer_id,)
                    )
                    histories = [dict(r) for r in await cur.fetchall()]
                    from json import dumps
                    for h in histories:
                        await db.execute(
                            "INSERT INTO archives(entity_type, entity_id, payload_json, archived_reason) VALUES(?, ?, ?, ?)",
                            ("order_status_history", h.get("order_id"), dumps(h, ensure_ascii=False), "account_deleted")
                        )
                except Exception as e:
                    log.error(f"Failed to archive status history for customer {customer_id}: {e}")

                await db.commit()
                
                # Логируем действие
                await log_action(
                    customer_id,
                    "account_deleted",
                    {
                        "phone": customer_data['phone'] if customer_data else "",
                        "address": customer_data['address'] if customer_data else "",
                        "deleted_at": datetime.now().isoformat()
                    }
                )
            
            # Запускаем полную синхронизацию (без промежуточных сообщений)
            from app.services.sync_manager import run_full_sync
            await run_full_sync(bot)
            
            # Очищаем состояние
            await state.clear()
            
            # Отправляем финальное сообщение (всегда новое, не редактируем)
            await m.answer(
                "✅ <b>Аккаунт успешно удален</b>\n\n"
                "Ваши данные были удалены из системы.\n\n"
                "Спасибо, что пользовались нашим сервисом!\n\n"
                "Если захотите вернуться, используйте команду /start для регистрации."
            )
            
            log.info(f"Customer {customer_id} account deleted successfully")
            
        except Exception as e:
            log.error(f"Error during account deletion for {customer_id}: {e}", exc_info=True)
            # Отправляем новое сообщение вместо редактирования
            await m.answer(
                "❌ <b>Ошибка при удалении аккаунта</b>\n\n"
                "Произошла ошибка. Пожалуйста, обратитесь к администратору."
            )
            await state.clear()
        
    except Exception as e:
        log.error(f"Error in process_delete_account: {e}", exc_info=True)
        await state.clear()
        await m.answer("❌ Произошла ошибка при удалении аккаунта. Попробуйте позже.", reply_markup=kb_main_menu())

# Другие обработчики клиентского бота
