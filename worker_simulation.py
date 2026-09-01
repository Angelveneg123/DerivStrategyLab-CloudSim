import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import websocket

# ---------------------------------------------------------------------
# Compatibilidad con el proyecto original.
# Estas variables evitan que config.py exija credenciales en esta
# simulación pública. NO se usan para autenticar ni enviar órdenes.
# ---------------------------------------------------------------------
os.environ.setdefault("DERIV_APP_ID", "cloud-sim-public")
os.environ.setdefault("DERIV_PAT_TOKEN", "cloud-sim-no-trading")

from config import (  # noqa: E402
    ATR_PERIOD,
    BACKTEST_COMMISSION_PER_TRADE_USD,
    BACKTEST_RANDOM_SEED,
    BACKTEST_SLIPPAGE_ATR_MAX,
    MAX_DRAWDOWN_STOP_PCT,
    REWARD_RATIO,
    RIESGO_POR_OPERACION_PCT,
    SL_ATR_MULT,
    SPREAD_ATR_FRAC,
)
from indicators.atr import calculate_atr  # noqa: E402
from strategy.registry import generate_registered_signals, validate_strategy_id  # noqa: E402
from strategy.risk_manager import calculate_position_size, calculate_stop_take  # noqa: E402
from strategy.strategy_engine import BUY, HOLD  # noqa: E402

# Solo mercado público; no autentica y no contiene funciones de compra.
DERIV_WS = os.getenv("DERIV_PUBLIC_WS", "wss://ws.binaryws.com/websockets/v3")

SYMBOL_QUERY = os.getenv("SIM_SYMBOL", "Crash 500 Index")
STRATEGY = os.getenv("SIM_STRATEGY", "hybrid_long_only")
GRANULARITY = int(os.getenv("SIM_GRANULARITY", "60"))
START_BALANCE = float(os.getenv("SIM_START_BALANCE", "100"))
WARMUP_CANDLES = int(os.getenv("SIM_WARMUP_CANDLES", "4000"))
RECONNECT_SECONDS = int(os.getenv("SIM_RECONNECT_SECONDS", "5"))

# Si Railway tiene un Volume montado en /data, usa almacenamiento persistente.
# Si no existe, cae a la carpeta local del proyecto.
DATA_ROOT = Path(os.getenv("SIM_DATA_DIR", "/data/cloud_sim"))
try:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    test_file = DATA_ROOT / ".write_test"
    test_file.write_text("ok", encoding="utf-8")
    test_file.unlink(missing_ok=True)
except Exception:
    DATA_ROOT = Path(__file__).resolve().parent / "cloud_sim"
    DATA_ROOT.mkdir(parents=True, exist_ok=True)

STATE_FILE = DATA_ROOT / "state.json"
TRADES_FILE = DATA_ROOT / "trades.jsonl"
EQUITY_FILE = DATA_ROOT / "equity.jsonl"

rng = random.Random(BACKTEST_RANDOM_SEED)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def load_state():
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            state["running"] = True
            state["storage_path"] = str(DATA_ROOT)
            return state
        except Exception:
            pass
    return {
        "mode": "CFD_STANDARD_SIMULATION",
        "market_data": "Deriv public WebSocket",
        "symbol": SYMBOL_QUERY,
        "symbol_code": None,
        "strategy": STRATEGY,
        "granularity": GRANULARITY,
        "running": True,
        "start_balance": START_BALANCE,
        "balance": START_BALANCE,
        "equity": START_BALANCE,
        "floating_pnl": 0.0,
        "pnl": 0.0,
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "peak_equity": START_BALANCE,
        "max_drawdown_pct": 0.0,
        "open_trade": None,
        "last_signal": HOLD,
        "last_price": None,
        "last_closed_candle_epoch": None,
        "updated_at": now_iso(),
        "storage_path": str(DATA_ROOT),
        "note": "Simulación con datos públicos; no envía órdenes."
    }


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)


def append_jsonl(path, obj):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def read_recent_trades(limit=20):
    if not TRADES_FILE.exists():
        return []
    rows = []
    try:
        with TRADES_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except Exception:
        return []
    return rows[-limit:]


