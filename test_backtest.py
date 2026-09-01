"""
Corre el backtest en 4 combinaciones sobre el mismo dataset:
  1. Sin filtro ADX, sin costos       (la versión más optimista posible)
  2. Con filtro ADX, sin costos       (lo que vimos la vez pasada)
  3. Sin filtro ADX, con spread real
  4. Con filtro ADX, con spread real  (la versión más realista — la que
                                        importa de verdad)
"""

from database.database import get_candles
from strategy.strategy_engine import generate_signals
from backtesting.backtester import run_backtest

SYMBOL = "R_50"
GRANULARITY = 60
INITIAL_BALANCE = 100
SPREAD_PCT = 0.05  # Estimación conservadora. Verifica el valor real en Deriv.


def imprimir_resultado(nombre, metrics):
    print(f"--- {nombre} ---")
    print(f"Operaciones totales: {metrics['total_trades']}")
    print(f"Win Rate:            {metrics['win_rate']:.1f}%")
    print(f"Profit Factor:       {metrics['profit_factor']:.2f}")
    print(f"Drawdown máximo:     {metrics['max_drawdown_pct']:.1f}%")
    print(f"Retorno total:       {metrics['total_return_pct']:+.1f}%")
    print(f"Balance final:       ${metrics['final_balance']:,.2f}\n")


def main():
    filas = get_candles(SYMBOL, GRANULARITY)

    if not filas:
        print(
            "No hay velas guardadas todavía. "
            "Corre primero: python download_historical.py"
        )
        return

    epochs = [f[0] for f in filas]
    highs = [f[2] for f in filas]
    lows = [f[3] for f in filas]
    closes = [f[4] for f in filas]

    print("=" * 40)
    print(" COMPARACIÓN COMPLETA ")
    print("=" * 40)
    print(f"Símbolo: {SYMBOL}   Velas: {len(closes)}   Spread asumido: {SPREAD_PCT}%\n")

    signals_sin_filtro = generate_signals(closes)
    signals_con_filtro = generate_signals(closes, highs=highs, lows=lows, adx_minimo=25)

    r1 = run_backtest(epochs, closes, signals_sin_filtro, highs=highs, lows=lows,
                       initial_balance=INITIAL_BALANCE, spread_pct=0)
    imprimir_resultado("1. SIN filtro ADX — SIN costos", r1["metrics"])

    r2 = run_backtest(epochs, closes, signals_con_filtro, highs=highs, lows=lows,
                       initial_balance=INITIAL_BALANCE, spread_pct=0)
    imprimir_resultado("2. CON filtro ADX — SIN costos", r2["metrics"])

    r3 = run_backtest(epochs, closes, signals_sin_filtro, highs=highs, lows=lows,
                       initial_balance=INITIAL_BALANCE, spread_pct=SPREAD_PCT)
    imprimir_resultado("3. SIN filtro ADX — CON spread real", r3["metrics"])

    r4 = run_backtest(epochs, closes, signals_con_filtro, highs=highs, lows=lows,
                       initial_balance=INITIAL_BALANCE, spread_pct=SPREAD_PCT)
    imprimir_resultado("4. CON filtro ADX — CON spread real  ⭐ (la que importa)", r4["metrics"])


if __name__ == "__main__":
    main()