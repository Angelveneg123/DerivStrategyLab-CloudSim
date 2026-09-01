"""
Prueba del indicador RSI usando los datos que ya descargaste con
download_historical.py.
"""

from database.database import get_candles
from indicators.rsi import calculate_rsi

SYMBOL = "R_50"
GRANULARITY = 60
PERIOD = 14


def main():
    filas = get_candles(SYMBOL, GRANULARITY)

    if not filas:
        print(
            "No hay velas guardadas todavía. "
            "Corre primero: python download_historical.py"
        )
        return

    closes = [fila[4] for fila in filas]
    rsi_values = calculate_rsi(closes, period=PERIOD)

    print(f"Total de velas: {len(closes)}")
    print(f"RSI({PERIOD}) — últimos 5 valores:\n")

    for epoch, close, rsi in list(zip(
        [f[0] for f in filas], closes, rsi_values
    ))[-5:]:
        etiqueta = ""
        if rsi >= 70:
            etiqueta = "  ← sobrecomprado"
        elif rsi <= 30:
            etiqueta = "  ← sobrevendido"
        print(f"  epoch={epoch}  close={close:.5f}  RSI={rsi:.2f}{etiqueta}")


if __name__ == "__main__":
    main()