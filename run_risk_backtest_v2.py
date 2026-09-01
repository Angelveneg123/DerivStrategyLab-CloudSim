"""Ejecutor de Risk Backtester V2."""

import argparse
import json

from database.database import (
    get_available_datasets,
    get_candles,
)

from strategy.registry import validate_strategy_id

from backtesting.risk_backtester_v2 import (
    run_risk_backtest_v2,
    run_walk_forward_v21,
    run_stress_test_v22,
    run_sensitivity_test_v23,
    run_monte_carlo_v24,
    run_regime_test_v25,
    run_directional_ablation_v26,
    run_direction_regime_audit_v27,
    run_short_failure_audit_v28,
    run_short_feature_separation_v29,
    run_short_candidate_lab_v30,
    run_short_temporal_stability_v31,
    run_short_temporal_confirmation_v32,
)


# ============================================================
# DATASET
# ============================================================

def resolve_dataset(symbol_query, granularity):
    """
    Busca el dataset solicitado.

    Primero intenta coincidencia exacta.
    Después intenta coincidencia parcial.
    """

    datasets = get_available_datasets()

    target = str(
        symbol_query
    ).strip().lower()

    matches = [
        d
        for d in datasets
        if int(d["granularity"])
        == int(granularity)
    ]

    # --------------------------------------------------------
    # Coincidencia exacta
    # --------------------------------------------------------

    exact = [
        d
        for d in matches
        if str(
            d["symbol"]
        ).lower() == target
    ]

    if exact:
        return exact[0]["symbol"]

    # --------------------------------------------------------
    # Coincidencia parcial
    # --------------------------------------------------------

    contains = [
        d
        for d in matches
        if (
            target
            in str(d["symbol"]).lower()
            or str(d["symbol"]).lower()
            in target
        )
    ]

    if contains:
        return contains[0]["symbol"]

    available = ", ".join(
        f'{d["symbol"]}/{d["granularity"]}s'
        for d in datasets
    )

    raise RuntimeError(
        f"No existe dataset para "
        f"{symbol_query!r}/{granularity}s. "
        f"Disponibles: {available}"
    )


# ============================================================
# IMPRESIÓN DE MÉTRICAS
# ============================================================

def print_metrics(title, result):
    """
    Imprime métricas generales y separadas por dirección.
    """

    metrics = result["metrics"]
    long_metrics = result["long"]
    short_metrics = result["short"]

    print()
    print("=" * 60)
    print(title)
    print("=" * 60)

    print(
        f"Trades:        "
        f"{metrics.get('total_trades', 0)}"
    )

    print(
        f"Win Rate:      "
        f"{metrics.get('win_rate', 0):.2f}%"
    )

    print(
        f"Profit Factor: "
        f"{metrics.get('profit_factor', 0):.3f}"
    )

    print(
        f"Max Drawdown:  "
        f"{metrics.get('max_drawdown_pct', 0):.2f}%"
    )

    print(
        f"Return:        "
        f"{metrics.get('total_return_pct', 0):.2f}%"
    )

    print(
        f"Average R:     "
        f"{metrics.get('r_multiple_promedio', 0):.3f}"
    )

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    print()
    print("LONG")
    print("-" * 30)

    print(
        f"Trades:        "
        f"{long_metrics['total_trades']}"
    )

    print(
        f"Win Rate:      "
        f"{long_metrics['win_rate']:.2f}%"
    )

    print(
        f"Profit Factor: "
        f"{long_metrics['profit_factor']:.3f}"
    )

    print(
        f"Average R:     "
        f"{long_metrics['average_r']:.3f}"
    )

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    print()
    print("SHORT")
    print("-" * 30)

    print(
        f"Trades:        "
        f"{short_metrics['total_trades']}"
    )

    print(
        f"Win Rate:      "
        f"{short_metrics['win_rate']:.2f}%"
    )

    print(
        f"Profit Factor: "
        f"{short_metrics['profit_factor']:.3f}"
    )

    print(
        f"Average R:     "
        f"{short_metrics['average_r']:.3f}"
    )


# ============================================================
# WALK-FORWARD V2.1 — SALIDA
# ============================================================

def print_walk_forward(result):
    """
    Imprime resultados del Walk-Forward V2.1.
    """

    print()
    print("=" * 60)
    print(" WALK-FORWARD V2.1")
    print("=" * 60)

    print(
        f"Windows: "
        f"{len(result['windows'])}"
    )

    print()

    for window in result["windows"]:

        metrics = (
            window["oos"]["metrics"]
        )

        print(
            f"Window {window['window']} | "
            f"OOS trades: "
            f"{metrics.get('total_trades', 0)} | "
            f"WR: "
            f"{metrics.get('win_rate', 0):.2f}% | "
            f"PF: "
            f"{metrics.get('profit_factor', 0):.3f} | "
            f"DD: "
            f"{metrics.get('max_drawdown_pct', 0):.2f}% | "
            f"R: "
            f"{metrics.get('r_multiple_promedio', 0):.3f}"
        )

    aggregate = (
        result["aggregate_oos"]
    )

    print()
    print("=" * 60)
    print(" AGGREGATE OOS")
    print("=" * 60)

    print(
        f"Total OOS trades: "
        f"{aggregate['total_trades']}"
    )

    print(
        f"Win Rate:         "
        f"{aggregate['win_rate']:.2f}%"
    )

    print(
        f"Profit Factor:    "
        f"{aggregate['profit_factor']:.3f}"
    )

    print(
        f"Average R:        "
        f"{aggregate['average_r']:.3f}"
    )

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    print()
    print("LONG")
    print("-" * 30)

    print(
        f"Trades: "
        f"{aggregate['long']['total_trades']} | "
        f"WR: "
        f"{aggregate['long']['win_rate']:.2f}% | "
        f"PF: "
        f"{aggregate['long']['profit_factor']:.3f} | "
        f"R: "
        f"{aggregate['long']['average_r']:.3f}"
    )

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    print()
    print("SHORT")
    print("-" * 30)

    print(
        f"Trades: "
        f"{aggregate['short']['total_trades']} | "
        f"WR: "
        f"{aggregate['short']['win_rate']:.2f}% | "
        f"PF: "
        f"{aggregate['short']['profit_factor']:.3f} | "
        f"R: "
        f"{aggregate['short']['average_r']:.3f}"
    )