def ws_request(ws, payload, expected_types, timeout=20):
    ws.send(json.dumps(payload))
    deadline = time.time() + timeout
    while time.time() < deadline:
        message = json.loads(ws.recv())
        if message.get("error"):
            raise RuntimeError(message["error"].get("message", str(message["error"])))
        if message.get("msg_type") in expected_types:
            return message
    raise TimeoutError(f"Sin respuesta para {payload}")


def resolve_symbol(ws):
    msg = ws_request(
        ws,
        {"active_symbols": "brief", "product_type": "basic", "req_id": 101},
        {"active_symbols"},
    )
    symbols = msg.get("active_symbols") or []
    q = SYMBOL_QUERY.strip().lower()

    # Coincidencia exacta por código o nombre.
    for item in symbols:
        code = str(item.get("symbol", "")).strip()
        name = str(item.get("display_name", "")).strip()
        if q in {code.lower(), name.lower()}:
            return code, name or code

    # Coincidencia flexible "Crash 500".
    candidates = []
    for item in symbols:
        code = str(item.get("symbol", "")).strip()
        name = str(item.get("display_name", "")).strip()
        haystack = f"{code} {name}".lower()
        if all(token in haystack for token in ["crash", "500"]):
            candidates.append((code, name or code))
    if candidates:
        return candidates[0]

    raise RuntimeError(
        f"No encontré '{SYMBOL_QUERY}' en active_symbols. "
        "Revisa el nombre/código del índice."
    )


def fetch_history(ws, symbol_code):
    request = {
        "ticks_history": symbol_code,
        "end": "latest",
        "count": WARMUP_CANDLES,
        "style": "candles",
        "granularity": GRANULARITY,
        "adjust_start_time": 1,
        "req_id": 102,
    }
    msg = ws_request(ws, request, {"candles", "history"})
    candles = msg.get("candles") or []
    if not candles:
        raise RuntimeError("Deriv no devolvió velas históricas.")

    cleaned = []
    for c in candles:
        epoch = int(c.get("epoch"))
        cleaned.append({
            "epoch": epoch,
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
        })
    cleaned.sort(key=lambda x: x["epoch"])

    # Evita usar como cerrada una vela todavía en formación.
    now_epoch = int(time.time())
    current_bucket = now_epoch - (now_epoch % GRANULARITY)
    cleaned = [c for c in cleaned if c["epoch"] < current_bucket]
    return cleaned


def arrays_from_candles(candles):
    return (
        [c["epoch"] for c in candles],
        [c["open"] for c in candles],
        [c["high"] for c in candles],
        [c["low"] for c in candles],
        [c["close"] for c in candles],
    )


def update_stats(state):
    trades = int(state.get("trades", 0))
    wins = int(state.get("wins", 0))
    state["win_rate"] = (wins / trades * 100.0) if trades else 0.0

    gp = safe_float(state.get("gross_profit"))
    gl = safe_float(state.get("gross_loss"))
    state["profit_factor"] = (gp / gl) if gl > 0 else (999.0 if gp > 0 else 0.0)

    equity = safe_float(state.get("equity"), safe_float(state.get("balance"), START_BALANCE))
    peak = max(safe_float(state.get("peak_equity"), START_BALANCE), equity)
    state["peak_equity"] = peak
    dd = ((peak - equity) / peak * 100.0) if peak > 0 else 0.0
    state["max_drawdown_pct"] = max(safe_float(state.get("max_drawdown_pct")), dd)


