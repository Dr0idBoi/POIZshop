# app/services/payments.py
import logging
import uuid
from typing import Dict, Any

log = logging.getLogger("payments")

class MockPayment:
    def __init__(self, order_id: str, amount: float):
        self.id = str(uuid.uuid4())
        self.order_id = order_id
        self.amount = amount
        self.status = "pending"
        self.provider = "mock"
        self.url = f"https://example.test/pay?pid={self.id}"

    def succeed(self):
        self.status = "succeeded"

# В памяти (для тестов). В проде пишем в БД.
_PAYMENTS: Dict[str, MockPayment] = {}

def create_mock_payment(order_id: str, amount: float) -> MockPayment:
    p = MockPayment(order_id, amount)
    _PAYMENTS[p.id] = p
    log.info("Mock payment created %s for order %s", p.id, order_id)
    return p

def get_mock_payment(pid: str) -> MockPayment | None:
    return _PAYMENTS.get(pid)

def succeed_payment(pid: str) -> bool:
    p = _PAYMENTS.get(pid)
    if not p:
        return False
    p.succeed()
    log.info("Mock payment %s succeeded", pid)
    return True