# ============================================================
# STRESS TEST V2.2 — SALIDA
# ============================================================
def print_stress_test(result):
    """
    Imprime los escenarios de costos del Stress Test V2.2.
    """

    print()
    print("=" * 60)
    print(" STRESS TEST V2.2")
    print("=" * 60)

    for name, scenario in result["scenarios"].items():

        metrics = scenario["result"]["metrics"]
        config = scenario["configuration"]

        print()
        print(name)
        print("-" * 60)

        # BASE usa directamente config.py.
        # Los demás escenarios muestran sus overrides.
        spread_value = config.get(
            "spread_atr_frac",
            "CONFIG"
        )

        slippage_value = config.get(
            "slippage_atr_max",
            "CONFIG"
        )

        if isinstance(spread_value, (int, float)):
            print(
                f"Spread ATR:    "
                f"{spread_value:.3f}"
            )
        else:
            print(
                f"Spread ATR:    "
                f"{spread_value}"
            )

        if isinstance(slippage_value, (int, float)):
            print(
                f"Slippage ATR:  "
                f"{slippage_value:.3f}"
            )
        else:
            print(
                f"Slippage ATR:  "
                f"{slippage_value}"
            )

        print(
            f"Trades:        "
            f"{metrics.get('total_trades', 0)}"
        )

        print(
            f"Win Rate:      "
            f"{metrics.get('win_rate', 0):.2f}%"
        )

        print(
            f"Profit Factor: "
            f"{metrics.get('profit_factor', 0):.3f}"
        )

        print(
            f"Max Drawdown:  "
            f"{metrics.get('max_drawdown_pct', 0):.2f}%"
        )

        print(
            f"Return:        "
            f"{metrics.get('total_return_pct', 0):.2f}%"
        )

        print(
            f"Average R:     "
            f"{metrics.get('r_multiple_promedio', 0):.3f}"
        )

    print()
    print("=" * 60)
    print(" DEGRADATION VS BASE")
    print("=" * 60)

    for name, changes in (
        result["comparison_vs_base"].items()
    ):

        if name == "BASE":
            continue

        print()
        print(name)
        print("-" * 30)

        for metric, value in changes.items():

            if value is None:
                print(
                    f"{metric}: N/A"
                )
            else:
                print(
                    f"{metric}: "
                    f"{value:+.2f}%"
                )



# ============================================================
# SENSITIVITY TEST V2.3 — SALIDA
# ============================================================

def print_sensitivity_test(result):
    """
    Imprime Sensitivity Test V2.3.
    """

    print()
    print("=" * 72)
    print(" SENSITIVITY TEST V2.3")
    print("=" * 72)

    config = result["configuration"]

    print("Dataset utilizado: DEVELOPMENT únicamente")
    print(
        f"Development candles: "
        f"{config['development_candles']:,}"
    )
    print(
        f"Baseline SL ATR:      "
        f"{config['baseline_stop_atr_mult']:.3f}"
    )
    print(
        f"Baseline RR:          "
        f"{config['baseline_reward_ratio']:.3f}"
    )

    print()
    print(
        "SL ATR | RR    | Trades | WR %   | PF    | DD %  | Avg R"
    )
    print("-" * 72)

    for scenario in result["scenarios"]:
        scenario_config = scenario["configuration"]
        metrics = scenario["metrics"]

        marker = "  < BASE" if scenario["baseline"] else ""

        print(
            f"{scenario_config['stop_atr_mult']:>6.3f} | "
            f"{scenario_config['reward_ratio']:>5.3f} | "
            f"{metrics.get('total_trades', 0):>6} | "
            f"{metrics.get('win_rate', 0):>6.2f} | "
            f"{metrics.get('profit_factor', 0):>5.3f} | "
            f"{metrics.get('max_drawdown_pct', 0):>5.2f} | "
            f"{metrics.get('r_multiple_promedio', 0):>6.3f}"
            f"{marker}"
        )

    summary = result["summary"]

    print()
    print("=" * 72)
    print(" SENSITIVITY SUMMARY")
    print("=" * 72)

    print(
        f"Scenarios:           "
        f"{summary['total_scenarios']}"
    )
    print(
        f"PF > 1 scenarios:    "
        f"{summary['profitable_scenarios']}/"
        f"{summary['total_scenarios']}"
    )
    print(
        f"PF range:            "
        f"{summary['profit_factor_min']:.3f} -> "
        f"{summary['profit_factor_max']:.3f}"
    )
    print(
        f"Win Rate range:      "
        f"{summary['win_rate_min']:.2f}% -> "
        f"{summary['win_rate_max']:.2f}%"
    )
    print(
        f"Drawdown range:      "
        f"{summary['drawdown_min']:.2f}% -> "
        f"{summary['drawdown_max']:.2f}%"
    )
    print(
        f"Average R range:     "
        f"{summary['average_r_min']:.3f} -> "
        f"{summary['average_r_max']:.3f}"
    )



# ============================================================
# MONTE CARLO V2.4 — SALIDA
# ============================================================

