"""Motor CFD para una cuenta Deriv MT5 Standard.

Este motor comparte estrategia, riesgo diario, asignación virtual y dashboard
con el motor Multiplier, pero la ejecución es completamente distinta:

* Multiplier usa Options API, stake y MULTUP/MULTDOWN.
* CFD Standard usa el terminal MetaTrader 5, lotes, margen, bid/ask y órdenes
  de mercado con SL/TP expresados como niveles de precio.

El módulo ``MetaTrader5`` se importa de forma diferida para que el backtester y
el dashboard sigan funcionando en equipos que no tienen el terminal instalado.
"""

from __future__ import annotations

import json
import math
import signal
import time
import unicodedata
from datetime import datetime, timedelta, timezone

from config import (
    ATR_PERIOD,
    DERIV_MAX_RECONNECT_ATTEMPTS,
    LIVE_ALLOCATED_CAPITAL_USD,
    LIVE_BLOCK_IF_OTHER_POSITIONS,
    LIVE_COOLDOWN_CANDLES,
    LIVE_CURRENCY,
    LIVE_DAILY_LOSS_LIMIT_PCT,
    LIVE_GRANULARITY,
    LIVE_MAX_CONSECUTIVE_LOSSES,
    LIVE_MAX_SIGNAL_AGE_SECONDS,
    LIVE_MAX_TRADES_PER_DAY,
    LIVE_REINVEST_PROFITS,
    LIVE_STRATEGY,
    LIVE_WARMUP_CANDLES,
    MAX_DRAWDOWN_STOP_PCT,
    MT5_DEVIATION_POINTS,
    MT5_LOGIN,
    MT5_MAGIC,
    MT5_MAX_MARGIN_USD,
    MT5_ORDER_COMMENT,
    MT5_PASSWORD,
    MT5_POLL_SECONDS,
    MT5_REQUIRE_DERIV_SERVER,
    MT5_SERVER,
    MT5_SYMBOL,
    MT5_TERMINAL_PATH,
    REAL_TRADING_ENABLED,
    REWARD_RATIO,
    RIESGO_POR_OPERACION_PCT,
    SL_ATR_MULT,
    validate_trading_mode,
)
from database.database import (
    apply_closed_trade_to_state,
    create_live_runtime_tables,
    get_bot_state,
    get_broker_trade_by_contract,
    get_latest_pending_trade,
    get_open_broker_trade,
    log_live_event,
    reset_bot_allocation,
    roll_bot_day_if_needed,
    save_broker_trade_open,
    save_candles,
    start_bot_session,
    stop_bot_session,
    update_bot_state,
    update_broker_trade,
    update_broker_trade_by_id,
    utc_now_iso,
)
from indicators.atr import calculate_atr
from live_trading.cfd_risk import build_cfd_order_plan
from strategy.registry import STRATEGIES, generate_registered_signals, validate_strategy_id
from strategy.strategy_engine import BUY, HOLD, SELL
from utils.logging_config import configure_logging


logger = configure_logging("mt5_cfd_trading")


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _normalize(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.lower().replace("_", " ").replace("-", " ").split())


def _namedtuple_dict(value):
    if value is None:
        return None
    if hasattr(value, "_asdict"):
        return dict(value._asdict())
    if isinstance(value, dict):
        return value
    return {name: getattr(value, name) for name in dir(value) if not name.startswith("_")}


def _load_mt5_module():
    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Falta el paquete MetaTrader5. En Windows ejecuta "
            "INSTALAR_DEPENDENCIAS.bat y asegúrate de tener Deriv MT5 instalado."
        ) from exc
    return mt5


def _timeframe_for_seconds(mt5, seconds):
    mapping = {
        60: "TIMEFRAME_M1",
        120: "TIMEFRAME_M2",
        180: "TIMEFRAME_M3",
        240: "TIMEFRAME_M4",
        300: "TIMEFRAME_M5",
        600: "TIMEFRAME_M10",
        900: "TIMEFRAME_M15",
        1800: "TIMEFRAME_M30",
        3600: "TIMEFRAME_H1",
        14400: "TIMEFRAME_H4",
        86400: "TIMEFRAME_D1",
    }
    constant = mapping.get(int(seconds))
    if not constant or not hasattr(mt5, constant):
        raise RuntimeError(f"Granularidad {seconds}s no soportada por MT5")
    return getattr(mt5, constant)


def _rate_to_candle(rate):
    return {
        "epoch": _as_int(rate["time"]),
        "open": _as_float(rate["open"]),
        "high": _as_float(rate["high"]),
        "low": _as_float(rate["low"]),
        "close": _as_float(rate["close"]),
    }


