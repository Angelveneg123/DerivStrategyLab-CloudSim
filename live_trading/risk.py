"""Planificación monetaria de contratos Multiplier sin exceder la asignación."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from strategy.risk_manager import calculate_stop_take


@dataclass(frozen=True)
class OrderPlan:
    direction: str
    entry_price: float
    atr: float
    stop_price: float
    take_price: float
    multiplier: int
    stake: float
    stop_loss_amount: float
    take_profit_amount: float
    target_risk_amount: float
    actual_risk_pct: float

    def to_dict(self):
        return asdict(self)


def _floor_cents(value):
    return math.floor(max(0.0, float(value)) * 100 + 1e-9) / 100


def build_order_plans(
    *,
    direction,
    entry_price,
    atr,
    virtual_balance,
    allocation_capital,
    reinvest_profits,
    risk_pct,
    multipliers,
    min_stake,
    max_stake,
    max_stake_pct,
    stop_atr_mult,
    reward_ratio,
):
    """Devuelve planes viables, ordenados por preferencia de multiplicador.

    La pérdida monetaria en el nivel ATR se mantiene por debajo del riesgo
    objetivo. Si el stake mínimo de Deriv obligaría a arriesgar de más, ese
    multiplicador se descarta en vez de forzar una orden.
    """
    entry_price = float(entry_price)
    atr = float(atr)
    virtual_balance = float(virtual_balance)
    allocation_capital = float(allocation_capital)
    if entry_price <= 0 or atr <= 0 or virtual_balance <= 0:
        return []

    risk_base = virtual_balance if reinvest_profits else min(virtual_balance, allocation_capital)
    if risk_base <= 0:
        return []
    target_risk = risk_base * (float(risk_pct) / 100.0)
    stake_cap = min(
        float(max_stake),
        risk_base * (float(max_stake_pct) / 100.0),
        virtual_balance,
    )
    if target_risk <= 0 or stake_cap < float(min_stake):
        return []

    stop_price, take_price = calculate_stop_take(
        entry_price,
        atr,
        direction=direction,
        stop_atr_mult=stop_atr_mult,
        reward_ratio=reward_ratio,
    )
    pct_stop = abs(entry_price - stop_price) / entry_price
    pct_take = abs(take_price - entry_price) / entry_price
    if pct_stop <= 0 or pct_take <= 0:
        return []

    plans = []
    for multiplier in multipliers:
        multiplier = int(multiplier)
        if multiplier <= 0:
            continue

        # Si el movimiento al stop equivale a casi todo el stake, el contrato
        # podría tocar el stop-out del broker antes del nivel ATR deseado.
        loss_fraction_of_stake = multiplier * pct_stop
        if loss_fraction_of_stake >= 0.95:
            continue

        theoretical_stake = target_risk / loss_fraction_of_stake
        stake = min(theoretical_stake, stake_cap)
        stake = _floor_cents(stake)

        if stake < float(min_stake):
            risk_at_minimum = float(min_stake) * loss_fraction_of_stake
            if risk_at_minimum > target_risk + 1e-9:
                continue
            stake = round(float(min_stake), 2)

        stop_loss_amount = _floor_cents(stake * multiplier * pct_stop)
        take_profit_amount = _floor_cents(stake * multiplier * pct_take)
        if stop_loss_amount < 0.01 or take_profit_amount < 0.01:
            continue
        if stop_loss_amount > target_risk + 0.01:
            continue
        if stop_loss_amount >= stake * 0.95:
            continue

        plans.append(
            OrderPlan(
                direction=direction,
                entry_price=entry_price,
                atr=atr,
                stop_price=stop_price,
                take_price=take_price,
                multiplier=multiplier,
                stake=stake,
                stop_loss_amount=stop_loss_amount,
                take_profit_amount=take_profit_amount,
                target_risk_amount=target_risk,
                actual_risk_pct=(stop_loss_amount / risk_base * 100),
            )
        )
    return plans