def print_monte_carlo(result):
    """
    Imprime el resumen del Monte Carlo V2.4.
    """

    config = result["configuration"]
    source = result["source"]
    dist = result["distribution"]

    print()
    print("=" * 72)
    print(" MONTE CARLO V2.4")
    print("=" * 72)

    print("Dataset utilizado: DEVELOPMENT únicamente")
    print(f"Development candles: {config['development_candles']:,}")
    print(f"Source trades:       {config['source_trades']}")
    print(f"Iterations:          {config['iterations']:,}")
    print(f"Risk per trade:      {config['risk_pct']:.3f}%")
    print(f"Random seed:         {config['random_seed']}")

    metrics = source["metrics"]

    print()
    print("SOURCE DEVELOPMENT")
    print("-" * 72)
    print(f"Trades:        {metrics.get('total_trades', 0)}")
    print(f"Win Rate:      {metrics.get('win_rate', 0):.2f}%")
    print(f"Profit Factor: {metrics.get('profit_factor', 0):.3f}")
    print(f"Average R:     {source.get('average_r', 0):.3f}")

    ret = dist["return_pct"]

    print()
    print("RETURN DISTRIBUTION")
    print("-" * 72)
    print(f"P05:    {ret['p05']:+.2f}%")
    print(f"P25:    {ret['p25']:+.2f}%")
    print(f"Median: {ret['median']:+.2f}%")
    print(f"P75:    {ret['p75']:+.2f}%")
    print(f"P95:    {ret['p95']:+.2f}%")
    print(
        f"Negative-return simulations: "
        f"{dist['probability_negative_return_pct']:.2f}%"
    )

    dd = dist["max_drawdown_pct"]

    print()
    print("MAX DRAWDOWN DISTRIBUTION")
    print("-" * 72)
    print(f"P50: {dd['p50']:.2f}%")
    print(f"P75: {dd['p75']:.2f}%")
    print(f"P90: {dd['p90']:.2f}%")
    print(f"P95: {dd['p95']:.2f}%")
    print(f"P99: {dd['p99']:.2f}%")
    print(f"Max: {dd['max']:.2f}%")

    streak = dist["max_losing_streak"]

    print()
    print("MAX LOSING STREAK")
    print("-" * 72)
    print(f"P50: {streak['p50']:.0f}")
    print(f"P90: {streak['p90']:.0f}")
    print(f"P95: {streak['p95']:.0f}")
    print(f"P99: {streak['p99']:.0f}")
    print(f"Max: {streak['max']:.0f}")



# ============================================================
# REGIME ROBUSTNESS V2.5 — SALIDA
# ============================================================

def print_regime_test(result):
    """
    Imprime robustez por régimen para DEVELOPMENT y OOS.
    """

    config = result["configuration"]

    print()
    print("=" * 86)
    print(" REGIME ROBUSTNESS V2.5")
    print("=" * 86)

    print(
        f"ATR period: {config['atr_period']} | "
        f"Trend lookback: {config['trend_lookback']} | "
        f"Vol lookback: {config['vol_lookback']} | "
        f"Trend threshold: {config['trend_threshold']:.2f}"
    )

    print()
    print(
        "REGIME              | Trades | WR %   | PF    | Avg R  | "
        "LONG | SHORT"
    )
    print("-" * 86)

    order = [
        "TREND_HIGH_VOL",
        "TREND_LOW_VOL",
        "RANGE_HIGH_VOL",
        "RANGE_LOW_VOL",
        "WARMUP",
        "UNMATCHED",
    ]

    for period_name in ("development", "oos"):
        print()
        print(period_name.upper())
        print("-" * 86)

        regimes = result[period_name]["regimes"]

        for name in order:
            metrics = regimes[name]

            print(
                f"{name:<19} | "
                f"{metrics['total_trades']:>6} | "
                f"{metrics['win_rate']:>6.2f} | "
                f"{metrics['profit_factor']:>5.3f} | "
                f"{metrics['average_r']:>6.3f} | "
                f"{metrics['long']['total_trades']:>4} | "
                f"{metrics['short']['total_trades']:>5}"
            )

    print()
    print("Nota: V2.5 solo clasifica trades existentes; no filtra entradas.")



# ============================================================
# DIRECTIONAL ABLATION V2.6 — SALIDA
# ============================================================

def print_directional_ablation(result):
    """
    Imprime diagnóstico ALL / LONG / SHORT.
    """

    print()
    print("=" * 86)
    print(" DIRECTIONAL ABLATION V2.6")
    print("=" * 86)
    print("Hybrid permanece congelada. Esta prueba NO cambia señales.")

    def print_block(title, block):
        print()
        print(title)
        print("-" * 86)
        print(
            "MODE  | Trades | WR %   | PF    | Avg R   | Net Profit"
        )
        print("-" * 86)

        for mode in ("ALL", "LONG", "SHORT"):
            metrics = block[mode]

            print(
                f"{mode:<5} | "
                f"{metrics['total_trades']:>6} | "
                f"{metrics['win_rate']:>6.2f} | "
                f"{metrics['profit_factor']:>5.3f} | "
                f"{metrics['average_r']:>7.3f} | "
                f"{metrics['net_profit']:>10.2f}"
            )

    print_block(
        "DEVELOPMENT",
        result["development"],
    )

    print_block(
        "OUT-OF-SAMPLE",
        result["oos"],
    )

    wf = result["walk_forward"]

    print_block(
        "WALK-FORWARD — AGGREGATE OOS",
        wf["aggregate_oos"],
    )

    print()
    print("WALK-FORWARD — CONSISTENCY")
    print("-" * 86)
    print(
        "MODE  | Active | Positive | Negative | Neutral | Avg Window R"
    )
    print("-" * 86)

    for mode in ("ALL", "LONG", "SHORT"):
        item = wf["consistency"][mode]

        print(
            f"{mode:<5} | "
            f"{item['active_windows']:>6} | "
            f"{item['positive_windows']:>8} | "
            f"{item['negative_windows']:>8} | "
            f"{item['neutral_windows']:>7} | "
            f"{item['average_window_r']:>12.3f}"
        )

    print()
    print("WINDOW DETAIL")
    print("-" * 86)

    for window in wf["windows"]:
        all_m = window["directional"]["ALL"]
        long_m = window["directional"]["LONG"]
        short_m = window["directional"]["SHORT"]

        print(
            f"W{window['window']:>2} | "
            f"ALL {all_m['total_trades']:>2} / R {all_m['average_r']:+.3f} | "
            f"LONG {long_m['total_trades']:>2} / R {long_m['average_r']:+.3f} | "
            f"SHORT {short_m['total_trades']:>2} / R {short_m['average_r']:+.3f}"
        )

    print()
    print(
        "Nota: V2.6 es diagnóstica. No elimina LONG/SHORT "
        "ni modifica Hybrid."
    )


# ============================================================
# MAIN
# ============================================================

