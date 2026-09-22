"""
Estrategia híbrida — Módulo optimizado y bidireccional.

1. Sesgo de tendencia en temporalidad superior (HTF) (trend_filter) — define la dirección permitida (ALCISTA o BAJISTA).
2. BOS en la temporalidad de entrada (market_structure) — confirma la rotura de estructura a favor.
3. RSI en rango — evita operar en extremos o sobreextensiones.
"""

from strategy.market_structure import analizar_estructura
from strategy.trend_filter import determinar_sesgo, ALCISTA, BAJISTA
from strategy.strategy_engine import BUY, SELL, HOLD
from indicators.rsi import calculate_rsi


def generate_hybrid_signals(
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
    rsi_max_compra=65,
    rsi_min_venta=35,
    rsi_max_venta=65,
    structure_lookback=3,
    adx_minimo_htf=25,
    diagnostico=False,
):
    """
    epochs, highs, lows, closes: velas de la temporalidad de ENTRADA (ej. M1).
    epochs_htf, highs_htf, lows_htf, closes_htf: velas de la temporalidad SUPERIOR (ej. M15).

    Devuelve una lista del mismo largo que `closes`, con BUY, SELL o HOLD en cada posición.
    """
    sesgos_htf = determinar_sesgo(
        highs_htf, lows_htf, closes_htf, adx_minimo=adx_minimo_htf
    )
    estructura = analizar_estructura(highs, lows, closes, lookback=structure_lookback)
    rsi = calculate_rsi(closes, period=rsi_period)

    # Agrupar las rupturas por índice para consulta O(1)
    rupturas_por_index = {}
    for r in estructura["rupturas"]:
        rupturas_por_index.setdefault(r["index"], []).append(r)

    n = len(closes)
    signals = [HOLD] * n

    # Puntero para recorrer la temporalidad superior
    idx_htf = 0
    sesgo_vigente = None

    for i in range(n):
        # Actualizar el sesgo HTF según la estampa de tiempo actual
        while idx_htf < len(epochs_htf) and epochs_htf[idx_htf] <= epochs[i]:
            if sesgos_htf[idx_htf] is not None:
                sesgo_vigente = sesgos_htf[idx_htf]
            idx_htf += 1

        # Descarte rápido si no hay RSI calculado aún
        if rsi[i] is None:
            continue

        # Obtener rupturas de la vela actual una sola vez
        rupturas_aqui = rupturas_por_index.get(i, [])

        # Calcular presencia de BOS antes de evaluar la dirección
        hay_bos_alcista = any(
            r["tipo"] == "BOS" and r["direccion"] == "alcista" for r in rupturas_aqui
        )
        hay_bos_bajista = any(
            r["tipo"] == "BOS" and r["direccion"] == "bajista" for r in rupturas_aqui
        )

        # Cerebro de la Estrategia (Lógica de decisión)
        if sesgo_vigente == ALCISTA:
            if rsi_min_compra < rsi[i] < rsi_max_compra and hay_bos_alcista:
                signals[i] = BUY

        elif sesgo_vigente == BAJISTA:
            if rsi_min_venta < rsi[i] < rsi_max_venta and hay_bos_bajista:
                signals[i] = SELL

    if diagnostico:
        print("--- DIAGNÓSTICO DE SEÑALES ---")
        print(f"Total velas procesadas: {n}")
        print(f"Señales BUY generadas:  {signals.count(BUY)}")
        print(f"Señales SELL generadas: {signals.count(SELL)}")
        print(f"Señales HOLD generadas: {signals.count(HOLD)}")
        print("-------------------------------")

    return signals


def diagnosticar_hybrid_ultima_vela(
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
    rsi_max_compra=65,
    rsi_min_venta=35,
    rsi_max_venta=65,
    structure_lookback=3,
    adx_minimo_htf=25,
):
    """Diagnóstico de solo lectura para la última vela.

    Usa exactamente los mismos componentes de la estrategia Hybrid para
    explicar por qué la última vela termina en BUY, SELL o HOLD.

    IMPORTANTE: esta función NO modifica señales, riesgo, SL, TP, balance,
    historial ni estado del simulador. Solo devuelve información diagnóstica.
    """
    if not epochs or not closes:
        return {
            "raw_signal": HOLD,
            "htf_bias": None,
            "rsi": None,
            "bos_bull": False,
            "bos_bear": False,
            "reason": "sin_datos",
        }

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
    rsi = calculate_rsi(closes, period=rsi_period)

    i = len(closes) - 1
    epoch_actual = epochs[i]

    # Reproduce exactamente la lógica de sesgo vigente usada por Hybrid.
    sesgo_vigente = None
    for epoch_htf, sesgo in zip(epochs_htf, sesgos_htf):
        if epoch_htf > epoch_actual:
            break
        if sesgo is not None:
            sesgo_vigente = sesgo

    rupturas_aqui = [
        r for r in estructura["rupturas"]
        if r.get("index") == i
    ]

    hay_bos_alcista = any(
        r.get("tipo") == "BOS" and r.get("direccion") == "alcista"
        for r in rupturas_aqui
    )
    hay_bos_bajista = any(
        r.get("tipo") == "BOS" and r.get("direccion") == "bajista"
        for r in rupturas_aqui
    )

    rsi_actual = rsi[i] if i < len(rsi) else None
    raw_signal = HOLD

    if rsi_actual is None:
        reason = "rsi_no_disponible"

    elif sesgo_vigente == ALCISTA:
        if not (rsi_min_compra < rsi_actual < rsi_max_compra):
            reason = "rsi_fuera_rango_compra"
        elif not hay_bos_alcista:
            reason = "sin_bos_alcista"
        else:
            raw_signal = BUY
            reason = "buy_valido"

    elif sesgo_vigente == BAJISTA:
        if not (rsi_min_venta < rsi_actual < rsi_max_venta):
            reason = "rsi_fuera_rango_venta"
        elif not hay_bos_bajista:
            reason = "sin_bos_bajista"
        else:
            raw_signal = SELL
            reason = "sell_valido"

    else:
        reason = "sesgo_htf_neutral_o_no_disponible"

    return {
        "raw_signal": raw_signal,
        "htf_bias": sesgo_vigente,
        "rsi": rsi_actual,
        "bos_bull": hay_bos_alcista,
        "bos_bear": hay_bos_bajista,
        "reason": reason,
    }
