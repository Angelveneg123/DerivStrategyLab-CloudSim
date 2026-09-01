"""Pruebas rápidas de las correcciones, sin conectarse a Deriv."""

from backtesting.backtester import calculate_metrics
from backtesting.metrics import calculate_sharpe_ratio
from backtesting.risk_backtester import run_backtest_risk
from strategy.market_structure import find_swing_points
from strategy.strategy_engine import BUY, HOLD, SELL
from utils.timeframes import aggregate_ohlc_to_timeframe


def probar_metricas():
    trades = [
        {"profit": 2, "profit_pct": 2},
        {"profit": -1, "profit_pct": -1},
    ]
    assert calculate_metrics(trades, 100)["final_balance"] == 101
    assert calculate_sharpe_ratio(trades) is not None


def probar_swings_causales():
    swings, _ = find_swing_points([1, 2, 5, 2, 1], [1, 1, 1, 1, 1], lookback=2)
    assert swings == [(4, 5)]


def probar_htf():
    epochs = list(range(0, 1800, 60))
    highs = [i + 1 for i in range(30)]
    lows = [i - 1 for i in range(30)]
    closes = list(range(30))
    e, _, _, c = aggregate_ohlc_to_timeframe(
        epochs, highs, lows, closes, 900, 60
    )
    assert e == [900, 1800]
    assert c == [14, 29]


def probar_buy_sell():
    epochs = list(range(8))
    highs = [100, 100, 103, 103, 103, 103.5, 103.5, 103.5]
    lows = [100, 100, 100, 100, 103, 100, 100, 100]
    closes = [100, 100, 102, 102, 103, 101, 101, 101]
    signals = [HOLD, BUY, HOLD, HOLD, SELL, HOLD, HOLD, HOLD]
    atr = [1] * 8
    resultado = run_backtest_risk(
        epochs,
        highs,
        lows,
        closes,
        signals,
        atr,
        initial_balance=100,
        risk_pct=1,
        stop_atr_mult=1,
        reward_ratio=2,
        spread_atr_frac=0,
        max_drawdown_stop_pct=20,
    )
    assert [t["direction"] for t in resultado["trades"]] == [BUY, SELL]


def main():
    probar_metricas()
    probar_swings_causales()
    probar_htf()
    probar_buy_sell()
    print("Todas las pruebas de corrección pasaron.")


if __name__ == "__main__":
    main()
