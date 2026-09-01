"""
ATR — Average True Range.

Mide la volatilidad del mercado (qué tanto se mueve el precio),
sin importar la dirección. Un ATR alto = mercado agitado.
Se usa mucho para calcular stop-loss dinámicos.
"""


def calculate_atr(highs, lows, closes, period=14):
    """
    highs, lows, closes: listas del mismo largo, ordenadas de la vela
    más vieja a la más nueva.
    period: cuántas velas usa el indicador (14 es el estándar).

    Devuelve una lista del mismo largo, con `None` donde no hay
    suficientes datos todavía.
    """
    n = len(closes)
    if n < period + 1:
        raise ValueError(
            f"Necesitas al menos {period + 1} velas para un ATR de {period} "
            f"períodos, pero solo hay {n}."
        )

    # Paso 1: True Range de cada vela (el rango más amplio de los 3 posibles).
    true_ranges = [None] * n
    for i in range(1, n):
        true_ranges[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    # Paso 2: el primer ATR es un promedio simple de los primeros `period` TR.
    atr_values = [None] * n
    atr_values[period] = sum(true_ranges[1:period + 1]) / period

    # Paso 3: de ahí en adelante, suavizado (misma idea que usamos en RSI).
    for i in range(period + 1, n):
        atr_anterior = atr_values[i - 1]
        atr_values[i] = (atr_anterior * (period - 1) + true_ranges[i]) / period

    return atr_values