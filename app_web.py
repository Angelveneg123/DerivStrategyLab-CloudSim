"""Dashboard Flask para backtesting y trading conectado."""

import re
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from backtesting.reporting import export_backtest_reports
from config import (
    BACKTEST_COMMISSION_PER_TRADE_USD,
    BACKTEST_RANDOM_SEED,
    BACKTEST_REPORTS_DIR,
    BACKTEST_SLIPPAGE_ATR_MAX,
    DASHBOARD_REFRESH_SECONDS,
    INITIAL_BALANCE,
    LIVE_ALLOCATED_CAPITAL_USD,
    LIVE_DAILY_LOSS_LIMIT_PCT,
    LIVE_EXECUTION_PRODUCT,
    LIVE_MAX_CONSECUTIVE_LOSSES,
    LIVE_MAX_TRADES_PER_DAY,
    LIVE_REINVEST_PROFITS,
    REAL_TRADING_ENABLED,
    RIESGO_POR_OPERACION_PCT,
    SPREAD_ATR_FRAC,
)
from database.database import (
    create_live_runtime_tables,
    get_available_datasets,
    get_bot_state,
    get_broker_metrics,
    get_broker_trades,
    get_candles,
    get_equity_curve,
    get_live_events,
    get_open_broker_trade,
)
from strategy.registry import DEFAULT_STRATEGY, STRATEGIES, run_registered_backtest

app = Flask(__name__)
VELAS_EN_GRAFICO = 1500
create_live_runtime_tables()


def _live_mode():
    mode = request.args.get("mode", "demo").lower().strip()
    return mode if mode in {"demo", "real"} else "demo"


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; connect-src 'self'",
    )
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/")
def dashboard():
    datasets = get_available_datasets()
    if not datasets:
        return render_template(
            "empty.html",
            message="No hay datos históricos descargados todavía.",
        )

    symbol = request.args.get("symbol", datasets[0]["symbol"])
    granularity = request.args.get(
        "granularity", datasets[0]["granularity"], type=int
    )
    strategy_id = request.args.get("estrategia", DEFAULT_STRATEGY)
    if strategy_id not in STRATEGIES:
        strategy_id = DEFAULT_STRATEGY

    rows = get_candles(symbol, granularity)
    if not rows:
        return render_template(
            "empty.html",
            message=f"No hay velas guardadas para {symbol} ({granularity}s).",
        )

    epochs = [row[0] for row in rows]
    opens = [row[1] for row in rows]
    highs = [row[2] for row in rows]
    lows = [row[3] for row in rows]
    closes = [row[4] for row in rows]
    try:
        result = run_registered_backtest(
            strategy_id,
            epochs,
            highs,
            lows,
            closes,
            source_granularity=granularity,
            opens=opens,
        )
    except ValueError as exc:
        # Pasa cuando el símbolo/granularidad elegido no tiene todavía
        # suficiente historial para la estrategia (ej. EMA200 en H4).
        # En vez de tronar con un traceback, avisamos qué falta y cómo
        # resolverlo, igual que ya se hace cuando no hay datos en absoluto.
        return render_template(
            "empty.html",
            message=(
                f"No hay suficiente historial para calcular '{STRATEGIES[strategy_id]}' "
                f"sobre {symbol} ({granularity}s): {exc} "
                f"Descarga más historial con: "
                f"python download_historical.py {symbol} {granularity}"
            ),
        )
    metrics = result["metrics"]
    trades = result["trades"]

    # El dashboard sobrescribe una carpeta "latest" por combinación, de modo
    # que cada backtest terminado deja sus cuatro reportes sin crear miles de
    # copias al refrescar el navegador.
    safe_symbol = re.sub(r"[^A-Za-z0-9_.-]+", "_", symbol).strip("_") or "symbol"
    report_dir = Path(BACKTEST_REPORTS_DIR) / f"latest_{safe_symbol}_{granularity}s_{strategy_id}"
    report_info = None
    report_error = None
    try:
        report_info = export_backtest_reports(
            result,
            report_dir,
            initial_balance=INITIAL_BALANCE,
            metadata={
                "symbol": symbol,
                "granularity": granularity,
                "strategy": strategy_id,
                "candles": len(closes),
                "slippage_atr_max": BACKTEST_SLIPPAGE_ATR_MAX,
                "random_seed": BACKTEST_RANDOM_SEED,
                "commission_per_trade_usd": BACKTEST_COMMISSION_PER_TRADE_USD,
            },
        )
    except Exception as exc:
        report_error = str(exc)

    chart_epochs = epochs[-VELAS_EN_GRAFICO:]
    chart_closes = closes[-VELAS_EN_GRAFICO:]
    index_by_epoch = {epoch: index for index, epoch in enumerate(chart_epochs)}
    buy_points = [None] * len(chart_closes)
    sell_points = [None] * len(chart_closes)
    for trade in trades:
        index = index_by_epoch.get(trade["entry_epoch"])
        if index is None:
            continue
        if trade["direction"] == "BUY":
            buy_points[index] = trade["entry_price"]
        else:
            sell_points[index] = trade["entry_price"]

    return render_template(
        "index.html",
        symbol=symbol,
        granularity=granularity,
        datasets=datasets,
        estrategias={key: {"nombre": value} for key, value in STRATEGIES.items()},
        estrategia_id=strategy_id,
        estrategia_nombre=STRATEGIES[strategy_id],
        candle_count=len(closes),
        initial_balance=INITIAL_BALANCE,
        riesgo_por_operacion_pct=RIESGO_POR_OPERACION_PCT,
        spread_atr_frac=SPREAD_ATR_FRAC,
        metrics=metrics,
        trades_table=list(reversed(trades)),
        trades_ticker=trades[-10:] if trades else [],
        chart_labels=[str(epoch) for epoch in chart_epochs],
        chart_closes=chart_closes,
        chart_buy_points=buy_points,
        chart_sell_points=sell_points,
        report_info=report_info,
        report_error=report_error,
    )


