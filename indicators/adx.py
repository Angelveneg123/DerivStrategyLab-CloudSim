"""
ADX — Average Directional Index.

Mide qué tan FUERTE es una tendencia, sin decir si es alcista o bajista.
Se apoya en dos líneas auxiliares, +DI y -DI, que sí indican dirección:
si +DI > -DI, la presión dominante es compradora, y viceversa.

Regla de lectura típica:
    ADX < 20  → mercado lateral, sin tendencia clara
    ADX > 25  → tendencia con fuerza (mejor momento para estrategias
                que siguen tendencia)
"""


def calculate_adx(highs, lows, closes, period=14):
    """
    highs, lows, closes: listas del mismo largo, ordenadas de la vela
    más vieja a la más nueva.
    period: cuántas velas usa el indicador (14 es el estándar).

    Devuelve una tupla (adx, plus_di, minus_di) — tres listas del mismo
    largo que `closes`, con `None` donde no hay suficientes datos.
    """
    n = len(closes)
    if n < period * 2:
        raise ValueError(
            f"Necesitas al menos {period * 2} velas para un ADX de {period} "
            f"períodos, pero solo hay {n}."
        )

    # Paso 1: movimiento direccional (+DM, -DM) y True Range de cada vela.
    tr = [None] * n
    plus_dm = [None] * n
    minus_dm = [None] * n

    for i in range(1, n):
        subida = highs[i] - highs[i - 1]
        bajada = lows[i - 1] - lows[i]

        # Solo cuenta como movimiento alcista si la subida fue mayor que
        # la bajada (y positiva) — así nunca hay +DM y -DM a la vez.
        plus_dm[i] = subida if (subida > bajada and subida > 0) else 0
        minus_dm[i] = bajada if (bajada > subida and bajada > 0) else 0

        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    # Paso 2: suavizar TR, +DM y -DM (mismo estilo de suavizado que en ATR).
    smoothed_tr = [None] * n
    smoothed_plus_dm = [None] * n
    smoothed_minus_dm = [None] * n

    smoothed_tr[period] = sum(tr[1:period + 1])
    smoothed_plus_dm[period] = sum(plus_dm[1:period + 1])
    smoothed_minus_dm[period] = sum(minus_dm[1:period + 1])

    for i in range(period + 1, n):
        smoothed_tr[i] = smoothed_tr[i - 1] - (smoothed_tr[i - 1] / period) + tr[i]
        smoothed_plus_dm[i] = (
            smoothed_plus_dm[i - 1] - (smoothed_plus_dm[i - 1] / period) + plus_dm[i]
        )
        smoothed_minus_dm[i] = (
            smoothed_minus_dm[i - 1] - (smoothed_minus_dm[i - 1] / period) + minus_dm[i]
        )

    # Paso 3: +DI, -DI y DX de cada vela a partir de los valores suavizados.
    plus_di = [None] * n
    minus_di = [None] * n
    dx = [None] * n

    for i in range(period, n):
        plus_di[i] = 100 * (smoothed_plus_dm[i] / smoothed_tr[i]) if smoothed_tr[i] else 0
        minus_di[i] = 100 * (smoothed_minus_dm[i] / smoothed_tr[i]) if smoothed_tr[i] else 0

        suma_di = plus_di[i] + minus_di[i]
        dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / suma_di if suma_di else 0

    # Paso 4: el ADX es el DX suavizado. El primer valor arranca en
    # (period * 2 - 1), porque necesita `period` valores de DX ya calculados.
    adx = [None] * n
    primer_indice = period * 2 - 1
    adx[primer_indice] = sum(dx[period:period * 2]) / period

    for i in range(primer_indice + 1, n):
        adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

    return adx, plus_di, minus_di