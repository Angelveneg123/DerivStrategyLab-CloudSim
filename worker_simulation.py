import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import websocket

# ---------------------------------------------------------------------
# Compatibilidad con el proyecto original.
# Estas variables solo evitan que config.py exija credenciales.
# NO se envían a Deriv y este worker NO coloca órdenes.
# ---------------------------------------------------------------------
os.environ.setdefault("DERIV_APP_ID", "cloud-sim-public")
os.environ.setdefault("DERIV_PAT_TOKEN", "cloud-sim-no-trading")

from config import (  # noqa: E402
    ATR_PERIOD,
    BACKTEST_COMMISSION_PER_TRADE_USD,
    BACKTEST_RANDOM_SEED,
    BACKTEST_SLIPPAGE_ATR_MAX,
    HTF_GRANULARITY,
    MAX_DRAWDOWN_STOP_PCT,
    REWARD_RATIO,
    RIESGO_POR_OPERACION_PCT,
    SL_ATR_MULT,
    SPREAD_ATR_FRAC,
)
from indicators.atr import calculate_atr  # noqa: E402
from strategy.registry import generate_registered_signals, validate_strategy_id  # noqa: E402
from strategy.hybrid_strategy import diagnosticar_hybrid_ultima_vela  # noqa: E402
from strategy.risk_manager import calculate_position_size, calculate_stop_take  # noqa: E402
from strategy.strategy_engine import BUY, HOLD  # noqa: E402
from telegram_notifier import send_pre_entry  # noqa: E402
from utils.timeframes import aggregate_ohlc_to_timeframe  # noqa: E402

# ---------------------------------------------------------------------
# DATOS PÚBLICOS DE MERCADO
# Endpoint público actual de Deriv. No necesita App ID, token ni OTP.
# ---------------------------------------------------------------------
DERIV_WS = os.getenv(
    "DERIV_PUBLIC_WS",
    "wss://api.derivws.com/trading/v1/options/ws/public",
)

# ---------------------------------------------------------------------
# SIMULACIÓN CFD STANDARD VIRTUAL
# La fuente de precios es pública; la contabilidad de la operación se hace
# localmente. No hay llamadas de compra/venta.
# ---------------------------------------------------------------------
SYMBOL_QUERY = os.getenv("SIM_SYMBOL", os.getenv("SIM_SYMBOL_QUERY", "Crash 500 Index"))
STRATEGY = os.getenv("SIM_STRATEGY", "hybrid_long_only")
GRANULARITY = int(os.getenv("SIM_GRANULARITY", "60"))
START_BALANCE = float(os.getenv("SIM_START_BALANCE", "100"))
WARMUP_CANDLES = int(os.getenv("SIM_WARMUP_CANDLES", "4000"))
RECONNECT_SECONDS = int(os.getenv("SIM_RECONNECT_SECONDS", "5"))
ENABLE_HYBRID_DIAGNOSTICS = os.getenv("SIM_DIAGNOSTICS", "1").strip().lower() in {"1", "true", "yes", "si", "sí", "on"}

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
            if state.get("symbol_code") and str(state.get("symbol_code")).lower() not in {SYMBOL_QUERY.lower(), SYMBOL_QUERY.lower().replace(" index", "").replace(" ", "")}:
                raise RuntimeError("El estado guardado pertenece a otro indice; no se reutilizara.")
            state["running"] = True
            state["storage_path"] = str(DATA_ROOT)
            state["mode"] = "CFD_STANDARD_SIMULATION"
            state["execution"] = "VIRTUAL_ONLY"
            return state
        except Exception as exc:
            raise RuntimeError(f"No se puede cargar el estado guardado: {exc}") from exc

    return {
        "mode": "CFD_STANDARD_SIMULATION",
        "execution": "VIRTUAL_ONLY",
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
        "last_error": None,
        "note": "CFD Standard virtual con datos públicos; no envía órdenes.",
    }


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)


