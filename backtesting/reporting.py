"""Exportación reproducible de resultados de backtesting."""

from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path


def _json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _equity_points(trades, initial_balance):
    balance = float(initial_balance)
    points = [(0, balance)]
    for index, trade in enumerate(trades, start=1):
        balance += float(trade.get("profit") or 0.0)
        points.append((index, balance))
    return points


def export_backtest_reports(result, output_dir, *, initial_balance, metadata=None):
    """Crea equity_curve.png, trades.csv, summary.json y metrics.json."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    trades = list(result.get("trades") or [])
    metrics = dict(result.get("metrics") or {})
    metadata = dict(metadata or {})
    generated_at = datetime.now(timezone.utc).isoformat()

    columns = []
    for trade in trades:
        for key in trade:
            if key not in columns:
                columns.append(key)
    with (output / "trades.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns or ["sin_operaciones"])
        writer.writeheader()
        if trades:
            writer.writerows(trades)

    safe_metrics = _json_safe(metrics)
    with (output / "metrics.json").open("w", encoding="utf-8") as fh:
        json.dump(safe_metrics, fh, ensure_ascii=False, indent=2)

    summary = {
        "generated_at_utc": generated_at,
        "initial_balance": float(initial_balance),
        "trades_count": len(trades),
        "metadata": metadata,
        "metrics": safe_metrics,
        "model": {
            "slippage": "adverso y reproducible; máximo configurable como fracción de ATR",
            "spread": "estimación histórica ATR; demo MT5 usa bid/ask observado",
            "commission": "configurable; demo MT5 registra commission/swap/fee reales",
        },
    }
    with (output / "summary.json").open("w", encoding="utf-8") as fh:
        json.dump(_json_safe(summary), fh, ensure_ascii=False, indent=2)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("Falta matplotlib para crear equity_curve.png") from exc

    points = _equity_points(trades, initial_balance)
    x = [item[0] for item in points]
    y = [item[1] for item in points]
    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.plot(x, y)
    ax.set_title("Curva de capital")
    ax.set_xlabel("Operaciones cerradas")
    ax.set_ylabel("Balance")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(output / "equity_curve.png", dpi=160)
    plt.close(fig)

    return {
        "output_dir": str(output.resolve()),
        "files": [
            str((output / "equity_curve.png").resolve()),
            str((output / "trades.csv").resolve()),
            str((output / "summary.json").resolve()),
            str((output / "metrics.json").resolve()),
        ],
    }
