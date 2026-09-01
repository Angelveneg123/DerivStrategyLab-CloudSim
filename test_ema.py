"""
Prueba del indicador EMA usando los datos que ya descargaste con
download_historical.py.
"""

from database.database import get_candles
from indicators.ema import calculate_ema

SYMBOL = "R_50"
GRANULARITY = 60
PERIOD = 20


def main():
    filas = get_candles(SYMBOL, GRANULARITY)

    if not filas:
        print(
            "No hay velas guardadas todavía. "
            "Corre primero: python download_historical.py"
        )
        return

    # Cada fila es (epoch, open, high, low, close) — solo nos interesa el cierre.
    closes = [fila[4] for fila in filas]

    ema_values = calculate_ema(closes, period=PERIOD)

    print(f"Total de velas: {len(closes)}")
    print(f"EMA({PERIOD}) — últimos 5 valores:\n")

    for epoch, close, ema in list(zip(
        [f[0] for f in filas], closes, ema_values
    ))[-5:]:
        print(f"  epoch={epoch}  close={close:.5f}  EMA={ema:.5f}")


if __name__ == "__main__":
    main()