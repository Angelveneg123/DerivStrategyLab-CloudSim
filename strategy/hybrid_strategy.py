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
    signals = [HOLD] * n  # Mejora 3: Inicializados todos en HOLD por defecto

    # Puntero para recorrer la temporalidad superior
    idx_htf = 0
    sesgo_vigente = None

    for i in range(n):
        # Actualizar el sesgo HTF según la estampa de tiempo actual
        while idx_htf < len(epochs_htf) and epochs_htf[idx_htf] <= epochs[i]:
            if sesgos_htf[idx_htf] is not None:
                sesgo_vigente = sesgos_htf[idx_htf]
            idx_htf += 1

        # Mejora 4: Descarte rápido si no hay RSI calculado aún
        if rsi[i] is None:
            continue

        # Mejora 2: Obtener rupturas de la vela actual una sola vez
        rupturas_aqui = rupturas_por_index.get(i, [])

        # Mejora 5: Calcular presencia de BOS antes de evaluar la dirección
        hay_bos_alcista = any(
            r["tipo"] == "BOS" and r["direccion"] == "alcista" for r in rupturas_aqui
        )
        hay_bos_bajista = any(
            r["tipo"] == "BOS" and r["direccion"] == "bajista" for r in rupturas_aqui
        )

        # Cerebro de la Estrategia (Lógica de decisión)
        if sesgo_vigente == ALCISTA:
            # Mejora 1: Rango de RSI delimitado para Compra
            if rsi_min_compra < rsi[i] < rsi_max_compra and hay_bos_alcista:
                signals[i] = BUY

        elif sesgo_vigente == BAJISTA:
            # Mejora 1: Rango de RSI delimitado para Venta
            if rsi_min_venta < rsi[i] < rsi_max_venta and hay_bos_bajista:
                signals[i] = SELL

        # Mejora 3: Se eliminó el 'else: signals[i] = HOLD' redundante

    if diagnostico:
        print("--- DIAGNÓSTICO DE SEÑALES ---")
        print(f"Total velas procesadas: {n}")
        print(f"Señales BUY generadas:  {signals.count(BUY)}")
        print(f"Señales SELL generadas: {signals.count(SELL)}")
        print(f"Señales HOLD generadas: {signals.count(HOLD)}")
        print("-------------------------------")

    return signals
