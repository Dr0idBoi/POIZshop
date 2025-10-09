# app/services/notifications.py
import logging
from typing import Optional, Union
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from ..db import get_db, log_action
from ..config import settings

log = logging.getLogger("notifications")

class NotificationService:
    """Сервис для управления уведомлениями в Telegram боте"""
    
    @staticmethod
    async def send_order_status_update(
        bot: Bot, 
        customer_id: str, 
        order_id: str, 
        old_status: str, 
        new_status: str, 
        additional_info: Optional[str] = None
    ) -> bool:
        """
        Отправляет уведомление клиенту об изменении статуса заказа
        
        Args:
            bot: Экземпляр Telegram бота
            customer_id: ID клиента в Telegram
            order_id: ID заказа
            old_status: Предыдущий статус заказа
            new_status: Новый статус заказа
            additional_info: Дополнительная информация (необязательно)
        
        Returns:
            bool: Успешность отправки уведомления
        """
        try:
            message = (
                f"📦 <b>Обновление заказа #{order_id}</b>\n\n"
                f"📊 <b>Статус изменен:</b> {old_status} → {new_status}\n"
            )
            
            if additional_info:
                message += f"📝 <b>Комментарий:</b> {additional_info}\n"
            
            message += f"\nПо всем вопросам обращайтесь к <a href='tg://user?id={settings.owner_id}'>администратору</a>"
            
            await bot.send_message(
                chat_id=int(customer_id), 
                text=message
            )
            
            # Логируем действие
            await log_action(
                "system", 
                "order_status_notification", 
                {
                    "customer_id": customer_id, 
                    "order_id": order_id, 
                    "old_status": old_status, 
                    "new_status": new_status
                }
            )
            
            return True
        
        except TelegramBadRequest as e:
            log.warning(f"Cannot send notification to {customer_id}: {e}")
            return False
        except Exception as e:
            log.error(f"Error sending order status notification: {e}")
            return False
    
    @staticmethod
    async def send_order_payment_notification(
        bot: Bot, 
        customer_id: str, 
        order_id: str, 
        final_price: float,
        payment_url: Optional[str] = None
    ) -> bool:
        """
        Отправляет уведомление клиенту о необходимости оплаты
        
        Args:
            bot: Экземпляр Telegram бота
            customer_id: ID клиента в Telegram
            order_id: ID заказа
            final_price: Итоговая стоимость заказа
            payment_url: Ссылка на оплату (необязательно)
        
        Returns:
            bool: Успешность отправки уведомления
        """
        try:
            message = (
                f"💳 <b>Заказ #{order_id} готов к оплате</b>\n\n"
                f"💰 <b>Сумма к оплате:</b> {final_price:.2f} ₽\n"
                f"📊 <b>Статус:</b> Ожидает оплату\n"
            )
            
            if payment_url:
                message += f"\n🔗 <b>Ссылка для оплаты:</b> {payment_url}"
            
            message += f"\n\nПо всем вопросам обращайтесь к <a href='tg://user?id={settings.owner_id}'>администратору</a>"
            
            await bot.send_message(
                chat_id=int(customer_id), 
                text=message
            )
            
            # Логируем действие
            await log_action(
                "system", 
                "order_payment_notification", 
                {
                    "customer_id": customer_id, 
                    "order_id": order_id, 
                    "amount": final_price
                }
            )
            
            return True
        
        except TelegramBadRequest as e:
            log.warning(f"Cannot send payment notification to {customer_id}: {e}")
            return False
        except Exception as e:
            log.error(f"Error sending order payment notification: {e}")
            return False
    
    @staticmethod
    async def send_admin_notification(
        bot: Bot, 
        message: str, 
        admin_ids: Optional[list] = None
    ) -> bool:
        """
        Отправляет уведомление администраторам
        
        Args:
            bot: Экземпляр Telegram бота
            message: Текст уведомления
            admin_ids: Список ID администраторов (если не указан, используется владелец)
        
        Returns:
            bool: Успешность отправки уведомления
        """
        try:
            if not admin_ids:
                admin_ids = [settings.owner_id]
            
            for admin_id in admin_ids:
                try:
                    await bot.send_message(
                        chat_id=int(admin_id), 
                        text=f"🚨 <b>Административное уведомление</b>\n\n{message}"
                    )
                except TelegramBadRequest as e:
                    log.warning(f"Cannot send admin notification to {admin_id}: {e}")
            
            # Логируем действие
            await log_action(
                "system", 
                "admin_notification", 
                {"message": message, "recipients": admin_ids}
            )
            
            return True
        
        except Exception as e:
            log.error(f"Error sending admin notification: {e}")
            return False
