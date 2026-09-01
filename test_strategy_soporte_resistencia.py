"""
Prueba de la estrategia de Soporte y Resistencia contra datos históricos
ya descargados. Compara distintos juegos de parámetros para ver cómo
cambian las métricas del backtest, usando la misma gestión de riesgo
(sizing por % de riesgo, SL/TP por ATR, circuit breaker) que el
dashboard y la simulación en vivo.

Corre primero `python download_historical.py` si todavía no tienes datos.
"""

from database.database import get_candles
from strategy.strategy_engine import generate_signals_soporte_resistencia, BUY, SELL
from backtesting.backtester import run_backtest
from config import (
    INITIAL_BALANCE,
    SPREAD_PCT,
    RIESGO_POR_OPERACION_PCT,
    MAX_DRAWDOWN_STOP_PCT,
)

SYMBOL = "R_50"
GRANULARITY = 60


def correr_config(nombre, epochs, closes, highs, lows, **kwargs):
    signals = generate_signals_soporte_resistencia(closes, highs, lows, **kwargs)
    resultado = run_backtest(
        epochs,
        closes,
        signals,
        highs=highs,
        lows=lows,
        initial_balance=INITIAL_BALANCE,
        spread_pct=SPREAD_PCT,
        riesgo_por_operacion_pct=RIESGO_POR_OPERACION_PCT,
        max_drawdown_stop_pct=MAX_DRAWDOWN_STOP_PCT,
    )
    m = resultado["metrics"]
    print(f"--- {nombre} ---")
    print(f"Operaciones totales: {m['total_trades']}")
    print(f"Win Rate:            {m['win_rate']:.1f}%")
    print(f"Profit Factor:       {m['profit_factor']:.2f}")
    print(f"Drawdown máximo:     {m['max_drawdown_pct']:.1f}%")
    print(f"Retorno total:       {m['total_return_pct']:+.1f}%")
    print(f"Balance final:       ${m['final_balance']:,.2f}")
    if m["circuit_breaker_activado"]:
        print("⚠️  Circuit breaker de drawdown activado durante este backtest.")
    print()


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

    print(f"Total de velas analizadas: {len(closes)}\n")

    # Configuración por defecto.
    correr_config(
        "Default (lookback=5, tolerancia=0.15%, min_toques=2)",
        epochs,
        closes,
        highs,
        lows,
    )

    # Niveles más exigentes: requieren más toques antes de confiar en ellos.
    correr_config(
        "Niveles más exigentes (min_toques=3)",
        epochs,
        closes,
        highs,
        lows,
        min_toques=3,
    )

    # Niveles más importantes: lookback mayor = pivotes más significativos.
    correr_config(
        "Pivotes más amplios (lookback=10)", epochs, closes, highs, lows, lookback=10
    )

    # Tolerancia más ancha: "toca" el nivel desde más lejos.
    correr_config(
        "Tolerancia más ancha (0.30%)", epochs, closes, highs, lows, tolerancia_pct=0.30
    )


if __name__ == "__main__":
    main()