class MT5CFDTradingEngine:
    execution_product = "cfd_standard"

    def __init__(
        self,
        *,
        mode="demo",
        symbol_query=MT5_SYMBOL,
        granularity=LIVE_GRANULARITY,
        strategy_id=LIVE_STRATEGY,
        allocation_capital=LIVE_ALLOCATED_CAPITAL_USD,
        check_only=False,
    ):
        self.mode = validate_trading_mode(mode)
        self.symbol_query = symbol_query or MT5_SYMBOL
        self.granularity = int(granularity)
        self.strategy_id = validate_strategy_id(strategy_id)
        self.allocation_capital = float(allocation_capital)
        self.check_only = bool(check_only)
        self.mt5 = None
        self.account = None
        self.terminal = None
        self.symbol_code = None
        self.symbol_name = None
        self.account_id = None
        self.account_server = None
        self.session_id = None
        self.current_position_id = None
        self.stop_requested = False
        self.epochs = []
        self.opens = []
        self.highs = []
        self.lows = []
        self.closes = []
        self.max_context = max(LIVE_WARMUP_CANDLES + 200, 4500)
        self.last_state_heartbeat_at = 0.0
        self.last_account_refresh_at = 0.0
        self._session_started = False
        self._install_signal_handlers()
        create_live_runtime_tables()

    def _install_signal_handlers(self):
        def request_stop(_signum, _frame):
            self.stop_requested = True
            logger.warning("Se solicitó detener el motor CFD; no se abrirán posiciones nuevas")

        try:
            signal.signal(signal.SIGINT, request_stop)
            signal.signal(signal.SIGTERM, request_stop)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Terminal, cuenta y símbolo
    # ------------------------------------------------------------------
    def _connect_terminal(self):
        self.mt5 = _load_mt5_module()
        mt5 = self.mt5
        kwargs = {"timeout": 60_000}
        if MT5_LOGIN is not None:
            kwargs["login"] = int(MT5_LOGIN)
        if MT5_PASSWORD:
            kwargs["password"] = MT5_PASSWORD
        if MT5_SERVER:
            kwargs["server"] = MT5_SERVER

        if MT5_TERMINAL_PATH:
            connected = mt5.initialize(MT5_TERMINAL_PATH, **kwargs)
        else:
            connected = mt5.initialize(**kwargs)
        if not connected:
            raise RuntimeError(f"MT5 initialize() falló: {mt5.last_error()}")

        account = mt5.account_info()
        terminal = mt5.terminal_info()
        if account is None or terminal is None:
            raise RuntimeError(f"MT5 no devolvió cuenta/terminal: {mt5.last_error()}")

        trade_mode = _as_int(getattr(account, "trade_mode", -1), -1)
        demo_constant = _as_int(getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0), 0)
        real_constant = _as_int(getattr(mt5, "ACCOUNT_TRADE_MODE_REAL", 2), 2)
        expected = demo_constant if self.mode == "demo" else real_constant
        if trade_mode != expected:
            actual = "DEMO" if trade_mode == demo_constant else "REAL/OTRO"
            raise RuntimeError(
                f"La cuenta activa de MT5 es {actual}, pero el bot está en modo {self.mode.upper()}."
            )

        server = str(getattr(account, "server", "") or "")
        company = str(getattr(account, "company", "") or "")
        if MT5_REQUIRE_DERIV_SERVER and "deriv" not in f"{server} {company}".lower():
            raise RuntimeError(
                f"La cuenta MT5 activa no parece ser de Deriv (server={server!r}, company={company!r})."
            )

        if not bool(getattr(terminal, "connected", True)):
            raise RuntimeError("El terminal MT5 no está conectado al servidor")
        if not bool(getattr(terminal, "trade_allowed", True)):
            raise RuntimeError(
                "MT5 tiene AutoTrading/Algo Trading desactivado. Actívalo antes de operar."
            )
        if bool(getattr(terminal, "tradeapi_disabled", False)):
            raise RuntimeError("El terminal MT5 tiene deshabilitado el trading desde Python/API")

        self.account = account
        self.terminal = terminal
        self.account_id = str(getattr(account, "login", ""))
        self.account_server = server
        self.symbol_code = self._resolve_symbol(self.symbol_query)
        self.symbol_name = self.symbol_code
        return account

    def _disconnect_terminal(self):
        if self.mt5 is not None:
            try:
                self.mt5.shutdown()
            except Exception:  # noqa: BLE001, S110
                pass
        self.mt5 = None

    def _resolve_symbol(self, query):
        mt5 = self.mt5
        symbols = mt5.symbols_get()
        if not symbols:
            raise RuntimeError(f"MT5 no devolvió símbolos: {mt5.last_error()}")
        normalized = _normalize(query)
        exact = []
        scored = []
        query_tokens = set(normalized.split())
        for item in symbols:
            name = str(getattr(item, "name", "") or "")
            description = str(getattr(item, "description", "") or "")
            path = str(getattr(item, "path", "") or "")
            fields = [_normalize(name), _normalize(description), _normalize(path)]
            if normalized in fields:
                exact.append(item)
                continue
            tokens = set(" ".join(fields).split())
            score = len(query_tokens & tokens)
            if any(normalized and normalized in value for value in fields):
                score += 20
            # Evita seleccionar Boom cuando se pidió Crash y viceversa.
            if "crash" in normalized and "crash" not in " ".join(fields):
                score = 0
            if "boom" in normalized and "boom" not in " ".join(fields):
                score = 0
            if score:
                scored.append((score, len(name), item))
        selected = exact[0] if exact else (min(scored, key=lambda x: (-x[0], x[1]))[2] if scored else None)
        if selected is None:
            raise RuntimeError(f"No se encontró {query!r} en Market Watch de MT5")
        name = str(getattr(selected, "name", ""))
        if not mt5.symbol_select(name, True):
            raise RuntimeError(f"No se pudo habilitar {name} en Market Watch: {mt5.last_error()}")
        return name

    def _account_balance(self):
        info = self.mt5.account_info()
        if info is None:
            raise RuntimeError(f"No se pudo leer account_info(): {self.mt5.last_error()}")
        self.account = info
        currency = str(getattr(info, "currency", "") or LIVE_CURRENCY)
        if currency.upper() != LIVE_CURRENCY.upper():
            raise RuntimeError(
                f"La cuenta MT5 usa {currency}; LIVE_CURRENCY={LIVE_CURRENCY}."
            )
        return _as_float(getattr(info, "balance", 0)), currency

    # ------------------------------------------------------------------
    # Validación sin enviar órdenes
    # ------------------------------------------------------------------
    def check_connection(self):
        try:
            account = self._connect_terminal()
            candles = self._get_completed_candles(350)
            if len(candles) < 220:
                raise RuntimeError("MT5 no devolvió suficiente histórico cerrado")
            highs = [c["high"] for c in candles]
            lows = [c["low"] for c in candles]
            closes = [c["close"] for c in candles]
            atr = calculate_atr(highs, lows, closes, period=ATR_PERIOD)[-1]
            balance, currency = self._account_balance()
            allocation = min(balance, self.allocation_capital)

            validation = None
            errors = []
            for direction in (BUY, SELL):
                plan = build_cfd_order_plan(
                    mt5=self.mt5,
                    symbol=self.symbol_code,
                    direction=direction,
                    signal_price=closes[-1],
                    atr=atr,
                    virtual_balance=allocation,
                    allocation_capital=allocation,
                    reinvest_profits=False,
                    risk_pct=RIESGO_POR_OPERACION_PCT,
                    stop_atr_mult=SL_ATR_MULT,
                    reward_ratio=REWARD_RATIO,
                    max_margin=MT5_MAX_MARGIN_USD,
                )
                if plan is None:
                    errors.append({"direction": direction, "error": "sin plan de riesgo viable"})
                    continue
                checked = self._prepare_checked_request(plan)
                if checked is None:
                    errors.append({"direction": direction, "error": "order_check rechazado"})
                    continue
                request, check_result = checked
                validation = {
                    "direction": direction,
                    "volume_lots": plan.volume_lots,
                    "entry_bid_ask": plan.entry_price,
                    "stop_loss_price": plan.stop_price,
                    "take_profit_price": plan.take_price,
                    "estimated_loss": plan.estimated_loss,
                    "estimated_profit": plan.estimated_profit,
                    "margin_required": plan.margin_required,
                    "spread_points": plan.spread_points,
                    "order_check_retcode": _as_int(getattr(check_result, "retcode", 0)),
                    "order_check_comment": str(getattr(check_result, "comment", "")),
                    "request": {k: v for k, v in request.items() if k not in {"password"}},
                }
                break
            if validation is None:
                raise RuntimeError(f"Ninguna orden CFD pasó order_check: {errors}")

            return {
                "mode": self.mode,
                "execution_product": self.execution_product,
                "platform": "Deriv MT5",
                "account_id": self.account_id,
                "account_server": self.account_server,
                "account_name": str(getattr(account, "name", "") or ""),
                "account_balance": balance,
                "currency": currency,
                "symbol_query": self.symbol_query,
                "symbol_code": self.symbol_code,
                "strategy_id": self.strategy_id,
                "strategy_name": STRATEGIES[self.strategy_id],
                "allocation_capital": allocation,
                "order_validation": validation,
                "purchase_sent": False,
                "important": (
                    "La etiqueta comercial Standard se confirma seleccionando la cuenta "
                    "Standard correcta en Deriv MT5; MetaTrader expone login/server, no esa etiqueta."
                ),
            }
        finally:
            self._disconnect_terminal()

    # ------------------------------------------------------------------
    # Ciclo principal
    # ------------------------------------------------------------------
    def reset_allocation(self):
        return reset_bot_allocation(self.mode, self.allocation_capital)

    def run_forever(self):
        reconnect_attempt = 0
        last_error = None
        while not self.stop_requested:
            try:
                self._connect_and_run()
                reconnect_attempt = 0
                if not self.stop_requested:
                    raise RuntimeError("La sesión MT5 terminó inesperadamente")
            except KeyboardInterrupt:
                self.stop_requested = True
            except Exception as exc:
                last_error = str(exc)
                logger.exception("Fallo del motor CFD: %s", exc)  # noqa: TRY401
                log_live_event(
                    self.mode,
                    "ERROR",
                    "mt5_engine_error",
                    str(exc),
                    {"type": type(exc).__name__, "product": self.execution_product},
                )
                reconnect_attempt += 1
                update_bot_state(
                    self.mode,
                    status="reconnecting",
                    last_error=last_error,
                    reconnect_attempts=reconnect_attempt,
                )
                if reconnect_attempt > DERIV_MAX_RECONNECT_ATTEMPTS:
                    break
                end = time.monotonic() + min(60, 2 ** min(reconnect_attempt, 6))
                while time.monotonic() < end and not self.stop_requested:
                    time.sleep(0.25)
            finally:
                self._disconnect_terminal()

        status = "stopped" if self.stop_requested else "error"
        stop_bot_session(self.mode, status=status, error=last_error if status == "error" else None)
        logger.info("Motor CFD detenido. El SL/TP permanece alojado en MT5/broker.")

    def _connect_and_run(self):
        self._connect_terminal()
        balance, currency = self._account_balance()
        if balance <= 0:
            raise RuntimeError("La cuenta MT5 no tiene saldo disponible")

        state = get_bot_state(self.mode)
        if not state or not self._session_started:
            state = start_bot_session(
                self.mode,
                account_id=self.account_id,
                execution_product=self.execution_product,
                symbol_query=self.symbol_query,
                symbol_code=self.symbol_code,
                symbol_name=self.symbol_name,
                strategy_id=self.strategy_id,
                granularity=self.granularity,
                currency=currency,
                allocation_capital=min(self.allocation_capital, balance),
                account_balance=balance,
            )
            self._session_started = True
            self.session_id = state["session_id"]
        else:
            self.session_id = state.get("session_id")
            update_bot_state(
                self.mode,
                execution_product=self.execution_product,
                status="paused" if state.get("circuit_breaker") else "running",
                account_balance=balance,
                reconnect_attempts=0,
            )

        if float(state["allocation_capital"]) > balance:
            raise RuntimeError(
                "El saldo MT5 es menor que la asignación virtual persistida. "
                "Usa --reset-allocation con la cuenta sin posiciones."
            )

        self._load_warmup()
        self._recover_or_validate_positions()
        logger.warning(
            "CFD STANDARD %s | cuenta=%s | server=%s | símbolo=%s | asignación=$%.2f",
            self.mode.upper(),
            self.account_id,
            self.account_server,
            self.symbol_code,
            float(state["allocation_capital"]),
        )
        log_live_event(
            self.mode,
            "INFO",
            "mt5_connected",
            f"Conectado a Deriv MT5 {self.symbol_code}",
            {
                "execution_product": self.execution_product,
                "account_id": self.account_id,
                "server": self.account_server,
                "balance": balance,
            },
        )

        while not self.stop_requested:
            now = time.monotonic()
            self._monitor_position()
            self._poll_closed_candles()
            if now - self.last_state_heartbeat_at >= 5:
                state = get_bot_state(self.mode) or {}
                update_bot_state(
                    self.mode,
                    status="paused" if state.get("circuit_breaker") else "running",
                    execution_product=self.execution_product,
                )
                self.last_state_heartbeat_at = now
            if now - self.last_account_refresh_at >= 5:
                balance, _ = self._account_balance()
                update_bot_state(self.mode, account_balance=balance)
                self.last_account_refresh_at = now
            time.sleep(max(0.25, MT5_POLL_SECONDS))

    # ------------------------------------------------------------------
    # Datos y señales
    # ------------------------------------------------------------------
    def _get_completed_candles(self, count):
        timeframe = _timeframe_for_seconds(self.mt5, self.granularity)
        rates = self.mt5.copy_rates_from_pos(self.symbol_code, timeframe, 0, int(count) + 1)
        if rates is None:
            raise RuntimeError(f"copy_rates_from_pos falló: {self.mt5.last_error()}")
        candles = [_rate_to_candle(rate) for rate in rates]
        # La última barra es la vela actual y todavía no está cerrada.
        return candles[:-1] if candles else []

    def _load_warmup(self):
        completed = self._get_completed_candles(LIVE_WARMUP_CANDLES)
        if len(completed) < 3200 and self.strategy_id == "hybrid":
            raise RuntimeError(
                f"MT5 solo devolvió {len(completed)} velas cerradas; hybrid necesita ~3200 M1. "
                "Aumenta 'Max. bars in chart' en MT5 y abre el gráfico del símbolo."
            )
        if len(completed) < 220:
            raise RuntimeError("Histórico MT5 insuficiente para indicadores")
        save_candles(self.symbol_code, self.granularity, completed)
        self.epochs = [c["epoch"] for c in completed][-self.max_context :]
        self.opens = [c["open"] for c in completed][-self.max_context :]
        self.highs = [c["high"] for c in completed][-self.max_context :]
        self.lows = [c["low"] for c in completed][-self.max_context :]
        self.closes = [c["close"] for c in completed][-self.max_context :]

    def _poll_closed_candles(self):
        completed = self._get_completed_candles(10)
        if not completed:
            return
        for candle in completed:
            if not self.epochs or candle["epoch"] > self.epochs[-1]:
                self._on_candle_closed(candle)

    def _on_candle_closed(self, candle):
        epoch = int(candle["epoch"])
        save_candles(self.symbol_code, self.granularity, [candle])
        self.epochs.append(epoch)
        self.opens.append(float(candle["open"]))
        self.highs.append(float(candle["high"]))
        self.lows.append(float(candle["low"]))
        self.closes.append(float(candle["close"]))
        self.epochs = self.epochs[-self.max_context :]
        self.opens = self.opens[-self.max_context :]
        self.highs = self.highs[-self.max_context :]
        self.lows = self.lows[-self.max_context :]
        self.closes = self.closes[-self.max_context :]

        signals = generate_registered_signals(
            self.strategy_id,
            self.epochs,
            self.highs,
            self.lows,
            self.closes,
            source_granularity=self.granularity,
            opens=self.opens,
        )
        signal_value = signals[-1] if signals else HOLD
        signal_value = signal_value or HOLD
        tick = self.mt5.symbol_info_tick(self.symbol_code)
        last_price = _as_float(getattr(tick, "last", 0)) or _as_float(getattr(tick, "bid", 0))
        update_bot_state(
            self.mode,
            last_candle_epoch=epoch,
            last_signal=signal_value,
            last_signal_epoch=epoch,
            last_price=last_price or candle["close"],
            execution_product=self.execution_product,
        )
        logger.info("MT5 vela cerrada %s close=%.5f señal=%s", epoch, candle["close"], signal_value)
        if signal_value in (BUY, SELL):
            allowed, reason = self._risk_gate(epoch)
            if allowed:
                self._execute_signal(signal_value, epoch, float(candle["close"]))
            else:
                log_live_event(
                    self.mode,
                    "INFO",
                    "cfd_signal_blocked",
                    f"Señal {signal_value} bloqueada: {reason}",
                    {"signal_epoch": epoch},
                )

    # ------------------------------------------------------------------
    # Riesgo y órdenes
    # ------------------------------------------------------------------
    def _risk_gate(self, signal_epoch):
        state = roll_bot_day_if_needed(self.mode)
        if self.current_position_id or state.get("current_contract_id") or get_open_broker_trade(self.mode):
            return False, "ya existe una posición abierta"
        if state.get("circuit_breaker"):
            return False, state.get("circuit_breaker_reason") or "circuit breaker activo"
        if float(state["virtual_balance"]) <= 0:
            self._activate_breaker("asignación virtual agotada")
            return False, "asignación virtual agotada"
        if int(state["trades_today"]) >= LIVE_MAX_TRADES_PER_DAY:
            self._activate_breaker("límite diario de operaciones alcanzado")
            return False, "límite diario de operaciones"
        if int(state["consecutive_losses"]) >= LIVE_MAX_CONSECUTIVE_LOSSES:
            self._activate_breaker("máximo de pérdidas consecutivas alcanzado")
            return False, "máximo de pérdidas consecutivas"
        daily_limit = float(state["allocation_capital"]) * LIVE_DAILY_LOSS_LIMIT_PCT / 100
        if float(state["realized_pnl_today"]) <= -daily_limit:
            self._activate_breaker("límite de pérdida diaria alcanzado")
            return False, "límite de pérdida diaria"
        peak = float(state.get("peak_balance") or state["allocation_capital"])
        virtual = float(state["virtual_balance"])
        drawdown = ((peak - virtual) / peak * 100) if peak > 0 else 0
        if drawdown >= MAX_DRAWDOWN_STOP_PCT:
            self._activate_breaker("drawdown máximo alcanzado")
            return False, "drawdown máximo"
        cooldown_until = _as_int(state.get("cooldown_until_epoch"))
        if cooldown_until and signal_epoch < cooldown_until:
            return False, f"cooldown hasta epoch {cooldown_until}"
        age = int(time.time()) - (signal_epoch + self.granularity)
        if age > LIVE_MAX_SIGNAL_AGE_SECONDS:
            return False, f"señal antigua ({age}s)"
        return True, "ok"

    def _activate_breaker(self, reason):
        update_bot_state(
            self.mode,
            status="paused",
            circuit_breaker=1,
            circuit_breaker_reason=reason,
        )
        log_live_event(self.mode, "WARNING", "cfd_circuit_breaker", reason)
        logger.warning("CIRCUIT BREAKER CFD: %s", reason)

    def _base_order_request(self, plan, filling):
        mt5 = self.mt5
        order_type = mt5.ORDER_TYPE_BUY if plan.direction == BUY else mt5.ORDER_TYPE_SELL
        tick = mt5.symbol_info_tick(self.symbol_code)
        current_price = _as_float(tick.ask if plan.direction == BUY else tick.bid)
        return {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol_code,
            "volume": float(plan.volume_lots),
            "type": order_type,
            "price": current_price,
            "sl": float(plan.stop_price),
            "tp": float(plan.take_price),
            "deviation": int(MT5_DEVIATION_POINTS),
            "magic": int(MT5_MAGIC),
            "comment": MT5_ORDER_COMMENT,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

    def _filling_candidates(self):
        mt5 = self.mt5
        values = []
        for name in ("ORDER_FILLING_FOK", "ORDER_FILLING_IOC", "ORDER_FILLING_RETURN"):
            if hasattr(mt5, name):
                value = getattr(mt5, name)
                if value not in values:
                    values.append(value)
        return values

    def _prepare_checked_request(self, plan):
        mt5 = self.mt5
        errors = []

        for filling in self._filling_candidates():
            request = self._base_order_request(plan, filling)
            check = mt5.order_check(request)

            if check is None:
                errors.append({
                    "filling": filling,
                    "error": str(mt5.last_error()),
                })
                continue

            retcode = _as_int(getattr(check, "retcode", -1), -1)
            comment = str(getattr(check, "comment", ""))

            if retcode == 0:
                return request, check

            errors.append({
                "filling": filling,
                "retcode": retcode,
                "comment": comment,
                "volume": request["volume"],
                "price": request["price"],
                "sl": request["sl"],
                "tp": request["tp"],
            })

        raise RuntimeError(f"Todos los order_check fueron rechazados: {errors}")

    def _execute_signal(self, direction, signal_epoch, signal_price):
        state = roll_bot_day_if_needed(self.mode)
        broker_balance, _ = self._account_balance()
        update_bot_state(self.mode, account_balance=broker_balance)
        risk_balance = min(float(state["virtual_balance"]), broker_balance)
        atr_values = calculate_atr(self.highs, self.lows, self.closes, period=ATR_PERIOD)
        atr = atr_values[-1] if atr_values else None
        if atr is None or atr <= 0:
            return

        plan = build_cfd_order_plan(
            mt5=self.mt5,
            symbol=self.symbol_code,
            direction=direction,
            signal_price=signal_price,
            atr=atr,
            virtual_balance=risk_balance,
            allocation_capital=min(float(state["allocation_capital"]), broker_balance),
            reinvest_profits=LIVE_REINVEST_PROFITS,
            risk_pct=RIESGO_POR_OPERACION_PCT,
            stop_atr_mult=SL_ATR_MULT,
            reward_ratio=REWARD_RATIO,
            max_margin=MT5_MAX_MARGIN_USD,
        )
        if plan is None:
            log_live_event(
                self.mode,
                "WARNING",
                "cfd_risk_plan_rejected",
                "El lote mínimo, riesgo o margen del CFD excede la asignación permitida",
                {"direction": direction, "signal_price": signal_price, "atr": atr},
            )
            return

        checked = self._prepare_checked_request(plan)
        if checked is None:
            log_live_event(
                self.mode,
                "ERROR",
                "cfd_order_check_failed",
                "MT5 rechazó la orden en order_check para todas las políticas de llenado",
                plan.to_dict(),
            )
            return
        request, check = checked

        trade = {
            "session_id": self.session_id,
            "mode": self.mode,
            "execution_product": self.execution_product,
            "account_id": self.account_id,
            "symbol_code": self.symbol_code,
            "symbol_name": self.symbol_name,
            "granularity": self.granularity,
            "strategy_id": self.strategy_id,
            "direction": direction,
            "contract_type": "CFD_BUY" if direction == BUY else "CFD_SELL",
            "signal_epoch": signal_epoch,
            "proposal_id": None,
            "contract_id": None,
            "transaction_id": None,
            "order_id": None,
            # Campos heredados: stake representa margen y multiplier=0 en CFD.
            "stake": plan.margin_required,
            "multiplier": 0,
            "volume_lots": plan.volume_lots,
            "margin_required": plan.margin_required,
            "stop_loss_amount": plan.estimated_loss,
            "take_profit_amount": plan.estimated_profit,
            "stop_loss_price": plan.stop_price,
            "take_profit_price": plan.take_price,
            "entry_price_signal": signal_price,
            "entry_spot": plan.entry_price,
            "buy_price": plan.entry_price,
            "spread_points": plan.spread_points,
            "slippage_points": 0.0,
            "magic": MT5_MAGIC,
            "account_server": self.account_server,
            "status": "order_ready",
            "opened_at": utc_now_iso(),
            "raw_open_json": json.dumps(
                {"plan": plan.to_dict(), "order_check": _namedtuple_dict(check), "request": request},
                ensure_ascii=False,
                default=str,
            ),
        }
        trade_id = save_broker_trade_open(trade)
        update_broker_trade_by_id(trade_id, status="order_submitted")

        result = self.mt5.order_send(request)
        if result is None:
            self._mark_execution_unknown(trade_id, "order_send devolvió None")
            raise RuntimeError(f"order_send devolvió None: {self.mt5.last_error()}")

        retcode = _as_int(getattr(result, "retcode", -1), -1)
        accepted = {
            _as_int(getattr(self.mt5, "TRADE_RETCODE_DONE", 10009)),
            _as_int(getattr(self.mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010)),
            _as_int(getattr(self.mt5, "TRADE_RETCODE_PLACED", 10008)),
        }
        if retcode not in accepted:
            update_broker_trade_by_id(
                trade_id,
                status="failed",
                exit_reason=f"mt5_rejected:{retcode}:{getattr(result, 'comment', '')}",
                closed_at=utc_now_iso(),
                raw_close_json=json.dumps(_namedtuple_dict(result), ensure_ascii=False, default=str),
            )
            log_live_event(
                self.mode,
                "ERROR",
                "cfd_order_rejected",
                str(getattr(result, "comment", "Orden CFD rechazada")),
                _namedtuple_dict(result),
            )
            return

        order_id = _as_int(getattr(result, "order", 0))
        deal_id = _as_int(getattr(result, "deal", 0))
        fill_price = _as_float(getattr(result, "price", plan.entry_price), plan.entry_price)
        position = self._find_new_bot_position(direction, order_id)
        position_id = _as_int(getattr(position, "ticket", 0)) if position is not None else order_id
        if not position_id:
            position_id = deal_id
        if not position_id:
            self._mark_execution_unknown(trade_id, "MT5 aceptó la orden sin ticket identificable")
            raise RuntimeError("MT5 aceptó la orden, pero no devolvió order/deal/position ticket")

        slippage_points = abs(fill_price - plan.entry_price) / plan.point if plan.point else 0.0
        self.current_position_id = position_id
        update_bot_state(
            self.mode,
            current_contract_id=position_id,
            current_direction=direction,
            current_unrealized_pnl=0.0,
            execution_product=self.execution_product,
        )
        update_broker_trade_by_id(
            trade_id,
            contract_id=position_id,
            order_id=order_id or None,
            transaction_id=str(deal_id or ""),
            entry_spot=fill_price,
            buy_price=fill_price,
            slippage_points=slippage_points,
            status="position_open",
            raw_open_json=json.dumps(
                {
                    "plan": plan.to_dict(),
                    "order_check": _namedtuple_dict(check),
                    "request": request,
                    "order_send": _namedtuple_dict(result),
                    "position": _namedtuple_dict(position),
                },
                ensure_ascii=False,
                default=str,
            ),
        )
        logger.warning(
            "CFD DEMO ABIERTO %s | posición=%s | %.2f lot | entrada=%.5f | SL=%.5f | TP=%.5f | margen=$%.2f",
            direction,
            position_id,
            plan.volume_lots,
            fill_price,
            plan.stop_price,
            plan.take_price,
            plan.margin_required,
        )
        log_live_event(
            self.mode,
            "TRADE",
            "cfd_position_opened",
            f"{direction} {self.symbol_code} posición {position_id}",
            {**plan.to_dict(), "fill_price": fill_price, "position_id": position_id},
        )

    def _mark_execution_unknown(self, trade_id, message):
        update_broker_trade_by_id(trade_id, status="execution_unknown", exit_reason=message)
        reason = (
            "Resultado de orden CFD incierto. El bot queda bloqueado hasta reconciliar "
            "las posiciones de MT5 al reiniciar."
        )
        update_bot_state(
            self.mode,
            circuit_breaker=1,
            circuit_breaker_reason=reason,
            last_error=message,
        )
        log_live_event(self.mode, "CRITICAL", "cfd_execution_unknown", reason, {"error": message})

    def _find_new_bot_position(self, direction, order_id=0):
        deadline = time.monotonic() + 5.0
        wanted_type = self.mt5.POSITION_TYPE_BUY if direction == BUY else self.mt5.POSITION_TYPE_SELL
        while time.monotonic() < deadline:
            if order_id:
                direct = self.mt5.positions_get(ticket=order_id)
                if direct:
                    return direct[0]
            positions = self.mt5.positions_get(symbol=self.symbol_code) or ()
            candidates = [
                p
                for p in positions
                if _as_int(getattr(p, "magic", 0)) == MT5_MAGIC
                and _as_int(getattr(p, "type", -1), -1) == _as_int(wanted_type)
            ]
            if candidates:
                return max(candidates, key=lambda p: _as_int(getattr(p, "time_msc", 0)))
            time.sleep(0.2)
        return None

    # ------------------------------------------------------------------
    # Recuperación, monitoreo y cierre
    # ------------------------------------------------------------------
    def _bot_positions(self):
        positions = list(self.mt5.positions_get() or ())
        bot = [p for p in positions if _as_int(getattr(p, "magic", 0)) == MT5_MAGIC]
        foreign = [p for p in positions if _as_int(getattr(p, "magic", 0)) != MT5_MAGIC]
        return bot, foreign

    def _recover_or_validate_positions(self):
        bot_positions, foreign = self._bot_positions()
        if foreign and LIVE_BLOCK_IF_OTHER_POSITIONS:
            tickets = [_as_int(getattr(p, "ticket", 0)) for p in foreign]
            raise RuntimeError(
                f"La cuenta MT5 tiene posiciones ajenas al bot: {tickets}. "
                "Usa una cuenta DEMO dedicada o ciérralas."
            )
        if len(bot_positions) > 1:
            raise RuntimeError(
                "Hay más de una posición del bot en MT5; se bloquea para evitar sobreexposición."
            )

        open_db = get_open_broker_trade(self.mode)
        pending = get_latest_pending_trade(self.mode)
        if bot_positions:
            position = bot_positions[0]
            ticket = _as_int(getattr(position, "ticket", 0))
            direction = BUY if _as_int(getattr(position, "type", 0)) == _as_int(self.mt5.POSITION_TYPE_BUY) else SELL
            if open_db and _as_int(open_db.get("contract_id")) not in {0, ticket}:
                raise RuntimeError(
                    f"SQLite espera la posición {open_db.get('contract_id')}, pero MT5 tiene {ticket}."
                )
            if pending and not open_db:
                update_broker_trade_by_id(
                    pending["id"],
                    contract_id=ticket,
                    order_id=_as_int(getattr(position, "ticket", 0)) or None,
                    entry_spot=_as_float(getattr(position, "price_open", 0)),
                    buy_price=_as_float(getattr(position, "price_open", 0)),
                    status="position_open",
                    raw_open_json=json.dumps(
                        {"recovered_position": _namedtuple_dict(position), "previous": pending.get("raw_open_json")},
                        ensure_ascii=False,
                        default=str,
                    ),
                )
                breaker_reason = str((get_bot_state(self.mode) or {}).get("circuit_breaker_reason") or "").lower()
                if "incierto" in breaker_reason or "reconciliar" in breaker_reason:
                    update_bot_state(
                        self.mode,
                        circuit_breaker=0,
                        circuit_breaker_reason=None,
                        last_error=None,
                        status="running",
                    )
            elif not open_db and not pending:
                raise RuntimeError(
                    f"MT5 tiene la posición del magic {MT5_MAGIC} #{ticket}, pero no existe registro local. "
                    "No se abrirán más operaciones hasta revisarla."
                )
            self.current_position_id = ticket
            update_bot_state(
                self.mode,
                current_contract_id=ticket,
                current_direction=direction,
                current_unrealized_pnl=_as_float(getattr(position, "profit", 0)),
                execution_product=self.execution_product,
            )
            log_live_event(
                self.mode,
                "WARNING",
                "cfd_position_recovered",
                f"Posición MT5 {ticket} recuperada después del reinicio",
                _namedtuple_dict(position),
            )
            return

        if open_db:
            ticket = _as_int(open_db.get("contract_id"))
            if ticket and self._finalize_position(ticket, open_db):
                return
            raise RuntimeError(
                f"La posición {ticket} figura abierta en SQLite, pero no aparece en MT5 ni pudo cerrarse desde el historial."
            )
        if pending:
            status = str(pending.get("status") or "")
            if status == "order_ready":
                update_broker_trade_by_id(
                    pending["id"],
                    status="failed",
                    exit_reason="interrupted_before_order_send",
                    closed_at=utc_now_iso(),
                )
            else:
                reason = (
                    "Hay una orden CFD pendiente sin posición visible. Revisa History en MT5 antes de reanudar."
                )
                update_bot_state(
                    self.mode,
                    circuit_breaker=1,
                    circuit_breaker_reason=reason,
                    last_error=reason,
                )
                raise RuntimeError(reason)

    def _monitor_position(self):
        if not self.current_position_id:
            return
        positions = self.mt5.positions_get(ticket=int(self.current_position_id)) or ()
        if positions:
            position = positions[0]
            unrealized = _as_float(getattr(position, "profit", 0)) + _as_float(getattr(position, "swap", 0))
            update_bot_state(
                self.mode,
                current_contract_id=int(self.current_position_id),
                current_unrealized_pnl=unrealized,
                last_price=_as_float(getattr(position, "price_current", 0)),
            )
            return

        open_trade = get_open_broker_trade(self.mode)
        if open_trade and self._finalize_position(int(self.current_position_id), open_trade):
            self.current_position_id = None

    def _history_deals_for_position(self, position_id, opened_at=None):
        deals = self.mt5.history_deals_get(position=int(position_id))
        if deals:
            return list(deals)
        start = datetime.now(timezone.utc) - timedelta(days=30)
        if opened_at:
            try:
                start = datetime.fromisoformat(opened_at).astimezone(timezone.utc) - timedelta(days=1)
            except (ValueError, TypeError):
                pass
        all_deals = self.mt5.history_deals_get(start, datetime.now(timezone.utc) + timedelta(minutes=1)) or ()
        return [
            deal
            for deal in all_deals
            if _as_int(getattr(deal, "position_id", 0)) == int(position_id)
            or _as_int(getattr(deal, "order", 0)) == int(position_id)
        ]

    def _finalize_position(self, position_id, open_trade):
        deals = self._history_deals_for_position(position_id, open_trade.get("opened_at"))
        if not deals:
            return False
        entry_out_values = {
            _as_int(getattr(self.mt5, "DEAL_ENTRY_OUT", 1)),
            _as_int(getattr(self.mt5, "DEAL_ENTRY_OUT_BY", 3)),
            _as_int(getattr(self.mt5, "DEAL_ENTRY_INOUT", 2)),
        }
        exit_deals = [d for d in deals if _as_int(getattr(d, "entry", -1), -1) in entry_out_values]
        if not exit_deals:
            return False
        entry_deals = [d for d in deals if d not in exit_deals]
        exit_deal = sorted(exit_deals, key=lambda d: _as_int(getattr(d, "time_msc", 0)))[-1]  # noqa: FURB192
        entry_deal = sorted(entry_deals or deals, key=lambda d: _as_int(getattr(d, "time_msc", 0)))[0]  # noqa: FURB192

        gross_profit = sum(_as_float(getattr(d, "profit", 0)) for d in deals)
        commission = sum(_as_float(getattr(d, "commission", 0)) for d in deals)
        swap = sum(_as_float(getattr(d, "swap", 0)) for d in deals)
        fee = sum(_as_float(getattr(d, "fee", 0)) for d in deals)
        net_profit = gross_profit + commission + swap + fee
        margin = _as_float(open_trade.get("margin_required") or open_trade.get("stake"), 0)
        profit_pct = (net_profit / margin * 100) if margin else 0.0
        entry_price = _as_float(getattr(entry_deal, "price", 0), open_trade.get("entry_spot"))
        exit_price = _as_float(getattr(exit_deal, "price", 0))
        reason = self._deal_reason_name(_as_int(getattr(exit_deal, "reason", -1), -1))

        update_broker_trade(
            position_id,
            entry_spot=entry_price,
            exit_spot=exit_price,
            buy_price=entry_price,
            sell_price=exit_price,
            profit=net_profit,
            net_profit=net_profit,
            profit_pct=profit_pct,
            commission=commission,
            swap=swap,
            fee=fee,
            status="closed",
            exit_reason=reason,
            closed_at=utc_now_iso(),
            raw_close_json=json.dumps(
                {"deals": [_namedtuple_dict(d) for d in deals]},
                ensure_ascii=False,
                default=str,
            ),
        )
        state = apply_closed_trade_to_state(self.mode, net_profit)
        cooldown_until = _as_int(state.get("last_candle_epoch")) + LIVE_COOLDOWN_CANDLES * self.granularity
        update_bot_state(self.mode, cooldown_until_epoch=cooldown_until)
        self.current_position_id = None
        logger.warning(
            "CFD CERRADO posición=%s | bruto=%+.2f | comisión=%+.2f | swap=%+.2f | fee=%+.2f | neto=%+.2f",
            position_id,
            gross_profit,
            commission,
            swap,
            fee,
            net_profit,
        )
        log_live_event(
            self.mode,
            "TRADE",
            "cfd_position_closed",
            f"Posición {position_id} cerrada con P&L neto {net_profit:+.2f}",
            {
                "gross_profit": gross_profit,
                "commission": commission,
                "swap": swap,
                "fee": fee,
                "net_profit": net_profit,
                "exit_reason": reason,
            },
        )
        self._risk_gate(_as_int(state.get("last_candle_epoch")))
        return True

    def _deal_reason_name(self, value):
        names = {
            _as_int(getattr(self.mt5, "DEAL_REASON_SL", -100)): "stop_loss",
            _as_int(getattr(self.mt5, "DEAL_REASON_TP", -101)): "take_profit",
            _as_int(getattr(self.mt5, "DEAL_REASON_EXPERT", -102)): "expert",
            _as_int(getattr(self.mt5, "DEAL_REASON_CLIENT", -103)): "client",
            _as_int(getattr(self.mt5, "DEAL_REASON_MOBILE", -104)): "mobile",
            _as_int(getattr(self.mt5, "DEAL_REASON_WEB", -105)): "web",
        }
        return names.get(value, f"deal_reason_{value}")
