# app/services/payments.py
import logging
import uuid
from typing import Optional, Dict, Any, NamedTuple
from datetime import datetime, timedelta
import json
import hmac
import hashlib
from dataclasses import dataclass

from ..db import get_db
from ..config import settings
from .yookassa_service import yookassa_service

log = logging.getLogger("payments")

class MockPayment(NamedTuple):
    """Mock payment details"""
    id: str
    url: str

@dataclass
class PaymentInfo:
    """Payment information"""
    id: str
    order_id: str
    amount: float
    status: str
    provider: str
    created_at: datetime
    updated_at: datetime
    metadata: Dict[str, Any]

def _generate_payment_id() -> str:
    """Generate unique payment ID"""
    return f"PAY_{uuid.uuid4().hex[:16].upper()}"

def _generate_signature(data: Dict[str, Any], secret: str) -> str:
    """Generate HMAC signature for payment data"""
    sorted_data = dict(sorted(data.items()))
    message = json.dumps(sorted_data, separators=(',', ':'))
    return hmac.new(
        secret.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()

async def create_payment_with_yookassa(order_id: str, amount: float, description: str = "Оплата заказа") -> Optional[Dict[str, Any]]:
    """
    Create real payment with YooKassa
    
    Args:
        order_id: Order ID
        amount: Payment amount in RUB
        description: Payment description
    
    Returns:
        Payment data with ID and URL
    """
    try:
        # Создаем платеж через YooKassa
        payment_data = await yookassa_service.create_payment(
            order_id=order_id,
            amount=amount,
            description=description
        )
        
        if not payment_data:
            log.error(f"Failed to create YooKassa payment for order {order_id}")
            return None
        
        # Сохраняем платеж в БД
        payment_info = await create_payment(
            order_id=order_id,
            amount=amount,
            provider='yookassa',
            metadata={
                'yookassa_payment_id': payment_data['id'],
                'payment_url': payment_data['url'],
                'description': description
            }
        )
        
        if payment_info:
            # Обновляем payment_url в заказе
            await update_order_payment_url(order_id, payment_data['url'])
            
        return {
            'payment_id': payment_data['id'],
            'url': payment_data['url'],
            'amount': amount,
            'status': payment_data['status']
        }
        
    except Exception as e:
        log.error(f"Error creating YooKassa payment for order {order_id}: {e}")
        return None

def create_mock_payment(order_id: str, amount: float) -> MockPayment:
    """
    Create mock payment for testing (deprecated, use create_payment_with_yookassa)
    
    Args:
        order_id: Order ID
        amount: Payment amount
    
    Returns:
        MockPayment with ID and URL
    """
    payment_id = _generate_payment_id()
    return MockPayment(
        id=payment_id,
        url=f"https://example.com/pay/{payment_id}?amount={amount}"
    )

async def get_payment_info(payment_id: str) -> Optional[PaymentInfo]:
    """
    Get payment information
    
    Args:
        payment_id: Payment ID
    
    Returns:
        PaymentInfo if found, None otherwise
    """
    try:
        async with get_db() as db:
            cur = await db.execute(
                "SELECT * FROM payments WHERE id=?",
                (payment_id,)
            )
            row = await cur.fetchone()
            
            if row:
                return PaymentInfo(
                    id=row['id'],
                    order_id=row['order_id'],
                    amount=float(row['amount']),
                    status=row['status'],
                    provider=row['provider'],
                    created_at=datetime.fromisoformat(row['created_at']),
                    updated_at=datetime.fromisoformat(row['updated_at']),
                    metadata=json.loads(row.get('metadata_json', '{}'))
                )
    except Exception as e:
        log.error(f"Error getting payment info for {payment_id}: {e}")
    return None

async def create_payment(
    order_id: str,
    amount: float,
    provider: str = 'mock',
    metadata: Optional[Dict[str, Any]] = None
) -> Optional[PaymentInfo]:
    """
    Create new payment record
    
    Args:
        order_id: Order ID
        amount: Payment amount
        provider: Payment provider
        metadata: Additional payment metadata
    
    Returns:
        PaymentInfo if created, None on error
    """
    try:
        payment_id = _generate_payment_id()
        now = datetime.now()
        
        async with get_db() as db:
            await db.execute(
                "INSERT INTO payments ("
                "id, order_id, provider, amount, status, "
                "metadata_json, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    payment_id,
                    order_id,
                    provider,
                    amount,
                    'pending',
                    json.dumps(metadata or {}),
                    now.isoformat(),
                    now.isoformat()
                )
            )
            await db.commit()
            
            return PaymentInfo(
                id=payment_id,
                order_id=order_id,
                amount=amount,
                status='pending',
                provider=provider,
                created_at=now,
                updated_at=now,
                metadata=metadata or {}
            )
    except Exception as e:
        log.error(f"Error creating payment for order {order_id}: {e}")
        return None

