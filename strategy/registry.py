"""Registro único de estrategias para backtest, simulación y trading conectado.

Versión experimental:
- conserva "hybrid" intacta;
- añade "hybrid_short_v2_candidate" para validación DEMO/backtest.
"""

from backtesting.risk_backtester import run_backtest_risk
from config import (
    ATR_PERIOD,
    BACKTEST_COMMISSION_PER_TRADE_USD,
    BACKTEST_RANDOM_SEED,
    BACKTEST_SLIPPAGE_ATR_MAX,
    HTF_GRANULARITY,
    INITIAL_BALANCE,
    MAX_DRAWDOWN_STOP_PCT,
    REWARD_RATIO,
    RIESGO_POR_OPERACION_PCT,
    SL_ATR_MULT,
    SPREAD_ATR_FRAC,
)
from indicators.atr import calculate_atr
from strategy.hybrid_strategy import generate_hybrid_signals
from strategy.hybrid_short_v2_candidate import (
    generate_hybrid_short_v2_candidate_signals,
)
from strategy.quant_structure_liquidity import (
    generate_quant_structure_liquidity_signals,
)
from strategy.strategy_engine import (
    HOLD,
    SELL,
    generate_signals as generate_signals_ema_rsi_adx,
    generate_signals_soporte_resistencia,
)
from utils.timeframes import aggregate_ohlc_to_timeframe


STRATEGIES = {
    "hybrid": "Híbrida (BOS + Tendencia HTF + RSI)",
    "hybrid_long_only": (
        "Hybrid LONG Only — experimental DEMO/backtest"
    ),
    "hybrid_short_v2_candidate": (
        "Hybrid + SHORT V2 Candidate (BOS bajista >= 1.00 ATR)"
    ),
    "ema_rsi_adx": "Cruce EMA + RSI + ADX",
    "soporte_resistencia": "Soporte / Resistencia",
    "quant_structure_liquidity_v01": (
        "QSL v0.1 (Estructura + Liquidez + FVG/OB + MTF)"
    ),
}
DEFAULT_STRATEGY = "hybrid"


def validate_strategy_id(strategy_id):
    if strategy_id not in STRATEGIES:
        disponibles = ", ".join(STRATEGIES)
        raise ValueError(
            f"Estrategia desconocida: {strategy_id!r}. Disponibles: {disponibles}"
        )
    return strategy_id


def generate_registered_signals(
    strategy_id,
    epochs,
    highs,
    lows,
    closes,
    source_granularity=60,
    htf_granularity=HTF_GRANULARITY,
    opens=None,
):
    validate_strategy_id(strategy_id)

    if strategy_id == "ema_rsi_adx":
        return generate_signals_ema_rsi_adx(
            closes,
            highs=highs,
            lows=lows,
        )

    if strategy_id == "soporte_resistencia":
        return generate_signals_soporte_resistencia(
            closes,
            highs,
            lows,
        )

    if strategy_id == "quant_structure_liquidity_v01":
        return generate_quant_structure_liquidity_signals(
            epochs,
            highs,
            lows,
            closes,
            opens=opens,
            source_granularity=source_granularity,
        )

    epochs_htf, highs_htf, lows_htf, closes_htf = aggregate_ohlc_to_timeframe(
        epochs,
        highs,
        lows,
        closes,
        target_granularity=htf_granularity,
        source_granularity=source_granularity,
    )

    if strategy_id == "hybrid_short_v2_candidate":
        return generate_hybrid_short_v2_candidate_signals(
            epochs,
            highs,
            lows,
            closes,
            epochs_htf,
            highs_htf,
            lows_htf,
            closes_htf,
            adx_minimo_htf=25,
            rsi_max_compra=80,
            structure_lookback=2,
            atr_period=ATR_PERIOD,
            short_bos_min_atr=1.00,
            diagnostico=False,
        )

    signals = generate_hybrid_signals(
        epochs,
        highs,
        lows,
        closes,
        epochs_htf,
        highs_htf,
        lows_htf,
        closes_htf,
        adx_minimo_htf=25,
        rsi_max_compra=80,
        structure_lookback=2,
        diagnostico=False,
    )

    if strategy_id == "hybrid_long_only":
        return [
            HOLD if signal == SELL else signal
            for signal in signals
        ]

    return signals


def run_registered_backtest(
    strategy_id,
    epochs,
    highs,
    lows,
    closes,
    source_granularity=60,
    opens=None,
    backtest_overrides=None,
):
    signals = generate_registered_signals(
        strategy_id,
        epochs,
        highs,
        lows,
        closes,
        source_granularity=source_granularity,
        opens=opens,
    )

    atr_values = calculate_atr(
        highs,
        lows,
        closes,
        period=ATR_PERIOD,
    )

    overrides = backtest_overrides or {}

    return run_backtest_risk(
        epochs,
        highs,
        lows,
        closes,
        signals,
        atr_values,
        initial_balance=overrides.get(
            "initial_balance",
            INITIAL_BALANCE,
        ),
        risk_pct=overrides.get(
            "risk_pct",
            RIESGO_POR_OPERACION_PCT,
        ),
        stop_atr_mult=overrides.get(
            "stop_atr_mult",
            SL_ATR_MULT,
        ),
        reward_ratio=overrides.get(
            "reward_ratio",
            REWARD_RATIO,
        ),
        spread_atr_frac=overrides.get(
            "spread_atr_frac",
            SPREAD_ATR_FRAC,
        ),
        max_drawdown_stop_pct=overrides.get(
            "max_drawdown_stop_pct",
            MAX_DRAWDOWN_STOP_PCT,
        ),
        slippage_atr_max=overrides.get(
            "slippage_atr_max",
            BACKTEST_SLIPPAGE_ATR_MAX,
        ),
        random_seed=overrides.get(
            "random_seed",
            BACKTEST_RANDOM_SEED,
        ),
        commission_per_trade=overrides.get(
            "commission_per_trade",
            BACKTEST_COMMISSION_PER_TRADE_USD,
        ),
    )