def close_trade(state, exit_price, exit_epoch, reason):
    pos = state.get("open_trade")
    if not pos:
        return

    direction = pos["direction"]
    entry = float(pos["entry_price"])
    units = float(pos["position_size"])
    atr = float(pos["entry_atr"])

    exit_slippage = rng.uniform(
        0.0,
        max(0.0, BACKTEST_SLIPPAGE_ATR_MAX) * atr
    )

    if reason == "take_profit":
        executed_exit = max(1e-12, exit_price - exit_slippage)
    else:
        executed_exit = exit_price + exit_slippage

    gross = (
        (executed_exit - entry) * units
        if direction == BUY
        else (entry - executed_exit) * units
    )
    spread_cost = atr * SPREAD_ATR_FRAC * units
    commission = max(0.0, BACKTEST_COMMISSION_PER_TRADE_USD)
    profit = gross - spread_cost - commission

    state["balance"] = safe_float(state["balance"]) + profit
    state["equity"] = state["balance"]
    state["floating_pnl"] = 0.0
    state["pnl"] = state["balance"] - safe_float(state["start_balance"], START_BALANCE)
    state["trades"] = int(state.get("trades", 0)) + 1

    if profit > 0:
        state["wins"] = int(state.get("wins", 0)) + 1
        state["gross_profit"] = safe_float(state.get("gross_profit")) + profit
    else:
        state["losses"] = int(state.get("losses", 0)) + 1
        state["gross_loss"] = safe_float(state.get("gross_loss")) + abs(profit)

    trade = dict(pos)
    trade.update({
        "exit_price": executed_exit,
        "exit_epoch": int(exit_epoch),
        "exit_time": datetime.fromtimestamp(int(exit_epoch), timezone.utc).isoformat(),
        "exit_reason": reason,
        "profit": profit,
        "balance_after": state["balance"],
        "spread_cost": spread_cost,
        "exit_slippage": exit_slippage,
    })
    append_jsonl(TRADES_FILE, trade)
    state["open_trade"] = None
    update_stats(state)
    append_jsonl(EQUITY_FILE, {
        "epoch": int(exit_epoch),
        "equity": state["equity"],
        "balance": state["balance"],
    })


def mark_to_market(state, price):
    pos = state.get("open_trade")
    if not pos:
        state["floating_pnl"] = 0.0
        state["equity"] = safe_float(state.get("balance"), START_BALANCE)
        return

    units = float(pos["position_size"])
    entry = float(pos["entry_price"])
    direction = pos["direction"]
    floating = (
        (price - entry) * units
        if direction == BUY
        else (entry - price) * units
    )
    state["floating_pnl"] = floating
    state["equity"] = safe_float(state["balance"]) + floating
    update_stats(state)


def maybe_exit_on_tick(state, price, epoch):
    pos = state.get("open_trade")
    if not pos:
        return
    stop = float(pos["stop_price"])
    take = float(pos["take_price"])
    direction = pos["direction"]

    if direction == BUY:
        if price <= stop:
            close_trade(state, stop, epoch, "stop_loss")
        elif price >= take:
            close_trade(state, take, epoch, "take_profit")
    else:
        # hybrid_long_only no debería abrir SELL, pero se mantiene simetría.
        if price >= stop:
            close_trade(state, stop, epoch, "stop_loss")
        elif price <= take:
            close_trade(state, take, epoch, "take_profit")


def maybe_open_from_closed_candle(state, candles):
    if state.get("open_trade") is not None:
        return

    epochs, opens, highs, lows, closes = arrays_from_candles(candles)

    signals = generate_registered_signals(
        STRATEGY,
        epochs,
        highs,
        lows,
        closes,
        source_granularity=GRANULARITY,
        opens=opens,
    )
    signal = signals[-1] if signals else HOLD
    state["last_signal"] = signal or HOLD

    if signal != BUY:
        return

    atr_values = calculate_atr(highs, lows, closes, period=ATR_PERIOD)
    atr = atr_values[-1]
    if atr is None or atr <= 0:
        return

    # Circuit breaker de drawdown del simulador.
    if safe_float(state.get("max_drawdown_pct")) >= MAX_DRAWDOWN_STOP_PCT:
        state["note"] = "Circuit breaker virtual por drawdown máximo."
        return

    signal_entry = closes[-1]
    entry_slippage = rng.uniform(
        0.0,
        max(0.0, BACKTEST_SLIPPAGE_ATR_MAX) * atr
    )
    entry = signal_entry + entry_slippage

    stop, take = calculate_stop_take(
        entry,
        atr,
        direction=BUY,
        stop_atr_mult=SL_ATR_MULT,
        reward_ratio=REWARD_RATIO,
    )
    units = calculate_position_size(
        safe_float(state["balance"], START_BALANCE),
        RIESGO_POR_OPERACION_PCT,
        entry,
        stop,
    )
    if units <= 0:
        return

    state["open_trade"] = {
        "direction": BUY,
        "signal_entry_price": signal_entry,
        "entry_price": entry,
        "entry_epoch": int(epochs[-1]),
        "entry_time": datetime.fromtimestamp(int(epochs[-1]), timezone.utc).isoformat(),
        "entry_atr": atr,
        "entry_slippage": entry_slippage,
        "stop_price": stop,
        "take_price": take,
        "position_size": units,
        "risk_pct": RIESGO_POR_OPERACION_PCT,
        "stop_atr_mult": SL_ATR_MULT,
        "reward_ratio": REWARD_RATIO,
    }


