# app/services/pricing.py
from typing import Optional

def calc_final_price(
    poizon_price_cny: float,
    cny_rate: float,
    cost_var: float,
    cost_fixed: float,
) -> tuple[float, float, float]:
    """
    Base = price*CNY + var + fixed
    margin = 10% * Base
    final = Base + margin
    """
    base = poizon_price_cny * cny_rate + cost_var + cost_fixed
    margin = round(base * 0.10, 2)
    final = round(base + margin, 2)
    return round(base, 2), margin, final
