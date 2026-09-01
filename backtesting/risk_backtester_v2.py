"""
Risk Backtester V2
==================

Motor de validación cuantitativa para DerivStrategyLab.

OBJETIVO:
- NO reemplazar risk_backtester.py.
- NO modificar la estrategia Hybrid.
- Validar estrategias mediante división cronológica:
      70% DEVELOPMENT
      30% OUT-OF-SAMPLE (OOS)
- Analizar LONG y SHORT por separado.
- Detectar degradación fuera de muestra.

Esta V2 es exclusivamente para BACKTEST / INVESTIGACIÓN.

Flujo:
    V2.0 -> Development / OOS
    V2.1 -> Walk-forward
    V2.2 -> Stress spread/slippage
    V2.3 -> Sensibilidad de parámetros
    V2.4 -> Monte Carlo
    V2.5 -> Robustez por régimen
    V2.6 -> Directional Ablation
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List
import random

from strategy.registry import run_registered_backtest, generate_registered_signals
from backtesting.risk_backtester import run_backtest_risk
from strategy.strategy_engine import BUY, SELL, HOLD
from strategy.market_structure import analizar_estructura
from indicators.rsi import calculate_rsi
from indicators.atr import calculate_atr
from indicators.ema import calculate_ema
from indicators.adx import calculate_adx
from utils.timeframes import aggregate_ohlc_to_timeframe

from config import (
    SPREAD_ATR_FRAC,
    BACKTEST_SLIPPAGE_ATR_MAX,
    BACKTEST_COMMISSION_PER_TRADE_USD,
    SL_ATR_MULT,
    REWARD_RATIO,
    RIESGO_POR_OPERACION_PCT,
    ATR_PERIOD,
    HTF_GRANULARITY,
    INITIAL_BALANCE,
    MAX_DRAWDOWN_STOP_PCT,
    BACKTEST_RANDOM_SEED,
)


# ============================================================
# CONFIGURACIÓN
# ============================================================

DEFAULT_DEVELOPMENT_RATIO = 0.70

MIN_CANDLES_TOTAL = 1000
MIN_CANDLES_PER_SPLIT = 220


# ============================================================
# ESTRUCTURAS
# ============================================================

@dataclass
class DatasetSplit:
    """
    Representa una división cronológica del histórico.
    """

    development_epochs: List
    development_opens: List
    development_highs: List
    development_lows: List
    development_closes: List

    oos_epochs: List
    oos_opens: List
    oos_highs: List
    oos_lows: List
    oos_closes: List

    split_index: int
    development_ratio: float


# ============================================================
# VALIDACIÓN DE DATOS
# ============================================================

def validate_ohlc_data(
    epochs,
    opens,
    highs,
    lows,
    closes,
):
    """
    Verifica que las series OHLC sean utilizables.
    """

    lengths = {
        len(epochs),
        len(opens),
        len(highs),
        len(lows),
        len(closes),
    }

    if len(lengths) != 1:
        raise ValueError(
            "epochs, opens, highs, lows y closes "
            "deben tener exactamente la misma longitud."
        )

    total = len(closes)

    if total < MIN_CANDLES_TOTAL:
        raise ValueError(
            f"Solo hay {total} velas. "
            f"Risk Backtester V2 requiere al menos "
            f"{MIN_CANDLES_TOTAL} velas."
        )

    for i in range(total):
        o = opens[i]
        h = highs[i]
        l = lows[i]
        c = closes[i]

        if None in (o, h, l, c):
            raise ValueError(
                f"Datos OHLC incompletos en índice {i}."
            )

        if h < l:
            raise ValueError(
                f"High menor que Low en índice {i}."
            )

        if h < max(o, c):
            raise ValueError(
                f"High inválido en índice {i}."
            )

        if l > min(o, c):
            raise ValueError(
                f"Low inválido en índice {i}."
            )


# ============================================================
# SPLIT DEVELOPMENT / OOS
# ============================================================

def chronological_split(
    epochs,
    opens,
    highs,
    lows,
    closes,
    development_ratio=DEFAULT_DEVELOPMENT_RATIO,
):
    """
    Divide los datos SIN barajarlos.

    Ejemplo:

        40,000 velas

        0 ---------------- 27,999 | 28,000 -------- 39,999
             DEVELOPMENT             OOS
                 70%                 30%

    Esto es crítico:
    los datos futuros nunca deben mezclarse con los datos pasados.
    """

    validate_ohlc_data(
        epochs,
        opens,
        highs,
        lows,
        closes,
    )

    if not 0.50 <= development_ratio <= 0.90:
        raise ValueError(
            "development_ratio debe estar entre 0.50 y 0.90."
        )

    total = len(closes)

    split_index = int(
        total * development_ratio
    )

    development_size = split_index
    oos_size = total - split_index

    if development_size < MIN_CANDLES_PER_SPLIT:
        raise ValueError(
            "El bloque DEVELOPMENT tiene muy pocas velas."
        )

    if oos_size < MIN_CANDLES_PER_SPLIT:
        raise ValueError(
            "El bloque OOS tiene muy pocas velas."
        )

    return DatasetSplit(
        development_epochs=epochs[:split_index],
        development_opens=opens[:split_index],
        development_highs=highs[:split_index],
        development_lows=lows[:split_index],
        development_closes=closes[:split_index],

        oos_epochs=epochs[split_index:],
        oos_opens=opens[split_index:],
        oos_highs=highs[split_index:],
        oos_lows=lows[split_index:],
        oos_closes=closes[split_index:],

        split_index=split_index,
        development_ratio=development_ratio,
    )


# ============================================================
# MÉTRICAS POR DIRECCIÓN
# ============================================================

def _side_metrics(
    trades: List[Dict[str, Any]],
    direction: str,
):
    """
    Calcula métricas descriptivas para BUY o SELL.

    No modifica las operaciones ni vuelve a simularlas.
    """

    direction = str(direction).upper()

    selected = [
        trade
        for trade in trades
        if str(
            trade.get("direction", "")
        ).upper() == direction
    ]

    total = len(selected)

    if total == 0:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "net_profit": 0.0,
            "profit_factor": 0.0,
            "average_profit": 0.0,
            "average_r": 0.0,
        }

    winners = [
        trade
        for trade in selected
        if float(trade.get("profit", 0.0)) > 0
    ]

    losers = [
        trade
        for trade in selected
        if float(trade.get("profit", 0.0)) <= 0
    ]

    gross_profit = sum(
        float(trade.get("profit", 0.0))
        for trade in winners
    )

    gross_loss = abs(
        sum(
            float(trade.get("profit", 0.0))
            for trade in losers
        )
    )

    net_profit = sum(
        float(trade.get("profit", 0.0))
        for trade in selected
    )

    win_rate = (
        len(winners) / total * 100
    )

    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0
        else float("inf")
    )

    r_values = [
        float(trade.get("r_multiple", 0.0))
        for trade in selected
        if trade.get("r_multiple") is not None
    ]

    average_r = (
        sum(r_values) / len(r_values)
        if r_values
        else 0.0
    )

    return {
        "total_trades": total,
        "wins": len(winners),
        "losses": len(losers),
        "win_rate": win_rate,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "net_profit": net_profit,
        "profit_factor": profit_factor,
        "average_profit": net_profit / total,
        "average_r": average_r,
    }


# ============================================================
# EJECUCIÓN DE UN BLOQUE
# ============================================================

def _run_period(
    strategy_id,
    epochs,
    opens,
    highs,
    lows,
    closes,
    granularity,
    backtest_overrides=None,
):
    """
    Ejecuta una estrategia registrada sobre un período concreto.
    """

    result = run_registered_backtest(
        strategy_id,
        epochs,
        highs,
        lows,
        closes,
        granularity,
        opens=opens,
        backtest_overrides=backtest_overrides,
    )

    trades = result.get(
        "trades",
        [],
    )

    metrics = result.get(
        "metrics",
        {},
    )

    return {
        "metrics": metrics,

        "long": _side_metrics(
            trades,
            "BUY",
        ),

        "short": _side_metrics(
            trades,
            "SELL",
        ),

        "trades": trades,
    }


# ============================================================
# DEGRADACIÓN OOS
# ============================================================

def _percentage_change(
    development_value,
    oos_value,
):
    """
    Cambio relativo de DEVELOPMENT hacia OOS.

    Negativo = degradación.
    Positivo = mejora.
    """

    try:
        development_value = float(
            development_value
        )

        oos_value = float(
            oos_value
        )

    except (TypeError, ValueError):
        return None

    if development_value == 0:
        return None

    return (
        (oos_value - development_value)
        / abs(development_value)
        * 100
    )


def _build_stability_report(
    development_metrics,
    oos_metrics,
):
    """
    Compara las métricas principales entre DEVELOPMENT y OOS.
    """

    fields = [
        "win_rate",
        "profit_factor",
        "max_drawdown_pct",
        "total_return_pct",
        "r_multiple_promedio",
    ]

    degradation = {}

    for field in fields:
        if (
            field in development_metrics
            and field in oos_metrics
        ):
            degradation[field] = _percentage_change(
                development_metrics[field],
                oos_metrics[field],
            )

    development_pf = float(
        development_metrics.get(
            "profit_factor",
            0.0,
        )
        or 0.0
    )

    oos_pf = float(
        oos_metrics.get(
            "profit_factor",
            0.0,
        )
        or 0.0
    )

    development_wr = float(
        development_metrics.get(
            "win_rate",
            0.0,
        )
        or 0.0
    )

    oos_wr = float(
        oos_metrics.get(
            "win_rate",
            0.0,
        )
        or 0.0
    )

    development_trades = int(
        development_metrics.get(
            "total_trades",
            0,
        )
        or 0
    )

    oos_trades = int(
        oos_metrics.get(
            "total_trades",
            0,
        )
        or 0
    )

    # --------------------------------------------------------
    # Diagnóstico simple.
    #
    # NO significa que una estrategia sea rentable.
    # Solo sirve para detectar degradación evidente.
    # --------------------------------------------------------

    if oos_trades < 20:
        status = "INSUFFICIENT_OOS_TRADES"

    elif oos_pf < 1.0:
        status = "FAILED_OOS"

    elif (
        development_pf > 0
        and oos_pf < development_pf * 0.50
    ):
        status = "SEVERE_DEGRADATION"

    elif abs(
        oos_wr - development_wr
    ) > 20:
        status = "UNSTABLE"

    else:
        status = "OOS_SURVIVED"

    return {
        "status": status,
        "degradation_pct": degradation,
        "development_trades": development_trades,
        "oos_trades": oos_trades,
    }


# ============================================================
# MOTOR PRINCIPAL V2
# ============================================================

def run_risk_backtest_v2(
    strategy_id,
    epochs,
    opens,
    highs,
    lows,
    closes,
    granularity,
    development_ratio=DEFAULT_DEVELOPMENT_RATIO,
):
    """
    Ejecuta la validación DEVELOPMENT / OOS.

    IMPORTANTE:

    No optimiza parámetros.
    No modifica la estrategia.
    No mezcla datos futuros con datos pasados.
    """

    split = chronological_split(
        epochs=epochs,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        development_ratio=development_ratio,
    )

    # --------------------------------------------------------
    # DEVELOPMENT
    # --------------------------------------------------------

    development_result = _run_period(
        strategy_id=strategy_id,

        epochs=split.development_epochs,
        opens=split.development_opens,
        highs=split.development_highs,
        lows=split.development_lows,
        closes=split.development_closes,

        granularity=granularity,
    )

    # --------------------------------------------------------
    # OUT OF SAMPLE
    # --------------------------------------------------------

    oos_result = _run_period(
        strategy_id=strategy_id,

        epochs=split.oos_epochs,
        opens=split.oos_opens,
        highs=split.oos_highs,
        lows=split.oos_lows,
        closes=split.oos_closes,

        granularity=granularity,
    )

    stability = _build_stability_report(
        development_result["metrics"],
        oos_result["metrics"],
    )

    return {
        "strategy": strategy_id,

        "split": {
            "development_ratio": development_ratio,
            "oos_ratio": 1.0 - development_ratio,

            "total_candles": len(closes),

            "development_candles": len(
                split.development_closes
            ),

            "oos_candles": len(
                split.oos_closes
            ),

            "split_index": split.split_index,

            "development_start": (
                split.development_epochs[0]
                if split.development_epochs
                else None
            ),

            "development_end": (
                split.development_epochs[-1]
                if split.development_epochs
                else None
            ),

            "oos_start": (
                split.oos_epochs[0]
                if split.oos_epochs
                else None
            ),

            "oos_end": (
                split.oos_epochs[-1]
                if split.oos_epochs
                else None
            ),
        },

        "development": development_result,

        "oos": oos_result,

        "stability": stability,
    }
    
    # ============================================================
# WALK-FORWARD V2.1
# ============================================================

def _combine_trades(results):
    """
    Une las operaciones de varias ventanas OOS.
    """

    combined = []

    for result in results:
        combined.extend(
            result.get("trades", [])
        )

    return combined


def _aggregate_walk_forward_metrics(window_results):
    """
    Resume todas las ventanas OOS del walk-forward.
    """

    if not window_results:
        return {
            "windows": 0,
            "total_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "average_r": 0.0,
            "long": {},
            "short": {},
        }

    all_trades = _combine_trades(
        [
            window["oos"]
            for window in window_results
        ]
    )

    total = len(all_trades)

    if total == 0:
        return {
            "windows": len(window_results),
            "total_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "average_r": 0.0,
            "long": _side_metrics([], "BUY"),
            "short": _side_metrics([], "SELL"),
        }

    winners = [
        trade
        for trade in all_trades
        if float(trade.get("profit", 0.0)) > 0
    ]

    losers = [
        trade
        for trade in all_trades
        if float(trade.get("profit", 0.0)) <= 0
    ]

    gross_profit = sum(
        float(trade.get("profit", 0.0))
        for trade in winners
    )

    gross_loss = abs(
        sum(
            float(trade.get("profit", 0.0))
            for trade in losers
        )
    )

    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0
        else float("inf")
    )

    r_values = [
        float(trade.get("r_multiple", 0.0))
        for trade in all_trades
        if trade.get("r_multiple") is not None
    ]

    average_r = (
        sum(r_values) / len(r_values)
        if r_values
        else 0.0
    )

    return {
        "windows": len(window_results),
        "total_trades": total,
        "wins": len(winners),
        "losses": len(losers),
        "win_rate": len(winners) / total * 100,
        "profit_factor": profit_factor,
        "average_r": average_r,
        "long": _side_metrics(
            all_trades,
            "BUY",
        ),
        "short": _side_metrics(
            all_trades,
            "SELL",
        ),
    }


def run_walk_forward_v21(
    strategy_id,
    epochs,
    opens,
    highs,
    lows,
    closes,
    granularity,
    development_size=12000,
    oos_size=5000,
    step_size=5000,
):
    """
    Walk-forward V2.1.

    Ejemplo:

        DEV 12000 / OOS 5000 / STEP 5000

        Ventana 1:
        [ DEV 12000 ][ OOS 5000 ]

        Ventana 2:
             [ DEV 12000 ][ OOS 5000 ]

        Ventana 3:
                  [ DEV 12000 ][ OOS 5000 ]

    IMPORTANTE:
    - No optimiza parámetros.
    - Hybrid permanece congelada.
    - Cada OOS está cronológicamente después de su DEV.
    """

    validate_ohlc_data(
        epochs,
        opens,
        highs,
        lows,
        closes,
    )

    total = len(closes)

    if development_size < MIN_CANDLES_PER_SPLIT:
        raise ValueError(
            "development_size demasiado pequeño."
        )

    if oos_size < MIN_CANDLES_PER_SPLIT:
        raise ValueError(
            "oos_size demasiado pequeño."
        )

    if step_size <= 0:
        raise ValueError(
            "step_size debe ser mayor que cero."
        )

    windows = []

    start = 0
    window_number = 1

    while True:
        development_start = start
        development_end = (
            development_start
            + development_size
        )

        oos_start = development_end
        oos_end = (
            oos_start
            + oos_size
        )

        if oos_end > total:
            break

        development_result = _run_period(
            strategy_id=strategy_id,

            epochs=epochs[
                development_start:development_end
            ],
            opens=opens[
                development_start:development_end
            ],
            highs=highs[
                development_start:development_end
            ],
            lows=lows[
                development_start:development_end
            ],
            closes=closes[
                development_start:development_end
            ],

            granularity=granularity,
        )

        oos_result = _run_period(
            strategy_id=strategy_id,

            epochs=epochs[
                oos_start:oos_end
            ],
            opens=opens[
                oos_start:oos_end
            ],
            highs=highs[
                oos_start:oos_end
            ],
            lows=lows[
                oos_start:oos_end
            ],
            closes=closes[
                oos_start:oos_end
            ],

            granularity=granularity,
        )

        stability = _build_stability_report(
            development_result["metrics"],
            oos_result["metrics"],
        )

        windows.append(
            {
                "window": window_number,

                "development_range": {
                    "start_index": development_start,
                    "end_index": development_end - 1,
                    "candles": development_size,
                    "start_epoch": epochs[
                        development_start
                    ],
                    "end_epoch": epochs[
                        development_end - 1
                    ],
                },

                "oos_range": {
                    "start_index": oos_start,
                    "end_index": oos_end - 1,
                    "candles": oos_size,
                    "start_epoch": epochs[
                        oos_start
                    ],
                    "end_epoch": epochs[
                        oos_end - 1
                    ],
                },

                "development": development_result,
                "oos": oos_result,
                "stability": stability,
            }
        )

        window_number += 1
        start += step_size

    aggregate = (
        _aggregate_walk_forward_metrics(
            windows
        )
    )

    return {
        "strategy": strategy_id,

        "configuration": {
            "development_size": development_size,
            "oos_size": oos_size,
            "step_size": step_size,
            "total_candles": total,
        },

        "windows": windows,

        "aggregate_oos": aggregate,
    }
    

# ============================================================
# STRESS TEST V2.2
# ============================================================

STRESS_SCENARIOS_V22 = {
    "BASE": {
        "spread_atr_frac": SPREAD_ATR_FRAC,
        "slippage_atr_max": BACKTEST_SLIPPAGE_ATR_MAX,
        "commission_per_trade": BACKTEST_COMMISSION_PER_TRADE_USD,
    },

    "MODERATE": {
        "spread_atr_frac": SPREAD_ATR_FRAC * 1.25,
        "slippage_atr_max": BACKTEST_SLIPPAGE_ATR_MAX * 1.25,
        "commission_per_trade": BACKTEST_COMMISSION_PER_TRADE_USD,
    },

    "HIGH": {
        "spread_atr_frac": SPREAD_ATR_FRAC * 1.50,
        "slippage_atr_max": BACKTEST_SLIPPAGE_ATR_MAX * 1.50,
        "commission_per_trade": BACKTEST_COMMISSION_PER_TRADE_USD,
    },

    "EXTREME": {
        "spread_atr_frac": SPREAD_ATR_FRAC * 2.00,
        "slippage_atr_max": BACKTEST_SLIPPAGE_ATR_MAX * 2.00,
        "commission_per_trade": BACKTEST_COMMISSION_PER_TRADE_USD,
    },
}


def run_stress_test_v22(
    strategy_id,
    epochs,
    opens,
    highs,
    lows,
    closes,
    granularity,
):
    """
    V2.2 - Stress test de costos de ejecución.

    La estrategia permanece congelada.

    Solo cambian:
    - spread
    - slippage

    El random_seed permanece fijo para que las comparaciones
    entre escenarios sean reproducibles.
    """

    validate_ohlc_data(
        epochs,
        opens,
        highs,
        lows,
        closes,
    )

    scenarios = {}

    for scenario_name, overrides in (
        STRESS_SCENARIOS_V22.items()
    ):
        result = _run_period(
            strategy_id=strategy_id,
            epochs=epochs,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            granularity=granularity,
            backtest_overrides=overrides,
        )

        scenarios[scenario_name] = {
            "configuration": dict(overrides),
            "result": result,
        }

    base_metrics = (
        scenarios["BASE"]["result"]["metrics"]
    )

    comparison = {}

    fields = [
        "win_rate",
        "profit_factor",
        "max_drawdown_pct",
        "total_return_pct",
        "r_multiple_promedio",
    ]

    for scenario_name, scenario in scenarios.items():
        metrics = scenario["result"]["metrics"]

        scenario_changes = {}

        for field in fields:
            scenario_changes[field] = (
                _percentage_change(
                    base_metrics.get(field, 0),
                    metrics.get(field, 0),
                )
            )

        comparison[scenario_name] = (
            scenario_changes
        )

    return {
        "strategy": strategy_id,
        "total_candles": len(closes),
        "scenarios": scenarios,
        "comparison_vs_base": comparison,
    }

# ============================================================
# SENSITIVITY TEST V2.3
# ============================================================

SENSITIVITY_FACTORS_V23 = (
    0.80,
    1.00,
    1.20,
)


def run_sensitivity_test_v23(
    strategy_id,
    epochs,
    opens,
    highs,
    lows,
    closes,
    granularity,
    development_ratio=DEFAULT_DEVELOPMENT_RATIO,
):
    """
    V2.3 - Sensibilidad de parámetros.

    OBJETIVO:
    Comprobar si el resultado depende demasiado de un valor
    exacto de SL o RR.

    IMPORTANTE:
    - Solo utiliza DEVELOPMENT.
    - OOS permanece completamente intacto.
    - No optimiza parámetros.
    - No modifica las reglas de entrada de Hybrid.
    - El riesgo por operación permanece congelado.

    Parámetros probados:

        stop_atr_mult
        reward_ratio

    Variaciones:

        -20%
         BASE
        +20%
    """

    split = chronological_split(
        epochs=epochs,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        development_ratio=development_ratio,
    )

    results = []

    scenario_number = 1

    for stop_factor in SENSITIVITY_FACTORS_V23:

        for rr_factor in SENSITIVITY_FACTORS_V23:

            stop_value = (
                SL_ATR_MULT
                * stop_factor
            )

            reward_value = (
                REWARD_RATIO
                * rr_factor
            )

            overrides = {
                "stop_atr_mult": stop_value,
                "reward_ratio": reward_value,
            }

            result = _run_period(
                strategy_id=strategy_id,

                epochs=split.development_epochs,
                opens=split.development_opens,
                highs=split.development_highs,
                lows=split.development_lows,
                closes=split.development_closes,

                granularity=granularity,

                backtest_overrides=overrides,
            )

            metrics = result["metrics"]

            is_baseline = (
                stop_factor == 1.00
                and rr_factor == 1.00
            )

            results.append(
                {
                    "scenario": scenario_number,

                    "baseline": is_baseline,

                    "configuration": {
                        "stop_atr_mult": stop_value,
                        "reward_ratio": reward_value,
                        "stop_factor": stop_factor,
                        "reward_factor": rr_factor,
                    },

                    "metrics": metrics,

                    "long": result["long"],
                    "short": result["short"],
                }
            )

            scenario_number += 1

    # ========================================================
    # BASELINE
    # ========================================================

    baseline_result = next(
        item
        for item in results
        if item["baseline"]
    )

    baseline_metrics = (
        baseline_result["metrics"]
    )

    # ========================================================
    # COMPARACIÓN VS BASE
    # ========================================================

    comparison_fields = [
        "win_rate",
        "profit_factor",
        "max_drawdown_pct",
        "total_return_pct",
        "r_multiple_promedio",
    ]

    for item in results:

        changes = {}

        for field in comparison_fields:

            changes[field] = (
                _percentage_change(
                    baseline_metrics.get(
                        field,
                        0,
                    ),
                    item["metrics"].get(
                        field,
                        0,
                    ),
                )
            )

        item["comparison_vs_base"] = (
            changes
        )

    # ========================================================
    # RESUMEN
    # ========================================================

    profit_factors = [
        float(
            item["metrics"].get(
                "profit_factor",
                0.0,
            )
            or 0.0
        )
        for item in results
    ]

    win_rates = [
        float(
            item["metrics"].get(
                "win_rate",
                0.0,
            )
            or 0.0
        )
        for item in results
    ]

    drawdowns = [
        float(
            item["metrics"].get(
                "max_drawdown_pct",
                0.0,
            )
            or 0.0
        )
        for item in results
    ]

    average_rs = [
        float(
            item["metrics"].get(
                "r_multiple_promedio",
                0.0,
            )
            or 0.0
        )
        for item in results
    ]

    profitable_scenarios = sum(
        1
        for pf in profit_factors
        if pf > 1.0
    )

    return {
        "strategy": strategy_id,

        "development_only": True,

        "configuration": {
            "development_ratio": development_ratio,
            "development_candles": len(
                split.development_closes
            ),

            "baseline_stop_atr_mult": (
                SL_ATR_MULT
            ),

            "baseline_reward_ratio": (
                REWARD_RATIO
            ),

            "factors": list(
                SENSITIVITY_FACTORS_V23
            ),
        },

        "baseline": baseline_result,

        "scenarios": results,

        "summary": {
            "total_scenarios": len(
                results
            ),

            "profitable_scenarios": (
                profitable_scenarios
            ),

            "profit_factor_min": min(
                profit_factors
            ),

            "profit_factor_max": max(
                profit_factors
            ),

            "win_rate_min": min(
                win_rates
            ),

            "win_rate_max": max(
                win_rates
            ),

            "drawdown_min": min(
                drawdowns
            ),

            "drawdown_max": max(
                drawdowns
            ),

            "average_r_min": min(
                average_rs
            ),

            "average_r_max": max(
                average_rs
            ),
        },
    }

# ============================================================
# MONTE CARLO V2.4
# ============================================================

MONTE_CARLO_ITERATIONS_V24 = 10000
MONTE_CARLO_SEED_V24 = 20260830


def _percentile(values, percentile):
    """
    Percentil lineal simple sin dependencias externas.
    """
    if not values:
        return 0.0

    ordered = sorted(float(v) for v in values)

    if len(ordered) == 1:
        return ordered[0]

    position = (len(ordered) - 1) * (float(percentile) / 100.0)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower

    return (
        ordered[lower] * (1.0 - weight)
        + ordered[upper] * weight
    )


def _max_losing_streak(r_values):
    """
    Mayor racha consecutiva de operaciones con R <= 0.
    """
    worst = 0
    current = 0

    for r_value in r_values:
        if float(r_value) <= 0.0:
            current += 1
            worst = max(worst, current)
        else:
            current = 0

    return worst


def _simulate_r_path(
    r_values,
    risk_pct,
    rng,
):
    """
    Bootstrap con reemplazo de la secuencia de R.

    La curva comienza en 100 unidades normalizadas.
    Cada operación arriesga el mismo porcentaje de equity
    definido por la configuración base.

    Esto NO predice el futuro. Solo estudia incertidumbre
    de secuencia a partir de las operaciones históricas.
    """
    sampled = [
        rng.choice(r_values)
        for _ in range(len(r_values))
    ]

    equity = 100.0
    peak = equity
    max_drawdown_pct = 0.0

    for r_value in sampled:
        trade_return = (
            float(r_value)
            * float(risk_pct)
            / 100.0
        )

        equity *= max(
            0.0,
            1.0 + trade_return,
        )

        peak = max(peak, equity)

        if peak > 0:
            drawdown = (
                (peak - equity)
                / peak
                * 100.0
            )
            max_drawdown_pct = max(
                max_drawdown_pct,
                drawdown,
            )

    return {
        "final_equity": equity,
        "total_return_pct": equity - 100.0,
        "max_drawdown_pct": max_drawdown_pct,
        "max_losing_streak": _max_losing_streak(sampled),
    }


def run_monte_carlo_v24(
    strategy_id,
    epochs,
    opens,
    highs,
    lows,
    closes,
    granularity,
    development_ratio=DEFAULT_DEVELOPMENT_RATIO,
    iterations=MONTE_CARLO_ITERATIONS_V24,
    random_seed=MONTE_CARLO_SEED_V24,
):
    """
    V2.4 - Monte Carlo por bootstrap de operaciones.

    REGLAS:
    - Usa DEVELOPMENT únicamente.
    - OOS permanece intacto.
    - No cambia parámetros de Hybrid.
    - No busca el mejor resultado.
    - Remuestrea con reemplazo los R-multiples observados.
    - Usa una semilla fija para reproducibilidad.
    """

    if int(iterations) < 100:
        raise ValueError(
            "Monte Carlo requiere al menos 100 iteraciones."
        )

    split = chronological_split(
        epochs=epochs,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        development_ratio=development_ratio,
    )

    development_result = _run_period(
        strategy_id=strategy_id,
        epochs=split.development_epochs,
        opens=split.development_opens,
        highs=split.development_highs,
        lows=split.development_lows,
        closes=split.development_closes,
        granularity=granularity,
    )

    trades = development_result["trades"]

    r_values = [
        float(trade["r_multiple"])
        for trade in trades
        if trade.get("r_multiple") is not None
    ]

    if len(r_values) < 20:
        raise ValueError(
            "Monte Carlo V2.4 requiere al menos 20 trades "
            "con r_multiple en DEVELOPMENT."
        )

    rng = random.Random(
        int(random_seed)
    )

    simulations = [
        _simulate_r_path(
            r_values=r_values,
            risk_pct=RIESGO_POR_OPERACION_PCT,
            rng=rng,
        )
        for _ in range(int(iterations))
    ]

    returns = [
        item["total_return_pct"]
        for item in simulations
    ]

    drawdowns = [
        item["max_drawdown_pct"]
        for item in simulations
    ]

    losing_streaks = [
        item["max_losing_streak"]
        for item in simulations
    ]

    losing_runs = sum(
        1
        for value in returns
        if value < 0.0
    )

    return {
        "strategy": strategy_id,
        "development_only": True,

        "configuration": {
            "development_ratio": development_ratio,
            "development_candles": len(
                split.development_closes
            ),
            "source_trades": len(r_values),
            "iterations": int(iterations),
            "random_seed": int(random_seed),
            "risk_pct": float(
                RIESGO_POR_OPERACION_PCT
            ),
            "method": "bootstrap_with_replacement_r_multiple",
        },

        "source": {
            "metrics": development_result["metrics"],
            "long": development_result["long"],
            "short": development_result["short"],
            "average_r": (
                sum(r_values) / len(r_values)
            ),
        },

        "distribution": {
            "return_pct": {
                "p05": _percentile(returns, 5),
                "p25": _percentile(returns, 25),
                "median": _percentile(returns, 50),
                "p75": _percentile(returns, 75),
                "p95": _percentile(returns, 95),
                "min": min(returns),
                "max": max(returns),
            },

            "max_drawdown_pct": {
                "p50": _percentile(drawdowns, 50),
                "p75": _percentile(drawdowns, 75),
                "p90": _percentile(drawdowns, 90),
                "p95": _percentile(drawdowns, 95),
                "p99": _percentile(drawdowns, 99),
                "max": max(drawdowns),
            },

            "max_losing_streak": {
                "p50": _percentile(losing_streaks, 50),
                "p90": _percentile(losing_streaks, 90),
                "p95": _percentile(losing_streaks, 95),
                "p99": _percentile(losing_streaks, 99),
                "max": max(losing_streaks),
            },

            "probability_negative_return_pct": (
                losing_runs
                / len(simulations)
                * 100.0
            ),
        },
    }

# ============================================================
# REGIME ROBUSTNESS V2.5
# ============================================================

REGIME_ATR_PERIOD_V25 = 14
REGIME_TREND_LOOKBACK_V25 = 50
REGIME_VOL_LOOKBACK_V25 = 200
REGIME_TREND_THRESHOLD_V25 = 1.0


def _rolling_mean_v25(values, period):
    """
    Media móvil simple causal.
    Solo utiliza información disponible hasta la vela actual.
    """
    period = int(period)
    result = [None] * len(values)

    if period <= 0:
        raise ValueError("period debe ser mayor que cero.")

    running_sum = 0.0

    for i, value in enumerate(values):
        running_sum += float(value)

        if i >= period:
            running_sum -= float(values[i - period])

        if i >= period - 1:
            result[i] = running_sum / period

    return result


def _atr_series_v25(
    highs,
    lows,
    closes,
    period=REGIME_ATR_PERIOD_V25,
):
    """
    ATR causal para clasificación de régimen.
    """
    true_ranges = []

    for i in range(len(closes)):
        high = float(highs[i])
        low = float(lows[i])

        if i == 0:
            tr = high - low
        else:
            previous_close = float(closes[i - 1])
            tr = max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )

        true_ranges.append(max(0.0, tr))

    return _rolling_mean_v25(
        true_ranges,
        period,
    )


def _regime_labels_v25(
    highs,
    lows,
    closes,
    atr_period=REGIME_ATR_PERIOD_V25,
    trend_lookback=REGIME_TREND_LOOKBACK_V25,
    vol_lookback=REGIME_VOL_LOOKBACK_V25,
    trend_threshold=REGIME_TREND_THRESHOLD_V25,
):
    """
    Clasifica cada vela usando únicamente datos pasados/presentes.

    Tendencia:
        abs(close - close[t-lookback]) / ATR actual

    Volatilidad:
        ATR actual frente a su media causal de vol_lookback.

    Resultado:
        TREND_HIGH_VOL
        TREND_LOW_VOL
        RANGE_HIGH_VOL
        RANGE_LOW_VOL
        WARMUP
    """
    atr_values = _atr_series_v25(
        highs,
        lows,
        closes,
        period=atr_period,
    )

    atr_for_mean = [
        float(value) if value is not None else 0.0
        for value in atr_values
    ]

    atr_mean = _rolling_mean_v25(
        atr_for_mean,
        vol_lookback,
    )

    labels = ["WARMUP"] * len(closes)

    warmup = max(
        atr_period - 1,
        trend_lookback,
        vol_lookback - 1,
    )

    for i in range(warmup, len(closes)):
        atr = atr_values[i]
        avg_atr = atr_mean[i]

        if (
            atr is None
            or avg_atr is None
            or float(atr) <= 0.0
        ):
            continue

        displacement = abs(
            float(closes[i])
            - float(closes[i - trend_lookback])
        )

        trend_score = (
            displacement
            / float(atr)
        )

        trend_state = (
            "TREND"
            if trend_score >= float(trend_threshold)
            else "RANGE"
        )

        volatility_state = (
            "HIGH_VOL"
            if float(atr) >= float(avg_atr)
            else "LOW_VOL"
        )

        labels[i] = (
            f"{trend_state}_{volatility_state}"
        )

    return labels


def _trade_metrics_v25(trades):
    """
    Métricas descriptivas para un grupo de trades ya ejecutados.
    No vuelve a simular ni modifica la estrategia.
    """
    total = len(trades)

    if total == 0:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "net_profit": 0.0,
            "average_r": 0.0,
            "long": _side_metrics([], "BUY"),
            "short": _side_metrics([], "SELL"),
        }

    winners = [
        trade
        for trade in trades
        if float(trade.get("profit", 0.0)) > 0.0
    ]

    losers = [
        trade
        for trade in trades
        if float(trade.get("profit", 0.0)) <= 0.0
    ]

    gross_profit = sum(
        float(trade.get("profit", 0.0))
        for trade in winners
    )

    gross_loss = abs(
        sum(
            float(trade.get("profit", 0.0))
            for trade in losers
        )
    )

    net_profit = sum(
        float(trade.get("profit", 0.0))
        for trade in trades
    )

    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0.0
        else float("inf")
    )

    r_values = [
        float(trade.get("r_multiple", 0.0))
        for trade in trades
        if trade.get("r_multiple") is not None
    ]

    average_r = (
        sum(r_values) / len(r_values)
        if r_values
        else 0.0
    )

    return {
        "total_trades": total,
        "wins": len(winners),
        "losses": len(losers),
        "win_rate": len(winners) / total * 100.0,
        "profit_factor": profit_factor,
        "net_profit": net_profit,
        "average_r": average_r,
        "long": _side_metrics(trades, "BUY"),
        "short": _side_metrics(trades, "SELL"),
    }


def run_regime_test_v25(
    strategy_id,
    epochs,
    opens,
    highs,
    lows,
    closes,
    granularity,
    development_ratio=DEFAULT_DEVELOPMENT_RATIO,
    atr_period=REGIME_ATR_PERIOD_V25,
    trend_lookback=REGIME_TREND_LOOKBACK_V25,
    vol_lookback=REGIME_VOL_LOOKBACK_V25,
    trend_threshold=REGIME_TREND_THRESHOLD_V25,
):
    """
    V2.5 - Robustez por régimen.

    PRINCIPIOS:
    - Clasificación causal: no usa velas futuras.
    - Hybrid permanece congelada.
    - No selecciona ni bloquea operaciones.
    - No optimiza parámetros.
    - DEVELOPMENT y OOS se reportan por separado.
    - Cada trade se clasifica por el régimen existente
      en su entry_epoch.
    """

    split = chronological_split(
        epochs=epochs,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        development_ratio=development_ratio,
    )

    development_result = _run_period(
        strategy_id=strategy_id,
        epochs=split.development_epochs,
        opens=split.development_opens,
        highs=split.development_highs,
        lows=split.development_lows,
        closes=split.development_closes,
        granularity=granularity,
    )

    oos_result = _run_period(
        strategy_id=strategy_id,
        epochs=split.oos_epochs,
        opens=split.oos_opens,
        highs=split.oos_highs,
        lows=split.oos_lows,
        closes=split.oos_closes,
        granularity=granularity,
    )

    regime_names = [
        "TREND_HIGH_VOL",
        "TREND_LOW_VOL",
        "RANGE_HIGH_VOL",
        "RANGE_LOW_VOL",
        "WARMUP",
        "UNMATCHED",
    ]

    def analyze_period(
        period_epochs,
        period_highs,
        period_lows,
        period_closes,
        period_result,
    ):
        labels = _regime_labels_v25(
            highs=period_highs,
            lows=period_lows,
            closes=period_closes,
            atr_period=atr_period,
            trend_lookback=trend_lookback,
            vol_lookback=vol_lookback,
            trend_threshold=trend_threshold,
        )

        epoch_to_label = {
            epoch: labels[i]
            for i, epoch in enumerate(period_epochs)
        }

        grouped = {
            name: []
            for name in regime_names
        }

        for trade in period_result["trades"]:
            entry_epoch = trade.get("entry_epoch")
            label = epoch_to_label.get(
                entry_epoch,
                "UNMATCHED",
            )

            if label not in grouped:
                label = "UNMATCHED"

            grouped[label].append(trade)

        regimes = {
            name: _trade_metrics_v25(grouped[name])
            for name in regime_names
        }

        return {
            "overall": {
                "metrics": period_result["metrics"],
                "long": period_result["long"],
                "short": period_result["short"],
            },
            "regimes": regimes,
        }

    development_analysis = analyze_period(
        split.development_epochs,
        split.development_highs,
        split.development_lows,
        split.development_closes,
        development_result,
    )

    oos_analysis = analyze_period(
        split.oos_epochs,
        split.oos_highs,
        split.oos_lows,
        split.oos_closes,
        oos_result,
    )

    return {
        "strategy": strategy_id,
        "configuration": {
            "development_ratio": development_ratio,
            "atr_period": int(atr_period),
            "trend_lookback": int(trend_lookback),
            "vol_lookback": int(vol_lookback),
            "trend_threshold": float(trend_threshold),
            "classification": (
                "trend=abs(close-close[t-lookback])/ATR; "
                "high_vol=ATR>=causal_ATR_mean"
            ),
        },
        "split": {
            "development_candles": len(
                split.development_closes
            ),
            "oos_candles": len(
                split.oos_closes
            ),
        },
        "development": development_analysis,
        "oos": oos_analysis,
    }

# ============================================================
# DIRECTIONAL ABLATION V2.6
# ============================================================

def _direction_subset_v26(
    trades,
    mode,
):
    """
    Selecciona trades existentes sin modificar Hybrid.

    mode:
        ALL
        LONG
        SHORT
    """
    mode = str(mode).upper()

    if mode == "ALL":
        return list(trades)

    if mode == "LONG":
        return [
            trade
            for trade in trades
            if str(
                trade.get("direction", "")
            ).upper() == "BUY"
        ]

    if mode == "SHORT":
        return [
            trade
            for trade in trades
            if str(
                trade.get("direction", "")
            ).upper() == "SELL"
        ]

    raise ValueError(
        "mode debe ser ALL, LONG o SHORT."
    )


def _directional_metrics_v26(
    trades,
    mode,
):
    """
    Métricas descriptivas sobre un subconjunto de trades.

    IMPORTANTE:
    - No vuelve a ejecutar la estrategia.
    - No bloquea señales.
    - No modifica Hybrid.
    """
    selected = _direction_subset_v26(
        trades,
        mode,
    )

    metrics = _trade_metrics_v25(
        selected
    )

    return {
        "mode": str(mode).upper(),
        "total_trades": metrics["total_trades"],
        "wins": metrics["wins"],
        "losses": metrics["losses"],
        "win_rate": metrics["win_rate"],
        "profit_factor": metrics["profit_factor"],
        "net_profit": metrics["net_profit"],
        "average_r": metrics["average_r"],
    }


def _directional_block_v26(
    trades,
):
    """
    Devuelve ALL / LONG / SHORT para el mismo conjunto de trades.
    """
    return {
        "ALL": _directional_metrics_v26(
            trades,
            "ALL",
        ),
        "LONG": _directional_metrics_v26(
            trades,
            "LONG",
        ),
        "SHORT": _directional_metrics_v26(
            trades,
            "SHORT",
        ),
    }


def run_directional_ablation_v26(
    strategy_id,
    epochs,
    opens,
    highs,
    lows,
    closes,
    granularity,
    development_ratio=DEFAULT_DEVELOPMENT_RATIO,
    development_size=12000,
    oos_size=5000,
    step_size=5000,
):
    """
    V2.6 - Directional Ablation.

    OBJETIVO:
    Diagnosticar si la asimetría LONG/SHORT explica parte
    de la inestabilidad observada.

    PRINCIPIOS:
    - Hybrid permanece 100% congelada.
    - No cambia reglas de entrada.
    - No elimina señales en el motor original.
    - No optimiza parámetros.
    - Solo analiza a posteriori los trades ya producidos.
    - DEVELOPMENT y OOS se mantienen separados.
    - También analiza las ventanas OOS del Walk-Forward.
    """

    base = run_risk_backtest_v2(
        strategy_id=strategy_id,
        epochs=epochs,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        granularity=granularity,
        development_ratio=development_ratio,
    )

    walk_forward = run_walk_forward_v21(
        strategy_id=strategy_id,
        epochs=epochs,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        granularity=granularity,
        development_size=development_size,
        oos_size=oos_size,
        step_size=step_size,
    )

    development_trades = (
        base["development"]["trades"]
    )

    oos_trades = (
        base["oos"]["trades"]
    )

    windows = []

    for window in walk_forward["windows"]:
        trades = window["oos"]["trades"]

        directional = _directional_block_v26(
            trades
        )

        windows.append(
            {
                "window": window["window"],
                "oos_range": window["oos_range"],
                "directional": directional,
            }
        )

    aggregate_oos_trades = _combine_trades(
        [
            window["oos"]
            for window in walk_forward["windows"]
        ]
    )

    aggregate_directional = (
        _directional_block_v26(
            aggregate_oos_trades
        )
    )

    # Resumen de consistencia por ventanas.
    consistency = {}

    for mode in ("ALL", "LONG", "SHORT"):
        active = []
        positive = 0
        negative = 0
        neutral = 0

        for window in windows:
            metrics = (
                window["directional"][mode]
            )

            if metrics["total_trades"] <= 0:
                continue

            active.append(
                metrics["average_r"]
            )

            if metrics["average_r"] > 0:
                positive += 1
            elif metrics["average_r"] < 0:
                negative += 1
            else:
                neutral += 1

        consistency[mode] = {
            "active_windows": len(active),
            "positive_windows": positive,
            "negative_windows": negative,
            "neutral_windows": neutral,
            "average_window_r": (
                sum(active) / len(active)
                if active
                else 0.0
            ),
        }

    return {
        "strategy": strategy_id,
        "diagnostic_only": True,

        "configuration": {
            "development_ratio": development_ratio,
            "development_size": development_size,
            "oos_size": oos_size,
            "step_size": step_size,
            "note": (
                "No modifica Hybrid; analiza trades existentes "
                "por dirección."
            ),
        },

        "development": _directional_block_v26(
            development_trades
        ),

        "oos": _directional_block_v26(
            oos_trades
        ),

        "walk_forward": {
            "windows": windows,
            "aggregate_oos": aggregate_directional,
            "consistency": consistency,
        },
    }



# ============================================================
# V2.7 - DIRECTION x REGIME AUDIT
# ============================================================

def _direction_regime_period_v27(
    period_epochs,
    period_highs,
    period_lows,
    period_closes,
    period_result,
    atr_period=REGIME_ATR_PERIOD_V25,
    trend_lookback=REGIME_TREND_LOOKBACK_V25,
    vol_lookback=REGIME_VOL_LOOKBACK_V25,
    trend_threshold=REGIME_TREND_THRESHOLD_V25,
):
    """
    Clasifica trades ya ejecutados por régimen y dirección.

    Diagnóstico puro:
    - no filtra señales;
    - no cambia Hybrid;
    - no vuelve a ejecutar LONG-only/SHORT-only;
    - no optimiza parámetros.
    """

    regime_names = [
        "TREND_HIGH_VOL",
        "TREND_LOW_VOL",
        "RANGE_HIGH_VOL",
        "RANGE_LOW_VOL",
        "WARMUP",
        "UNMATCHED",
    ]

    labels = _regime_labels_v25(
        highs=period_highs,
        lows=period_lows,
        closes=period_closes,
        atr_period=atr_period,
        trend_lookback=trend_lookback,
        vol_lookback=vol_lookback,
        trend_threshold=trend_threshold,
    )

    epoch_to_label = {
        epoch: labels[i]
        for i, epoch in enumerate(period_epochs)
    }

    grouped = {
        name: []
        for name in regime_names
    }

    for trade in period_result["trades"]:
        entry_epoch = trade.get("entry_epoch")
        label = epoch_to_label.get(entry_epoch, "UNMATCHED")

        if label not in grouped:
            label = "UNMATCHED"

        grouped[label].append(trade)

    matrix = {}

    for regime_name in regime_names:
        regime_trades = grouped[regime_name]
        matrix[regime_name] = {
            "ALL": _directional_metrics_v26(regime_trades, "ALL"),
            "LONG": _directional_metrics_v26(regime_trades, "LONG"),
            "SHORT": _directional_metrics_v26(regime_trades, "SHORT"),
        }

    return {
        "overall": _directional_block_v26(period_result["trades"]),
        "matrix": matrix,
    }


def run_direction_regime_audit_v27(
    strategy_id,
    epochs,
    opens,
    highs,
    lows,
    closes,
    granularity,
    development_ratio=DEFAULT_DEVELOPMENT_RATIO,
    atr_period=REGIME_ATR_PERIOD_V25,
    trend_lookback=REGIME_TREND_LOOKBACK_V25,
    vol_lookback=REGIME_VOL_LOOKBACK_V25,
    trend_threshold=REGIME_TREND_THRESHOLD_V25,
):
    """
    V2.7 - Direction x Regime Audit.

    Objetivo:
    separar la contribución de LONG y SHORT dentro de cada
    régimen sin alterar una sola señal de la estrategia Hybrid.

    Esta prueba es descriptiva y post-hoc. No constituye una
    simulación contrafactual de una estrategia con direcciones
    bloqueadas.
    """

    split = chronological_split(
        epochs=epochs,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        development_ratio=development_ratio,
    )

    development_result = _run_period(
        strategy_id=strategy_id,
        epochs=split.development_epochs,
        opens=split.development_opens,
        highs=split.development_highs,
        lows=split.development_lows,
        closes=split.development_closes,
        granularity=granularity,
    )

    oos_result = _run_period(
        strategy_id=strategy_id,
        epochs=split.oos_epochs,
        opens=split.oos_opens,
        highs=split.oos_highs,
        lows=split.oos_lows,
        closes=split.oos_closes,
        granularity=granularity,
    )

    development = _direction_regime_period_v27(
        period_epochs=split.development_epochs,
        period_highs=split.development_highs,
        period_lows=split.development_lows,
        period_closes=split.development_closes,
        period_result=development_result,
        atr_period=atr_period,
        trend_lookback=trend_lookback,
        vol_lookback=vol_lookback,
        trend_threshold=trend_threshold,
    )

    oos = _direction_regime_period_v27(
        period_epochs=split.oos_epochs,
        period_highs=split.oos_highs,
        period_lows=split.oos_lows,
        period_closes=split.oos_closes,
        period_result=oos_result,
        atr_period=atr_period,
        trend_lookback=trend_lookback,
        vol_lookback=vol_lookback,
        trend_threshold=trend_threshold,
    )

    return {
        "strategy": strategy_id,
        "diagnostic_only": True,
        "configuration": {
            "development_ratio": development_ratio,
            "atr_period": int(atr_period),
            "trend_lookback": int(trend_lookback),
            "vol_lookback": int(vol_lookback),
            "trend_threshold": float(trend_threshold),
            "classification": (
                "trend=abs(close-close[t-lookback])/ATR; "
                "high_vol=ATR>=causal_ATR_mean"
            ),
            "note": (
                "No modifica Hybrid; cruza trades existentes "
                "por dirección y régimen."
            ),
        },
        "split": {
            "development_candles": len(split.development_closes),
            "oos_candles": len(split.oos_closes),
        },
        "development": development,
        "oos": oos,
    }


# ============================================================
# V2.8 - SHORT FAILURE AUDIT
# ============================================================

SHORT_AUDIT_STRUCTURE_LOOKBACK_V28 = 2
SHORT_AUDIT_RSI_PERIOD_V28 = 14
SHORT_AUDIT_HTF_EMA_FAST_V28 = 50
SHORT_AUDIT_HTF_EMA_SLOW_V28 = 200
SHORT_AUDIT_HTF_ADX_PERIOD_V28 = 14
SHORT_AUDIT_HTF_SLOPE_LOOKBACK_V28 = 5


def _safe_mean_v28(values):
    clean = [
        float(v)
        for v in values
        if v is not None
    ]
    return sum(clean) / len(clean) if clean else None


def _fmt_group_v28(rows):
    r_values = [row["r_multiple"] for row in rows]
    return {
        "trades": len(rows),
        "wins": sum(1 for row in rows if row["outcome"] == "WIN"),
        "losses": sum(1 for row in rows if row["outcome"] == "LOSS"),
        "average_r": _safe_mean_v28(r_values) or 0.0,
        "avg_rsi": _safe_mean_v28([row.get("rsi") for row in rows]),
        "avg_bos_strength_atr": _safe_mean_v28(
            [row.get("bos_strength_atr") for row in rows]
        ),
        "avg_body_atr": _safe_mean_v28(
            [row.get("body_atr") for row in rows]
        ),
        "avg_htf_adx": _safe_mean_v28(
            [row.get("htf_adx") for row in rows]
        ),
        "avg_htf_ema_sep_atr": _safe_mean_v28(
            [row.get("htf_ema_sep_atr") for row in rows]
        ),
        "avg_htf_slope_atr": _safe_mean_v28(
            [row.get("htf_slope_atr") for row in rows]
        ),
    }


def _bearish_bos_context_v28(highs, lows, closes, structure_lookback):
    """
    Reconstruye el nivel exacto del último swing low que cada BOS bajista
    rompió. No cambia market_structure.py y no crea señales nuevas.
    """
    structure = analizar_estructura(
        highs,
        lows,
        closes,
        lookback=structure_lookback,
    )

    events = structure["eventos"]
    ruptures = structure["rupturas"]

    latest_low = None
    event_idx = 0
    result = {}

    for rupture in ruptures:
        idx = int(rupture["index"])

        while event_idx < len(events) and events[event_idx]["index"] <= idx:
            event = events[event_idx]
            if event["type"] == "low":
                latest_low = float(event["price"])
            event_idx += 1

        if (
            rupture.get("tipo") == "BOS"
            and rupture.get("direccion") == "bajista"
        ):
            result[idx] = {
                "bos_close": float(rupture["price"]),
                "broken_swing_low": latest_low,
                "swing_origin_high": rupture.get("swing_origen_price"),
            }

    return result


def _htf_feature_map_v28(
    epochs,
    highs,
    lows,
    closes,
    source_granularity,
):
    """
    Calcula las variables HTF observables usadas alrededor de la señal.
    Replica la agregación temporal del registry, pero solo para diagnóstico.
    """
    epochs_htf, highs_htf, lows_htf, closes_htf = aggregate_ohlc_to_timeframe(
        epochs,
        highs,
        lows,
        closes,
        target_granularity=HTF_GRANULARITY,
        source_granularity=source_granularity,
    )

    ema_fast = calculate_ema(
        closes_htf,
        period=SHORT_AUDIT_HTF_EMA_FAST_V28,
    )
    ema_slow = calculate_ema(
        closes_htf,
        period=SHORT_AUDIT_HTF_EMA_SLOW_V28,
    )
    adx, _, _ = calculate_adx(
        highs_htf,
        lows_htf,
        closes_htf,
        period=SHORT_AUDIT_HTF_ADX_PERIOD_V28,
    )
    atr_htf = calculate_atr(
        highs_htf,
        lows_htf,
        closes_htf,
        period=ATR_PERIOD,
    )

    feature_rows = []

    for i, epoch in enumerate(epochs_htf):
        ef = ema_fast[i]
        es = ema_slow[i]
        adx_value = adx[i]
        atr_value = atr_htf[i]

        slope = None
        slope_atr = None
        ema_sep_atr = None

        if (
            i >= SHORT_AUDIT_HTF_SLOPE_LOOKBACK_V28
            and es is not None
            and ema_slow[i - SHORT_AUDIT_HTF_SLOPE_LOOKBACK_V28] is not None
        ):
            slope = (
                float(es)
                - float(
                    ema_slow[
                        i - SHORT_AUDIT_HTF_SLOPE_LOOKBACK_V28
                    ]
                )
            )

        if atr_value not in (None, 0):
            if ef is not None and es is not None:
                ema_sep_atr = (
                    float(ef) - float(es)
                ) / float(atr_value)

            if slope is not None:
                slope_atr = float(slope) / float(atr_value)

        feature_rows.append(
            {
                "epoch": epoch,
                "ema_fast": None if ef is None else float(ef),
                "ema_slow": None if es is None else float(es),
                "adx": None if adx_value is None else float(adx_value),
                "atr": None if atr_value is None else float(atr_value),
                "ema_sep_atr": ema_sep_atr,
                "slope_atr": slope_atr,
            }
        )

    return epochs_htf, feature_rows


def _latest_htf_feature_v28(epochs_htf, feature_rows, epoch_target):
    """
    Última vela HTF disponible en o antes del epoch de entrada.
    """
    if not epochs_htf:
        return None

    lo = 0
    hi = len(epochs_htf) - 1
    answer = -1

    while lo <= hi:
        mid = (lo + hi) // 2
        if epochs_htf[mid] <= epoch_target:
            answer = mid
            lo = mid + 1
        else:
            hi = mid - 1

    if answer < 0:
        return None

    return feature_rows[answer]


def run_short_failure_audit_v28(
    strategy_id,
    epochs,
    opens,
    highs,
    lows,
    closes,
    granularity,
    development_ratio=DEFAULT_DEVELOPMENT_RATIO,
):
    """
    V2.8 - SHORT Failure Audit.

    SOLO DEVELOPMENT.

    No cambia Hybrid, no bloquea SELL y no optimiza parámetros.
    Describe qué condiciones estaban presentes en cada SHORT ya ejecutado.

    Variables:
    - RSI de entrada.
    - régimen causal V2.5.
    - BOS bajista y fuerza de ruptura normalizada por ATR.
    - tamaño del cuerpo de la vela / ATR.
    - EMA50/EMA200 HTF, separación normalizada, pendiente EMA200 y ADX HTF.
    """

    if strategy_id != "hybrid":
        raise ValueError(
            "SHORT Failure Audit V2.8 está diseñado para strategy='hybrid'."
        )

    split = chronological_split(
        epochs=epochs,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        development_ratio=development_ratio,
    )

    dev_result = _run_period(
        strategy_id=strategy_id,
        epochs=split.development_epochs,
        opens=split.development_opens,
        highs=split.development_highs,
        lows=split.development_lows,
        closes=split.development_closes,
        granularity=granularity,
    )

    short_trades = [
        trade
        for trade in dev_result["trades"]
        if str(trade.get("direction", "")).upper() == "SELL"
    ]

    rsi = calculate_rsi(
        split.development_closes,
        period=SHORT_AUDIT_RSI_PERIOD_V28,
    )
    atr = calculate_atr(
        split.development_highs,
        split.development_lows,
        split.development_closes,
        period=ATR_PERIOD,
    )

    regime_labels = _regime_labels_v25(
        highs=split.development_highs,
        lows=split.development_lows,
        closes=split.development_closes,
    )

    bos_context = _bearish_bos_context_v28(
        split.development_highs,
        split.development_lows,
        split.development_closes,
        structure_lookback=SHORT_AUDIT_STRUCTURE_LOOKBACK_V28,
    )

    epochs_htf, htf_features = _htf_feature_map_v28(
        epochs=split.development_epochs,
        highs=split.development_highs,
        lows=split.development_lows,
        closes=split.development_closes,
        source_granularity=granularity,
    )

    epoch_to_index = {
        epoch: i
        for i, epoch in enumerate(split.development_epochs)
    }

    rows = []

    for number, trade in enumerate(short_trades, start=1):
        entry_epoch = trade.get("entry_epoch")
        idx = epoch_to_index.get(entry_epoch)

        if idx is None:
            continue

        atr_value = atr[idx]
        close_value = float(split.development_closes[idx])
        open_value = float(split.development_opens[idx])
        context = bos_context.get(idx)
        htf = _latest_htf_feature_v28(
            epochs_htf,
            htf_features,
            entry_epoch,
        )

        broken_level = (
            context.get("broken_swing_low")
            if context
            else None
        )

        bos_strength = None
        if (
            broken_level is not None
            and atr_value not in (None, 0)
        ):
            bos_strength = (
                float(broken_level) - close_value
            ) / float(atr_value)

        body_atr = None
        if atr_value not in (None, 0):
            body_atr = abs(
                close_value - open_value
            ) / float(atr_value)

        r_multiple = float(
            trade.get("r_multiple", 0.0) or 0.0
        )
        profit = float(
            trade.get("profit", 0.0) or 0.0
        )

        rows.append(
            {
                "trade_no": number,
                "entry_epoch": entry_epoch,
                "entry_index": idx,
                "entry_price": trade.get("entry_price"),
                "profit": profit,
                "r_multiple": r_multiple,
                "outcome": "WIN" if profit > 0 else "LOSS",
                "regime": (
                    regime_labels[idx]
                    if idx < len(regime_labels)
                    else "UNMATCHED"
                ),
                "rsi": (
                    None
                    if rsi[idx] is None
                    else float(rsi[idx])
                ),
                "atr": (
                    None
                    if atr_value is None
                    else float(atr_value)
                ),
                "body_atr": body_atr,
                "bos_found": context is not None,
                "bos_close": (
                    context.get("bos_close")
                    if context
                    else None
                ),
                "broken_swing_low": broken_level,
                "swing_origin_high": (
                    context.get("swing_origin_high")
                    if context
                    else None
                ),
                "bos_strength_atr": bos_strength,
                "htf_epoch": (
                    htf.get("epoch")
                    if htf
                    else None
                ),
                "htf_ema50": (
                    htf.get("ema_fast")
                    if htf
                    else None
                ),
                "htf_ema200": (
                    htf.get("ema_slow")
                    if htf
                    else None
                ),
                "htf_adx": (
                    htf.get("adx")
                    if htf
                    else None
                ),
                "htf_ema_sep_atr": (
                    htf.get("ema_sep_atr")
                    if htf
                    else None
                ),
                "htf_slope_atr": (
                    htf.get("slope_atr")
                    if htf
                    else None
                ),
            }
        )

    winners = [row for row in rows if row["outcome"] == "WIN"]
    losers = [row for row in rows if row["outcome"] == "LOSS"]

    by_regime = {}
    for regime in (
        "TREND_HIGH_VOL",
        "TREND_LOW_VOL",
        "RANGE_HIGH_VOL",
        "RANGE_LOW_VOL",
        "WARMUP",
        "UNMATCHED",
    ):
        group = [row for row in rows if row["regime"] == regime]
        by_regime[regime] = _fmt_group_v28(group)

    return {
        "strategy": strategy_id,
        "diagnostic_only": True,
        "scope": "DEVELOPMENT_ONLY",
        "configuration": {
            "development_ratio": development_ratio,
            "structure_lookback": SHORT_AUDIT_STRUCTURE_LOOKBACK_V28,
            "rsi_period": SHORT_AUDIT_RSI_PERIOD_V28,
            "atr_period": ATR_PERIOD,
            "htf_granularity": HTF_GRANULARITY,
            "htf_ema_fast": SHORT_AUDIT_HTF_EMA_FAST_V28,
            "htf_ema_slow": SHORT_AUDIT_HTF_EMA_SLOW_V28,
            "htf_adx_period": SHORT_AUDIT_HTF_ADX_PERIOD_V28,
            "htf_slope_lookback": SHORT_AUDIT_HTF_SLOPE_LOOKBACK_V28,
            "note": (
                "Diagnóstico post-hoc sobre SHORT existentes; "
                "no modifica señales ni parámetros."
            ),
        },
        "development_candles": len(split.development_closes),
        "short_trades": len(rows),
        "summary": {
            "all": _fmt_group_v28(rows),
            "winners": _fmt_group_v28(winners),
            "losers": _fmt_group_v28(losers),
        },
        "by_regime": by_regime,
        "trades": rows,
    }


# ============================================================
# V2.9 - SHORT FEATURE SEPARATION
# ============================================================

def _bucketize_v29(value, edges):
    if value is None:
        return "N/A"
    v = float(value)
    for low, high, label in edges:
        if low is None and v < high:
            return label
        if high is None and v >= low:
            return label
        if low is not None and high is not None and low <= v < high:
            return label
    return "N/A"


def _group_metrics_v29(rows):
    trades = len(rows)
    wins = sum(1 for r in rows if r["outcome"] == "WIN")
    losses = sum(1 for r in rows if r["outcome"] == "LOSS")
    wr = (wins / trades * 100.0) if trades else 0.0

    gross_win = sum(max(0.0, float(r.get("profit", 0.0) or 0.0)) for r in rows)
    gross_loss = abs(sum(min(0.0, float(r.get("profit", 0.0) or 0.0)) for r in rows))
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)

    avg_r = (
        sum(float(r.get("r_multiple", 0.0) or 0.0) for r in rows) / trades
        if trades else 0.0
    )

    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate": wr,
        "profit_factor": pf,
        "average_r": avg_r,
    }


def _feature_table_v29(rows, field_name, edges):
    grouped = {}
    for row in rows:
        label = _bucketize_v29(row.get(field_name), edges)
        grouped.setdefault(label, []).append(row)

    ordered = []
    for _, _, label in edges:
        ordered.append({
            "bucket": label,
            **_group_metrics_v29(grouped.get(label, [])),
        })

    if "N/A" in grouped:
        ordered.append({
            "bucket": "N/A",
            **_group_metrics_v29(grouped["N/A"]),
        })

    return ordered


def _combo_table_v29(rows):
    """
    Exploración diagnóstica simple:
    - BOS strong/weak using 0.75 ATR
    - HTF extension mild/extended using abs(EMA separation) 3.0 ATR

    Estas fronteras NO son reglas de trading. Solo sirven para separar
    visualmente la pequeña muestra de DEVELOPMENT.
    """
    groups = {
        "BOS_WEAK + HTF_MILD": [],
        "BOS_WEAK + HTF_EXTENDED": [],
        "BOS_STRONG + HTF_MILD": [],
        "BOS_STRONG + HTF_EXTENDED": [],
        "N/A": [],
    }

    for row in rows:
        bos = row.get("bos_strength_atr")
        sep = row.get("htf_ema_sep_atr")

        if bos is None or sep is None:
            groups["N/A"].append(row)
            continue

        bos_state = "BOS_STRONG" if float(bos) >= 0.75 else "BOS_WEAK"
        extension = "HTF_EXTENDED" if abs(float(sep)) >= 3.0 else "HTF_MILD"
        groups[f"{bos_state} + {extension}"].append(row)

    result = []
    for label in (
        "BOS_WEAK + HTF_MILD",
        "BOS_WEAK + HTF_EXTENDED",
        "BOS_STRONG + HTF_MILD",
        "BOS_STRONG + HTF_EXTENDED",
        "N/A",
    ):
        result.append({
            "bucket": label,
            **_group_metrics_v29(groups[label]),
        })
    return result


def run_short_feature_separation_v29(
    strategy_id,
    epochs,
    opens,
    highs,
    lows,
    closes,
    granularity,
    development_ratio=DEFAULT_DEVELOPMENT_RATIO,
):
    """
    V2.9 - SHORT Feature Separation.

    DEVELOPMENT ONLY.
    Reutiliza V2.8 y separa las features en rangos descriptivos.
    No cambia Hybrid y no crea filtros.
    """
    audit = run_short_failure_audit_v28(
        strategy_id=strategy_id,
        epochs=epochs,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        granularity=granularity,
        development_ratio=development_ratio,
    )

    rows = audit["trades"]

    feature_specs = {
        "RSI": (
            "rsi",
            [
                (35.0, 37.0, "35-37"),
                (37.0, 40.0, "37-40"),
                (40.0, 45.0, "40-45"),
                (45.0, None, "45+"),
            ],
        ),
        "BOS_ATR": (
            "bos_strength_atr",
            [
                (None, 0.25, "<0.25"),
                (0.25, 0.50, "0.25-0.50"),
                (0.50, 1.00, "0.50-1.00"),
                (1.00, None, "1.00+"),
            ],
        ),
        "BODY_ATR": (
            "body_atr",
            [
                (None, 1.5, "<1.50"),
                (1.5, 2.5, "1.50-2.50"),
                (2.5, 3.5, "2.50-3.50"),
                (3.5, None, "3.50+"),
            ],
        ),
        "HTF_ADX": (
            "htf_adx",
            [
                (None, 30.0, "<30"),
                (30.0, 35.0, "30-35"),
                (35.0, 40.0, "35-40"),
                (40.0, None, "40+"),
            ],
        ),
        "EMA_SEPARATION_ATR": (
            "htf_ema_sep_atr",
            [
                (None, -6.0, "<-6"),
                (-6.0, -3.0, "-6 to -3"),
                (-3.0, -1.5, "-3 to -1.5"),
                (-1.5, None, ">=-1.5"),
            ],
        ),
        "EMA200_SLOPE_ATR": (
            "htf_slope_atr",
            [
                (None, -0.45, "<-0.45"),
                (-0.45, -0.25, "-0.45 to -0.25"),
                (-0.25, -0.10, "-0.25 to -0.10"),
                (-0.10, None, ">=-0.10"),
            ],
        ),
    }

    tables = {}
    for name, (field, edges) in feature_specs.items():
        tables[name] = _feature_table_v29(rows, field, edges)

    combos = _combo_table_v29(rows)

    return {
        "strategy": strategy_id,
        "diagnostic_only": True,
        "scope": "DEVELOPMENT_ONLY",
        "short_trades": len(rows),
        "summary": audit["summary"]["all"],
        "feature_tables": tables,
        "combo_table": combos,
        "note": (
            "Los buckets son descriptivos y exploratorios. "
            "No deben usarse como reglas sin validación posterior en datos frescos."
        ),
    }


# ============================================================
# V3.0 - SHORT CANDIDATE LAB
# ============================================================

V30_CANDIDATES = {
    "BASE": {
        "description": "Hybrid original sin cambios.",
    },
    "CANDIDATE_A_BOS": {
        "description": "Conserva SELL solo si BOS bajista >= 1.00 ATR.",
        "min_bos_atr": 1.00,
    },
    "CANDIDATE_B_EXTENSION": {
        "description": "Conserva SELL solo si separación EMA50-EMA200 HTF >= -1.50 ATR.",
        "min_ema_sep_atr": -1.50,
    },
    "CANDIDATE_C_COMBINED": {
        "description": "BOS >= 1.00 ATR y separación HTF >= -1.50 ATR.",
        "min_bos_atr": 1.00,
        "min_ema_sep_atr": -1.50,
    },
}


def _candidate_signals_v30(
    baseline_signals,
    epochs,
    opens,
    highs,
    lows,
    closes,
    granularity,
    candidate_name,
):
    """
    Filtra únicamente señales SELL de la Hybrid base.
    BUY permanece byte-a-byte equivalente en la lista de señales.
    """
    if candidate_name == "BASE":
        return list(baseline_signals), {
            "sell_seen": sum(1 for s in baseline_signals if s == SELL),
            "sell_kept": sum(1 for s in baseline_signals if s == SELL),
            "sell_removed": 0,
        }

    cfg = V30_CANDIDATES[candidate_name]

    atr = calculate_atr(
        highs,
        lows,
        closes,
        period=ATR_PERIOD,
    )

    bos_context = _bearish_bos_context_v28(
        highs,
        lows,
        closes,
        structure_lookback=SHORT_AUDIT_STRUCTURE_LOOKBACK_V28,
    )

    epochs_htf, htf_features = _htf_feature_map_v28(
        epochs=epochs,
        highs=highs,
        lows=lows,
        closes=closes,
        source_granularity=granularity,
    )

    filtered = list(baseline_signals)
    sell_seen = 0
    sell_kept = 0

    for i, signal in enumerate(baseline_signals):
        if signal != SELL:
            continue

        sell_seen += 1
        keep = True

        atr_value = atr[i]
        bos = bos_context.get(i)

        bos_strength_atr = None
        if (
            bos is not None
            and bos.get("broken_swing_low") is not None
            and atr_value not in (None, 0)
        ):
            bos_strength_atr = (
                float(bos["broken_swing_low"]) - float(closes[i])
            ) / float(atr_value)

        htf = _latest_htf_feature_v28(
            epochs_htf,
            htf_features,
            epochs[i],
        )
        ema_sep_atr = htf.get("ema_sep_atr") if htf else None

        min_bos = cfg.get("min_bos_atr")
        if min_bos is not None:
            if bos_strength_atr is None or bos_strength_atr < min_bos:
                keep = False

        min_sep = cfg.get("min_ema_sep_atr")
        if min_sep is not None:
            if ema_sep_atr is None or ema_sep_atr < min_sep:
                keep = False

        if keep:
            sell_kept += 1
        else:
            filtered[i] = HOLD

    return filtered, {
        "sell_seen": sell_seen,
        "sell_kept": sell_kept,
        "sell_removed": sell_seen - sell_kept,
    }


def _run_signals_v30(
    epochs,
    highs,
    lows,
    closes,
    signals,
):
    atr_values = calculate_atr(
        highs,
        lows,
        closes,
        period=ATR_PERIOD,
    )

    result = run_backtest_risk(
        epochs,
        highs,
        lows,
        closes,
        signals,
        atr_values,
        initial_balance=INITIAL_BALANCE,
        risk_pct=RIESGO_POR_OPERACION_PCT,
        stop_atr_mult=SL_ATR_MULT,
        reward_ratio=REWARD_RATIO,
        spread_atr_frac=SPREAD_ATR_FRAC,
        max_drawdown_stop_pct=MAX_DRAWDOWN_STOP_PCT,
        slippage_atr_max=BACKTEST_SLIPPAGE_ATR_MAX,
        random_seed=BACKTEST_RANDOM_SEED,
        commission_per_trade=BACKTEST_COMMISSION_PER_TRADE_USD,
    )

    trades = result.get("trades", [])
    return {
        "metrics": result.get("metrics", {}),
        "long": _side_metrics(trades, "BUY"),
        "short": _side_metrics(trades, "SELL"),
        "trades": trades,
    }


def _compact_candidate_v30(name, result, signal_stats):
    m = result["metrics"]
    return {
        "candidate": name,
        "description": V30_CANDIDATES[name]["description"],
        "signal_stats": signal_stats,
        "all": {
            "trades": m.get("total_trades", 0),
            "win_rate": m.get("win_rate", 0.0),
            "profit_factor": m.get("profit_factor", 0.0),
            "max_drawdown_pct": m.get("max_drawdown_pct", 0.0),
            "total_return_pct": m.get("total_return_pct", 0.0),
            "average_r": m.get("r_multiple_promedio", 0.0),
        },
        "long": result["long"],
        "short": result["short"],
    }


def run_short_candidate_lab_v30(
    strategy_id,
    epochs,
    opens,
    highs,
    lows,
    closes,
    granularity,
    development_ratio=DEFAULT_DEVELOPMENT_RATIO,
):
    """
    V3.0 - SHORT Candidate Lab.

    Re-simula cada candidato desde cero sobre DEVELOPMENT.
    LONG no se filtra. Solo se decide si cada SELL base se conserva o se
    convierte en HOLD antes de enviar toda la secuencia al motor oficial.

    IMPORTANTE:
    - No modifica hybrid_strategy.py.
    - No modifica registry.py.
    - No usa OOS.
    - Los umbrales son hipótesis exploratorias derivadas de V2.9, no reglas validadas.
    """
    if strategy_id != "hybrid":
        raise ValueError(
            "SHORT Candidate Lab V3.0 está diseñado para strategy='hybrid'."
        )

    split = chronological_split(
        epochs=epochs,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        development_ratio=development_ratio,
    )

    de = split.development_epochs
    do = split.development_opens
    dh = split.development_highs
    dl = split.development_lows
    dc = split.development_closes

    baseline_signals = generate_registered_signals(
        strategy_id,
        de,
        dh,
        dl,
        dc,
        source_granularity=granularity,
        opens=do,
    )

    results = []

    for name in V30_CANDIDATES:
        candidate_signals, signal_stats = _candidate_signals_v30(
            baseline_signals=baseline_signals,
            epochs=de,
            opens=do,
            highs=dh,
            lows=dl,
            closes=dc,
            granularity=granularity,
            candidate_name=name,
        )

        simulated = _run_signals_v30(
            epochs=de,
            highs=dh,
            lows=dl,
            closes=dc,
            signals=candidate_signals,
        )

        results.append(
            _compact_candidate_v30(
                name,
                simulated,
                signal_stats,
            )
        )

    baseline = results[0]
    base_long_trades = baseline["long"]["total_trades"]

    for row in results:
        row["long_trade_count_matches_base"] = (
            row["long"]["total_trades"] == base_long_trades
        )

    return {
        "strategy": strategy_id,
        "scope": "DEVELOPMENT_ONLY",
        "diagnostic_only": True,
        "development_candles": len(dc),
        "candidates": results,
        "methodology": {
            "long_frozen": True,
            "true_resimulation": True,
            "same_risk_engine": True,
            "uses_oos": False,
            "selection_warning": (
                "No elegir automáticamente el PF máximo. "
                "V3.0 formula candidatos; cualquier cambio requiere validación temporal "
                "y posteriormente datos frescos no usados en diseño."
            ),
        },
    }


# ============================================================
# V3.1 - SHORT TEMPORAL STABILITY
# ============================================================

V31_BLOCKS = 6
V31_CANDIDATES = (
    "BASE",
    "CANDIDATE_A_BOS",
    "CANDIDATE_B_EXTENSION",
)


def _split_into_blocks_v31(
    epochs,
    opens,
    highs,
    lows,
    closes,
    n_blocks=V31_BLOCKS,
):
    n = len(closes)
    if n_blocks <= 0:
        raise ValueError("n_blocks debe ser > 0")
    if n < n_blocks:
        raise ValueError("No hay suficientes velas para dividir en bloques.")

    blocks = []
    base = n // n_blocks
    remainder = n % n_blocks
    start = 0

    for b in range(n_blocks):
        size = base + (1 if b < remainder else 0)
        end = start + size

        blocks.append(
            {
                "block": b + 1,
                "epochs": epochs[start:end],
                "opens": opens[start:end],
                "highs": highs[start:end],
                "lows": lows[start:end],
                "closes": closes[start:end],
                "start_index": start,
                "end_index_exclusive": end,
            }
        )
        start = end

    return blocks


def _candidate_block_result_v31(
    candidate_name,
    epochs,
    opens,
    highs,
    lows,
    closes,
    granularity,
):
    baseline_signals = generate_registered_signals(
        "hybrid",
        epochs,
        highs,
        lows,
        closes,
        source_granularity=granularity,
        opens=opens,
    )

    candidate_signals, signal_stats = _candidate_signals_v30(
        baseline_signals=baseline_signals,
        epochs=epochs,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        granularity=granularity,
        candidate_name=candidate_name,
    )

    simulated = _run_signals_v30(
        epochs=epochs,
        highs=highs,
        lows=lows,
        closes=closes,
        signals=candidate_signals,
    )

    m = simulated["metrics"]
    short = simulated["short"]
    long_ = simulated["long"]

    return {
        "candidate": candidate_name,
        "all": {
            "trades": m.get("total_trades", 0),
            "win_rate": m.get("win_rate", 0.0),
            "profit_factor": m.get("profit_factor", 0.0),
            "max_drawdown_pct": m.get("max_drawdown_pct", 0.0),
            "total_return_pct": m.get("total_return_pct", 0.0),
            "average_r": m.get("r_multiple_promedio", 0.0),
        },
        "short": short,
        "long": long_,
        "signal_stats": signal_stats,
    }


def _stability_summary_v31(block_rows):
    active_short_blocks = 0
    positive_short_blocks = 0
    negative_short_blocks = 0
    total_short_trades = 0
    weighted_r_num = 0.0

    for row in block_rows:
        s = row["short"]
        trades = int(s.get("total_trades", 0) or 0)
        avg_r = float(s.get("average_r", 0.0) or 0.0)

        if trades > 0:
            active_short_blocks += 1
            total_short_trades += trades
            weighted_r_num += avg_r * trades

            if avg_r > 0:
                positive_short_blocks += 1
            elif avg_r < 0:
                negative_short_blocks += 1

    weighted_avg_r = (
        weighted_r_num / total_short_trades
        if total_short_trades > 0
        else 0.0
    )

    return {
        "active_short_blocks": active_short_blocks,
        "positive_short_blocks": positive_short_blocks,
        "negative_short_blocks": negative_short_blocks,
        "total_short_trades": total_short_trades,
        "weighted_short_avg_r": weighted_avg_r,
        "positive_block_ratio": (
            positive_short_blocks / active_short_blocks
            if active_short_blocks > 0
            else 0.0
        ),
    }


def run_short_temporal_stability_v31(
    strategy_id,
    epochs,
    opens,
    highs,
    lows,
    closes,
    granularity,
    development_ratio=DEFAULT_DEVELOPMENT_RATIO,
    n_blocks=V31_BLOCKS,
):
    """
    V3.1 - SHORT Temporal Stability.

    Divide únicamente DEVELOPMENT en bloques cronológicos contiguos y
    re-simula BASE, Candidate A y Candidate B dentro de cada bloque.

    Objetivo:
    comprobar si la mejora de SHORT aparece repartida temporalmente o si
    depende de una zona histórica concreta.

    No usa OOS.
    No modifica Hybrid.
    No selecciona automáticamente ganador.
    """

    if strategy_id != "hybrid":
        raise ValueError(
            "SHORT Temporal Stability V3.1 está diseñado para strategy='hybrid'."
        )

    split = chronological_split(
        epochs=epochs,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        development_ratio=development_ratio,
    )

    blocks = _split_into_blocks_v31(
        split.development_epochs,
        split.development_opens,
        split.development_highs,
        split.development_lows,
        split.development_closes,
        n_blocks=n_blocks,
    )

    candidate_results = {
        candidate: []
        for candidate in V31_CANDIDATES
    }

    for block in blocks:
        for candidate in V31_CANDIDATES:
            result = _candidate_block_result_v31(
                candidate_name=candidate,
                epochs=block["epochs"],
                opens=block["opens"],
                highs=block["highs"],
                lows=block["lows"],
                closes=block["closes"],
                granularity=granularity,
            )

            result["block"] = block["block"]
            result["candles"] = len(block["closes"])
            result["start_epoch"] = block["epochs"][0] if block["epochs"] else None
            result["end_epoch"] = block["epochs"][-1] if block["epochs"] else None

            candidate_results[candidate].append(result)

    summaries = {
        candidate: _stability_summary_v31(rows)
        for candidate, rows in candidate_results.items()
    }

    return {
        "strategy": strategy_id,
        "scope": "DEVELOPMENT_ONLY",
        "development_candles": len(split.development_closes),
        "blocks": n_blocks,
        "candidate_results": candidate_results,
        "summaries": summaries,
        "methodology": {
            "uses_oos": False,
            "true_resimulation": True,
            "long_logic_changed": False,
            "note": (
                "Cada bloque se re-simula desde cero. "
                "El objetivo es estabilidad temporal de SHORT, "
                "no maximizar PF."
            ),
        },
    }


# ============================================================
# V3.2 - SHORT TEMPORAL CONFIRMATION WITH PRESERVED WARMUP
# ============================================================

V32_BLOCKS = 6
V32_WARMUP_CANDLES = 3000
V32_CANDIDATES = (
    "BASE",
    "CANDIDATE_A_BOS",
    "CANDIDATE_B_EXTENSION",
)


def _block_ranges_v32(n, n_blocks=V32_BLOCKS):
    if n_blocks <= 0:
        raise ValueError("n_blocks debe ser > 0")
    if n < n_blocks:
        raise ValueError("No hay suficientes velas para dividir DEVELOPMENT.")

    base = n // n_blocks
    remainder = n % n_blocks
    ranges = []
    start = 0

    for b in range(n_blocks):
        size = base + (1 if b < remainder else 0)
        end = start + size
        ranges.append((start, end))
        start = end

    return ranges


def _filter_trades_to_epoch_window_v32(trades, start_epoch, end_epoch):
    out = []
    for t in trades:
        entry_epoch = t.get("entry_epoch")
        if entry_epoch is None:
            continue
        if start_epoch <= entry_epoch <= end_epoch:
            out.append(t)
    return out


def _metrics_from_trade_subset_v32(trades):
    if not trades:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "average_r": 0.0,
            "net_profit": 0.0,
        }

    wins = [t for t in trades if float(t.get("profit", 0.0) or 0.0) > 0]
    losses = [t for t in trades if float(t.get("profit", 0.0) or 0.0) < 0]

    gross_profit = sum(float(t.get("profit", 0.0) or 0.0) for t in wins)
    gross_loss = abs(sum(float(t.get("profit", 0.0) or 0.0) for t in losses))

    if gross_loss == 0:
        pf = float("inf") if gross_profit > 0 else 0.0
    else:
        pf = gross_profit / gross_loss

    rs = [float(t.get("r_multiple", 0.0) or 0.0) for t in trades]

    return {
        "total_trades": len(trades),
        "win_rate": len(wins) / len(trades) * 100.0,
        "profit_factor": pf,
        "average_r": sum(rs) / len(rs) if rs else 0.0,
        "net_profit": sum(float(t.get("profit", 0.0) or 0.0) for t in trades),
    }


def _system_metrics_window_v32(trades):
    """
    Métricas descriptivas de trades cuyo ENTRY cae dentro del bloque objetivo.
    El motor se ejecuta con warmup preservado; estas métricas NO reconstruyen
    drawdown/equity aislado del bloque.
    """
    m = _metrics_from_trade_subset_v32(trades)
    return {
        "trades": m["total_trades"],
        "win_rate": m["win_rate"],
        "profit_factor": m["profit_factor"],
        "average_r": m["average_r"],
        "net_profit": m["net_profit"],
    }


def run_short_temporal_confirmation_v32(
    strategy_id,
    epochs,
    opens,
    highs,
    lows,
    closes,
    granularity,
    development_ratio=DEFAULT_DEVELOPMENT_RATIO,
    n_blocks=V32_BLOCKS,
    warmup_candles=V32_WARMUP_CANDLES,
):
    """
    V3.2 - confirmación temporal preservando contexto causal previo.

    Cada bloque objetivo usa las velas anteriores como warmup. Las señales y
    posiciones se re-simulan desde el inicio del segmento con warmup, pero solo
    se contabilizan trades cuyo entry_epoch pertenece al bloque objetivo.

    DEVELOPMENT only. OOS no se usa.
    """

    if strategy_id != "hybrid":
        raise ValueError(
            "SHORT Temporal Confirmation V3.2 está diseñado para strategy='hybrid'."
        )

    split = chronological_split(
        epochs=epochs,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        development_ratio=development_ratio,
    )

    de = split.development_epochs
    do = split.development_opens
    dh = split.development_highs
    dl = split.development_lows
    dc = split.development_closes

    ranges = _block_ranges_v32(len(dc), n_blocks=n_blocks)

    candidate_results = {c: [] for c in V32_CANDIDATES}

    for block_idx, (target_start, target_end) in enumerate(ranges, start=1):
        sim_start = max(0, target_start - warmup_candles)

        seg_epochs = de[sim_start:target_end]
        seg_opens = do[sim_start:target_end]
        seg_highs = dh[sim_start:target_end]
        seg_lows = dl[sim_start:target_end]
        seg_closes = dc[sim_start:target_end]

        target_start_epoch = de[target_start]
        target_end_epoch = de[target_end - 1]

        baseline_signals = generate_registered_signals(
            strategy_id,
            seg_epochs,
            seg_highs,
            seg_lows,
            seg_closes,
            source_granularity=granularity,
            opens=seg_opens,
        )

        for candidate in V32_CANDIDATES:
            candidate_signals, signal_stats = _candidate_signals_v30(
                baseline_signals=baseline_signals,
                epochs=seg_epochs,
                opens=seg_opens,
                highs=seg_highs,
                lows=seg_lows,
                closes=seg_closes,
                granularity=granularity,
                candidate_name=candidate,
            )

            simulated = _run_signals_v30(
                epochs=seg_epochs,
                highs=seg_highs,
                lows=seg_lows,
                closes=seg_closes,
                signals=candidate_signals,
            )

            window_trades = _filter_trades_to_epoch_window_v32(
                simulated["trades"],
                target_start_epoch,
                target_end_epoch,
            )

            short_trades = [t for t in window_trades if t.get("direction") == "SELL"]
            long_trades = [t for t in window_trades if t.get("direction") == "BUY"]

            row = {
                "block": block_idx,
                "target_candles": target_end - target_start,
                "warmup_used": target_start - sim_start,
                "start_epoch": target_start_epoch,
                "end_epoch": target_end_epoch,
                "candidate": candidate,
                "system": _system_metrics_window_v32(window_trades),
                "short": _metrics_from_trade_subset_v32(short_trades),
                "long": _metrics_from_trade_subset_v32(long_trades),
                "signal_stats_segment": signal_stats,
            }

            candidate_results[candidate].append(row)

    summaries = {}

    for candidate, rows in candidate_results.items():
        active = 0
        positive = 0
        negative = 0
        total_short = 0
        weighted_r = 0.0

        for row in rows:
            s = row["short"]
            ntr = s["total_trades"]
            if ntr > 0:
                active += 1
                total_short += ntr
                weighted_r += s["average_r"] * ntr
                if s["average_r"] > 0:
                    positive += 1
                elif s["average_r"] < 0:
                    negative += 1

        summaries[candidate] = {
            "active_short_blocks": active,
            "positive_short_blocks": positive,
            "negative_short_blocks": negative,
            "total_short_trades": total_short,
            "weighted_short_avg_r": weighted_r / total_short if total_short else 0.0,
            "positive_block_ratio": positive / active if active else 0.0,
        }

    return {
        "strategy": strategy_id,
        "scope": "DEVELOPMENT_ONLY",
        "development_candles": len(dc),
        "blocks": n_blocks,
        "warmup_candles": warmup_candles,
        "candidate_results": candidate_results,
        "summaries": summaries,
        "methodology": {
            "uses_oos": False,
            "preserved_warmup": True,
            "true_resimulation": True,
            "block_metrics_note": (
                "Las métricas por bloque se calculan sobre trades cuyo ENTRY cae "
                "en el bloque objetivo. El segmento de simulación incluye warmup previo."
            ),
        },
    }

