"""
Filtro de tendencia (temporalidad superior) — define el sesgo.

La idea: antes de buscar cualquier entrada, primero preguntamos "¿en qué
dirección está el mercado en el marco de tiempo superior (HTF)?" Solo se
permite buscar compras cuando el sesgo es alcista, y solo ventas cuando
es bajista. Cuando no hay una tendencia clara, el sesgo es "neutral" y
no se opera — evita el error clásico de operar cruces de EMA en un
mercado sin dirección (que fue justo lo que le pasó a la estrategia
anterior).

Tres condiciones tienen que estar de acuerdo para definir un sesgo:
  1. EMA50 por encima (o debajo) de la EMA200 — tendencia establecida.
  2. La EMA200 tiene pendiente en esa misma dirección — no está plana.
  3. El ADX confirma que hay fuerza de tendencia real, no ruido.
"""

from indicators.ema import calculate_ema
from indicators.adx import calculate_adx

ALCISTA = "alcista"
BAJISTA = "bajista"
NEUTRAL = "neutral"


def determinar_sesgo(
    highs,
    lows,
    closes,
    ema_fast_period=50,
    ema_slow_period=200,
    adx_period=14,
    adx_minimo=25,
    pendiente_lookback=5,
):
    """
        highs, lows, closes: listas de la temporalidad SUPERIOR (ej. M15 o H4),
            de la vela más vieja a la más nueva.
        pendiente_lookback: cuántas velas atrás comparar para medir si la
            EMA200 está subiendo, bajando, o plana.
    py
        Devuelve una lista del mismo largo, con ALCISTA / BAJISTA / NEUTRAL
        en cada posición (None donde todavía no hay suficientes datos).
    """
    ema_fast = calculate_ema(closes, period=ema_fast_period)
    ema_slow = calculate_ema(closes, period=ema_slow_period)
    adx, _, _ = calculate_adx(highs, lows, closes, period=adx_period)

    n = len(closes)
    sesgos = [None] * n

    inicio = (
        max(ema_fast_period, ema_slow_period, adx_period * 2, pendiente_lookback) + 1
    )

    for i in range(inicio, n):
        if ema_fast[i] is None or ema_slow[i] is None or adx[i] is None:
            continue
        if ema_slow[i - pendiente_lookback] is None:
            continue

        pendiente = ema_slow[i] - ema_slow[i - pendiente_lookback]
        tendencia_fuerte = adx[i] > adx_minimo

        if ema_fast[i] > ema_slow[i] and pendiente > 0 and tendencia_fuerte:
            sesgos[i] = ALCISTA
        elif ema_fast[i] < ema_slow[i] and pendiente < 0 and tendencia_fuerte:
            sesgos[i] = BAJISTA
        else:
            sesgos[i] = NEUTRAL

    return sesgos


def sesgo_en_epoch(epochs_htf, sesgos_htf, epoch_objetivo):
    """
    Dado un epoch de una vela en temporalidad MENOR (ej. la vela donde
    se está evaluando una entrada en M1), encuentra cuál era el sesgo
    HTF vigente en ese momento — es decir, la última vela de H4 cuyo
    epoch sea menor o igual al epoch objetivo.

    Esto es lo que conecta el filtro de tendencia (HTF) con las entradas
    de la temporalidad de operación (ej. M1) — cada entrada se evalúa
    contra el sesgo HTF que ya estaba confirmado en ese momento, nunca
    contra una vela de H4 que "todavía no había cerrado" (eso sería ver
    el futuro).

    Devuelve el sesgo (ALCISTA/BAJISTA/NEUTRAL) o None si el epoch
    objetivo es anterior a todo el historial HTF disponible.
    """
    sesgo_vigente = None
    for epoch_h4, sesgo in zip(epochs_htf, sesgos_htf):
        if epoch_h4 > epoch_objetivo:
            break
        if sesgo is not None:
            sesgo_vigente = sesgo
    return sesgo_vigente