def _fmt_pf_v27(value):
    if value == float("inf"):
        return "inf"
    return f"{value:.3f}"


def print_direction_regime_audit_v27(result):
    print()
    print("=" * 94)
    print(" DIRECTION x REGIME AUDIT V2.7")
    print("=" * 94)

    cfg = result["configuration"]
    print(
        f"ATR: {cfg['atr_period']} | "
        f"Trend lookback: {cfg['trend_lookback']} | "
        f"Vol lookback: {cfg['vol_lookback']} | "
        f"Threshold: {cfg['trend_threshold']:.2f}"
    )
    print("Hybrid permanece congelada. No se filtran señales.")

    regime_order = [
        "TREND_HIGH_VOL",
        "TREND_LOW_VOL",
        "RANGE_HIGH_VOL",
        "RANGE_LOW_VOL",
        "WARMUP",
        "UNMATCHED",
    ]

    for section_key, section_name in (
        ("development", "DEVELOPMENT"),
        ("oos", "OUT-OF-SAMPLE"),
    ):
        print()
        print(section_name)
        print("-" * 94)
        print(
            "REGIME              | MODE  | Trades | WR %   | PF    | Avg R   | Net Profit"
        )
        print("-" * 94)

        matrix = result[section_key]["matrix"]

        for regime in regime_order:
            for mode in ("LONG", "SHORT"):
                m = matrix[regime][mode]
                print(
                    f"{regime:<19} | "
                    f"{mode:<5} | "
                    f"{m['total_trades']:>6} | "
                    f"{m['win_rate']:>6.2f} | "
                    f"{_fmt_pf_v27(m['profit_factor']):>5} | "
                    f"{m['average_r']:>7.3f} | "
                    f"{m['net_profit']:>10.2f}"
                )
            print("-" * 94)

    print()
    print(
        "Nota: V2.7 es diagnóstica y post-hoc; "
        "no simula una Hybrid LONG-only/SHORT-only."
    )



def _fmt_optional_v28(value, digits=3):
    if value is None:
        return "n/a"
    return f"{float(value):.{digits}f}"


def print_short_failure_audit_v28(result):
    print()
    print("=" * 112)
    print(" SHORT FAILURE AUDIT V2.8 — DEVELOPMENT ONLY")
    print("=" * 112)

    cfg = result["configuration"]
    print(
        f"Development candles: {result['development_candles']:,} | "
        f"SHORT trades: {result['short_trades']} | "
        f"HTF: {cfg['htf_granularity']}s | "
        f"Structure lookback: {cfg['structure_lookback']}"
    )
    print("Hybrid permanece congelada. Esta prueba NO filtra ni modifica SELL.")

    print()
    print("WINNERS vs LOSERS")
    print("-" * 112)
    print(
        "GROUP   | Trades | Avg R   | RSI    | BOS/ATR | Body/ATR | HTF ADX | EMA Sep/ATR | EMA200 Slope/ATR"
    )
    print("-" * 112)

    for key, label in (("winners", "WINNERS"), ("losers", "LOSERS")):
        s = result["summary"][key]
        print(
            f"{label:<7} | "
            f"{s['trades']:>6} | "
            f"{s['average_r']:>7.3f} | "
            f"{_fmt_optional_v28(s['avg_rsi']):>6} | "
            f"{_fmt_optional_v28(s['avg_bos_strength_atr']):>7} | "
            f"{_fmt_optional_v28(s['avg_body_atr']):>8} | "
            f"{_fmt_optional_v28(s['avg_htf_adx']):>7} | "
            f"{_fmt_optional_v28(s['avg_htf_ema_sep_atr']):>11} | "
            f"{_fmt_optional_v28(s['avg_htf_slope_atr']):>16}"
        )

    print()
    print("SHORT BY REGIME")
    print("-" * 112)
    print(
        "REGIME              | Trades | Wins | Loss | Avg R   | RSI    | BOS/ATR | HTF ADX"
    )
    print("-" * 112)

    for regime in (
        "TREND_HIGH_VOL",
        "TREND_LOW_VOL",
        "RANGE_HIGH_VOL",
        "RANGE_LOW_VOL",
        "WARMUP",
        "UNMATCHED",
    ):
        s = result["by_regime"][regime]
        print(
            f"{regime:<19} | "
            f"{s['trades']:>6} | "
            f"{s['wins']:>4} | "
            f"{s['losses']:>4} | "
            f"{s['average_r']:>7.3f} | "
            f"{_fmt_optional_v28(s['avg_rsi']):>6} | "
            f"{_fmt_optional_v28(s['avg_bos_strength_atr']):>7} | "
            f"{_fmt_optional_v28(s['avg_htf_adx']):>7}"
        )

    print()
    print("TRADE DETAIL")
    print("-" * 112)
    print(
        "#  | Result | R       | Regime              | RSI    | BOS/ATR | Body/ATR | HTF ADX | EMAsep/ATR | Slope/ATR"
    )
    print("-" * 112)

    for row in result["trades"]:
        print(
            f"{row['trade_no']:>2} | "
            f"{row['outcome']:<6} | "
            f"{row['r_multiple']:>+7.3f} | "
            f"{row['regime']:<19} | "
            f"{_fmt_optional_v28(row.get('rsi')):>6} | "
            f"{_fmt_optional_v28(row.get('bos_strength_atr')):>7} | "
            f"{_fmt_optional_v28(row.get('body_atr')):>8} | "
            f"{_fmt_optional_v28(row.get('htf_adx')):>7} | "
            f"{_fmt_optional_v28(row.get('htf_ema_sep_atr')):>10} | "
            f"{_fmt_optional_v28(row.get('htf_slope_atr')):>9}"
        )

    missing_bos = sum(
        1 for row in result["trades"]
        if not row.get("bos_found")
    )

    print()
    print(
        f"Chequeo de integridad: SHORT sin BOS bajista asociado = {missing_bos}."
    )
    print(
        "Nota: V2.8 usa solo DEVELOPMENT para formular hipótesis. "
        "No optimiza parámetros y no altera Hybrid."
    )