async def update_payment_status(
    payment_id: str,
    new_status: str,
    metadata: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Update payment status
    
    Args:
        payment_id: Payment ID
        new_status: New payment status
        metadata: Additional metadata to update
    
    Returns:
        True if updated successfully
    """
    try:
        async with get_db() as db:
            # Get current payment data
            cur = await db.execute(
                "SELECT metadata_json FROM payments WHERE id=?",
                (payment_id,)
            )
            row = await cur.fetchone()
            if not row:
                return False
                
            # Merge metadata
            current_metadata = json.loads(row['metadata_json'])
            if metadata:
                current_metadata.update(metadata)
            
            # Update payment
            await db.execute(
                "UPDATE payments SET "
                "status=?, metadata_json=?, updated_at=? "
                "WHERE id=?",
                (
                    new_status,
                    json.dumps(current_metadata),
                    datetime.now().isoformat(),
                    payment_id
                )
            )
            await db.commit()
            return True
            
    except Exception as e:
        log.error(f"Error updating payment {payment_id}: {e}")
        return False

def succeed_payment(payment_id: str) -> bool:
    """
    Mark mock payment as successful
    
    Args:
        payment_id: Payment ID
    
    Returns:
        True if payment was found and updated
    """
    try:
        # Validate payment ID format
        if not payment_id.startswith('PAY_'):
            return False
            
        # For mock payments, just return success
        return True
        
    except Exception as e:
        log.error(f"Error processing mock payment {payment_id}: {e}")
        return False

async def update_order_payment_url(order_id: str, payment_url: str) -> bool:
    """
    Update payment URL in order
    
    Args:
        order_id: Order ID
        payment_url: Payment URL
    
    Returns:
        True if updated successfully
    """
    try:
        async with get_db() as db:
            await db.execute(
                "UPDATE crm_orders SET payment_url=? WHERE id=?",
                (payment_url, order_id)
            )
            await db.commit()
            return True
    except Exception as e:
        log.error(f"Error updating payment URL for order {order_id}: {e}")
        return False

async def check_payment_status_via_api(payment_id: str) -> bool:
    """
    Проверка статуса платежа через YooKassa API (замена webhook)
    
    Args:
        payment_id: ID платежа для проверки
    
    Returns:
        True если статус обновлен
    """
    try:
        # Проверяем статус через YooKassa API
        payment_data = await yookassa_service.check_payment_status(payment_id)
        
        if not payment_data:
            return False
        
        order_id = payment_data.get('order_id')
        if not order_id:
            log.warning("No order_id in payment data")
            return False
        
        # Обновляем статус платежа в БД
        if payment_data.get('status') == 'succeeded':
            # Платеж успешен - обновляем статус заказа
            await update_payment_status(
                payment_data['id'], 
                'succeeded',
                {'api_checked': True}
            )
            
            # Обновляем статус заказа на "Оплачен"
            async with get_db() as db:
                await db.execute(
                    """UPDATE crm_orders SET 
                    status=?, payment_status=?, updated_at=datetime('now'), rev=rev+1
                    WHERE id=?""",
                    ('Оплачен', 'paid', order_id)
                )
                await db.commit()
                
                # Обновляем статус и оплату в Google Sheets
                try:
                    from ..crm.sheets import partial_update_by_ext_id
                    from ..constants import ORDER_COLUMNS, SHEET_NAMES, ORDER_STATUSES
                    partial_update_by_ext_id(
                        SHEET_NAMES["ORDERS"],
                        order_id,
                        {
                            "STATUS": ORDER_STATUSES["PAID"],
                            "PAYMENT_STATUS": "paid",
                            "UPDATED_AT": datetime.now().isoformat()
                        }
                    )
                    log.info(f"Updated Sheets: STATUS=Оплачен, PAYMENT_STATUS=paid for order {order_id}")
                except Exception as e:
                    log.error(f"Failed to update payment fields in Sheets: {e}")
                
                # Уведомляем клиента об успешной оплате
                try:
                    from ..bot.main import bot
                    cur = await db.execute(
                        "SELECT customer_id FROM crm_orders WHERE id=?",
                        (order_id,)
                    )
                    order_data = await cur.fetchone()
                    
                    if order_data and order_data["customer_id"]:
                        await bot.send_message(
                            int(order_data["customer_id"]),
                            f"✅ <b>Платеж успешно получен!</b>\n\n"
                            f"Ваш заказ #{order_id} оплачен и принят в обработку.\n\n"
                            f"Спасибо за заказ! 🎉"
                        )
                        log.info(f"Customer {order_data['customer_id']} notified about successful payment for order {order_id}")
                except Exception as e:
                    log.error(f"Failed to notify customer about payment: {e}")
                
                log.info(f"Order {order_id} marked as paid via YooKassa API check")
                return True
            
        elif payment_data.get('status') == 'canceled':
            # Платеж отменен
            await update_payment_status(
                payment_data['id'], 
                'canceled',
                {'api_checked': True}
            )
            
            log.info(f"Payment for order {order_id} was canceled")
            return True
        
        return False
        
    except Exception as e:
        log.error(f"Error checking YooKassa payment status via API: {e}")
        return False

async def check_all_pending_payments() -> int:
    """
    Проверка статуса всех ожидающих платежей через YooKassa API
    
    Returns:
        Количество обновленных платежей
    """
    try:
        # Альтернативный подход: проверяем заказы с ожидающими платежами
        async with get_db() as db:
            cur = await db.execute(
                """SELECT o.id, o.payment_url, p.metadata_json, p.id as payment_id
                FROM crm_orders o
                LEFT JOIN payments p ON o.id = p.order_id
                WHERE o.status = 'Ожидает оплату' 
                AND o.payment_url IS NOT NULL 
                AND o.payment_url != ''
                AND p.status = 'pending'
                AND p.provider = 'yookassa'
                ORDER BY o.updated_at DESC
                LIMIT 20"""
            )
            orders = await cur.fetchall()
        
        log.info(f"Checking {len(orders)} orders with pending YooKassa payments")
        updated_count = 0
        
        for order in orders:
            try:
                # Получаем YooKassa payment ID из metadata
                metadata = json.loads(order['metadata_json'] if order['metadata_json'] else '{}')
                yookassa_payment_id = metadata.get('yookassa_payment_id')
                
                if not yookassa_payment_id:
                    log.warning(f"No yookassa_payment_id for order {order['id']}")
                    continue
                
                log.info(f"Checking payment {yookassa_payment_id} for order {order['id']}")
                
                # Проверяем статус через API
                success = await check_payment_status_via_api(yookassa_payment_id)
                if success:
                    updated_count += 1
                    log.info(f"Payment {yookassa_payment_id} status updated successfully")
                    
            except Exception as e:
                log.error(f"Error checking payment for order {order['id']}: {e}")
                continue
        
        if updated_count > 0:
            log.info(f"Updated {updated_count} payment statuses via YooKassa API")
        
        return updated_count
        
    except Exception as e:
        log.error(f"Error checking all pending payments: {e}")
        return 0

async def get_pending_payments(
    hours: int = 24,
    limit: int = 100
) -> list[PaymentInfo]:
    """
    Get list of pending payments
    
    Args:
        hours: Get payments from last N hours
        limit: Maximum number of payments to return
    
    Returns:
        List of PaymentInfo objects
    """
    try:
        async with get_db() as db:
            cur = await db.execute(
                "SELECT * FROM payments "
                "WHERE status='pending' "
                "AND datetime(created_at) > datetime('now', ? || ' hours') "
                "ORDER BY created_at DESC LIMIT ?",
                (-hours, limit)
            )
            payments = []
            rows = await cur.fetchall()
            for row in rows:
                payments.append(PaymentInfo(
                    id=row['id'],
                    order_id=row['order_id'],
                    amount=float(row['amount']),
                    status=row['status'],
                    provider=row['provider'],
                    created_at=datetime.fromisoformat(row['created_at']),
                    updated_at=datetime.fromisoformat(row['updated_at']),
                    metadata=json.loads(row['metadata_json'] if row['metadata_json'] else '{}')
                ))
            return payments
            
    except Exception as e:
        log.error(f"Error getting pending payments: {e}")
        return []

async def cancel_stale_payments(hours: int = 48) -> int:
    """
    Cancel payments that have been pending for too long
    
    Args:
        hours: Cancel payments older than N hours
        
    Returns:
        Number of payments cancelled
    """
    try:
        async with get_db() as db:
            cur = await db.execute(
                "UPDATE payments SET "
                "status='cancelled', "
                "updated_at=datetime('now'), "
                "metadata_json=json_set(metadata_json, '$.cancel_reason', 'timeout') "
                "WHERE status='pending' "
                "AND datetime(created_at) < datetime('now', ? || ' hours')",
                (-hours,)
            )
            await db.commit()
            return cur.rowcount
            
    except Exception as e:
        log.error(f"Error cancelling stale payments: {e}")
        return 0

async def get_payment_stats(days: int = 30) -> Dict[str, Any]:
    """
    Get payment statistics
    
    Args:
        days: Number of days to analyze
        
    Returns:
        Dictionary with payment statistics
    """
    try:
        async with get_db() as db:
            stats = {}
            
            # Total payments
            cur = await db.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN status='succeeded' THEN amount ELSE 0 END) as volume "
                "FROM payments "
                "WHERE datetime(created_at) > datetime('now', ? || ' days')",
                (-days,)
            )
            row = await cur.fetchone()
            stats['total_count'] = row['total']
            stats['total_volume'] = float(row['volume'] or 0)
            
            # Status breakdown
            cur = await db.execute(
                "SELECT status, COUNT(*) as count "
                "FROM payments "
                "WHERE datetime(created_at) > datetime('now', ? || ' days') "
                "GROUP BY status",
                (-days,)
            )
            stats['by_status'] = {
                row['status']: row['count']
                async for row in cur
            }
            
            # Daily volumes
            cur = await db.execute(
                "SELECT date(created_at) as date, "
                "COUNT(*) as count, "
                "SUM(CASE WHEN status='succeeded' THEN amount ELSE 0 END) as volume "
                "FROM payments "
                "WHERE datetime(created_at) > datetime('now', ? || ' days') "
                "GROUP BY date(created_at) "
                "ORDER BY date",
                (-days,)
            )
            stats['daily'] = {
                row['date']: {
                    'count': row['count'],
                    'volume': float(row['volume'] or 0)
                }
                async for row in cur
            }
            
            return stats
            
    except Exception as e:
        log.error(f"Error getting payment stats: {e}")
        return {}
