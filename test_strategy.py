"""
Prueba del motor de estrategias usando los datos ya descargados.
Muestra únicamente las velas donde hubo una señal real (BUY o SELL) —
la mayoría de las velas van a ser HOLD, así que no las imprimimos todas.
"""

from database.database import get_candles
from strategy.strategy_engine import generate_signals, BUY, SELL

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
    closes = [f[4] for f in filas]

    signals = generate_signals(closes)

    print(f"Total de velas analizadas: {len(closes)}\n")

    total_buy = 0
    total_sell = 0

    for i, signal in enumerate(signals):
        if signal == BUY:
            total_buy += 1
            print(f"🟢 BUY   epoch={epochs[i]}  close={closes[i]:.5f}")
        elif signal == SELL:
            total_sell += 1
            print(f"🔴 SELL  epoch={epochs[i]}  close={closes[i]:.5f}")

    print(f"\nResumen: {total_buy} señales de compra, {total_sell} señales de venta.")


if __name__ == "__main__":
    main()