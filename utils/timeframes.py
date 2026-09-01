"""Utilidades para construir temporalidades superiores sin mirar el futuro."""


def aggregate_ohlc_to_timeframe(
    epochs,
    highs,
    lows,
    closes,
    target_granularity,
    source_granularity=None,
):
    """
    Agrega velas OHLC parciales a una temporalidad superior.

    El epoch devuelto representa el momento de CIERRE/confirmación de la
    vela superior, no su inicio. De esta manera, el filtro HTF solo puede
    usar una vela cuando ya terminó y su close es conocido.

    La última vela superior se descarta si todavía no puede demostrarse
    que está cerrada con los datos de entrada disponibles.
    """
    if target_granularity <= 0:
        raise ValueError("target_granularity debe ser mayor que cero")
    if not (len(epochs) == len(highs) == len(lows) == len(closes)):
        raise ValueError("epochs/highs/lows/closes deben tener el mismo largo")
    if not epochs:
        return [], [], [], []

    if source_granularity is None:
        diferencias = [b - a for a, b in zip(epochs, epochs[1:]) if b > a]
        source_granularity = min(diferencias) if diferencias else target_granularity

    buckets = []
    actual = None

    for epoch, high, low, close in zip(epochs, highs, lows, closes):
        bucket_start = epoch - (epoch % target_granularity)

        if actual is None or actual["start"] != bucket_start:
            if actual is not None:
                buckets.append(actual)
            actual = {
                "start": bucket_start,
                "high": high,
                "low": low,
                "close": close,
                "first_epoch": epoch,
                "last_epoch": epoch,
                "count": 1,
            }
        else:
            actual["high"] = max(actual["high"], high)
            actual["low"] = min(actual["low"], low)
            actual["close"] = close
            actual["last_epoch"] = epoch
            actual["count"] += 1

    if actual is not None:
        buckets.append(actual)

    epochs_htf = []
    highs_htf = []
    lows_htf = []
    closes_htf = []

    for bucket in buckets:
        close_epoch = bucket["start"] + target_granularity
        ultima_vela_cierra = bucket["last_epoch"] + source_granularity
        esperado = max(1, target_granularity // source_granularity)
        if (
            bucket["first_epoch"] != bucket["start"]
            or ultima_vela_cierra < close_epoch
            or bucket["count"] < esperado
        ):
            continue

        epochs_htf.append(close_epoch)
        highs_htf.append(bucket["high"])
        lows_htf.append(bucket["low"])
        closes_htf.append(bucket["close"])

    return epochs_htf, highs_htf, lows_htf, closes_htf


def mark_candles_available_at_close(rows, granularity):
    """
    Convierte filas SQLite `(epoch, open, high, low, close)` a series HTF.

    Deriv guarda el epoch de inicio de vela. Para evitar que el backtest use
    el cierre de esa vela desde su inicio, el epoch de disponibilidad se
    desplaza hasta el cierre real (`epoch + granularity`).
    """
    return (
        [row[0] + granularity for row in rows],
        [row[2] for row in rows],
        [row[3] for row in rows],
        [row[4] for row in rows],
    )