def print_short_feature_separation_v29(result):
    print()
    print("=" * 98)
    print(" SHORT FEATURE SEPARATION V2.9 — DEVELOPMENT ONLY")
    print("=" * 98)
    print(
        f"SHORT trades: {result['short_trades']} | "
        f"Avg R total: {result['summary']['average_r']:.3f}"
    )
    print("Hybrid permanece congelada. Los rangos NO son reglas de trading.")

    for feature_name, rows in result["feature_tables"].items():
        print()
        print(feature_name)
        print("-" * 72)
        print("BUCKET              | Trades | WR %   | PF    | Avg R")
        print("-" * 72)

        for row in rows:
            pf = row["profit_factor"]
            pf_text = "inf" if pf == float("inf") else f"{pf:.3f}"
            print(
                f"{row['bucket']:<19} | "
                f"{row['trades']:>6} | "
                f"{row['win_rate']:>6.2f} | "
                f"{pf_text:>5} | "
                f"{row['average_r']:>+6.3f}"
            )

    print()
    print("COMBINATION VIEW — EXPLORATORY ONLY")
    print("-" * 84)
    print("BUCKET                         | Trades | WR %   | PF    | Avg R")
    print("-" * 84)

    for row in result["combo_table"]:
        pf = row["profit_factor"]
        pf_text = "inf" if pf == float("inf") else f"{pf:.3f}"
        print(
            f"{row['bucket']:<30} | "
            f"{row['trades']:>6} | "
            f"{row['win_rate']:>6.2f} | "
            f"{pf_text:>5} | "
            f"{row['average_r']:>+6.3f}"
        )

    print()
    print(
        "Nota: V2.9 solo busca separación de características en DEVELOPMENT. "
        "No optimiza Hybrid y no valida ninguna regla."
    )


def _pf_text_v30(value):
    return "inf" if value == float("inf") else f"{value:.3f}"


def print_short_candidate_lab_v30(result):
    print()
    print("=" * 118)
    print(" SHORT CANDIDATE LAB V3.0 — TRUE RE-SIMULATION / DEVELOPMENT ONLY")
    print("=" * 118)
    print(
        f"Development candles: {result['development_candles']:,} | "
        "LONG congelado | OOS no utilizado"
    )
    print(
        "Cada candidato vuelve a pasar por el motor de riesgo completo. "
        "No se eliminan trades después del backtest."
    )

    print()
    print("SYSTEM RESULTS")
    print("-" * 118)
    print(
        "CANDIDATE              | Trades | WR %   | PF    | DD %  | Return % | Avg R  | SELL kept/seen | LONG same"
    )
    print("-" * 118)

    for row in result["candidates"]:
        a = row["all"]
        ss = row["signal_stats"]
        print(
            f"{row['candidate']:<22} | "
            f"{a['trades']:>6} | "
            f"{a['win_rate']:>6.2f} | "
            f"{_pf_text_v30(a['profit_factor']):>5} | "
            f"{a['max_drawdown_pct']:>5.2f} | "
            f"{a['total_return_pct']:>8.2f} | "
            f"{a['average_r']:>+6.3f} | "
            f"{ss['sell_kept']:>4}/{ss['sell_seen']:<4}      | "
            f"{str(row['long_trade_count_matches_base']):>9}"
        )

    print()
    print("SHORT ONLY")
    print("-" * 100)
    print(
        "CANDIDATE              | Trades | WR %   | PF    | Avg R   | Net Profit"
    )
    print("-" * 100)

    for row in result["candidates"]:
        s = row["short"]
        print(
            f"{row['candidate']:<22} | "
            f"{s['total_trades']:>6} | "
            f"{s['win_rate']:>6.2f} | "
            f"{_pf_text_v30(s['profit_factor']):>5} | "
            f"{s['average_r']:>+7.3f} | "
            f"{s['net_profit']:>+10.2f}"
        )

    print()
    print("CANDIDATE DEFINITIONS")
    print("-" * 118)
    for row in result["candidates"]:
        print(f"{row['candidate']}: {row['description']}")

    print()
    print(
        "ADVERTENCIA METODOLÓGICA: estos umbrales nacieron de DEVELOPMENT. "
        "El mejor resultado aquí NO constituye validación."
    )


def print_short_temporal_stability_v31(result):
    print()
    print("=" * 122)
    print(" SHORT TEMPORAL STABILITY V3.1 — DEVELOPMENT ONLY")
    print("=" * 122)
    print(
        f"Development candles: {result['development_candles']:,} | "
        f"Blocks: {result['blocks']} | OOS no utilizado"
    )
    print(
        "Cada bloque se re-simula de forma independiente. "
        "Buscamos consistencia temporal, no el PF más alto."
    )

    for candidate in (
        "BASE",
        "CANDIDATE_A_BOS",
        "CANDIDATE_B_EXTENSION",
    ):
        print()
        print(candidate)
        print("-" * 112)
        print(
            "Block | Candles | SHORT | WR %   | PF    | Avg R   | Net Profit | System PF | DD % | Return %"
        )
        print("-" * 112)

        for row in result["candidate_results"][candidate]:
            s = row["short"]
            a = row["all"]

            short_pf = s["profit_factor"]
            short_pf_txt = "inf" if short_pf == float("inf") else f"{short_pf:.3f}"

            sys_pf = a["profit_factor"]
            sys_pf_txt = "inf" if sys_pf == float("inf") else f"{sys_pf:.3f}"

            print(
                f"{row['block']:>5} | "
                f"{row['candles']:>7} | "
                f"{s['total_trades']:>5} | "
                f"{s['win_rate']:>6.2f} | "
                f"{short_pf_txt:>5} | "
                f"{s['average_r']:>+7.3f} | "
                f"{s['net_profit']:>+10.2f} | "
                f"{sys_pf_txt:>9} | "
                f"{a['max_drawdown_pct']:>4.2f} | "
                f"{a['total_return_pct']:>8.2f}"
            )

        summary = result["summaries"][candidate]
        print()
        print(
            "Stability summary: "
            f"active SHORT blocks={summary['active_short_blocks']} | "
            f"positive={summary['positive_short_blocks']} | "
            f"negative={summary['negative_short_blocks']} | "
            f"SHORT trades={summary['total_short_trades']} | "
            f"weighted Avg R={summary['weighted_short_avg_r']:+.3f} | "
            f"positive ratio={summary['positive_block_ratio']*100:.1f}%"
        )

    print()
    print(
        "Nota: una mejora con muy pocos SHORT o concentrada en uno solo de los bloques "
        "no se considera estable."
    )


