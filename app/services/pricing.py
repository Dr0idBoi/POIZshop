# app/services/pricing.py
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Tuple, Dict, Optional
from dataclasses import dataclass

from ..db import get_db
from ..crm.sheets import get_financial_settings as sheets_get_financial_settings, read_fin_settings
from ..config import settings

log = logging.getLogger("pricing")

@dataclass
class PricingResult:
    """Result of price calculation"""
    base_price: Decimal  # Base price in RUB
    margin: Decimal      # Margin amount
    final_price: Decimal # Final price with margin
    components: Dict[str, Decimal]  # Breakdown of price components

def _to_decimal(value: float, places: int = 2) -> Decimal:
    """Convert float to Decimal with rounding"""
    return Decimal(str(value)).quantize(
        Decimal(f'0.{"0" * places}'),
        rounding=ROUND_HALF_UP
    )

def calc_total_rub(price_cny: int, rate: Decimal, indiv: int, fixed: int, margin_pct: int) -> int:
    """
    Рассчитывает итоговую цену в рублях по формуле
    
    Args:
        price_cny: Цена в CNY (целое)
        rate: Курс CNY→RUB (Decimal)
        indiv: Индивидуальные траты (целое)
        fixed: Постоянные траты (целое)
        margin_pct: Процент маржи (целое 1-100)
        
    Returns:
        Итоговая цена в рублях (целое)
    """
    # Преобразуем входные данные в Decimal для точных вычислений
    price_cny_dec = Decimal(str(price_cny))
    indiv_dec = Decimal(str(indiv))
    fixed_dec = Decimal(str(fixed))
    margin_pct_dec = Decimal(str(margin_pct))
    
    # Базовая цена: цена_cny * курс + инд_траты + пост_траты
    base_price = price_cny_dec * rate + indiv_dec + fixed_dec
    
    # Маржа: базовая_цена * (маржа_процент / 100)
    margin = base_price * (margin_pct_dec / Decimal('100'))
    
    # Итоговая цена: базовая_цена + маржа
    total = base_price + margin
    
    # Округляем до целого числа
    return int(total.quantize(Decimal('1'), rounding=ROUND_HALF_UP))

def get_financial_settings() -> Dict[str, Optional[float]]:
    """
    Получает финансовые настройки (постоянные траты, процент маржи) из Google Sheets.
    Использует функцию read_fin_settings для чтения из ячеек L5 и L6.
    """
    try:
        margin_pct, fixed_costs = read_fin_settings()
        
        return {
            "fixed_costs": float(fixed_costs),
            "margin_percent": float(margin_pct)
        }
    except Exception as e:
        log.error(f"Ошибка чтения финансовых настроек из Sheets: {e}")
        return {"fixed_costs": 0.0, "margin_percent": 0.0}

async def calc_final_price(
    poizon_price: float,
    cny_rate: float,
    var_costs: float = 0.0,
    fixed_costs_from_sheet: Optional[float] = None,
    margin_percent_from_sheet: Optional[float] = None
) -> PricingResult:
    """
    Calculate final price with margin
    
    Args:
        poizon_price: Price on POIZON in CNY
        cny_rate: CNY to RUB exchange rate
        var_costs: Variable costs in RUB
        fixed_costs_from_sheet: Total fixed costs from financial settings sheet
        margin_percent_from_sheet: Margin percentage from financial settings sheet
        
    Returns:
        PricingResult with calculated prices and components
    """
    try:
        # Получаем финансовые настройки из Sheets, если не предоставлены
        if fixed_costs_from_sheet is None or margin_percent_from_sheet is None:
            margin_pct, fixed_costs = read_fin_settings()
            
            if fixed_costs_from_sheet is None:
                fixed_costs_from_sheet = fixed_costs
            if margin_percent_from_sheet is None:
                margin_percent_from_sheet = margin_pct

        # Convert inputs to Decimal for precise calculation
        poizon_cny = _to_decimal(poizon_price)
        rate = _to_decimal(cny_rate)
        var_costs_rub = _to_decimal(var_costs)
        fixed_costs_rub = _to_decimal(fixed_costs_from_sheet)
        margin_pct = _to_decimal(margin_percent_from_sheet)
        
        # Используем функцию calc_total_rub для расчета итоговой цены
        final_price_int = calc_total_rub(
            price_cny=int(poizon_price),
            rate=rate,
            indiv=int(var_costs),
            fixed=int(fixed_costs_from_sheet),
            margin_pct=int(margin_percent_from_sheet)
        )
        
        # Рассчитываем промежуточные значения для компонентов
        poizon_rub = poizon_cny * rate
        base_price = poizon_rub + var_costs_rub + fixed_costs_rub
        margin = (base_price * margin_pct / Decimal('100.0')).quantize(
            Decimal('0.01'),
            rounding=ROUND_HALF_UP
        )
        
        # Преобразуем итоговую цену в Decimal
        final_price = Decimal(str(final_price_int))
        
        # Prepare components breakdown
        components = {
            'POIZON (CNY)': poizon_cny,
            'Курс CNY': rate,
            'POIZON (RUB)': poizon_rub,
            'Переменные издержки': var_costs_rub,
            'Постоянные издержки': fixed_costs_rub,
            'Базовая цена': base_price,
            'Маржа (%)': margin_pct,
            'Маржа (RUB)': margin,
            'Итоговая цена': final_price
        }
        
        return PricingResult(
            base_price=base_price,
            margin=margin,
            final_price=final_price,
            components=components
        )
        
    except Exception as e:
        log.error(f"Error calculating price: {e}")
        # Return safe defaults
        return PricingResult(
            base_price=Decimal('0.00'),
            margin=Decimal('0.00'),
            final_price=Decimal('0.00'),
            components={}
        )

