"""
RSI — Índice de Fuerza Relativa (Relative Strength Index).

Mide la velocidad y magnitud de los movimientos de precio para
determinar condiciones de sobrecompra (>70) o sobreventa (<30).
Se mueve siempre entre 0 y 100.
"""


def calculate_rsi(closes, period=14):
    """
    Calcula el RSI sobre una lista de precios de cierre.

    closes: lista de precios de cierre, ordenados del más viejo al más nuevo.
    period: cuántas velas usa el indicador (14 es el estándar de mercado).

    Devuelve una lista del mismo largo que `closes`, con `None` en las
    posiciones donde todavía no hay suficientes datos.
    """
    if len(closes) < period + 1:
        raise ValueError(
            f"Necesitas al menos {period + 1} velas para calcular un RSI de "
            f"{period} períodos, pero solo hay {len(closes)}."
        )

    rsi_values = [None] * len(closes)

    # Paso 1: calcular la ganancia o pérdida de cada vela frente a la anterior.
    gains = [0.0] * len(closes)
    losses = [0.0] * len(closes)
    for i in range(1, len(closes)):
        cambio = closes[i] - closes[i - 1]
        if cambio > 0:
            gains[i] = cambio
        else:
            losses[i] = -cambio  # guardamos la pérdida como valor positivo

    # Paso 2: promedio inicial de ganancias/pérdidas de los primeros `period` cambios.
    avg_gain = sum(gains[1:period + 1]) / period
    avg_loss = sum(losses[1:period + 1]) / period

    rsi_values[period] = _rsi_from_averages(avg_gain, avg_loss)

    # Paso 3: de ahí en adelante, cada promedio se actualiza con una fórmula
    # suavizada (parecida en espíritu a la EMA: mezcla el promedio anterior
    # con el dato nuevo).
    for i in range(period + 1, len(closes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsi_values[i] = _rsi_from_averages(avg_gain, avg_loss)

    return rsi_values


def _rsi_from_averages(avg_gain, avg_loss):
    """Convierte un promedio de ganancias/pérdidas en el valor final de RSI (0-100)."""
    if avg_loss == 0:
        return 100.0  # solo hubo subidas, RSI al máximo
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))