def print_short_temporal_confirmation_v32(result):
    print()
    print("=" * 124)
    print(" SHORT TEMPORAL CONFIRMATION V3.2 — PRESERVED WARMUP / DEVELOPMENT ONLY")
    print("=" * 124)
    print(
        f"Development candles: {result['development_candles']:,} | "
        f"Blocks: {result['blocks']} | "
        f"Warmup max: {result['warmup_candles']:,} candles | OOS no utilizado"
    )

    for candidate in (
        "BASE",
        "CANDIDATE_A_BOS",
        "CANDIDATE_B_EXTENSION",
    ):
        print()
        print(candidate)
        print("-" * 114)
        print(
            "Block | Warmup | SHORT | WR %   | PF    | Avg R   | Net Profit | "
            "System Trades | System PF | System Avg R"
        )
        print("-" * 114)

        for row in result["candidate_results"][candidate]:
            s = row["short"]
            sysm = row["system"]

            spf = s["profit_factor"]
            spf_txt = "inf" if spf == float("inf") else f"{spf:.3f}"

            syspf = sysm["profit_factor"]
            syspf_txt = "inf" if syspf == float("inf") else f"{syspf:.3f}"

            print(
                f"{row['block']:>5} | "
                f"{row['warmup_used']:>6} | "
                f"{s['total_trades']:>5} | "
                f"{s['win_rate']:>6.2f} | "
                f"{spf_txt:>5} | "
                f"{s['average_r']:>+7.3f} | "
                f"{s['net_profit']:>+10.2f} | "
                f"{sysm['trades']:>13} | "
                f"{syspf_txt:>9} | "
                f"{sysm['average_r']:>+12.3f}"
            )

        summary = result["summaries"][candidate]
        print()
        print(
            "Confirmation summary: "
            f"active SHORT blocks={summary['active_short_blocks']} | "
            f"positive={summary['positive_short_blocks']} | "
            f"negative={summary['negative_short_blocks']} | "
            f"SHORT trades={summary['total_short_trades']} | "
            f"weighted Avg R={summary['weighted_short_avg_r']:+.3f} | "
            f"positive ratio={summary['positive_block_ratio']*100:.1f}%"
        )

    print()
    print(
        "Criterio práctico: preferimos un candidato con expectativa positiva "
        "repetida en varios bloques y una muestra razonable, no uno con PF enorme "
        "por 2 o 3 operaciones."
    )

