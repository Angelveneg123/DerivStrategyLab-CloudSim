"""
Hybrid SHORT V2 Candidate — experimental / DEMO-backtest only.

LONG:
- Conserva exactamente la lógica de generate_hybrid_signals().

SHORT:
- Conserva la lógica Hybrid original.
- Añade una única condición congelada:
    fuerza del BOS bajista >= 1.00 ATR.

Importante:
- No reemplaza la estrategia "hybrid" oficial.
- El nivel roto del BOS bajista se reconstruye desde los eventos causales
  de market_structure.py, igual que en la auditoría V2.8/V3.x.
"""

from indicators.atr import calculate_atr
from indicators.rsi import calculate_rsi
from strategy.market_structure import analizar_estructura
from strategy.strategy_engine import BUY, SELL, HOLD
from strategy.trend_filter import determinar_sesgo, ALCISTA, BAJISTA


SHORT_BOS_MIN_ATR = 1.00


def _bearish_bos_context(
    highs,
    lows,
    closes,
    structure_lookback,
):
    """
    Reconstruye el último swing low conocido que cada BOS bajista rompió.

    market_structure.py guarda swing_origen_price como el swing opuesto,
    no como el nivel roto. Por eso aquí se reconstruye explícitamente el
    último swing low disponible en el instante causal de la ruptura.
    """
    structure = analizar_estructura(
        highs,
        lows,
        closes,
        lookback=structure_lookback,
    )

    events = structure["eventos"]
    ruptures = structure["rupturas"]

    latest_low = None
    event_idx = 0
    result = {}

    for rupture in ruptures:
        idx = int(rupture["index"])

        while event_idx < len(events) and events[event_idx]["index"] <= idx:
            event = events[event_idx]
            if event["type"] == "low":
                latest_low = float(event["price"])
            event_idx += 1

        if (
            rupture.get("tipo") == "BOS"
            and rupture.get("direccion") == "bajista"
        ):
            result[idx] = {
                "broken_swing_low": latest_low,
                "bos_close": float(rupture["price"]),
            }

    return result


def generate_hybrid_short_v2_candidate_signals(
    epochs,
    highs,
    lows,
    closes,
    epochs_htf,
    highs_htf,
    lows_htf,
    closes_htf,
    rsi_period=14,
    rsi_min_compra=35,
    rsi_max_compra=80,
    rsi_min_venta=35,
    rsi_max_venta=65,
    structure_lookback=2,
    adx_minimo_htf=25,
    atr_period=14,
    short_bos_min_atr=SHORT_BOS_MIN_ATR,
    diagnostico=False,
):
    """
    LONG = Hybrid oficial.
    SHORT = Hybrid oficial + BOS bajista >= 1.00 ATR.
    """
    sesgos_htf = determinar_sesgo(
        highs_htf,
        lows_htf,
        closes_htf,
        adx_minimo=adx_minimo_htf,
    )

    estructura = analizar_estructura(
        highs,
        lows,
        closes,
        lookback=structure_lookback,
    )

    bearish_bos = _bearish_bos_context(
        highs,
        lows,
        closes,
        structure_lookback=structure_lookback,
    )

    rsi = calculate_rsi(closes, period=rsi_period)
    atr = calculate_atr(
        highs,
        lows,
        closes,
        period=atr_period,
    )

    rupturas_por_index = {}
    for r in estructura["rupturas"]:
        rupturas_por_index.setdefault(r["index"], []).append(r)

    n = len(closes)
    signals = [HOLD] * n
    idx_htf = 0
    sesgo_vigente = None

    for i in range(n):
        while idx_htf < len(epochs_htf) and epochs_htf[idx_htf] <= epochs[i]:
            if sesgos_htf[idx_htf] is not None:
                sesgo_vigente = sesgos_htf[idx_htf]
            idx_htf += 1

        if rsi[i] is None:
            continue

        rupturas_aqui = rupturas_por_index.get(i, [])

        hay_bos_alcista = any(
            r["tipo"] == "BOS" and r["direccion"] == "alcista"
            for r in rupturas_aqui
        )

        hay_bos_bajista = any(
            r["tipo"] == "BOS" and r["direccion"] == "bajista"
            for r in rupturas_aqui
        )

        # LONG: misma regla que Hybrid oficial.
        if sesgo_vigente == ALCISTA:
            if (
                rsi_min_compra < rsi[i] < rsi_max_compra
                and hay_bos_alcista
            ):
                signals[i] = BUY

        # SHORT candidato congelado.
        elif sesgo_vigente == BAJISTA:
            if not (
                rsi_min_venta < rsi[i] < rsi_max_venta
                and hay_bos_bajista
            ):
                continue

            atr_i = atr[i]
            context = bearish_bos.get(i)

            if (
                atr_i in (None, 0)
                or context is None
                or context["broken_swing_low"] is None
            ):
                continue

            bos_strength_atr = (
                float(context["broken_swing_low"]) - float(closes[i])
            ) / float(atr_i)

            if bos_strength_atr >= short_bos_min_atr:
                signals[i] = SELL

    if diagnostico:
        print("--- HYBRID SHORT V2 CANDIDATE ---")
        print(f"Velas: {n}")
        print(f"BUY:  {signals.count(BUY)}")
        print(f"SELL: {signals.count(SELL)}")
        print(f"HOLD: {signals.count(HOLD)}")
        print(f"BOS mínimo SHORT: {short_bos_min_atr:.2f} ATR")
        print("---------------------------------")

    return signals
