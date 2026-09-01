"""
Prueba de los 4 indicadores del Módulo 3, usando los datos que ya
descargaste con download_historical.py.
"""

from database.database import get_candles
from indicators.ema import calculate_ema
from indicators.rsi import calculate_rsi
from indicators.atr import calculate_atr
from indicators.adx import calculate_adx

SYMBOL = "R_50"
GRANULARITY = 60


def main():
    filas = get_candles(SYMBOL, GRANULARITY)

    if not filas:
        print(
            "No hay velas guardadas todavía. "
            "Corre primero: python download_historical.py"
        )
        return

    epochs = [f[0] for f in filas]
    opens = [f[1] for f in filas]
    highs = [f[2] for f in filas]
    lows = [f[3] for f in filas]
    closes = [f[4] for f in filas]

    ema_values = calculate_ema(closes, period=20)
    rsi_values = calculate_rsi(closes, period=14)
    atr_values = calculate_atr(highs, lows, closes, period=14)
    adx_values, plus_di, minus_di = calculate_adx(highs, lows, closes, period=14)

    print(f"Total de velas: {len(closes)}")
    print("Últimas 5 velas — los 4 indicadores juntos:\n")

    for i in range(len(closes) - 5, len(closes)):
        print(f"epoch={epochs[i]}  close={closes[i]:.5f}")
        print(f"    EMA(20)={ema_values[i]:.5f}   RSI(14)={rsi_values[i]:.2f}")
        print(f"    ATR(14)={atr_values[i]:.5f}   ADX(14)={adx_values[i]:.2f}"
              f"  (+DI={plus_di[i]:.2f}  -DI={minus_di[i]:.2f})")
        print()


if __name__ == "__main__":
    main()