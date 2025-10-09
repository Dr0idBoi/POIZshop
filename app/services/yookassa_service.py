# app/services/yookassa_service.py
import logging
import uuid
import asyncio
import aiohttp
import base64
from typing import Optional, Dict, Any
from datetime import datetime

from ..config import settings

log = logging.getLogger("yookassa")

class YooKassaService:
    """Сервис для работы с YooKassa API через HTTP"""
    
    def __init__(self):
        """Инициализация сервиса"""
        self._mock_payments_db = {}  # In-memory mock DB
        
        if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
            log.warning("YooKassa credentials not configured. Using mock mode.")
            self._mock_mode = True
        else:
            self._mock_mode = False
            self.shop_id = settings.yookassa_shop_id
            self.secret_key = settings.yookassa_secret_key.get_secret_value()
            self.base_url = "https://api.yookassa.ru/v3"
            self.auth_header = self._create_auth_header()
    
    def _create_auth_header(self) -> str:
        """Создание заголовка авторизации для Basic Auth"""
        credentials = f"{self.shop_id}:{self.secret_key}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        return f"Basic {encoded_credentials}"
    
    async def create_payment(
        self, 
        order_id: str, 
        amount: float, 
        description: str = "Оплата заказа",
        return_url: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Создание платежа в YooKassa через HTTP API
        
        Args:
            order_id: ID заказа
            amount: Сумма в рублях
            description: Описание платежа
            return_url: URL для возврата после оплаты
            
        Returns:
            Словарь с данными платежа или None при ошибке
        """
        try:
            if self._mock_mode:
                return self._create_mock_payment(order_id, amount, description)
            
            # Подготавливаем данные для API
            payment_data = {
                "amount": {
                    "value": f"{amount:.2f}",
                    "currency": "RUB"
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": return_url or settings.yookassa_return_url or "https://t.me/ZakazPOIZBot"
                },
                "capture": True,
                "description": description,
                "metadata": {
                    "order_id": order_id,
                    "created_at": datetime.now().isoformat()
                }
            }
            
            # Отправляем HTTP запрос к YooKassa API
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/payments",
                    json=payment_data,
                    headers={
                        "Authorization": self.auth_header,
                        "Content-Type": "application/json",
                        "Idempotence-Key": str(uuid.uuid4())
                    }
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        
                        log.info(f"Created YooKassa payment {result['id']} for order {order_id}, amount: {amount} RUB")
                        
                        return {
                            "id": result["id"],
                            "status": result["status"],
                            "url": result["confirmation"]["confirmation_url"],
                            "amount": amount,
                            "currency": "RUB",
                            "description": description,
                            "metadata": result.get("metadata", {}),
                            "created_at": result["created_at"]
                        }
                    else:
                        error_text = await response.text()
                        log.error(f"YooKassa API error {response.status}: {error_text}")
                        return None
            
        except Exception as e:
            log.error(f"Error creating YooKassa payment for order {order_id}: {e}")
            # В случае ошибки возвращаем mock платеж
            return self._create_mock_payment(order_id, amount, description)
    
    async def get_payment_status(self, payment_id: str) -> Optional[Dict[str, Any]]:
        """
        Получение статуса платежа через HTTP API
        
        Args:
            payment_id: ID платежа
            
        Returns:
            Словарь с данными платежа или None
        """
        try:
            if self._mock_mode:
                return self._get_mock_payment_status(payment_id)
            
            # Отправляем HTTP запрос к YooKassa API
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.get(
                    f"{self.base_url}/payments/{payment_id}",
                    headers={
                        "Authorization": self.auth_header,
                        "Content-Type": "application/json"
                    }
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        
                        return {
                            "id": result["id"],
                            "status": result["status"],
                            "amount": float(result["amount"]["value"]),
                            "currency": result["amount"]["currency"],
                            "description": result.get("description", ""),
                            "metadata": result.get("metadata", {}),
                            "created_at": result["created_at"],
                            "paid": result.get("paid", False)
                        }
                    elif response.status == 404:
                        log.warning(f"Payment {payment_id} not found in YooKassa")
                        return None
                    else:
                        error_text = await response.text()
                        log.error(f"YooKassa API error {response.status}: {error_text}")
                        return None
            
        except asyncio.TimeoutError:
            log.error(f"Timeout getting YooKassa payment status {payment_id}")
            return None
        except Exception as e:
            log.error(f"Error getting YooKassa payment status {payment_id}: {e}")
            return None
    
    async def check_payment_status(self, payment_id: str) -> Optional[Dict[str, Any]]:
        """
        Проверка статуса платежа (замена webhook)
        
        Args:
            payment_id: ID платежа
            
        Returns:
            Данные платежа или None
        """
        try:
            payment_data = await self.get_payment_status(payment_id)
            
            if not payment_data:
                return None
            
            # Проверяем статус платежа
            if payment_data.get("status") == "succeeded":
                return {
                    "id": payment_data["id"],
                    "status": "succeeded",
                    "amount": payment_data["amount"],
                    "currency": payment_data["currency"],
                    "order_id": payment_data.get("metadata", {}).get("order_id"),
                    "paid": True,
                    "created_at": payment_data["created_at"],
                    "metadata": payment_data.get("metadata", {})
                }
            elif payment_data.get("status") == "canceled":
                return {
                    "id": payment_data["id"],
                    "status": "canceled",
                    "amount": payment_data["amount"],
                    "currency": payment_data["currency"],
                    "order_id": payment_data.get("metadata", {}).get("order_id"),
                    "paid": False,
                    "created_at": payment_data["created_at"],
                    "metadata": payment_data.get("metadata", {})
                }
            
            return None
            
        except Exception as e:
            log.error(f"Error checking YooKassa payment status: {e}")
            return None
    
    def _create_mock_payment(self, order_id: str, amount: float, description: str) -> Dict[str, Any]:
        """Создание mock платежа для тестирования"""
        payment_id = f"mock_{uuid.uuid4().hex[:16]}"
        mock_payment = {
            "id": payment_id,
            "status": "pending",
            "url": f"https://mock-payment.example.com/pay/{payment_id}?amount={amount}",
            "amount": amount,
            "currency": "RUB",
            "description": description,
            "metadata": {"order_id": order_id},
            "created_at": datetime.now().isoformat(),
            "paid": False
        }
        self._mock_payments_db[payment_id] = mock_payment
        
        log.info(f"Created mock payment {payment_id} for order {order_id}, amount: {amount} RUB")
        
        return mock_payment
    
    def _get_mock_payment_status(self, payment_id: str) -> Optional[Dict[str, Any]]:
        """Получение статуса mock платежа"""
        if not payment_id.startswith("mock_"):
            return None
            
        # Проверяем mock базу данных
        payment = self._mock_payments_db.get(payment_id)
        if payment:
            # Simulate payment success after some time for mock
            from datetime import timedelta
            if (datetime.now() - datetime.fromisoformat(payment["created_at"])) > timedelta(seconds=30) and payment["status"] == "pending":
                payment["status"] = "succeeded"
                payment["paid"] = True
                log.info(f"Mock payment {payment_id} for order {payment['metadata']['order_id']} automatically succeeded.")
            return payment
            
        # Для новых mock платежей всегда возвращаем "pending"
        return {
            "id": payment_id,
            "status": "pending",
            "amount": 0.0,
            "currency": "RUB",
            "description": "Mock payment",
            "metadata": {},
            "created_at": datetime.now().isoformat(),
            "paid": False
        }
    

# Глобальный экземпляр сервиса
yookassa_service = YooKassaService()