def append_jsonl(path, obj):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def ws_request(ws, payload, expected_types, timeout=20):
    ws.send(json.dumps(payload))
    deadline = time.time() + timeout

    while time.time() < deadline:
        raw = ws.recv()
        if not raw:
            continue

        message = json.loads(raw)

        if message.get("error"):
            error = message["error"]
            if isinstance(error, dict):
                raise RuntimeError(error.get("message") or error.get("code") or str(error))
            raise RuntimeError(str(error))

        if message.get("msg_type") in expected_types:
            return message

    raise TimeoutError(f"Sin respuesta para: {payload}")


def resolve_symbol(ws):
    msg = ws_request(
        ws,
        {
            "active_symbols": "brief",
            "req_id": 101,
        },
        {"active_symbols"},
    )

    symbols = msg.get("active_symbols") or []
    q = SYMBOL_QUERY.strip().lower()

    for item in symbols:
        code = str(
            item.get("underlying_symbol")
            or item.get("symbol")
            or ""
        ).strip()
        name = str(
            item.get("underlying_symbol_name")
            or item.get("display_name")
            or ""
        ).strip()
        if q in {code.lower(), name.lower()}:
            return code, name or code

    raise RuntimeError(
        f"No encontré '{SYMBOL_QUERY}' en active_symbols."
    )


def fetch_history(ws, symbol_code):
    """
    Descarga el warmup histórico en varios bloques.

    El endpoint público puede devolver alrededor de 1000 velas por solicitud.
    Como Hybrid necesita más contexto, retrocedemos usando `end` hasta reunir
    WARMUP_CANDLES velas cerradas.
    """
    target = max(1, WARMUP_CANDLES)
    batch_size = 1000
    end_value = "latest"
    by_epoch = {}
    req_id = 200
    max_batches = (target // batch_size) + 4

    for _ in range(max_batches):
        request = {
            "ticks_history": symbol_code,
            "end": end_value,
            "count": batch_size,
            "style": "candles",
            "granularity": GRANULARITY,
            "adjust_start_time": 1,
            "req_id": req_id,
        }
        req_id += 1

        msg = ws_request(ws, request, {"candles", "history"})
        batch = msg.get("candles") or []

        if not batch:
            break

        oldest_epoch = None

        for c in batch:
            epoch = int(c["epoch"])
            oldest_epoch = epoch if oldest_epoch is None else min(oldest_epoch, epoch)

            by_epoch[epoch] = {
                "epoch": epoch,
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
            }

        now_epoch = int(time.time())
        current_bucket = now_epoch - (now_epoch % GRANULARITY)
        closed_count = sum(1 for epoch in by_epoch if epoch < current_bucket)

        if closed_count >= target:
            break

        if oldest_epoch is None:
            break

        end_value = max(1, oldest_epoch - 1)

    if not by_epoch:
        raise RuntimeError("Deriv no devolvió velas históricas.")

    now_epoch = int(time.time())
    current_bucket = now_epoch - (now_epoch % GRANULARITY)

    cleaned = [
        candle
        for epoch, candle in by_epoch.items()
        if epoch < current_bucket
    ]
    cleaned.sort(key=lambda x: x["epoch"])

    if len(cleaned) < target:
        raise RuntimeError(
            f"Histórico insuficiente tras paginar: "
            f"{len(cleaned)}/{target} velas."
        )

    cleaned = cleaned[-target:]

    print(
        f"[CloudSim] histórico listo: {len(cleaned)} velas cerradas "
        f"de {GRANULARITY}s para {symbol_code}",
        flush=True,
    )

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

    if gl > 0:
        state["profit_factor"] = gp / gl
    elif gp > 0:
        state["profit_factor"] = 999.0
    else:
        state["profit_factor"] = 0.0

    equity = safe_float(
        state.get("equity"),
        safe_float(state.get("balance"), START_BALANCE),
    )
    peak = max(safe_float(state.get("peak_equity"), START_BALANCE), equity)
    state["peak_equity"] = peak

    dd = ((peak - equity) / peak * 100.0) if peak > 0 else 0.0
    state["max_drawdown_pct"] = max(
        safe_float(state.get("max_drawdown_pct")),
        dd,
    )


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
        max(0.0, BACKTEST_SLIPPAGE_ATR_MAX) * atr,
    )

    # Long-only: TP recibe deslizamiento desfavorable hacia abajo.
    if reason == "take_profit":
        executed_exit = max(1e-12, exit_price - exit_slippage)
    else:
        executed_exit = max(1e-12, exit_price - exit_slippage)

    if direction == BUY:
        gross = (executed_exit - entry) * units
    else:
        gross = (entry - executed_exit) * units

    spread_cost = atr * SPREAD_ATR_FRAC * units
    commission = max(0.0, BACKTEST_COMMISSION_PER_TRADE_USD)
    profit = gross - spread_cost - commission

    state["balance"] = safe_float(state["balance"]) + profit
    state["equity"] = state["balance"]
    state["floating_pnl"] = 0.0
    state["pnl"] = state["balance"] - safe_float(
        state["start_balance"],
        START_BALANCE,
    )
    state["trades"] = int(state.get("trades", 0)) + 1

    if profit > 0:
        state["wins"] = int(state.get("wins", 0)) + 1
        state["gross_profit"] = safe_float(state.get("gross_profit")) + profit
    else:
        state["losses"] = int(state.get("losses", 0)) + 1
        state["gross_loss"] = safe_float(state.get("gross_loss")) + abs(profit)

    trade = dict(pos)
    trade.update(
        {
            "exit_price": executed_exit,
            "exit_epoch": int(exit_epoch),
            "exit_time": datetime.fromtimestamp(
                int(exit_epoch),
                timezone.utc,
            ).isoformat(),
            "exit_reason": reason,
            "profit": profit,
            "balance_after": state["balance"],
            "spread_cost": spread_cost,
            "exit_slippage": exit_slippage,
        }
    )

    append_jsonl(TRADES_FILE, trade)
    state["open_trade"] = None
    update_stats(state)

    append_jsonl(
        EQUITY_FILE,
        {
            "epoch": int(exit_epoch),
            "equity": state["equity"],
            "balance": state["balance"],
        },
    )


