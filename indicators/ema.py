"""
EMA — Media Móvil Exponencial.

Le da más peso a los precios recientes que a los antiguos, por eso
reacciona más rápido que una media móvil simple (SMA) ante cambios
de precio.
"""


def calculate_ema(closes, period=20):
    """
    Calcula la EMA sobre una lista de precios de cierre.

    closes: lista de precios de cierre, ordenados del más viejo al más nuevo.
    period: cuántas velas usa el indicador (ej. 20 = EMA de 20 períodos).

    Devuelve una lista del mismo largo que `closes`, donde las primeras
    (period - 1) posiciones son `None` (todavía no hay suficientes datos
    para calcular nada ahí) y de ahí en adelante sí hay un valor de EMA.
    """
    if len(closes) < period:
        raise ValueError(
            f"Necesitas al menos {period} velas para calcular una EMA de {period} "
            f"períodos, pero solo hay {len(closes)}."
        )

    multiplier = 2 / (period + 1)
    ema_values = [None] * len(closes)

    # El primer valor de EMA se arranca con una SMA simple: el promedio
    # de los primeros `period` precios. No hay forma de "encadenar" antes
    # de este punto porque no existe una EMA anterior.
    sma_inicial = sum(closes[:period]) / period
    ema_values[period - 1] = sma_inicial

    # De ahí en adelante, cada EMA depende de la anterior.
    for i in range(period, len(closes)):
        ema_anterior = ema_values[i - 1]
        ema_values[i] = (closes[i] - ema_anterior) * multiplier + ema_anterior

    return ema_values