def process_closed_candle(state, candles, candle):
    if candles and candle["epoch"] <= candles[-1]["epoch"]:
        return candles

    candles.append(candle)
    max_keep = max(WARMUP_CANDLES, 4500)
    if len(candles) > max_keep:
        candles = candles[-max_keep:]

    state["last_closed_candle_epoch"] = candle["epoch"]
    maybe_open_from_closed_candle(state, candles)
    return candles


def run_session(state):
    validate_strategy_id(STRATEGY)

    ws = websocket.create_connection(
        DERIV_WS,
        timeout=30,
        origin="https://developers.deriv.com",
    )

    try:
        symbol_code, symbol_name = resolve_symbol(ws)
        state["symbol"] = symbol_name
        state["symbol_code"] = symbol_code
        state["strategy"] = STRATEGY
        state["granularity"] = GRANULARITY
        state["market_data"] = "Deriv public WebSocket"
        state["running"] = True
        state["note"] = "Datos públicos de mercado activos. No envía órdenes."

        candles = fetch_history(ws, symbol_code)
        if len(candles) < 3200:
            raise RuntimeError(
                f"Histórico insuficiente para Hybrid: {len(candles)} velas."
            )

        # Sincroniza señal con la última vela histórica cerrada.
        last_epoch = candles[-1]["epoch"]
        if state.get("last_closed_candle_epoch") is None:
            state["last_closed_candle_epoch"] = last_epoch
        maybe_open_from_closed_candle(state, candles)

        ws.send(json.dumps({
            "ticks": symbol_code,
            "subscribe": 1,
            "req_id": 103,
        }))

        current = None

        while True:
            msg = json.loads(ws.recv())
            if msg.get("error"):
                raise RuntimeError(msg["error"].get("message", str(msg["error"])))
            if msg.get("msg_type") != "tick":
                continue

            tick = msg.get("tick") or {}
            price = float(tick["quote"])
            epoch = int(tick["epoch"])
            state["last_price"] = price

            maybe_exit_on_tick(state, price, epoch)
            mark_to_market(state, price)

            bucket = epoch - (epoch % GRANULARITY)
            if current is None:
                current = {
                    "epoch": bucket,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                }
            elif bucket == current["epoch"]:
                current["high"] = max(current["high"], price)
                current["low"] = min(current["low"], price)
                current["close"] = price
            elif bucket > current["epoch"]:
                candles = process_closed_candle(state, candles, current)
                current = {
                    "epoch": bucket,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                }

            state["updated_at"] = now_iso()
            update_stats(state)
            save_state(state)

    finally:
        try:
            ws.close()
        except Exception:
            pass


def main():
    state = load_state()
    while True:
        try:
            run_session(state)
        except KeyboardInterrupt:
            state["running"] = False
            state["updated_at"] = now_iso()
            save_state(state)
            break
        except Exception as exc:
            state["running"] = False
            state["last_error"] = str(exc)
            state["note"] = f"Reconectando mercado público: {exc}"
            state["updated_at"] = now_iso()
            save_state(state)
            time.sleep(RECONNECT_SECONDS)
            state["running"] = True


if __name__ == "__main__":
    main()
