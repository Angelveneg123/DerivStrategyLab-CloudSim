"""Compara las últimas 100 operaciones CFD DEMO con 100 del backtest.

No fuerza equivalencia operación por operación: compara muestras de igual tamaño
con la misma estrategia, símbolo y granularidad. También resume spread,
slippage, comisión, swap y fee observados por MT5 para calibrar el backtest.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from config import INITIAL_BALANCE, LIVE_GRANULARITY, LIVE_STRATEGY
from database.database import create_live_runtime_tables, get_candles, get_connection
from strategy.registry import run_registered_backtest


def _sample_metrics(profits, initial_balance):
    balance = float(initial_balance)
    peak = balance
    max_dd = 0.0
    wins = [p for p in profits if p > 0]
    losses = [p for p in profits if p <= 0]
    for profit in profits:
        balance += float(profit)
        peak = max(peak, balance)
        if peak > 0:
            max_dd = max(max_dd, (peak - balance) / peak * 100)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "total_trades": len(profits),
        "win_rate": len(wins) / len(profits) * 100 if profits else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else (float("inf") if gross_profit else 0.0),
        "max_drawdown_pct": max_dd,
        "total_return_pct": (balance - initial_balance) / initial_balance * 100 if initial_balance else 0.0,
        "final_balance": balance,
        "average_profit_usd": sum(profits) / len(profits) if profits else 0.0,
    }


def _relative_difference(a, b):
    a, b = float(a), float(b)
    if not math.isfinite(a) or not math.isfinite(b):
        return 0.0 if a == b else float("inf")
    denominator = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denominator * 100


def _comparison(demo, backtest, tolerance):
    rows = {}
    for key in ("win_rate", "profit_factor", "max_drawdown_pct", "total_return_pct", "average_profit_usd"):
        demo_value = float(demo.get(key, 0) or 0)
        bt_value = float(backtest.get(key, 0) or 0)
        # Win rate y drawdown se entienden mejor como puntos porcentuales.
        if key in {"win_rate", "max_drawdown_pct"}:
            difference = abs(demo_value - bt_value)
            unit = "puntos_porcentuales"
        else:
            difference = _relative_difference(demo_value, bt_value)
            unit = "porcentaje_relativo"
        rows[key] = {
            "demo": demo_value,
            "backtest": bt_value,
            "difference": difference,
            "unit": unit,
            "within_tolerance": difference <= tolerance,
        }
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--mode", choices=("demo", "real"), default="demo")
    parser.add_argument("--tolerance", type=float, default=5.0)
    parser.add_argument("--output", default="reports/validation")
    args = parser.parse_args()
    create_live_runtime_tables()

    conn = get_connection(row_factory=True)
    rows = conn.execute(
        """
        SELECT * FROM broker_trades
        WHERE mode = ? AND execution_product = 'cfd_standard'
          AND status = 'closed' AND COALESCE(net_profit, profit) IS NOT NULL
        ORDER BY id DESC LIMIT ?
        """,
        (args.mode, int(args.count)),
    ).fetchall()
    conn.close()
    trades = [dict(r) for r in reversed(rows)]

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "required_sample_each": args.count,
        "demo_cfd_trades_found": len(trades),
        "tolerance": args.tolerance,
        "status": "insufficient_demo_sample",
        "passed": False,
    }
    if len(trades) < args.count:
        report["message"] = (
            f"Faltan {args.count - len(trades)} operaciones CFD Standard DEMO cerradas. "
            "Los contratos Multiplier anteriores no se mezclan con esta comparación."
        )
    else:
        symbol = trades[-1]["symbol_code"]
        granularity = int(trades[-1]["granularity"] or LIVE_GRANULARITY)
        strategy = trades[-1]["strategy_id"] or LIVE_STRATEGY
        candles = get_candles(symbol, granularity)
        if len(candles) < 220:
            report["status"] = "insufficient_candles"
            report["message"] = f"Solo hay {len(candles)} velas para {symbol}/{granularity}s."
        else:
            epochs = [r[0] for r in candles]
            highs = [r[2] for r in candles]
            lows = [r[3] for r in candles]
            closes = [r[4] for r in candles]
            result = run_registered_backtest(strategy, epochs, highs, lows, closes, granularity)
            bt_trades = list(result["trades"])[-args.count:]
            if len(bt_trades) < args.count:
                report["status"] = "insufficient_backtest_sample"
                report["message"] = f"El backtest solo produjo {len(bt_trades)} operaciones; se requieren {args.count}."
            else:
                initial = float(INITIAL_BALANCE)
                demo_profits = [float(t.get("net_profit") if t.get("net_profit") is not None else t.get("profit") or 0) for t in trades]
                bt_profits = [float(t.get("profit") or 0) for t in bt_trades]
                demo_metrics = _sample_metrics(demo_profits, initial)
                bt_metrics = _sample_metrics(bt_profits, initial)
                comparison = _comparison(demo_metrics, bt_metrics, args.tolerance)
                passed = all(item["within_tolerance"] for item in comparison.values())
                spread_atr_ratios = []
                slippage_atr_ratios = []
                for trade in trades:
                    try:
                        raw = json.loads(trade.get("raw_open_json") or "{}")
                        plan = raw.get("plan") or {}
                        atr = float(plan.get("atr") or 0)
                        point = float(plan.get("point") or 0)
                        if atr > 0 and point > 0:
                            spread_atr_ratios.append(float(trade.get("spread_points") or 0) * point / atr)
                            slippage_atr_ratios.append(float(trade.get("slippage_points") or 0) * point / atr)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        pass
                total_commission = sum(float(t.get("commission") or 0) for t in trades)
                total_swap = sum(float(t.get("swap") or 0) for t in trades)
                total_fee = sum(float(t.get("fee") or 0) for t in trades)
                report.update({
                    "status": "passed" if passed else "outside_tolerance",
                    "passed": passed,
                    "symbol": symbol,
                    "granularity": granularity,
                    "strategy": strategy,
                    "demo_metrics": demo_metrics,
                    "backtest_metrics": bt_metrics,
                    "comparison": comparison,
                    "observed_cfd_costs": {
                        "average_spread_points": sum(float(t.get("spread_points") or 0) for t in trades) / len(trades),
                        "average_slippage_points": sum(float(t.get("slippage_points") or 0) for t in trades) / len(trades),
                        "average_spread_atr_fraction": sum(spread_atr_ratios) / len(spread_atr_ratios) if spread_atr_ratios else None,
                        "average_entry_slippage_atr_fraction": sum(slippage_atr_ratios) / len(slippage_atr_ratios) if slippage_atr_ratios else None,
                        "total_commission": total_commission,
                        "total_swap": total_swap,
                        "total_fee": total_fee,
                    },
                    "recommended_backtest_calibration": {
                        "SPREAD_ATR_FRAC": sum(spread_atr_ratios) / len(spread_atr_ratios) if spread_atr_ratios else None,
                        "BACKTEST_SLIPPAGE_ATR_MAX": max(slippage_atr_ratios) if slippage_atr_ratios else None,
                        "BACKTEST_COMMISSION_PER_TRADE_USD": max(0.0, -(total_commission + total_fee) / len(trades)),
                    },
                    "note": "La prueba usa 100 operaciones por muestra; no mezcla contratos Multiplier con CFDs.",
                })

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"demo_vs_backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({**report, "report_file": str(path.resolve())}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
