"""Ejecuta el backtest registrado y exporta reportes comparables."""

import argparse
import json
from datetime import datetime
from pathlib import Path

from backtesting.reporting import export_backtest_reports
from config import (
    BACKTEST_COMMISSION_PER_TRADE_USD,
    BACKTEST_RANDOM_SEED,
    BACKTEST_REPORTS_DIR,
    BACKTEST_SLIPPAGE_ATR_MAX,
    INITIAL_BALANCE,
    LIVE_GRANULARITY,
    LIVE_STRATEGY,
    LIVE_SYMBOL,
)
from database.database import get_available_datasets, get_candles
from strategy.registry import run_registered_backtest, validate_strategy_id


def _resolve_dataset(symbol_query, granularity):
    datasets = get_available_datasets()
    target = str(symbol_query).strip().lower()

    matches = [
        d for d in datasets
        if int(d["granularity"]) == int(granularity)
    ]

    exact = [
        d for d in matches
        if str(d["symbol"]).lower() == target
    ]

    if exact:
        return exact[0]["symbol"]

    contains = [
        d for d in matches
        if target in str(d["symbol"]).lower()
        or str(d["symbol"]).lower() in target
    ]

    if contains:
        return contains[0]["symbol"]

    crash = [
        d for d in matches
        if "crash500" in str(d["symbol"]).lower()
        and "crash" in target
    ]

    if crash:
        return crash[0]["symbol"]

    available = ", ".join(
        f'{d["symbol"]}/{d["granularity"]}s'
        for d in datasets
    )

    raise RuntimeError(
        f"No existe dataset para {symbol_query!r}/{granularity}s. "
        f"Disponibles: {available}"
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--symbol",
        default=LIVE_SYMBOL
    )

    parser.add_argument(
        "--granularity",
        type=int,
        default=LIVE_GRANULARITY
    )

    parser.add_argument(
        "--strategy",
        default=LIVE_STRATEGY
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="0 usa todas las velas"
    )

    parser.add_argument(
        "--output",
        default=None
    )

    args = parser.parse_args()

    validate_strategy_id(args.strategy)

    symbol = _resolve_dataset(
        args.symbol,
        args.granularity
    )

    rows = get_candles(
        symbol,
        args.granularity,
        limit=args.limit or None
    )

    if len(rows) < 220:
        raise RuntimeError(
            f"Solo hay {len(rows)} velas; "
            f"se requieren al menos 220"
        )

    # ============================
    # DATOS OHLC
    # ============================

    epochs = [r[0] for r in rows]

    opens = [r[1] for r in rows]

    highs = [r[2] for r in rows]

    lows = [r[3] for r in rows]

    closes = [r[4] for r in rows]

    # ============================
    # BACKTEST
    # ============================

    result = run_registered_backtest(
        args.strategy,
        epochs,
        highs,
        lows,
        closes,
        args.granularity,
        opens=opens,
    )

    # ============================
    # EXPORTAR REPORTE
    # ============================

    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    output = (
        Path(args.output or BACKTEST_REPORTS_DIR)
        / f"{symbol}_{args.strategy}_{stamp}"
    )

    report = export_backtest_reports(
        result,
        output,
        initial_balance=INITIAL_BALANCE,
        metadata={
            "symbol": symbol,
            "granularity": args.granularity,
            "strategy": args.strategy,
            "candles": len(rows),
            "slippage_atr_max": BACKTEST_SLIPPAGE_ATR_MAX,
            "random_seed": BACKTEST_RANDOM_SEED,
            "commission_per_trade_usd": BACKTEST_COMMISSION_PER_TRADE_USD,
        },
    )

    print(
        json.dumps(
            {
                "metrics": result["metrics"],
                **report,
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    )


if __name__ == "__main__":
    main()