from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PriceAnalysis:
    profit: float
    discount_percent: float


def analyze_prices(normal_price: float, warehouse_price: float) -> PriceAnalysis:
    if normal_price <= 0:
        return PriceAnalysis(profit=0.0, discount_percent=0.0)

    profit = normal_price - warehouse_price
    discount = (profit / normal_price) * 100
    return PriceAnalysis(profit=profit, discount_percent=discount)


def is_profitable(
    *,
    warehouse_condition: str | None,
    analysis: PriceAnalysis,
    min_profit: float,
    min_discount: float,
    require_condition_match: bool,
    allowed_conditions: tuple[str, ...],
) -> bool:
    if analysis.profit < min_profit:
        return False
    if analysis.discount_percent < min_discount:
        return False

    if not require_condition_match:
        return True

    if not warehouse_condition:
        return False

    normalized = warehouse_condition.strip().lower()
    return any(condition in normalized for condition in allowed_conditions)