async def calc_order_price(
    order_id: str,
    cny_rate: Optional[float] = None
) -> Optional[PricingResult]:
    """
    Calculate price for specific order
    
    Args:
        order_id: Order ID
        cny_rate: Optional CNY rate (fetched from DB if not provided)
    
    Returns:
        PricingResult or None if calculation failed
    """
    try:
        async with get_db() as db:
            # Get order details
            cur = await db.execute(
                "SELECT poizon_price, order_cost_var FROM crm_orders WHERE id=?",
                (order_id,)
            )
            order = await cur.fetchone()
            if not order:
                log.error(f"Order {order_id} not found")
                return None
                
            # Get CNY rate if not provided
            if cny_rate is None:
                cur = await db.execute(
                    "SELECT rate FROM exchange_rates "
                    "WHERE base='CNY' AND quote='RUB' "
                    "ORDER BY as_of DESC LIMIT 1"
                )
                rate_row = await cur.fetchone()
                if not rate_row:
                    log.error("No CNY rate found in database")
                    return None
                cny_rate = float(rate_row['rate'])
            
            # Получаем финансовые настройки из Sheets
            financial_settings = get_financial_settings()
            fixed_costs = financial_settings.get("fixed_costs", 0.0)
            margin_percent = financial_settings.get("margin_percent", 0.0)

            # Calculate price
            result = await calc_final_price(
                poizon_price=float(order['poizon_price'] or 0),
                cny_rate=cny_rate,
                var_costs=float(order['order_cost_var'] or 0),
                fixed_costs_from_sheet=fixed_costs,
                margin_percent_from_sheet=margin_percent
            )
            
            # Update order with calculated prices
            await db.execute(
                "UPDATE crm_orders SET "
                "margin=?, final_price=?, order_cost_fixed=?, updated_at=datetime('now') "
                "WHERE id=?",
                (float(result.margin), float(result.final_price), float(result.components['Постоянные издержки']), order_id)
            )
            await db.commit()
            
            return result
            
    except Exception as e:
        log.error(f"Error calculating price for order {order_id}: {e}")
        return None

def format_price_breakdown(result: PricingResult) -> str:
    """
    Format price calculation breakdown as human-readable text
    
    Args:
        result: PricingResult to format
        
    Returns:
        Formatted string with price breakdown
    """
    try:
        lines = ["💰 <b>Расчет цены:</b>\n"]
        
        # POIZON price
        lines.append(
            f"🏷️ Цена на POIZON: {result.components['POIZON (CNY)']:.2f} CNY "
            f"× {result.components['Курс CNY']:.2f} = "
            f"{result.components['POIZON (RUB)']:.2f} ₽"
        )
        
        # Costs
        if result.components['Переменные издержки']:
            lines.append(f"📊 Переменные издержки: {result.components['Переменные издержки']:.2f} ₽")
        # Постоянные издержки теперь всегда будут из Sheets, поэтому убираем проверку на 0
        lines.append(f"📈 Постоянные издержки: {result.components['Постоянные издержки']:.2f} ₽")
            
        # Base price
        lines.append(f"📌 Базовая цена: {result.components['Базовая цена']:.2f} ₽")
        
        # Margin
        lines.append(
            f"📊 Маржа {result.components['Маржа (%)']:.1f}%: "
            f"{result.components['Маржа (RUB)']:.2f} ₽"
        )
        
        # Final price
        lines.append(f"\n💵 <b>Итоговая цена: {result.components['Итоговая цена']:.2f} ₽</b>")
        
        return "\n".join(lines)
        
    except Exception as e:
        log.error(f"Error formatting price breakdown: {e}")
        return "❌ Ошибка форматирования расчета"