def main():

    parser = argparse.ArgumentParser(
        description="Risk Backtester V2"
    )

    # ========================================================
    # ARGUMENTOS GENERALES
    # ========================================================

    parser.add_argument(
        "--symbol",
        default="CRASH500",
    )

    parser.add_argument(
        "--granularity",
        type=int,
        default=60,
    )

    parser.add_argument(
        "--strategy",
        default="hybrid",
    )

    parser.add_argument(
        "--development-ratio",
        type=float,
        default=0.70,
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=0,
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help=(
            "Muestra también el resultado "
            "completo en JSON."
        ),
    )

    # ========================================================
    # WALK-FORWARD V2.1
    # ========================================================

    parser.add_argument(
        "--walk-forward",
        action="store_true",
        help=(
            "Ejecuta validación "
            "Walk-Forward V2.1."
        ),
    )

    parser.add_argument(
        "--development-size",
        type=int,
        default=12000,
    )

    parser.add_argument(
        "--oos-size",
        type=int,
        default=5000,
    )

    parser.add_argument(
        "--step-size",
        type=int,
        default=5000,
    )

    # ========================================================
    # STRESS TEST V2.2
    # ========================================================

    parser.add_argument(
        "--stress-test",
        action="store_true",
        help=(
            "Ejecuta Stress Test "
            "de costos V2.2."
        ),
    )

    # ========================================================
    # SENSITIVITY TEST V2.3
    # ========================================================

    parser.add_argument(
        "--sensitivity-test",
        action="store_true",
        help=(
            "Ejecuta Sensitivity Test "
            "V2.3 sobre DEVELOPMENT."
        ),
    )

    # ========================================================
    # MONTE CARLO V2.4
    # ========================================================

    parser.add_argument(
        "--monte-carlo",
        action="store_true",
        help=(
            "Ejecuta Monte Carlo V2.4 "
            "sobre DEVELOPMENT."
        ),
    )

    parser.add_argument(
        "--mc-iterations",
        type=int,
        default=10000,
        help="Número de simulaciones Monte Carlo.",
    )

    parser.add_argument(
        "--mc-seed",
        type=int,
        default=20260830,
        help="Semilla reproducible Monte Carlo.",
    )

    # ========================================================
    # REGIME ROBUSTNESS V2.5
    # ========================================================

    parser.add_argument(
        "--regime-test",
        action="store_true",
        help=(
            "Ejecuta Robustez por Régimen V2.5 "
            "en DEVELOPMENT y OOS."
        ),
    )

    # ========================================================
    # DIRECTIONAL ABLATION V2.6
    # ========================================================

    parser.add_argument(
        "--direction-test",
        action="store_true",
        help=(
            "Ejecuta Directional Ablation V2.6 "
            "sin modificar Hybrid."
        ),
    )

    # ========================================================
    # DIRECTION x REGIME AUDIT V2.7
    # ========================================================

    parser.add_argument(
        "--direction-regime-test",
        action="store_true",
        help=(
            "Ejecuta Direction x Regime Audit V2.7 "
            "sin modificar Hybrid."
        ),
    )

    # ========================================================
    # SHORT FAILURE AUDIT V2.8
    # ========================================================

    parser.add_argument(
        "--short-audit",
        action="store_true",
        help=(
            "Ejecuta SHORT Failure Audit V2.8 "
            "solo sobre DEVELOPMENT, sin modificar Hybrid."
        ),
    )

    # ========================================================
    # SHORT FEATURE SEPARATION V2.9
    # ========================================================

    parser.add_argument(
        "--short-feature-test",
        action="store_true",
        help=(
            "Ejecuta SHORT Feature Separation V2.9 "
            "sobre DEVELOPMENT sin modificar Hybrid."
        ),
    )

    # ========================================================
    # SHORT CANDIDATE LAB V3.0
    # ========================================================

    parser.add_argument(
        "--short-candidate-lab",
        action="store_true",
        help=(
            "Ejecuta V3.0 SHORT Candidate Lab con re-simulación real "
            "sobre DEVELOPMENT y LONG congelado."
        ),
    )

    # ========================================================
    # SHORT TEMPORAL STABILITY V3.1
    # ========================================================

    parser.add_argument(
        "--short-temporal-stability",
        action="store_true",
        help=(
            "Ejecuta V3.1 SHORT Temporal Stability sobre DEVELOPMENT "
            "en bloques cronológicos."
        ),
    )

    # ========================================================
    # SHORT TEMPORAL CONFIRMATION V3.2
    # ========================================================

    parser.add_argument(
        "--short-temporal-confirmation",
        action="store_true",
        help=(
            "Ejecuta V3.2 con warmup preservado para confirmar "
            "estabilidad temporal de los candidatos SHORT."
        ),
    )

    # ========================================================
    # PARSE
    # ========================================================

    args = parser.parse_args()

    # Solo se permite un modo especial por ejecución.
    special_modes = sum(
        [
            bool(args.walk_forward),
            bool(args.stress_test),
            bool(args.sensitivity_test),
            bool(args.monte_carlo),
            bool(args.regime_test),
            bool(args.direction_test),
            bool(args.direction_regime_test),
            bool(args.short_audit),
            bool(args.short_feature_test),
            bool(args.short_candidate_lab),
            bool(args.short_temporal_stability),
            bool(args.short_temporal_confirmation),
        ]
    )

    if special_modes > 1:
        parser.error(
            "Usa solamente uno de estos modos: "
            "--walk-forward, --stress-test, "
            "--sensitivity-test, --monte-carlo o "
            "--regime-test, --direction-test o "
            "--direction-regime-test, --short-audit, --short-feature-test, --short-candidate-lab, --short-temporal-stability o --short-temporal-confirmation."
        )

    # ========================================================
    # VALIDACIÓN DE ESTRATEGIA
    # ========================================================

    validate_strategy_id(
        args.strategy
    )

    # ========================================================
    # RESOLVER DATASET
    # ========================================================

    symbol = resolve_dataset(
        args.symbol,
        args.granularity,
    )

    rows = get_candles(
        symbol,
        args.granularity,
        limit=args.limit or None,
    )

    if not rows:

        raise RuntimeError(
            "No se encontraron velas."
        )

    # ========================================================
    # OHLC
    # ========================================================

    epochs = [
        r[0]
        for r in rows
    ]

    opens = [
        r[1]
        for r in rows
    ]

    highs = [
        r[2]
        for r in rows
    ]

    lows = [
        r[3]
        for r in rows
    ]

    closes = [
        r[4]
        for r in rows
    ]

    # ========================================================
    # CABECERA
    # ========================================================

    print()
    print("=" * 60)
    print(" RISK BACKTESTER V2")
    print("=" * 60)

    print(
        f"Strategy:    "
        f"{args.strategy}"
    )

    print(
        f"Dataset:     "
        f"{symbol}"
    )

    print(
        f"Granularity: "
        f"{args.granularity}s"
    )

    print(
        f"Candles:     "
        f"{len(rows):,}"
    )

    # ========================================================
    # SHORT TEMPORAL CONFIRMATION V3.2
    # ========================================================

    if args.short_temporal_confirmation:

        result = run_short_temporal_confirmation_v32(
            strategy_id=args.strategy,
            epochs=epochs,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            granularity=args.granularity,
            development_ratio=args.development_ratio,
        )

        print_short_temporal_confirmation_v32(result)

        if args.json:
            print()
            print("=" * 60)
            print("JSON COMPLETO")
            print("=" * 60)
            print(
                json.dumps(
                    result,
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            )

        return

    # ========================================================
    # SHORT TEMPORAL STABILITY V3.1
    # ========================================================

    if args.short_temporal_stability:

        result = run_short_temporal_stability_v31(
            strategy_id=args.strategy,
            epochs=epochs,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            granularity=args.granularity,
            development_ratio=args.development_ratio,
        )

        print_short_temporal_stability_v31(result)

        if args.json:
            print()
            print("=" * 60)
            print("JSON COMPLETO")
            print("=" * 60)
            print(
                json.dumps(
                    result,
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            )

        return

    # ========================================================
    # SHORT CANDIDATE LAB V3.0
    # ========================================================

    if args.short_candidate_lab:

        result = run_short_candidate_lab_v30(
            strategy_id=args.strategy,
            epochs=epochs,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            granularity=args.granularity,
            development_ratio=args.development_ratio,
        )

        print_short_candidate_lab_v30(result)

        if args.json:
            print()
            print("=" * 60)
            print("JSON COMPLETO")
            print("=" * 60)
            print(
                json.dumps(
                    result,
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            )

        return

    # ========================================================
    # SHORT FEATURE SEPARATION V2.9
    # ========================================================

    if args.short_feature_test:

        result = run_short_feature_separation_v29(
            strategy_id=args.strategy,
            epochs=epochs,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            granularity=args.granularity,
            development_ratio=args.development_ratio,
        )

        print_short_feature_separation_v29(result)

        if args.json:
            print()
            print("=" * 60)
            print("JSON COMPLETO")
            print("=" * 60)
            print(
                json.dumps(
                    result,
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            )

        return

    # ========================================================
    # SHORT FAILURE AUDIT V2.8
    # ========================================================

    if args.short_audit:

        result = run_short_failure_audit_v28(
            strategy_id=args.strategy,
            epochs=epochs,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            granularity=args.granularity,
            development_ratio=args.development_ratio,
        )

        print_short_failure_audit_v28(result)

        if args.json:
            print()
            print("=" * 60)
            print("JSON COMPLETO")
            print("=" * 60)
            print(
                json.dumps(
                    result,
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            )

        return

    # ========================================================
    # DIRECTION x REGIME AUDIT V2.7
    # ========================================================

    if args.direction_regime_test:

        result = run_direction_regime_audit_v27(
            strategy_id=args.strategy,
            epochs=epochs,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            granularity=args.granularity,
            development_ratio=args.development_ratio,
        )

        print_direction_regime_audit_v27(result)

        if args.json:
            print()
            print("=" * 60)
            print("JSON COMPLETO")
            print("=" * 60)
            print(
                json.dumps(
                    result,
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            )

        return

    # ========================================================
    # DIRECTIONAL ABLATION V2.6
    # ========================================================

    if args.direction_test:

        result = run_directional_ablation_v26(
            strategy_id=args.strategy,
            epochs=epochs,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            granularity=args.granularity,
            development_ratio=args.development_ratio,
            development_size=args.development_size,
            oos_size=args.oos_size,
            step_size=args.step_size,
        )

        print_directional_ablation(result)

        if args.json:
            print()
            print("=" * 60)
            print("JSON COMPLETO")
            print("=" * 60)

            print(
                json.dumps(
                    result,
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            )

        return

    # ========================================================
    # REGIME ROBUSTNESS V2.5
    # ========================================================

    if args.regime_test:

        result = run_regime_test_v25(
            strategy_id=args.strategy,
            epochs=epochs,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            granularity=args.granularity,
            development_ratio=args.development_ratio,
        )

        print_regime_test(result)

        if args.json:
            print()
            print("=" * 60)
            print("JSON COMPLETO")
            print("=" * 60)

            print(
                json.dumps(
                    result,
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            )

        return

    # ========================================================
    # MONTE CARLO V2.4
    # ========================================================

    if args.monte_carlo:

        result = run_monte_carlo_v24(
            strategy_id=args.strategy,
            epochs=epochs,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            granularity=args.granularity,
            development_ratio=args.development_ratio,
            iterations=args.mc_iterations,
            random_seed=args.mc_seed,
        )

        print_monte_carlo(result)

        if args.json:
            print()
            print("=" * 60)
            print("JSON COMPLETO")
            print("=" * 60)

            print(
                json.dumps(
                    result,
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            )

        return

    # ========================================================
    # SENSITIVITY TEST V2.3
    # ========================================================

    if args.sensitivity_test:

        result = run_sensitivity_test_v23(
            strategy_id=args.strategy,
            epochs=epochs,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            granularity=args.granularity,
            development_ratio=args.development_ratio,
        )

        print_sensitivity_test(result)

        if args.json:
            print()
            print("=" * 60)
            print("JSON COMPLETO")
            print("=" * 60)

            print(
                json.dumps(
                    result,
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            )

        return

    # ========================================================
    # STRESS TEST V2.2
    # ========================================================

    if args.stress_test:

        result = run_stress_test_v22(
            strategy_id=args.strategy,
            epochs=epochs,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            granularity=args.granularity,
        )

        print_stress_test(
            result
        )

        if args.json:

            print()
            print("=" * 60)
            print("JSON COMPLETO")
            print("=" * 60)

            print(
                json.dumps(
                    result,
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            )

        return

    # ========================================================
    # WALK-FORWARD V2.1
    # ========================================================

    if args.walk_forward:

        result = run_walk_forward_v21(
            strategy_id=args.strategy,
            epochs=epochs,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            granularity=args.granularity,
            development_size=(
                args.development_size
            ),
            oos_size=(
                args.oos_size
            ),
            step_size=(
                args.step_size
            ),
        )

        print_walk_forward(
            result
        )

        if args.json:

            print()
            print("=" * 60)
            print("JSON COMPLETO")
            print("=" * 60)

            print(
                json.dumps(
                    result,
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            )

        return

    # ========================================================
    # V2.0 — DEVELOPMENT / OOS
    # ========================================================

    result = run_risk_backtest_v2(
        strategy_id=args.strategy,
        epochs=epochs,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        granularity=args.granularity,
        development_ratio=(
            args.development_ratio
        ),
    )

    split = result["split"]

    print()
    print("DATA SPLIT")
    print("-" * 60)

    print(
        f"Development: "
        f"{split['development_candles']:,} "
        f"velas "
        f"("
        f"{split['development_ratio'] * 100:.0f}%"
        f")"
    )

    print(
        f"OOS:         "
        f"{split['oos_candles']:,} "
        f"velas "
        f"("
        f"{split['oos_ratio'] * 100:.0f}%"
        f")"
    )

    # --------------------------------------------------------
    # DEVELOPMENT
    # --------------------------------------------------------

    print_metrics(
        "DEVELOPMENT",
        result["development"],
    )

    # --------------------------------------------------------
    # OOS
    # --------------------------------------------------------

    print_metrics(
        "OUT-OF-SAMPLE",
        result["oos"],
    )

    # --------------------------------------------------------
    # ESTABILIDAD
    # --------------------------------------------------------

    stability = (
        result["stability"]
    )

    print()
    print("=" * 60)
    print("STABILITY")
    print("=" * 60)

    print(
        f"Status: "
        f"{stability['status']}"
    )

    print(
        f"Development trades: "
        f"{stability['development_trades']}"
    )

    print(
        f"OOS trades: "
        f"{stability['oos_trades']}"
    )

    print()
    print(
        "Cambio DEVELOPMENT -> OOS"
    )

    for metric, value in (
        stability[
            "degradation_pct"
        ].items()
    ):

        if value is None:

            print(
                f"{metric}: N/A"
            )

        else:

            print(
                f"{metric}: "
                f"{value:+.2f}%"
            )

    # ========================================================
    # JSON
    # ========================================================

    if args.json:

        print()
        print("=" * 60)
        print("JSON COMPLETO")
        print("=" * 60)

        print(
            json.dumps(
                result,
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()