@app.route("/live")
def live_dashboard():
    mode = _live_mode()
    return render_template(
        "live.html",
        mode=mode,
        refresh_seconds=DASHBOARD_REFRESH_SECONDS,
        real_enabled=REAL_TRADING_ENABLED,
        configured_allocation=LIVE_ALLOCATED_CAPITAL_USD,
        configured_product=LIVE_EXECUTION_PRODUCT,
    )


@app.route("/api/live/status")
def live_status_api():
    mode = _live_mode()
    state = get_bot_state(mode)
    metrics = get_broker_metrics(mode, LIVE_EXECUTION_PRODUCT)
    open_trade = get_open_broker_trade(mode)
    return jsonify(
        {
            "mode": mode,
            "state": state,
            "metrics": metrics,
            "open_trade": open_trade,
            "limits": {
                "configured_allocation": LIVE_ALLOCATED_CAPITAL_USD,
                "execution_product": LIVE_EXECUTION_PRODUCT,
                "risk_per_trade_pct": RIESGO_POR_OPERACION_PCT,
                "daily_loss_limit_pct": LIVE_DAILY_LOSS_LIMIT_PCT,
                "max_trades_per_day": LIVE_MAX_TRADES_PER_DAY,
                "max_consecutive_losses": LIVE_MAX_CONSECUTIVE_LOSSES,
                "reinvest_profits": LIVE_REINVEST_PROFITS,
            },
        }
    )


@app.route("/api/live/trades")
def live_trades_api():
    mode = _live_mode()
    limit = min(500, max(1, request.args.get("limit", 200, type=int)))
    return jsonify({"mode": mode, "execution_product": LIVE_EXECUTION_PRODUCT, "trades": get_broker_trades(mode, limit, LIVE_EXECUTION_PRODUCT)})


@app.route("/api/live/equity")
def live_equity_api():
    mode = _live_mode()
    return jsonify({"mode": mode, "execution_product": LIVE_EXECUTION_PRODUCT, "curve": get_equity_curve(mode, execution_product=LIVE_EXECUTION_PRODUCT)})


@app.route("/api/live/events")
def live_events_api():
    mode = _live_mode()
    limit = min(200, max(1, request.args.get("limit", 50, type=int)))
    return jsonify({"mode": mode, "events": get_live_events(mode, limit)})


@app.route("/healthz")
def healthcheck():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="127.0.0.1", debug=False, port=5000)