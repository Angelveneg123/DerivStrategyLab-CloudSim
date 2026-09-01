"""Plan de tamaño para CFDs de Deriv MT5 Standard.

A diferencia de Multiplier, aquí no existen stake ni x100. La exposición se
expresa en lotes, el broker inmoviliza margen y el riesgo se calcula con la
pérdida estimada entre el precio de entrada y el stop-loss mediante
``order_calc_profit`` del propio terminal MT5.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from strategy.risk_manager import calculate_stop_take


@dataclass(frozen=True)
class CFDOrderPlan:
    direction: str
    signal_price: float
    entry_price: float
    atr: float
    stop_price: float
    take_price: float
    volume_lots: float
    margin_required: float
    target_risk_amount: float
    estimated_loss: float
    estimated_profit: float
    actual_risk_pct: float
    spread_points: float
    point: float

    def to_dict(self):
        return asdict(self)


def _digits_for_step(step: float) -> int:
    text = f"{float(step):.10f}".rstrip("0")
    return len(text.split(".", 1)[1]) if "." in text else 0


def _floor_to_step(value: float, step: float) -> float:
    step = float(step)
    if step <= 0:
        return float(value)
    units = math.floor((float(value) + 1e-12) / step)
    return round(units * step, _digits_for_step(step))


def build_cfd_order_plan(
    *,
    mt5,
    symbol,
    direction,
    signal_price,
    atr,
    virtual_balance,
    allocation_capital,
    reinvest_profits,
    risk_pct,
    stop_atr_mult,
    reward_ratio,
    max_margin,
):
    """Crea un plan CFD validado con especificaciones reales del símbolo.

    Devuelve ``None`` cuando el lote mínimo del broker excede el riesgo o el
    margen permitido. Nunca aumenta el lote para forzar una entrada.
    """
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if info is None or tick is None:
        return None

    point = float(getattr(info, "point", 0) or 0)
    digits = int(getattr(info, "digits", 5) or 5)
    volume_min = float(getattr(info, "volume_min", 0) or 0)
    volume_max = float(getattr(info, "volume_max", 0) or 0)
    volume_step = float(getattr(info, "volume_step", 0) or 0)
    if point <= 0 or volume_min <= 0 or volume_max <= 0 or volume_step <= 0:
        return None

    entry_price = float(tick.ask if direction == "BUY" else tick.bid)
    if entry_price <= 0 or float(atr) <= 0:
        return None

    stop_price, take_price = calculate_stop_take(
        entry_price,
        float(atr),
        direction=direction,
        stop_atr_mult=stop_atr_mult,
        reward_ratio=reward_ratio,
    )

    # MT5 valida los stops usando el precio de cierre:
    # BID para BUY y ASK para SELL.
    # Se añade un pequeño margen para evitar quedar exactamente
    # sobre el límite de trade_stops_level.
    stops_level_points = float(getattr(info, "trade_stops_level", 0) or 0)
    safety_points = 2.0
    min_stop_distance = max(
        (stops_level_points + safety_points) * point,
        point,
    )
    if direction == "BUY":
        stop_reference = float(tick.bid)
        stop_price = min(stop_price, stop_reference - min_stop_distance)
        take_price = max(take_price, stop_reference + min_stop_distance)
        order_type = mt5.ORDER_TYPE_BUY
    else:
        stop_reference = float(tick.ask)
        stop_price = max(stop_price, stop_reference + min_stop_distance)
        take_price = min(take_price, stop_reference - min_stop_distance)
        order_type = mt5.ORDER_TYPE_SELL

    stop_price = round(stop_price, digits)
    take_price = round(take_price, digits)
    signal_price = float(signal_price)

    risk_base = (
        float(virtual_balance)
        if reinvest_profits
        else min(float(virtual_balance), float(allocation_capital))
    )
    target_risk = risk_base * (float(risk_pct) / 100.0)
    margin_cap = min(float(max_margin), risk_base)
    if target_risk <= 0 or margin_cap <= 0:
        return None

    loss_min = mt5.order_calc_profit(
        order_type, symbol, volume_min, entry_price, stop_price
    )
    if loss_min is None:
        return None
    loss_min = abs(float(loss_min))
    if loss_min <= 0 or loss_min > target_risk + 0.01:
        return None

    theoretical_volume = volume_min * target_risk / loss_min
    volume = min(theoretical_volume, volume_max)
    volume = _floor_to_step(volume, volume_step)
    if volume < volume_min:  # noqa: PLR1730
        volume = volume_min

    # Reducimos por pasos hasta cumplir simultáneamente riesgo y margen.
    chosen = None
    while volume >= volume_min - 1e-12:
        estimated_loss = mt5.order_calc_profit(
            order_type, symbol, volume, entry_price, stop_price
        )
        estimated_profit = mt5.order_calc_profit(
            order_type, symbol, volume, entry_price, take_price
        )
        margin = mt5.order_calc_margin(order_type, symbol, volume, entry_price)
        if estimated_loss is not None and estimated_profit is not None and margin is not None:
            estimated_loss = abs(float(estimated_loss))
            estimated_profit = abs(float(estimated_profit))
            margin = float(margin)
            if estimated_loss <= target_risk + 0.01 and margin <= margin_cap + 0.01:
                chosen = (volume, estimated_loss, estimated_profit, margin)
                break
        volume = _floor_to_step(volume - volume_step, volume_step)

    if chosen is None:
        return None

    volume, estimated_loss, estimated_profit, margin = chosen
    spread_points = max(0.0, (float(tick.ask) - float(tick.bid)) / point)
    return CFDOrderPlan(
        direction=direction,
        signal_price=signal_price,
        entry_price=entry_price,
        atr=float(atr),
        stop_price=stop_price,
        take_price=take_price,
        volume_lots=volume,
        margin_required=margin,
        target_risk_amount=target_risk,
        estimated_loss=estimated_loss,
        estimated_profit=estimated_profit,
        actual_risk_pct=(estimated_loss / risk_base * 100) if risk_base else 0.0,
        spread_points=spread_points,
        point=point,
    )