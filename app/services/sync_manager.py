# app/services/sync_manager.py
"""
Менеджер полной синхронизации всех компонентов системы
"""
import logging
from typing import Dict, Any
from datetime import datetime
from aiogram import Bot

log = logging.getLogger("sync_manager")


class FullSyncManager:
    """
    Менеджер для выполнения полной синхронизации всех компонентов
    
    Выполняет:
    1. Pull из Google Sheets → SQLite
    2. Push из SQLite → Google Sheets
    3. Проверка статусов платежей YooKassa
    4. Обновление курсов валют
    5. Обновление списков заказов клиентов
    """
    
    def __init__(self):
        self.results: Dict[str, Any] = {}
        self.start_time: datetime = None
        self.end_time: datetime = None
    
    async def run_full_sync(self, bot: Bot = None) -> Dict[str, Any]:
        """
        Выполнить полную синхронизацию всех компонентов
        
        Args:
            bot: Экземпляр бота для уведомлений
        
        Returns:
            Словарь с результатами синхронизации
        """
        self.start_time = datetime.now()
        self.results = {
            "started_at": self.start_time.isoformat(),
            "steps": {},
            "success": True,
            "errors": []
        }
        
        log.info("=" * 60)
        log.info("Starting FULL SYNC of all components")
        log.info("=" * 60)
        
        # Шаг 1: Pull из Sheets → SQLite
        await self._sync_sheets_to_db(bot)
        
        # Шаг 2: Push из SQLite → Sheets
        await self._sync_db_to_sheets()
        
        # Шаг 3: Проверка платежей YooKassa
        await self._check_payments()
        
        # Шаг 4: Обновление курсов валют
        await self._update_exchange_rates()
        
        # Шаг 5: Обновление списков заказов клиентов
        await self._update_customer_order_lists()
        
        self.end_time = datetime.now()
        duration = (self.end_time - self.start_time).total_seconds()
        
        self.results["completed_at"] = self.end_time.isoformat()
        self.results["duration_seconds"] = duration
        
        log.info("=" * 60)
        log.info(f"FULL SYNC completed in {duration:.2f}s")
        log.info(f"Success: {self.results['success']}")
        log.info(f"Errors: {len(self.results['errors'])}")
        log.info("=" * 60)
        
        return self.results
    
    async def _sync_sheets_to_db(self, bot: Bot = None):
        """Шаг 1: Синхронизация Sheets → SQLite"""
        step_name = "sheets_to_db"
        log.info("Step 1/5: Syncing Sheets → SQLite...")
        
        try:
            from ..crm.sheets import sync_sheets_to_db
            
            await sync_sheets_to_db(bot)
            
            self.results["steps"][step_name] = {
                "status": "success",
                "message": "Sheets → SQLite sync completed"
            }
            log.info("✅ Step 1/5: Sheets → SQLite completed")
            
        except Exception as e:
            log.error(f"❌ Step 1/5 failed: {e}", exc_info=True)
            self.results["success"] = False
            self.results["errors"].append(f"sheets_to_db: {str(e)}")
            self.results["steps"][step_name] = {
                "status": "error",
                "message": str(e)
            }
    
    async def _sync_db_to_sheets(self):
        """Шаг 2: Синхронизация SQLite → Sheets"""
        step_name = "db_to_sheets"
        log.info("Step 2/5: Syncing SQLite → Sheets...")
        
        try:
            from ..crm.sheets import sync_db_to_sheets
            
            await sync_db_to_sheets()
            
            self.results["steps"][step_name] = {
                "status": "success",
                "message": "SQLite → Sheets sync completed"
            }
            log.info("✅ Step 2/5: SQLite → Sheets completed")
            
        except Exception as e:
            log.error(f"❌ Step 2/5 failed: {e}", exc_info=True)
            self.results["success"] = False
            self.results["errors"].append(f"db_to_sheets: {str(e)}")
            self.results["steps"][step_name] = {
                "status": "error",
                "message": str(e)
            }
    
    async def _check_payments(self):
        """Шаг 3: Проверка статусов платежей YooKassa"""
        step_name = "check_payments"
        log.info("Step 3/5: Checking YooKassa payment statuses...")
        
        try:
            from ..services.payments import check_all_pending_payments
            
            updated_count = await check_all_pending_payments()
            
            self.results["steps"][step_name] = {
                "status": "success",
                "message": f"Checked payments, {updated_count} updated",
                "updated_count": updated_count
            }
            log.info(f"✅ Step 3/5: Payment check completed ({updated_count} updated)")
            
        except Exception as e:
            log.error(f"❌ Step 3/5 failed: {e}", exc_info=True)
            self.results["success"] = False
            self.results["errors"].append(f"check_payments: {str(e)}")
            self.results["steps"][step_name] = {
                "status": "error",
                "message": str(e)
            }
    
    async def _update_exchange_rates(self):
        """Шаг 4: Обновление курсов валют"""
        step_name = "exchange_rates"
        log.info("Step 4/5: Updating exchange rates...")
        
        try:
            from ..services.exchange import fetch_cbr_rate
            from ..db import get_db
            
            rate = await fetch_cbr_rate("CNY")
            
            if rate:
                # Курс уже содержит +1 рубль (добавлено в fetch_cbr_rate)
                async with get_db() as db:
                    await db.execute(
                        "INSERT INTO exchange_rates(base,quote,rate,as_of) "
                        "VALUES('CNY','RUB',?, datetime('now'))",
                        (rate,)
                    )
                    await db.commit()
                
                self.results["steps"][step_name] = {
                    "status": "success",
                    "message": f"Exchange rate updated: CNY = {rate:.4f} RUB (includes +1.0 adjustment)",
                    "rate": rate
                }
                log.info(f"✅ Step 4/5: Exchange rate updated (CNY = {rate:.4f} RUB, includes +1.0 adjustment)")
            else:
                self.results["steps"][step_name] = {
                    "status": "warning",
                    "message": "Failed to fetch exchange rate from CBR"
                }
                log.warning("⚠️ Step 4/5: Failed to fetch exchange rate")
                
        except Exception as e:
            log.error(f"❌ Step 4/5 failed: {e}", exc_info=True)
            self.results["success"] = False
            self.results["errors"].append(f"exchange_rates: {str(e)}")
            self.results["steps"][step_name] = {
                "status": "error",
                "message": str(e)
            }
    
    async def _update_customer_order_lists(self):
        """Шаг 5: Обновление списков заказов клиентов"""
        step_name = "customer_order_lists"
        log.info("Step 5/5: Updating customer order lists...")
        
        try:
            from ..crm.sheets import update_customer_order_lists
            
            await update_customer_order_lists()
            
            self.results["steps"][step_name] = {
                "status": "success",
                "message": "Customer order lists updated"
            }
            log.info("✅ Step 5/5: Customer order lists updated")
            
        except Exception as e:
            log.error(f"❌ Step 5/5 failed: {e}", exc_info=True)
            self.results["success"] = False
            self.results["errors"].append(f"customer_order_lists: {str(e)}")
            self.results["steps"][step_name] = {
                "status": "error",
                "message": str(e)
            }
    
    def get_summary_message(self) -> str:
        """
        Получить краткое сообщение о результатах синхронизации
        
        Returns:
            Форматированное сообщение для отправки пользователю
        """
        if not self.results:
            return "❌ Синхронизация не выполнялась"
        
        duration = self.results.get("duration_seconds", 0)
        success = self.results.get("success", False)
        steps = self.results.get("steps", {})
        errors = self.results.get("errors", [])
        
        # Заголовок
        if success:
            header = "✅ <b>Полная синхронизация завершена успешно</b>"
        else:
            header = "⚠️ <b>Синхронизация завершена с ошибками</b>"
        
        # Время выполнения
        time_info = f"⏱ <b>Время выполнения:</b> {duration:.2f}с"
        
        # Результаты по шагам
        step_results = []
        step_names = {
            "sheets_to_db": "1️⃣ Sheets → SQLite",
            "db_to_sheets": "2️⃣ SQLite → Sheets",
            "check_payments": "3️⃣ Проверка платежей",
            "exchange_rates": "4️⃣ Курсы валют",
            "customer_order_lists": "5️⃣ Списки заказов"
        }
        
        for step_key, step_title in step_names.items():
            step_data = steps.get(step_key, {})
            status = step_data.get("status", "unknown")
            message = step_data.get("message", "")
            
            if status == "success":
                emoji = "✅"
            elif status == "warning":
                emoji = "⚠️"
            elif status == "error":
                emoji = "❌"
            else:
                emoji = "❓"
            
            step_results.append(f"{emoji} {step_title}")
            
            # Дополнительная информация
            if step_key == "check_payments" and "updated_count" in step_data:
                step_results.append(f"   └ Обновлено платежей: {step_data['updated_count']}")
            elif step_key == "exchange_rates" and "rate" in step_data:
                step_results.append(f"   └ CNY = {step_data['rate']:.4f} RUB")
        
        steps_text = "\n".join(step_results)
        
        # Ошибки
        errors_text = ""
        if errors:
            errors_text = "\n\n<b>❌ Ошибки:</b>\n" + "\n".join(f"  • {e}" for e in errors[:3])
            if len(errors) > 3:
                errors_text += f"\n  • ... и еще {len(errors) - 3} ошибок"
        
        return f"{header}\n\n{time_info}\n\n<b>Результаты:</b>\n{steps_text}{errors_text}"


# Глобальный экземпляр менеджера
_sync_manager = FullSyncManager()


async def run_full_sync(bot: Bot = None) -> Dict[str, Any]:
    """
    Запустить полную синхронизацию всех компонентов
    
    Args:
        bot: Экземпляр бота для уведомлений
    
    Returns:
        Словарь с результатами синхронизации
    """
    return await _sync_manager.run_full_sync(bot)


def get_last_sync_summary() -> str:
    """Получить сообщение о последней синхронизации"""
    return _sync_manager.get_summary_message()