def mark_to_market(state, price):
    pos = state.get("open_trade")

    if not pos:
        state["floating_pnl"] = 0.0
        state["equity"] = safe_float(state.get("balance"), START_BALANCE)
        update_stats(state)
        return

    units = float(pos["position_size"])
    entry = float(pos["entry_price"])
    direction = pos["direction"]

    if direction == BUY:
        floating = (price - entry) * units
    else:
        floating = (entry - price) * units

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
            close_trade(state, min(stop, price), epoch, "stop_loss")
        elif price >= take:
            close_trade(state, take, epoch, "take_profit")
    else:
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

    # DIAGNOSTICO: solo informa lo que decidió la estrategia.
    # No modifica señales, entradas, riesgo, SL ni TP.
    candle_time = datetime.fromtimestamp(int(epochs[-1]), timezone.utc).isoformat()
    print(
        f"[CloudSim][DIAG] vela cerrada={candle_time} | "
        f"close={closes[-1]:.5f} | signal={signal or HOLD} | "
        f"open_trade={'SI' if state.get('open_trade') is not None else 'NO'}",
        flush=True,
    )

    # Diagnóstico profundo EXCLUSIVO de hybrid_long_only.
    # Solo imprime datos: NO altera señales ni abre/cierra operaciones.
    if ENABLE_HYBRID_DIAGNOSTICS and STRATEGY == "hybrid_long_only":
        try:
            epochs_htf, highs_htf, lows_htf, closes_htf = aggregate_ohlc_to_timeframe(
                epochs,
                highs,
                lows,
                closes,
                target_granularity=HTF_GRANULARITY,
                source_granularity=GRANULARITY,
            )

            diag = diagnosticar_hybrid_ultima_vela(
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
            )

            raw_signal = diag.get("raw_signal") or HOLD
            final_signal = signal or HOLD
            reason = diag.get("reason") or "sin_motivo"

            if raw_signal == "SELL" and final_signal == HOLD:
                reason = "sell_valido_convertido_a_hold_por_long_only"

            rsi_value = diag.get("rsi")
            rsi_text = "None" if rsi_value is None else f"{float(rsi_value):.2f}"

            print(
                f"[CloudSim][HYBRID_DIAG] vela={candle_time} | "
                f"raw={raw_signal} | final={final_signal} | "
                f"htf={diag.get('htf_bias')} | rsi={rsi_text} | "
                f"bos_up={diag.get('bos_bull')} | "
                f"bos_down={diag.get('bos_bear')} | "
                f"motivo={reason}",
                flush=True,
            )
        except Exception as diag_exc:
            print(
                f"[CloudSim][HYBRID_DIAG] error_solo_diagnostico={diag_exc}",
                flush=True,
            )

    # Esta versión es LONG-only.
    if signal != BUY:
        return

    print(
        f"[CloudSim][DIAG] SENAL BUY detectada | vela={candle_time} | "
        f"precio_senal={closes[-1]:.5f}",
        flush=True,
    )

    atr_values = calculate_atr(
        highs,
        lows,
        closes,
        period=ATR_PERIOD,
    )
    atr = atr_values[-1]

    if atr is None or atr <= 0:
        print(
            f"[CloudSim][DIAG] BUY descartado: ATR invalido ({atr}).",
            flush=True,
        )
        return

    if safe_float(state.get("max_drawdown_pct")) >= MAX_DRAWDOWN_STOP_PCT:
        state["note"] = "Circuit breaker virtual por drawdown máximo."
        print(
            f"[CloudSim][DIAG] BUY bloqueado por drawdown | "
            f"dd={safe_float(state.get('max_drawdown_pct')):.2f}% | "
            f"limite={MAX_DRAWDOWN_STOP_PCT:.2f}%",
            flush=True,
        )
        return

    signal_entry = closes[-1]

    entry_slippage = rng.uniform(
        0.0,
        max(0.0, BACKTEST_SLIPPAGE_ATR_MAX) * atr,
    )
    entry = signal_entry + entry_slippage

    stop, take = calculate_stop_take(
        entry,
        atr,
        direction=BUY,
        stop_atr_mult=SL_ATR_MULT,
        reward_ratio=REWARD_RATIO,
    )

    position_size = calculate_position_size(
        safe_float(state["balance"], START_BALANCE),
        RIESGO_POR_OPERACION_PCT,
        entry,
        stop,
    )

    if position_size <= 0:
        print(
            f"[CloudSim][DIAG] BUY descartado: position_size={position_size}.",
            flush=True,
        )
        return

    # TELEGRAM — PRE-ENTRADA.
    # Usa exactamente Entry/SL/TP ya calculados por el simulador.
    # No recalcula señales y no ejecuta operaciones.
    trade_number = int(state.get("trades", 0)) + 1
    signal_epoch = int(epochs[-1])

    # Evita repetir la alerta para la misma vela cerrada.
    if state.get("last_telegram_signal_epoch") != signal_epoch:
        send_pre_entry(
            trade_number=trade_number,
            symbol=state.get("symbol") or SYMBOL_QUERY,
            direction=BUY,
            entry=entry,
            stop=stop,
            take=take,
            reward_ratio=REWARD_RATIO,
            signal_time=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        )
        state["last_telegram_signal_epoch"] = signal_epoch

    state["last_entry_signal_epoch"] = signal_epoch
    state["open_trade"] = {
        "execution_model_version": "2_adverse_stop_slippage",
        "direction": BUY,
        "market_model": "CFD_STANDARD_VIRTUAL",
        "signal_entry_price": signal_entry,
        "entry_price": entry,
        "entry_epoch": int(epochs[-1]),
        "entry_time": datetime.fromtimestamp(
            int(epochs[-1]),
            timezone.utc,
        ).isoformat(),
        "entry_atr": atr,
        "entry_slippage": entry_slippage,
        "stop_price": stop,
        "take_price": take,
        "position_size": position_size,
        "risk_pct": RIESGO_POR_OPERACION_PCT,
        "stop_atr_mult": SL_ATR_MULT,
        "reward_ratio": REWARD_RATIO,
    }

    print(
        f"[CloudSim][DIAG] OPERACION VIRTUAL ABIERTA | BUY | "
        f"entry={entry:.5f} | SL={stop:.5f} | TP={take:.5f} | "
        f"size={position_size:.6f}",
        flush=True,
    )


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

    # suppress_origin=True evita enviar un Origin innecesario.
    # El endpoint es público y de solo lectura.
    ws = websocket.create_connection(
        DERIV_WS,
        timeout=30,
        suppress_origin=True,
    )

    try:
        symbol_code, symbol_name = resolve_symbol(ws)

        state["symbol"] = symbol_name
        state["symbol_code"] = symbol_code
        state["strategy"] = STRATEGY
        state["granularity"] = GRANULARITY
        state["market_data"] = "Deriv public WebSocket"
        state["mode"] = "CFD_STANDARD_SIMULATION"
        state["execution"] = "VIRTUAL_ONLY"
        state["running"] = True
        state["last_error"] = None
        state["note"] = (
            "Datos públicos activos; motor CFD Standard virtual; "
            "no envía órdenes."
        )

        candles = fetch_history(ws, symbol_code)

        if len(candles) < 3200:
            raise RuntimeError(
                f"Histórico insuficiente para Hybrid: {len(candles)} velas."
            )

        last_epoch = candles[-1]["epoch"]

        if state.get("last_closed_candle_epoch") is None:
            state["last_closed_candle_epoch"] = last_epoch

        # No repetir una entrada sobre la misma vela tras una reconexion.
        if state.get("last_entry_signal_epoch") != last_epoch:
            maybe_open_from_closed_candle(state, candles)

        ws.send(
            json.dumps(
                {
                    "ticks": symbol_code,
                    "subscribe": 1,
                    "req_id": 103,
                }
            )
        )

        current = None

        while True:
            raw = ws.recv()
            if not raw:
                raise ConnectionError("WebSocket cerrado sin datos.")

            msg = json.loads(raw)

            if msg.get("error"):
                error = msg["error"]
                if isinstance(error, dict):
                    raise RuntimeError(
                        error.get("message")
                        or error.get("code")
                        or str(error)
                    )
                raise RuntimeError(str(error))

            if msg.get("msg_type") != "tick":
                continue

            tick = msg.get("tick") or {}
            if "quote" not in tick or "epoch" not in tick:
                continue

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
                candles = process_closed_candle(
                    state,
                    candles,
                    current,
                )
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
    print(
        f"[CloudSim] iniciando CFD Standard virtual | "
        f"symbol={SYMBOL_QUERY} | strategy={STRATEGY} | ws={DERIV_WS}",
        flush=True,
    )
    state = load_state()

    # Guarda un estado visible desde el inicio.
    state["updated_at"] = now_iso()
    save_state(state)

    while True:
        try:
            run_session(state)

        except KeyboardInterrupt:
            state["running"] = False
            state["updated_at"] = now_iso()
            save_state(state)
            break

        except Exception as exc:
            print(f"[CloudSim] error: {exc}", flush=True)
            state["running"] = False
            state["last_error"] = str(exc)
            state["note"] = f"Reconectando mercado público: {exc}"
            state["updated_at"] = now_iso()
            save_state(state)

            time.sleep(RECONNECT_SECONDS)
            state["running"] = True


if __name__ == "__main__":
    main()
