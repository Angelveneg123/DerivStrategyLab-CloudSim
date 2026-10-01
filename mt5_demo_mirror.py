"""MT5 DEMO MIRROR para Deriv Strategy Lab.

Objetivo:
- reproducir la MISMA señal del CloudSim `hybrid_long_only`;
- usar datos públicos Deriv por WebSocket y velas de 60 s;
- ATR(14), SL = 1.5 ATR, TP = 2R, riesgo = 0.5%;
- ejecutar exclusivamente en una cuenta Deriv MT5 DEMO;
- NO aplicar límites diarios/cooldown del motor live normal;
- NO modificar silenciosamente SL/TP para satisfacer al broker:
  si MT5 no acepta los niveles exactos, la señal se rechaza y se registra.

Diferencia deliberada con el simulador:
el CloudSim modela slippage virtual. En MT5 Demo NO se añade slippage artificial;
se usa la entrada real del broker (ask para BUY) y el spread/slippage observado.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import websocket

# strategy.registry importa config.py. Estos placeholders solo evitan que
# config.py exija credenciales de Deriv Options. Este modo usa datos PUBLICOS
# por WebSocket y autenticación independiente de MT5.
os.environ.setdefault("DERIV_APP_ID", "mt5-demo-mirror-public")
os.environ.setdefault("DERIV_PAT_TOKEN", "mt5-demo-mirror-no-options-trading")

from indicators.atr import calculate_atr  # noqa: E402
from strategy.registry import generate_registered_signals  # noqa: E402
from strategy.risk_manager import calculate_stop_take  # noqa: E402
from strategy.strategy_engine import BUY, HOLD  # noqa: E402


DERIV_PUBLIC_WS = os.getenv(
    "DERIV_PUBLIC_WS",
    "wss://api.derivws.com/trading/v1/options/ws/public",
)

# ---------------------------------------------------------------------------
# Parámetros CONGELADOS del experimento espejo.
# No se leen del motor live normal para evitar cambiar el experimento por error.
# ---------------------------------------------------------------------------
MIRROR_STRATEGY = "hybrid_long_only"
MIRROR_GRANULARITY = 60
MIRROR_HTF_GRANULARITY = 900
MIRROR_ATR_PERIOD = 14
MIRROR_RISK_PCT = 0.5
MIRROR_STOP_ATR_MULT = 1.5
MIRROR_REWARD_RATIO = 2.0
MIRROR_MAX_DRAWDOWN_PCT = 12.0

MIRROR_SYMBOL_QUERY = os.getenv("MIRROR_SYMBOL", "Crash 500 Index").strip()
MIRROR_WARMUP_CANDLES = int(os.getenv("MIRROR_WARMUP_CANDLES", "4000"))
MIRROR_START_BALANCE = float(os.getenv("MIRROR_START_BALANCE", "100"))
MIRROR_RECONNECT_SECONDS = int(os.getenv("MIRROR_RECONNECT_SECONDS", "5"))
MIRROR_MAX_MARGIN_USD = float(os.getenv("MIRROR_MAX_MARGIN_USD", "100"))

MT5_TERMINAL_PATH = os.getenv("MT5_TERMINAL_PATH", "").strip() or None
MT5_LOGIN_RAW = os.getenv("MT5_LOGIN", "").strip()
MT5_LOGIN = int(MT5_LOGIN_RAW) if MT5_LOGIN_RAW else None
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "").strip() or None
MT5_SYMBOL_QUERY = os.getenv("MT5_SYMBOL", MIRROR_SYMBOL_QUERY).strip()
MT5_DEVIATION_POINTS = int(os.getenv("MT5_DEVIATION_POINTS", "50"))
MT5_MIRROR_MAGIC = int(os.getenv("MT5_MIRROR_MAGIC", "20261001"))
MT5_MIRROR_COMMENT = os.getenv("MT5_MIRROR_COMMENT", "DEMO_MIRROR").strip()[:31]
MT5_REQUIRE_DERIV_SERVER = os.getenv("MT5_REQUIRE_DERIV_SERVER", "true").strip().lower() in {
    "1", "true", "yes", "si", "sí", "on"
}

DATA_ROOT = Path(os.getenv("MIRROR_DATA_DIR", str(Path(__file__).resolve().parents[1] / "mirror_data")))
STATE_FILE = DATA_ROOT / "state.json"
TRADES_FILE = DATA_ROOT / "trades.jsonl"
EVENTS_FILE = DATA_ROOT / "events.jsonl"


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _as_int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _namedtuple_dict(value):
    if value is None:
        return None
    if hasattr(value, "_asdict"):
        return dict(value._asdict())
    return str(value)


def _digits_for_step(step: float) -> int:
    text = f"{float(step):.10f}".rstrip("0")
    return len(text.split(".", 1)[1]) if "." in text else 0


def _floor_to_step(value: float, step: float) -> float:
    if step <= 0:
        return float(value)
    units = math.floor((float(value) + 1e-12) / float(step))
    return round(units * float(step), _digits_for_step(step))


def append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def log_event(event: str, message: str, data: dict | None = None) -> None:
    payload = {
        "time": utc_iso(),
        "event": event,
        "message": message,
        "data": data or {},
    }
    append_jsonl(EVENTS_FILE, payload)
    print(f"[MT5-MIRROR] {event}: {message}", flush=True)


def default_state() -> dict:
    return {
        "mode": "MT5_DEMO_MIRROR",
        "execution": "DERIV_MT5_DEMO_ONLY",
        "strategy": MIRROR_STRATEGY,
        "symbol": MIRROR_SYMBOL_QUERY,
        "granularity": MIRROR_GRANULARITY,
        "start_balance": MIRROR_START_BALANCE,
        "virtual_balance": MIRROR_START_BALANCE,
        "peak_balance": MIRROR_START_BALANCE,
        "max_drawdown_pct": 0.0,
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "current_position_ticket": None,
        "last_entry_signal_epoch": None,
        "last_closed_candle_epoch": None,
        "circuit_breaker": False,
        "circuit_breaker_reason": None,
        "updated_at": utc_iso(),
    }


def load_state() -> dict:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        state = default_state()
        save_state(state)
        return state

    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    if state.get("mode") != "MT5_DEMO_MIRROR":
        raise RuntimeError("El state.json existente no pertenece a MT5_DEMO_MIRROR.")
    return state


def save_state(state: dict) -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utc_iso()
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)


def update_drawdown(state: dict) -> None:
    balance = _as_float(state.get("virtual_balance"), MIRROR_START_BALANCE)
    peak = max(_as_float(state.get("peak_balance"), MIRROR_START_BALANCE), balance)
    state["peak_balance"] = peak
    dd = ((peak - balance) / peak * 100.0) if peak > 0 else 0.0
    state["max_drawdown_pct"] = max(_as_float(state.get("max_drawdown_pct")), dd)
    if state["max_drawdown_pct"] >= MIRROR_MAX_DRAWDOWN_PCT:
        state["circuit_breaker"] = True
        state["circuit_breaker_reason"] = (
            f"Drawdown espejo {state['max_drawdown_pct']:.2f}% >= "
            f"{MIRROR_MAX_DRAWDOWN_PCT:.2f}%"
        )


# ---------------------------------------------------------------------------
# Fuente publica Deriv: misma idea del worker_simulation.py
# ---------------------------------------------------------------------------
def ws_request(ws, payload, expected_types, timeout=20):
    ws.send(json.dumps(payload))
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = ws.recv()
        if not raw:
            continue
        msg = json.loads(raw)
        if msg.get("error"):
            error = msg["error"]
            if isinstance(error, dict):
                raise RuntimeError(error.get("message") or error.get("code") or str(error))
            raise RuntimeError(str(error))
        if msg.get("msg_type") in expected_types:
            return msg
    raise TimeoutError(f"Sin respuesta para: {payload}")


def resolve_public_symbol(ws):
    msg = ws_request(
        ws,
        {"active_symbols": "brief", "req_id": 101},
        {"active_symbols"},
    )
    q = MIRROR_SYMBOL_QUERY.lower()
    for item in msg.get("active_symbols") or []:
        code = str(item.get("underlying_symbol") or item.get("symbol") or "").strip()
        name = str(item.get("underlying_symbol_name") or item.get("display_name") or "").strip()
        if q in {code.lower(), name.lower()}:
            return code, name or code
    raise RuntimeError(f"No encontré '{MIRROR_SYMBOL_QUERY}' en active_symbols.")


def fetch_public_history(ws, symbol_code):
    target = max(1, MIRROR_WARMUP_CANDLES)
    batch_size = 1000
    end_value = "latest"
    by_epoch = {}
    req_id = 200
    max_batches = (target // batch_size) + 4

    for _ in range(max_batches):
        msg = ws_request(
            ws,
            {
                "ticks_history": symbol_code,
                "end": end_value,
                "count": batch_size,
                "style": "candles",
                "granularity": MIRROR_GRANULARITY,
                "adjust_start_time": 1,
                "req_id": req_id,
            },
            {"candles", "history"},
        )
        req_id += 1
        batch = msg.get("candles") or []
        if not batch:
            break

        oldest = None
        for c in batch:
            epoch = int(c["epoch"])
            oldest = epoch if oldest is None else min(oldest, epoch)
            by_epoch[epoch] = {
                "epoch": epoch,
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
            }

        now_epoch = int(time.time())
        current_bucket = now_epoch - (now_epoch % MIRROR_GRANULARITY)
        if sum(1 for e in by_epoch if e < current_bucket) >= target:
            break
        if oldest is None:
            break
        end_value = max(1, oldest - 1)

    now_epoch = int(time.time())
    current_bucket = now_epoch - (now_epoch % MIRROR_GRANULARITY)
    cleaned = [c for e, c in by_epoch.items() if e < current_bucket]
    cleaned.sort(key=lambda x: x["epoch"])
    if len(cleaned) < target:
        raise RuntimeError(f"Histórico insuficiente: {len(cleaned)}/{target} velas.")
    return cleaned[-target:]


def arrays_from_candles(candles):
    return (
        [c["epoch"] for c in candles],
        [c["open"] for c in candles],
        [c["high"] for c in candles],
        [c["low"] for c in candles],
        [c["close"] for c in candles],
    )


# ---------------------------------------------------------------------------
# MT5 DEMO
# ---------------------------------------------------------------------------
def load_mt5():
    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Falta MetaTrader5. Este modo debe ejecutarse en Windows con MT5 instalado."
        ) from exc
    return mt5


def connect_mt5():
    mt5 = load_mt5()
    kwargs = {"timeout": 60_000}
    if MT5_LOGIN is not None:
        kwargs["login"] = MT5_LOGIN
    if MT5_PASSWORD:
        kwargs["password"] = MT5_PASSWORD
    if MT5_SERVER:
        kwargs["server"] = MT5_SERVER

    ok = (
        mt5.initialize(MT5_TERMINAL_PATH, **kwargs)
        if MT5_TERMINAL_PATH
        else mt5.initialize(**kwargs)
    )
    if not ok:
        raise RuntimeError(f"MT5 initialize() falló: {mt5.last_error()}")

    account = mt5.account_info()
    terminal = mt5.terminal_info()
    if account is None or terminal is None:
        raise RuntimeError(f"MT5 no devolvió cuenta/terminal: {mt5.last_error()}")

    demo_constant = _as_int(getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0), 0)
    if _as_int(getattr(account, "trade_mode", -1), -1) != demo_constant:
        raise RuntimeError("SEGURIDAD: MT5 DEMO MIRROR solo acepta una cuenta DEMO.")

    server = str(getattr(account, "server", "") or "")
    company = str(getattr(account, "company", "") or "")
    if MT5_REQUIRE_DERIV_SERVER and "deriv" not in f"{server} {company}".lower():
        raise RuntimeError(
            f"SEGURIDAD: la cuenta no parece Deriv (server={server!r}, company={company!r})."
        )

    if not bool(getattr(terminal, "connected", True)):
        raise RuntimeError("MT5 no está conectado al servidor.")
    if not bool(getattr(terminal, "trade_allowed", True)):
        raise RuntimeError("Activa Algo Trading/AutoTrading en MT5.")
    if bool(getattr(terminal, "tradeapi_disabled", False)):
        raise RuntimeError("MT5 tiene deshabilitado el trading desde Python/API.")

    symbol = resolve_mt5_symbol(mt5, MT5_SYMBOL_QUERY)
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"No pude habilitar el símbolo MT5 {symbol!r}.")
    return mt5, account, terminal, symbol


def resolve_mt5_symbol(mt5, query: str) -> str:
    q = " ".join(query.lower().replace("_", " ").replace("-", " ").split())
    symbols = mt5.symbols_get() or ()
    exact = []
    partial = []
    for info in symbols:
        name = str(getattr(info, "name", "") or "")
        desc = str(getattr(info, "description", "") or "")
        path = str(getattr(info, "path", "") or "")
        values = [
            " ".join(v.lower().replace("_", " ").replace("-", " ").split())
            for v in (name, desc, path)
        ]
        if q in values:
            exact.append(name)
        elif any(q in v for v in values):
            partial.append(name)
    if exact:
        return exact[0]
    if partial:
        return partial[0]
    raise RuntimeError(f"No encontré el símbolo MT5 solicitado: {query!r}")


def bot_positions(mt5):
    positions = list(mt5.positions_get() or ())
    ours = [p for p in positions if _as_int(getattr(p, "magic", 0)) == MT5_MIRROR_MAGIC]
    foreign = [p for p in positions if _as_int(getattr(p, "magic", 0)) != MT5_MIRROR_MAGIC]
    return ours, foreign


def ensure_account_is_clean(mt5, state):
    ours, foreign = bot_positions(mt5)
    if foreign:
        tickets = [_as_int(getattr(p, "ticket", 0)) for p in foreign]
        raise RuntimeError(
            f"SEGURIDAD: hay posiciones ajenas al mirror en la cuenta: {tickets}. "
            "Usa una cuenta DEMO dedicada o ciérralas."
        )
    if len(ours) > 1:
        raise RuntimeError("SEGURIDAD: hay más de una posición del mirror abierta.")
    if ours:
        ticket = _as_int(getattr(ours[0], "ticket", 0))
        state["current_position_ticket"] = ticket
        save_state(state)
    return ours


def strict_mirror_plan(mt5, symbol: str, atr: float, virtual_balance: float) -> dict:
    """Crea el plan exacto. No ensancha ni mueve SL/TP."""
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if info is None or tick is None:
        raise RuntimeError("MT5 no devolvió info/tick del símbolo.")

    entry = float(tick.ask)
    if entry <= 0 or atr <= 0:
        raise RuntimeError("Entrada/ATR inválidos.")

    raw_stop, raw_take = calculate_stop_take(
        entry,
        atr,
        direction=BUY,
        stop_atr_mult=MIRROR_STOP_ATR_MULT,
        reward_ratio=MIRROR_REWARD_RATIO,
    )
    digits = int(getattr(info, "digits", 5) or 5)
    stop = round(raw_stop, digits)
    take = round(raw_take, digits)

    point = float(getattr(info, "point", 0) or 0)
    stops_level_points = float(getattr(info, "trade_stops_level", 0) or 0)
    bid = float(tick.bid)
    min_distance = max(stops_level_points * point, 0.0)

    # Se RECHAZA la operación en lugar de cambiar SL/TP.
    if point <= 0:
        raise RuntimeError("El símbolo MT5 reporta point inválido.")
    if stop >= bid - min_distance:
        raise RuntimeError(
            f"SL exacto no permitido por MT5: SL={stop}, bid={bid}, "
            f"distancia mínima={min_distance}."
        )
    if take <= bid + min_distance:
        raise RuntimeError(
            f"TP exacto no permitido por MT5: TP={take}, bid={bid}, "
            f"distancia mínima={min_distance}."
        )

    volume_min = float(getattr(info, "volume_min", 0) or 0)
    volume_max = float(getattr(info, "volume_max", 0) or 0)
    volume_step = float(getattr(info, "volume_step", 0) or 0)
    if volume_min <= 0 or volume_max <= 0 or volume_step <= 0:
        raise RuntimeError("Especificación de volumen MT5 inválida.")

    target_risk = float(virtual_balance) * MIRROR_RISK_PCT / 100.0
    order_type = mt5.ORDER_TYPE_BUY
    loss_min = mt5.order_calc_profit(order_type, symbol, volume_min, entry, stop)
    if loss_min is None:
        raise RuntimeError(f"order_calc_profit falló: {mt5.last_error()}")
    loss_min = abs(float(loss_min))
    if loss_min <= 0:
        raise RuntimeError("Pérdida calculada para lote mínimo inválida.")
    if loss_min > target_risk + 0.01:
        raise RuntimeError(
            f"Lote mínimo excede riesgo espejo: mínimo=${loss_min:.4f}, "
            f"objetivo=${target_risk:.4f}."
        )

    theoretical = volume_min * target_risk / loss_min
    volume = _floor_to_step(min(theoretical, volume_max), volume_step)
    if volume < volume_min:
        volume = volume_min

    chosen = None
    while volume >= volume_min - 1e-12:
        loss = mt5.order_calc_profit(order_type, symbol, volume, entry, stop)
        profit = mt5.order_calc_profit(order_type, symbol, volume, entry, take)
        margin = mt5.order_calc_margin(order_type, symbol, volume, entry)
        if loss is not None and profit is not None and margin is not None:
            loss = abs(float(loss))
            profit = abs(float(profit))
            margin = float(margin)
            if loss <= target_risk + 0.01 and margin <= MIRROR_MAX_MARGIN_USD + 0.01:
                chosen = (volume, loss, profit, margin)
                break
        volume = _floor_to_step(volume - volume_step, volume_step)

    if chosen is None:
        raise RuntimeError("Ningún lote cumple simultáneamente riesgo 0.5% y margen máximo.")

    volume, estimated_loss, estimated_profit, margin = chosen
    return {
        "direction": BUY,
        "entry_price": entry,
        "atr": float(atr),
        "stop_price": stop,
        "take_price": take,
        "raw_stop_price": raw_stop,
        "raw_take_price": raw_take,
        "volume_lots": volume,
        "target_risk_amount": target_risk,
        "estimated_loss": estimated_loss,
        "estimated_profit": estimated_profit,
        "actual_risk_pct": estimated_loss / virtual_balance * 100 if virtual_balance else 0.0,
        "margin_required": margin,
        "spread": float(tick.ask) - float(tick.bid),
        "bid": float(tick.bid),
        "ask": float(tick.ask),
    }


def filling_candidates(mt5):
    values = []
    for name in ("ORDER_FILLING_FOK", "ORDER_FILLING_IOC", "ORDER_FILLING_RETURN"):
        if hasattr(mt5, name):
            value = getattr(mt5, name)
            if value not in values:
                values.append(value)
    return values


def checked_request(mt5, symbol: str, plan: dict):
    errors = []
    for filling in filling_candidates(mt5):
        tick = mt5.symbol_info_tick(symbol)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(plan["volume_lots"]),
            "type": mt5.ORDER_TYPE_BUY,
            "price": float(tick.ask),
            "sl": float(plan["stop_price"]),
            "tp": float(plan["take_price"]),
            "deviation": MT5_DEVIATION_POINTS,
            "magic": MT5_MIRROR_MAGIC,
            "comment": MT5_MIRROR_COMMENT,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }
        check = mt5.order_check(request)
        if check is None:
            errors.append({"filling": filling, "error": str(mt5.last_error())})
            continue
        retcode = _as_int(getattr(check, "retcode", -1), -1)
        if retcode == 0:
            return request, check
        errors.append(
            {
                "filling": filling,
                "retcode": retcode,
                "comment": str(getattr(check, "comment", "")),
            }
        )
    raise RuntimeError(f"MT5 order_check rechazó el plan exacto: {errors}")


def execute_plan(mt5, symbol: str, plan: dict) -> int:
    request, check = checked_request(mt5, symbol, plan)
    result = mt5.order_send(request)
    if result is None:
        raise RuntimeError(f"order_send devolvió None: {mt5.last_error()}")

    done = _as_int(getattr(mt5, "TRADE_RETCODE_DONE", 10009), 10009)
    retcode = _as_int(getattr(result, "retcode", -1), -1)
    if retcode != done:
        raise RuntimeError(
            f"order_send rechazado retcode={retcode}, comment={getattr(result, 'comment', '')!r}"
        )

    # Buscar la posición creada por magic/símbolo.
    for _ in range(20):
        positions = list(mt5.positions_get(symbol=symbol) or ())
        candidates = [
            p for p in positions
            if _as_int(getattr(p, "magic", 0)) == MT5_MIRROR_MAGIC
        ]
        if candidates:
            pos = max(candidates, key=lambda p: _as_int(getattr(p, "time_msc", 0)))
            return _as_int(getattr(pos, "ticket", 0))
        time.sleep(0.2)

    raise RuntimeError(
        "La orden fue aceptada pero no pude resolver el ticket de la posición. "
        "DETENER y revisar MT5 antes de reiniciar."
    )


def finalize_if_closed(mt5, state: dict) -> None:
    ticket = _as_int(state.get("current_position_ticket"))
    if not ticket:
        return

    positions = mt5.positions_get(ticket=ticket) or ()
    if positions:
        return

    deals = list(mt5.history_deals_get(position=ticket) or ())
    if not deals:
        log_event(
            "POSITION_UNRESOLVED",
            f"Posición #{ticket} no aparece abierta y aún no hay historial.",
        )
        return

    gross_profit = sum(_as_float(getattr(d, "profit", 0)) for d in deals)
    commission = sum(_as_float(getattr(d, "commission", 0)) for d in deals)
    swap = sum(_as_float(getattr(d, "swap", 0)) for d in deals)
    fee = sum(_as_float(getattr(d, "fee", 0)) for d in deals)
    net = gross_profit + commission + swap + fee

    state["virtual_balance"] = _as_float(state.get("virtual_balance"), MIRROR_START_BALANCE) + net
    state["trades"] = _as_int(state.get("trades")) + 1
    if net > 0:
        state["wins"] = _as_int(state.get("wins")) + 1
        state["gross_profit"] = _as_float(state.get("gross_profit")) + net
    else:
        state["losses"] = _as_int(state.get("losses")) + 1
        state["gross_loss"] = _as_float(state.get("gross_loss")) + abs(net)

    trade = {
        "closed_at": utc_iso(),
        "position_ticket": ticket,
        "gross_profit": gross_profit,
        "commission": commission,
        "swap": swap,
        "fee": fee,
        "net_profit": net,
        "balance_after": state["virtual_balance"],
        "deals": [_namedtuple_dict(d) for d in deals],
    }
    append_jsonl(TRADES_FILE, trade)
    state["current_position_ticket"] = None
    update_drawdown(state)
    save_state(state)
    log_event(
        "POSITION_CLOSED",
        f"#{ticket} neto={net:+.4f}, balance espejo=${state['virtual_balance']:.4f}",
        trade,
    )


def maybe_open_from_closed_candle(mt5, mt5_symbol: str, state: dict, candles) -> None:
    finalize_if_closed(mt5, state)
    if state.get("current_position_ticket"):
        return
    if state.get("circuit_breaker"):
        return

    ours, foreign = bot_positions(mt5)
    if foreign:
        raise RuntimeError("Hay posiciones ajenas al mirror; se bloquean nuevas entradas.")
    if ours:
        state["current_position_ticket"] = _as_int(getattr(ours[0], "ticket", 0))
        save_state(state)
        return

    epochs, opens, highs, lows, closes = arrays_from_candles(candles)
    signals = generate_registered_signals(
        MIRROR_STRATEGY,
        epochs,
        highs,
        lows,
        closes,
        source_granularity=MIRROR_GRANULARITY,
        htf_granularity=MIRROR_HTF_GRANULARITY,
        opens=opens,
    )
    signal = signals[-1] if signals else HOLD
    signal_epoch = int(epochs[-1])
    state["last_closed_candle_epoch"] = signal_epoch

    if signal != BUY:
        save_state(state)
        return
    if state.get("last_entry_signal_epoch") == signal_epoch:
        return

    atr_values = calculate_atr(highs, lows, closes, period=MIRROR_ATR_PERIOD)
    atr = atr_values[-1] if atr_values else None
    if atr is None or atr <= 0:
        log_event("SIGNAL_REJECTED", "BUY con ATR inválido.", {"epoch": signal_epoch})
        return

    update_drawdown(state)
    if state.get("circuit_breaker"):
        save_state(state)
        log_event("CIRCUIT_BREAKER", state.get("circuit_breaker_reason") or "drawdown")
        return

    state["last_entry_signal_epoch"] = signal_epoch
    save_state(state)

    try:
        plan = strict_mirror_plan(
            mt5,
            mt5_symbol,
            float(atr),
            _as_float(state.get("virtual_balance"), MIRROR_START_BALANCE),
        )
        request, check = checked_request(mt5, mt5_symbol, plan)
    except Exception as exc:
        log_event(
            "SIGNAL_REJECTED",
            f"BUY {signal_epoch}: {exc}",
            {
                "signal_epoch": signal_epoch,
                "signal_close": closes[-1],
                "atr": atr,
            },
        )
        return

    ticket = execute_plan(mt5, mt5_symbol, plan)
    state["current_position_ticket"] = ticket
    save_state(state)
    log_event(
        "POSITION_OPENED",
        (
            f"BUY #{ticket} | entry≈{plan['entry_price']:.5f} | "
            f"SL={plan['stop_price']:.5f} | TP={plan['take_price']:.5f} | "
            f"lot={plan['volume_lots']} | riesgo≈{plan['actual_risk_pct']:.4f}%"
        ),
        {
            "signal_epoch": signal_epoch,
            "signal_close": closes[-1],
            "plan": plan,
            "order_check": _namedtuple_dict(check),
        },
    )


def process_closed_candle(mt5, mt5_symbol, state, candles, closed):
    if candles and int(candles[-1]["epoch"]) == int(closed["epoch"]):
        candles[-1] = closed
    else:
        candles.append(closed)
    if len(candles) > MIRROR_WARMUP_CANDLES:
        candles = candles[-MIRROR_WARMUP_CANDLES:]
    maybe_open_from_closed_candle(mt5, mt5_symbol, state, candles)
    return candles


def run_check() -> None:
    """Diagnóstico completo sin enviar ninguna orden."""
    mt5, account, terminal, mt5_symbol = connect_mt5()
    ws = None
    try:
        state = load_state()
        ensure_account_is_clean(mt5, state)

        ws = websocket.create_connection(DERIV_PUBLIC_WS, timeout=30)
        public_code, public_name = resolve_public_symbol(ws)
        candles = fetch_public_history(ws, public_code)
        epochs, opens, highs, lows, closes = arrays_from_candles(candles)
        signals = generate_registered_signals(
            MIRROR_STRATEGY,
            epochs,
            highs,
            lows,
            closes,
            source_granularity=MIRROR_GRANULARITY,
            htf_granularity=MIRROR_HTF_GRANULARITY,
            opens=opens,
        )
        atr = calculate_atr(highs, lows, closes, period=MIRROR_ATR_PERIOD)[-1]
        plan = strict_mirror_plan(
            mt5,
            mt5_symbol,
            float(atr),
            _as_float(state.get("virtual_balance"), MIRROR_START_BALANCE),
        )
        request, check = checked_request(mt5, mt5_symbol, plan)

        result = {
            "ok": True,
            "purchase_sent": False,
            "mode": "MT5_DEMO_MIRROR",
            "account_trade_mode": "DEMO",
            "account_login": str(getattr(account, "login", "")),
            "account_server": str(getattr(account, "server", "")),
            "mt5_symbol": mt5_symbol,
            "public_symbol_code": public_code,
            "public_symbol_name": public_name,
            "strategy": MIRROR_STRATEGY,
            "granularity": MIRROR_GRANULARITY,
            "atr_period": MIRROR_ATR_PERIOD,
            "risk_pct": MIRROR_RISK_PCT,
            "stop_atr_mult": MIRROR_STOP_ATR_MULT,
            "reward_ratio": MIRROR_REWARD_RATIO,
            "latest_signal": signals[-1] if signals else HOLD,
            "latest_atr": atr,
            "virtual_balance": state["virtual_balance"],
            "strict_plan": plan,
            "order_check_retcode": _as_int(getattr(check, "retcode", -1), -1),
            "order_check_comment": str(getattr(check, "comment", "")),
        }
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        mt5.shutdown()


def run_forever() -> None:
    state = load_state()
    mt5, account, terminal, mt5_symbol = connect_mt5()
    ensure_account_is_clean(mt5, state)

    log_event(
        "START",
        (
            f"DEMO MIRROR | public={MIRROR_SYMBOL_QUERY} | mt5={mt5_symbol} | "
            f"{MIRROR_STRATEGY} | M1 | riesgo=0.5% | SL=1.5ATR | TP=2R"
        ),
        {
            "login": str(getattr(account, "login", "")),
            "server": str(getattr(account, "server", "")),
        },
    )

    try:
        while True:
            ws = None
            try:
                finalize_if_closed(mt5, state)
                ws = websocket.create_connection(DERIV_PUBLIC_WS, timeout=30)
                public_code, _ = resolve_public_symbol(ws)
                candles = fetch_public_history(ws, public_code)

                ws.send(json.dumps({"ticks": public_code, "subscribe": 1, "req_id": 500}))
                current = None

                while True:
                    finalize_if_closed(mt5, state)
                    raw = ws.recv()
                    if not raw:
                        continue
                    msg = json.loads(raw)
                    if msg.get("error"):
                        raise RuntimeError(str(msg["error"]))
                    if msg.get("msg_type") != "tick":
                        continue

                    tick = msg.get("tick") or {}
                    if "quote" not in tick or "epoch" not in tick:
                        continue
                    price = float(tick["quote"])
                    epoch = int(tick["epoch"])
                    bucket = epoch - (epoch % MIRROR_GRANULARITY)

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
                            mt5, mt5_symbol, state, candles, current
                        )
                        current = {
                            "epoch": bucket,
                            "open": price,
                            "high": price,
                            "low": price,
                            "close": price,
                        }

            except KeyboardInterrupt:
                log_event("STOP", "Detenido por el usuario.")
                break
            except Exception as exc:
                log_event("RECONNECT", str(exc))
                time.sleep(MIRROR_RECONNECT_SECONDS)
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass
    finally:
        mt5.shutdown()


def build_parser():
    parser = argparse.ArgumentParser(description="Deriv MT5 DEMO MIRROR")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Valida feed, cuenta DEMO, símbolo, riesgo, SL/TP y order_check SIN enviar orden.",
    )
    return parser


def main():
    args = build_parser().parse_args()
    if args.check:
        run_check()
    else:
        run_forever()


if __name__ == "__main__":
    main()
