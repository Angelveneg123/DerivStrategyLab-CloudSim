"""Motor de ejecución para cuenta DEMO/REAL de Deriv.

Por defecto solo DEMO pasa los candados. El motor:
- resuelve el símbolo por nombre o código mediante active_symbols;
- calienta indicadores con histórico real del broker;
- genera la misma señal registrada que usa el backtest;
- compra MULTUP/MULTDOWN con stop y take enviados al broker;
- persiste contrato, P&L, estado, eventos y heartbeat para el dashboard;
- conserva la asignación virtual de US$100 a través de reinicios.
"""

from __future__ import annotations

import json
import signal
import time
from datetime import datetime, timezone

from api.deriv import DerivAPIError, DerivClient, DerivConnectionError
from config import (
    ATR_PERIOD,
    DEMO_TRADING_ENABLED,
    DERIV_API_BASE,
    DERIV_APP_ID,
    DERIV_HEARTBEAT_SECONDS,
    DERIV_MAX_RECONNECT_ATTEMPTS,
    DERIV_DEMO_ACCOUNT_ID,
    DERIV_REAL_ACCOUNT_ID,
    DERIV_PAT_TOKEN,
    LIVE_ALLOCATED_CAPITAL_USD,
    LIVE_BLOCK_IF_OTHER_POSITIONS,
    LIVE_COOLDOWN_CANDLES,
    LIVE_CURRENCY,
    LIVE_DAILY_LOSS_LIMIT_PCT,
    LIVE_GRANULARITY,
    LIVE_MAX_CONSECUTIVE_LOSSES,
    LIVE_MAX_SIGNAL_AGE_SECONDS,
    LIVE_MAX_SLIPPAGE_PCT,
    LIVE_MAX_STAKE_PCT,
    LIVE_MAX_STAKE_USD,
    LIVE_MAX_TRADES_PER_DAY,
    LIVE_MIN_STAKE_USD,
    LIVE_MULTIPLIERS,
    LIVE_REINVEST_PROFITS,
    LIVE_STRATEGY,
    LIVE_SYMBOL,
    LIVE_WARMUP_CANDLES,
    MAX_DRAWDOWN_STOP_PCT,
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
from live_trading.risk import build_order_plans
from strategy.registry import STRATEGIES, generate_registered_signals, validate_strategy_id
from strategy.strategy_engine import BUY, HOLD, SELL
from utils.logging_config import configure_logging


logger = configure_logging("live_trading")


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


def parse_balance_response(response):
    payload = response.get("balance") or response.get("data") or response
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    balance = payload.get("balance", payload.get("amount", 0)) if isinstance(payload, dict) else 0
    currency = payload.get("currency") if isinstance(payload, dict) else None
    return _as_float(balance), currency


def parse_portfolio_contracts(response):
    payload = response.get("portfolio") or response.get("data") or {}
    if isinstance(payload, dict):
        contracts = payload.get("contracts") or payload.get("positions") or []
    elif isinstance(payload, list):
        contracts = payload
    else:
        contracts = []
    return contracts or []


class LiveTradingEngine:
    def __init__(
        self,
        *,
        mode="demo",
        symbol_query=LIVE_SYMBOL,
        granularity=LIVE_GRANULARITY,
        strategy_id=LIVE_STRATEGY,
        allocation_capital=LIVE_ALLOCATED_CAPITAL_USD,
        check_only=False,
    ):
        self.mode = validate_trading_mode(mode)
        self.symbol_query = symbol_query
        self.granularity = int(granularity)
        self.strategy_id = validate_strategy_id(strategy_id)
        self.allocation_capital = float(allocation_capital)
        self.check_only = check_only
        self.client = None
        self.symbol_code = None
        self.symbol_name = None
        self.account_id = None
        self.session_id = None
        self.stop_requested = False
        self.current_candle = None
        self.epochs = []
        self.opens = []
        self.highs = []
        self.lows = []
        self.closes = []
        self.max_context = max(LIVE_WARMUP_CANDLES + 200, 4500)
        self.current_contract_id = None
        self.last_ping_at = 0.0
        self.last_state_heartbeat_at = 0.0
        self.last_price_persist_at = 0.0
        self._session_started = False
        self._install_signal_handlers()
        create_live_runtime_tables()

    def _install_signal_handlers(self):
        def request_stop(_signum, _frame):
            self.stop_requested = True
            logger.warning("Se solicitó detener el motor; no se abrirán nuevas operaciones")

        try:
            signal.signal(signal.SIGINT, request_stop)
            signal.signal(signal.SIGTERM, request_stop)
        except ValueError:
            # Los handlers solo pueden instalarse desde el hilo principal.
            pass

    def _new_client(self):
        return DerivClient(
            app_id=DERIV_APP_ID,
            access_token=DERIV_PAT_TOKEN,
            api_base=DERIV_API_BASE,
            threaded=True,
            allow_real=(self.mode == "real"),
        )

    def check_connection(self):
        """Valida token, cuenta, saldo, símbolo y contratos sin comprar."""
        client = self._new_client()
        try:
            requested_account_id = (
                DERIV_DEMO_ACCOUNT_ID if self.mode == "demo" else DERIV_REAL_ACCOUNT_ID
            )
            account = client.connect(
                account_type=self.mode, account_id=requested_account_id
            )
            account_id = str(account.get("account_id"))
            symbol = client.resolve_symbol(self.symbol_query)
            symbol_code = client.symbol_code(symbol)
            symbol_name = client.symbol_name(symbol)
            balance_response = client.get_balance()
            balance, currency = parse_balance_response(balance_response)
            contracts = client.get_contracts_for(symbol_code)
            candles = client.get_historical_candles(
                symbol_code, granularity=self.granularity, count=300
            )
            if len(candles) < 30:
                raise RuntimeError("No se recibió histórico suficiente para validar la propuesta")
            highs = [_as_float(item["high"]) for item in candles]
            lows = [_as_float(item["low"]) for item in candles]
            closes = [_as_float(item["close"]) for item in candles]
            atr = calculate_atr(highs, lows, closes, period=ATR_PERIOD)[-1]
            allocation = min(self.allocation_capital, balance)
            proposal_validation = None
            proposal_errors = []
            for direction in (BUY, SELL):
                plans = build_order_plans(
                    direction=direction,
                    entry_price=closes[-1],
                    atr=atr,
                    virtual_balance=allocation,
                    allocation_capital=allocation,
                    reinvest_profits=False,
                    risk_pct=RIESGO_POR_OPERACION_PCT,
                    multipliers=LIVE_MULTIPLIERS,
                    min_stake=LIVE_MIN_STAKE_USD,
                    max_stake=LIVE_MAX_STAKE_USD,
                    max_stake_pct=LIVE_MAX_STAKE_PCT,
                    stop_atr_mult=SL_ATR_MULT,
                    reward_ratio=REWARD_RATIO,
                )
                for plan in plans:
                    try:
                        response = client.request_proposal(
                            symbol=symbol_code,
                            direction=direction,
                            amount=plan.stake,
                            currency=currency or LIVE_CURRENCY,
                            multiplier=plan.multiplier,
                            stop_loss=plan.stop_loss_amount,
                            take_profit=plan.take_profit_amount,
                        )
                        proposal = response.get("proposal") or {}
                        proposal_validation = {
                            "direction": direction,
                            "multiplier": plan.multiplier,
                            "stake": plan.stake,
                            "stop_loss": plan.stop_loss_amount,
                            "take_profit": plan.take_profit_amount,
                            "ask_price": _as_float(proposal.get("ask_price"), plan.stake),
                            "proposal_id_received": bool(proposal.get("id")),
                        }
                        break
                    except DerivAPIError as exc:
                        proposal_errors.append({
                            "direction": direction,
                            "multiplier": plan.multiplier,
                            "code": exc.code,
                            "message": str(exc),
                        })
                if proposal_validation:
                    break
            if not proposal_validation:
                raise RuntimeError(
                    "La cuenta y el símbolo responden, pero ninguna propuesta "
                    f"Multiplier fue aceptada: {proposal_errors}"
                )
            result = {
                "mode": self.mode,
                "account_id": account_id,
                "account_balance": balance,
                "currency": currency or LIVE_CURRENCY,
                "symbol_query": self.symbol_query,
                "symbol_code": symbol_code,
                "symbol_name": symbol_name,
                "strategy_id": self.strategy_id,
                "strategy_name": STRATEGIES[self.strategy_id],
                "execution_product": "multiplier",
                "allocation_capital": allocation,
                "contracts_response_received": bool(contracts),
                "proposal_validation": proposal_validation,
                "purchase_sent": False,
            }
            logger.info("Verificación DEMO completada: %s", result)
            return result
        finally:
            client.disconnect()

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
                    raise DerivConnectionError("La sesión terminó inesperadamente")
            except KeyboardInterrupt:
                self.stop_requested = True
            except Exception as exc:
                last_error = str(exc)
                logger.exception("Fallo del motor: %s", exc)
                log_live_event(
                    self.mode,
                    "ERROR",
                    "engine_error",
                    str(exc),
                    {"type": type(exc).__name__},
                )
                reconnect_attempt += 1
                update_bot_state(
                    self.mode,
                    status="reconnecting",
                    last_error=last_error,
                    reconnect_attempts=reconnect_attempt,
                )
                if reconnect_attempt > DERIV_MAX_RECONNECT_ATTEMPTS:
                    logger.error("Se agotó el máximo de reconexiones")
                    break
                delay = min(60, 2 ** min(reconnect_attempt, 6))
                end = time.monotonic() + delay
                while time.monotonic() < end and not self.stop_requested:
                    time.sleep(0.25)
            finally:
                if self.client:
                    self.client.disconnect()
                    self.client = None

        status = "stopped" if self.stop_requested else "error"
        stop_bot_session(self.mode, status=status, error=last_error if status == "error" else None)
        logger.info("Motor detenido. Los stops/takes de cualquier contrato abierto quedan en Deriv.")

    def _connect_and_run(self):
        self.client = self._new_client()
        requested_account_id = (
            DERIV_DEMO_ACCOUNT_ID if self.mode == "demo" else DERIV_REAL_ACCOUNT_ID
        )
        account = self.client.connect(
            account_type=self.mode, account_id=requested_account_id
        )
        self.account_id = str(account.get("account_id"))

        symbol = self.client.resolve_symbol(self.symbol_query)
        self.symbol_code = self.client.symbol_code(symbol)
        self.symbol_name = self.client.symbol_name(symbol)
        if not self.symbol_code:
            raise RuntimeError("active_symbols no devolvió código para el activo")

        balance_response = self.client.get_balance()
        account_balance, account_currency = parse_balance_response(balance_response)
        currency = account_currency or LIVE_CURRENCY
        if account_balance <= 0:
            raise RuntimeError("La cuenta no tiene saldo disponible")
        if currency.upper() != LIVE_CURRENCY.upper():
            raise RuntimeError(
                f"La cuenta usa {currency}, pero LIVE_CURRENCY={LIVE_CURRENCY}. "
                "Configura la moneda correcta antes de operar."
            )

        state = get_bot_state(self.mode)
        if not state or not self._session_started:
            state = start_bot_session(
                self.mode,
                account_id=self.account_id,
                execution_product="multiplier",
                symbol_query=self.symbol_query,
                symbol_code=self.symbol_code,
                symbol_name=self.symbol_name,
                strategy_id=self.strategy_id,
                granularity=self.granularity,
                currency=currency,
                allocation_capital=min(self.allocation_capital, account_balance),
                account_balance=account_balance,
            )
            self._session_started = True
            self.session_id = state["session_id"]
        else:
            self.session_id = state.get("session_id")
            reconnect_status = "paused" if state.get("circuit_breaker") else "running"
            update_bot_state(
                self.mode,
                status=reconnect_status,
                account_balance=account_balance,
                last_error=None if not state.get("circuit_breaker") else state.get("last_error"),
                reconnect_attempts=0,
            )

        if float(state["allocation_capital"]) > account_balance:
            raise RuntimeError(
                "El saldo de la cuenta es menor que la asignación virtual persistida. "
                "Reinicia la asignación a un valor permitido."
            )

        logger.info(
            "Conectado %s | %s (%s) | asignación=$%.2f | saldo cuenta=$%.2f",
            self.mode.upper(),
            self.symbol_name,
            self.symbol_code,
            float(state["allocation_capital"]),
            account_balance,
        )
        log_live_event(
            self.mode,
            "INFO",
            "connected",
            f"Conectado a {self.symbol_name} ({self.symbol_code})",
            {"account_id": self.account_id, "account_balance": account_balance},
        )

        self._load_warmup()
        self._recover_or_validate_portfolio()
        initial_tick = self.client.subscribe_ticks(self.symbol_code)
        if initial_tick.get("tick"):
            self._handle_tick(initial_tick["tick"])

        self.last_ping_at = time.monotonic()
        while not self.stop_requested:
            event = self.client.recv_event(timeout=1.0)
            now = time.monotonic()
            if now - self.last_state_heartbeat_at >= 5:
                heartbeat_state = get_bot_state(self.mode) or {}
                heartbeat_status = (
                    "paused" if heartbeat_state.get("circuit_breaker") else "running"
                )
                update_bot_state(self.mode, status=heartbeat_status)
                self.last_state_heartbeat_at = now

            if event:
                self._handle_event(event)
            if now - self.last_ping_at >= DERIV_HEARTBEAT_SECONDS:
                self.client.ping()
                self.last_ping_at = now

    def _load_warmup(self):
        candles = self.client.get_historical_candles(
            self.symbol_code,
            granularity=self.granularity,
            count=LIVE_WARMUP_CANDLES,
        )
        now_epoch = int(time.time())
        completed = [
            candle
            for candle in candles
            if int(candle["epoch"]) + self.granularity <= now_epoch
        ]
        if len(completed) < 3200 and self.strategy_id == "hybrid":
            raise RuntimeError(
                f"Solo se recibieron {len(completed)} velas cerradas. "
                "La estrategia híbrida necesita aproximadamente 3200 M1 para EMA200 en M15."
            )
        if len(completed) < 220:
            raise RuntimeError("Histórico insuficiente para calcular indicadores")

        save_candles(self.symbol_code, self.granularity, completed)
        self.epochs = [int(c["epoch"]) for c in completed][-self.max_context :]
        self.highs = [_as_float(c["high"]) for c in completed][-self.max_context :]
        self.lows = [_as_float(c["low"]) for c in completed][-self.max_context :]
        self.closes = [_as_float(c["close"]) for c in completed][-self.max_context :]
        logger.info("Contexto cargado: %d velas cerradas", len(self.closes))

    def _recover_or_validate_portfolio(self):
        """Reconcilia SQLite con Deriv antes de aceptar nuevas señales.

        La compra es un efecto remoto y no puede incluirse en la misma transacción
        SQLite. Por eso se persiste una intención antes de llamar ``buy``. Si el
        proceso cae durante esa ventana, este método vincula la intención con el
        único contrato abierto de la cuenta o bloquea el bot ante ambigüedad.
        """
        portfolio_response = self.client.get_portfolio()
        contracts = parse_portfolio_contracts(portfolio_response)
        contracts_by_id = {
            _as_int(item.get("contract_id")): item
            for item in contracts
            if _as_int(item.get("contract_id"))
        }
        portfolio_ids = set(contracts_by_id)

        state = get_bot_state(self.mode) or {}
        open_db = get_open_broker_trade(self.mode)
        pending = get_latest_pending_trade(self.mode)
        open_id = _as_int(open_db.get("contract_id")) if open_db else 0
        state_contract_id = _as_int(state.get("current_contract_id"))

        # El estado persistente puede haberse actualizado justo antes de que la
        # fila de operación cambiara a ``open``. Recuperamos esa relación.
        if not open_id and state_contract_id:
            recovered = get_broker_trade_by_contract(state_contract_id)
            if recovered:
                update_broker_trade_by_id(recovered["id"], status="open")
                open_db = get_broker_trade_by_contract(state_contract_id)
                open_id = state_contract_id

        if pending and not open_id:
            pending_status = str(pending.get("status") or "")
            if len(portfolio_ids) == 1:
                recovered_id = next(iter(portfolio_ids))
                broker_contract = contracts_by_id.get(recovered_id) or {}
                update_broker_trade_by_id(
                    pending["id"],
                    contract_id=recovered_id,
                    transaction_id=str(
                        broker_contract.get("transaction_id")
                        or broker_contract.get("buy_transaction_id")
                        or pending.get("transaction_id")
                        or ""
                    ),
                    buy_price=_as_float(
                        broker_contract.get("buy_price"), pending.get("buy_price")
                    ),
                    entry_spot=_as_float(
                        broker_contract.get("entry_spot"), pending.get("entry_spot")
                    ),
                    status="open",
                    raw_open_json=json.dumps(
                        {
                            "recovered_from_portfolio": broker_contract,
                            "previous": pending.get("raw_open_json"),
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                )
                recovery_state = {
                    "current_contract_id": recovered_id,
                    "current_direction": pending.get("direction"),
                    "current_unrealized_pnl": 0.0,
                }
                breaker_reason = str(state.get("circuit_breaker_reason") or "").lower()
                if pending_status in {"buy_submitted", "purchase_unknown"} and (
                    "compra" in breaker_reason or "buy" in breaker_reason
                ):
                    recovery_state.update(
                        circuit_breaker=0,
                        circuit_breaker_reason=None,
                        last_error=None,
                        status="running",
                    )
                update_bot_state(self.mode, **recovery_state)
                open_db = get_broker_trade_by_contract(recovered_id)
                open_id = recovered_id
                log_live_event(
                    self.mode,
                    "WARNING",
                    "purchase_recovered",
                    f"Compra pendiente vinculada al contrato {recovered_id}",
                    {"previous_status": pending_status},
                )
            elif not portfolio_ids and pending_status == "proposal_ready":
                # Todavía no se había invocado buy; la propuesta puede
                # abandonarse sin riesgo de haber abierto una posición.
                update_broker_trade_by_id(
                    pending["id"],
                    status="failed",
                    exit_reason="interrupted_before_buy",
                    closed_at=utc_now_iso(),
                )
                pending = None
            elif not portfolio_ids:
                reason = (
                    "Existe una compra con resultado incierto y Deriv no muestra "
                    "un contrato abierto. Revisa el historial de transacciones "
                    "de la cuenta antes de reanudar."
                )
                update_bot_state(
                    self.mode,
                    circuit_breaker=1,
                    circuit_breaker_reason=reason,
                    last_error=reason,
                )
                raise RuntimeError(reason)
            else:
                reason = (
                    "No se puede reconciliar la compra pendiente porque hay "
                    f"varios contratos abiertos: {sorted(portfolio_ids)}"
                )
                update_bot_state(
                    self.mode,
                    circuit_breaker=1,
                    circuit_breaker_reason=reason,
                    last_error=reason,
                )
                raise RuntimeError(reason)

        if open_id:
            self.current_contract_id = open_id
            initial = self.client.subscribe_open_contract(open_id)
            self._handle_contract_update(initial.get("proposal_open_contract", {}))
            logger.warning("Contrato %s recuperado después del reinicio", open_id)

        foreign_ids = {
            contract_id
            for contract_id in portfolio_ids
            if contract_id and contract_id != open_id
        }
        if foreign_ids and LIVE_BLOCK_IF_OTHER_POSITIONS:
            raise RuntimeError(
                "La cuenta tiene contratos abiertos ajenos a este bot: "
                f"{sorted(foreign_ids)}. Ciérralos o desactiva explícitamente "
                "LIVE_BLOCK_IF_OTHER_POSITIONS."
            )

        if open_id and portfolio_ids and open_id not in portfolio_ids:
            # proposal_open_contract habrá confirmado si ya cerró. Si todavía
            # figura abierto en DB, no se permite duplicar posición.
            current = get_open_broker_trade(self.mode)
            if current:
                raise RuntimeError(
                    f"El contrato {open_id} figura abierto en SQLite pero no en portfolio. "
                    "Revisa el dashboard/eventos antes de continuar."
                )

    def _handle_event(self, event):
        msg_type = event.get("msg_type")
        if msg_type == "connection_error":
            message = (event.get("error") or {}).get("message", "Conexión perdida")
            raise DerivConnectionError(message)
        if event.get("error"):
            error = event["error"]
            raise DerivAPIError(error.get("message", "Error de Deriv"), error.get("code"), error)
        if msg_type == "tick" and event.get("tick"):
            self._handle_tick(event["tick"])
        elif msg_type == "proposal_open_contract":
            self._handle_contract_update(event.get("proposal_open_contract", {}))
        elif msg_type == "balance":
            balance, _ = parse_balance_response(event)
            update_bot_state(self.mode, account_balance=balance)

    def _handle_tick(self, tick):
        epoch = _as_int(tick.get("epoch"))
        price = _as_float(tick.get("quote"))
        if epoch <= 0 or price <= 0:
            return
        now = time.monotonic()
        if now - self.last_price_persist_at >= 1:
            update_bot_state(self.mode, last_price=price)
            self.last_price_persist_at = now
        bucket = epoch - (epoch % self.granularity)

        if self.current_candle is None:
            self.current_candle = {
                "epoch": bucket,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
            }
            return

        if bucket == self.current_candle["epoch"]:
            self.current_candle["high"] = max(self.current_candle["high"], price)
            self.current_candle["low"] = min(self.current_candle["low"], price)
            self.current_candle["close"] = price
            return

        # Si hubo un salto de varios buckets, cerramos la última vela conocida;
        # no inventamos velas sin ticks.
        closed = self.current_candle
        self.current_candle = {
            "epoch": bucket,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
        }
        self._on_candle_closed(closed)

    def _on_candle_closed(self, candle):
        epoch = int(candle["epoch"])
        if self.epochs and epoch <= self.epochs[-1]:
            return
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
        update_bot_state(
            self.mode,
            last_candle_epoch=epoch,
            last_signal=signal_value,
            last_signal_epoch=epoch,
        )
        logger.info(
            "Vela cerrada %s | close=%.5f | señal=%s",
            datetime.fromtimestamp(epoch, timezone.utc).isoformat(),
            candle["close"],
            signal_value,
        )
        if signal_value in (BUY, SELL):
            allowed, reason = self._risk_gate(epoch)
            if allowed:
                self._execute_signal(signal_value, epoch, float(candle["close"]))
            else:
                log_live_event(
                    self.mode,
                    "INFO",
                    "signal_blocked",
                    f"Señal {signal_value} bloqueada: {reason}",
                    {"signal_epoch": epoch},
                )

    def _risk_gate(self, signal_epoch):
        state = roll_bot_day_if_needed(self.mode)
        if self.current_contract_id or state.get("current_contract_id") or get_open_broker_trade(self.mode):
            return False, "ya existe un contrato abierto"
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

        peak = float(state["peak_balance"] or state["allocation_capital"])
        virtual = float(state["virtual_balance"])
        drawdown = ((peak - virtual) / peak * 100) if peak > 0 else 0
        if drawdown >= MAX_DRAWDOWN_STOP_PCT:
            self._activate_breaker("drawdown máximo alcanzado")
            return False, "drawdown máximo"

        cooldown_until = _as_int(state.get("cooldown_until_epoch"))
        if cooldown_until and signal_epoch < cooldown_until:
            return False, f"cooldown hasta epoch {cooldown_until}"

        signal_close_epoch = signal_epoch + self.granularity
        age = int(time.time()) - signal_close_epoch
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
        log_live_event(self.mode, "WARNING", "circuit_breaker", reason)
        logger.warning("CIRCUIT BREAKER: %s", reason)

    def _execute_signal(self, direction, signal_epoch, entry_price):
        state = roll_bot_day_if_needed(self.mode)
        balance_response = self.client.get_balance()
        broker_balance, broker_currency = parse_balance_response(balance_response)
        if broker_currency and broker_currency.upper() != LIVE_CURRENCY.upper():
            raise RuntimeError(
                f"La moneda de la cuenta cambió a {broker_currency}; se esperaba {LIVE_CURRENCY}"
            )
        update_bot_state(self.mode, account_balance=broker_balance)
        if broker_balance < LIVE_MIN_STAKE_USD:
            self._activate_breaker("saldo disponible de la cuenta insuficiente")
            return
        risk_virtual_balance = min(float(state["virtual_balance"]), broker_balance)
        atr_values = calculate_atr(self.highs, self.lows, self.closes, period=ATR_PERIOD)
        atr = atr_values[-1] if atr_values else None
        if atr is None or atr <= 0:
            return

        plans = build_order_plans(
            direction=direction,
            entry_price=entry_price,
            atr=atr,
            virtual_balance=risk_virtual_balance,
            allocation_capital=min(float(state["allocation_capital"]), broker_balance),
            reinvest_profits=LIVE_REINVEST_PROFITS,
            risk_pct=RIESGO_POR_OPERACION_PCT,
            multipliers=LIVE_MULTIPLIERS,
            min_stake=LIVE_MIN_STAKE_USD,
            max_stake=LIVE_MAX_STAKE_USD,
            max_stake_pct=LIVE_MAX_STAKE_PCT,
            stop_atr_mult=SL_ATR_MULT,
            reward_ratio=REWARD_RATIO,
        )
        if not plans:
            log_live_event(
                self.mode,
                "WARNING",
                "risk_plan_rejected",
                "No existe un plan que respete stake mínimo y riesgo máximo",
                {"entry_price": entry_price, "atr": atr, "direction": direction},
            )
            return

        proposal_response = None
        chosen = None
        proposal_errors = []
        for plan in plans:
            try:
                proposal_response = self.client.request_proposal(
                    symbol=self.symbol_code,
                    direction=direction,
                    amount=plan.stake,
                    currency=LIVE_CURRENCY,
                    multiplier=plan.multiplier,
                    stop_loss=plan.stop_loss_amount,
                    take_profit=plan.take_profit_amount,
                )
                chosen = plan
                break
            except DerivAPIError as exc:
                proposal_errors.append(
                    {"multiplier": plan.multiplier, "code": exc.code, "message": str(exc)}
                )
                logger.warning("Propuesta rechazada x%s: %s", plan.multiplier, exc)

        if not chosen or not proposal_response:
            log_live_event(
                self.mode,
                "ERROR",
                "proposal_failed",
                "Deriv rechazó todos los multiplicadores permitidos",
                proposal_errors,
            )
            return

        proposal = proposal_response.get("proposal") or {}
        proposal_id = proposal.get("id")
        ask_price = _as_float(proposal.get("ask_price"), chosen.stake)
        if not proposal_id or ask_price <= 0:
            raise RuntimeError("La propuesta no contiene id/ask_price válidos")
        max_price = ask_price * (1 + LIVE_MAX_SLIPPAGE_PCT / 100)
        if max_price > risk_virtual_balance or max_price > broker_balance:
            raise RuntimeError("El precio máximo de compra excede el saldo permitido")

        # Persistimos la intención ANTES del efecto remoto. Esto permite
        # reconciliar una compra si la aplicación o la red fallan durante ``buy``.
        trade = {
            "session_id": self.session_id,
            "mode": self.mode,
            "execution_product": "multiplier",
            "account_id": self.account_id,
            "symbol_code": self.symbol_code,
            "symbol_name": self.symbol_name,
            "granularity": self.granularity,
            "strategy_id": self.strategy_id,
            "direction": direction,
            "contract_type": "MULTUP" if direction == BUY else "MULTDOWN",
            "signal_epoch": signal_epoch,
            "proposal_id": str(proposal_id),
            "contract_id": None,
            "transaction_id": None,
            "stake": chosen.stake,
            "multiplier": chosen.multiplier,
            "stop_loss_amount": chosen.stop_loss_amount,
            "take_profit_amount": chosen.take_profit_amount,
            "entry_price_signal": entry_price,
            "entry_spot": _as_float(proposal.get("spot"), entry_price),
            "buy_price": ask_price,
            "status": "proposal_ready",
            "opened_at": utc_now_iso(),
            "raw_open_json": json.dumps(
                {"proposal": proposal_response, "plan": chosen.to_dict()},
                ensure_ascii=False,
                default=str,
            ),
        }
        trade_id = save_broker_trade_open(trade)
        update_broker_trade_by_id(trade_id, status="buy_submitted")

        try:
            buy_response = self.client.buy_contract(proposal_id, max_price)
        except DerivAPIError as exc:
            # Un rechazo explícito de Deriv confirma que no hubo compra.
            update_broker_trade_by_id(
                trade_id,
                status="failed",
                exit_reason=f"buy_rejected:{exc.code or 'unknown'}:{exc}",
                closed_at=utc_now_iso(),
            )
            log_live_event(
                self.mode,
                "ERROR",
                "buy_rejected",
                str(exc),
                {"proposal_id": str(proposal_id), "code": exc.code},
            )
            return
        except Exception as exc:
            # Una desconexión puede ocurrir después de que Deriv haya aceptado la
            # compra. Se marca como incierta y se bloquea hasta reconciliar.
            update_broker_trade_by_id(
                trade_id,
                status="purchase_unknown",
                exit_reason=f"buy_result_unknown:{exc}",
            )
            reason = (
                "Resultado de compra incierto por pérdida de conexión. "
                "El bot se bloqueará y reconciliará el portfolio al reiniciar."
            )
            update_bot_state(
                self.mode,
                circuit_breaker=1,
                circuit_breaker_reason=reason,
                last_error=str(exc),
            )
            log_live_event(
                self.mode,
                "CRITICAL",
                "buy_result_unknown",
                reason,
                {"proposal_id": str(proposal_id), "error": str(exc)},
            )
            raise

        buy = buy_response.get("buy") or {}
        contract_id = _as_int(buy.get("contract_id"))
        if not contract_id:
            update_broker_trade_by_id(
                trade_id,
                status="purchase_unknown",
                exit_reason="buy_response_without_contract_id",
                raw_open_json=json.dumps(
                    {
                        "proposal": proposal_response,
                        "buy": buy_response,
                        "plan": chosen.to_dict(),
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            )
            reason = "Deriv respondió a buy sin contract_id; se requiere reconciliación"
            update_bot_state(
                self.mode,
                circuit_breaker=1,
                circuit_breaker_reason=reason,
                last_error=reason,
            )
            raise RuntimeError(reason)

        buy_price = _as_float(buy.get("buy_price"), ask_price)
        entry_spot = _as_float(
            buy.get("start_spot", buy.get("entry_spot", proposal.get("spot"))),
            entry_price,
        )

        # Guardamos primero el identificador en el estado y luego completamos la
        # fila. Cualquiera de las dos fuentes permite recuperar tras un reinicio.
        self.current_contract_id = contract_id
        update_bot_state(
            self.mode,
            current_contract_id=contract_id,
            current_direction=direction,
            current_unrealized_pnl=0.0,
        )
        update_broker_trade_by_id(
            trade_id,
            contract_id=contract_id,
            transaction_id=str(buy.get("transaction_id") or ""),
            entry_spot=entry_spot,
            buy_price=buy_price,
            status="open",
            raw_open_json=json.dumps(
                {"proposal": proposal_response, "buy": buy_response, "plan": chosen.to_dict()},
                ensure_ascii=False,
                default=str,
            ),
        )
        logger.warning(
            "OPERACIÓN DEMO ABIERTA %s | contrato=%s | stake=$%.2f | x%s | SL=$%.2f | TP=$%.2f",
            direction,
            contract_id,
            chosen.stake,
            chosen.multiplier,
            chosen.stop_loss_amount,
            chosen.take_profit_amount,
        )
        log_live_event(
            self.mode,
            "TRADE",
            "trade_opened",
            f"{direction} {self.symbol_code} contrato {contract_id}",
            chosen.to_dict(),
        )

        initial = self.client.subscribe_open_contract(contract_id)
        self._handle_contract_update(initial.get("proposal_open_contract", {}))

    def _handle_contract_update(self, contract):
        if not contract:
            return
        contract_id = _as_int(contract.get("contract_id"))
        if not contract_id:
            return
        if self.current_contract_id and contract_id != self.current_contract_id:
            return

        profit = _as_float(contract.get("profit"))
        is_sold = bool(contract.get("is_sold")) or str(contract.get("status", "")).lower() in {
            "sold",
            "closed",
            "won",
            "lost",
        }
        update_bot_state(
            self.mode,
            current_contract_id=contract_id,
            current_unrealized_pnl=profit,
        )
        if not is_sold:
            return

        open_trade = get_open_broker_trade(self.mode)
        if not open_trade or _as_int(open_trade.get("contract_id")) != contract_id:
            logger.warning("Cierre recibido para contrato no encontrado en DB: %s", contract_id)
            self.current_contract_id = None
            update_bot_state(
                self.mode,
                current_contract_id=None,
                current_direction=None,
                current_unrealized_pnl=0.0,
            )
            return

        buy_price = _as_float(contract.get("buy_price"), open_trade.get("buy_price"))
        sell_price = _as_float(contract.get("sell_price", contract.get("bid_price")))
        profit_pct = (profit / buy_price * 100) if buy_price else 0.0
        status = str(contract.get("status") or "closed")
        exit_reason = (
            contract.get("sell_type")
            or contract.get("exit_reason")
            or status
        )
        update_broker_trade(
            contract_id,
            entry_spot=_as_float(contract.get("entry_spot"), open_trade.get("entry_spot")),
            exit_spot=_as_float(
                contract.get("exit_spot", contract.get("current_spot")),
                0,
            ),
            buy_price=buy_price,
            sell_price=sell_price,
            profit=profit,
            profit_pct=profit_pct,
            status="closed",
            exit_reason=str(exit_reason),
            closed_at=utc_now_iso(),
            raw_close_json=json.dumps(contract, ensure_ascii=False, default=str),
        )
        state = apply_closed_trade_to_state(self.mode, profit)
        cooldown_until = _as_int(state.get("last_candle_epoch")) + (
            LIVE_COOLDOWN_CANDLES * self.granularity
        )
        update_bot_state(self.mode, cooldown_until_epoch=cooldown_until)
        self.current_contract_id = None

        logger.warning(
            "OPERACIÓN CERRADA contrato=%s | pnl=%+.2f | balance virtual=$%.2f | motivo=%s",
            contract_id,
            profit,
            float(state["virtual_balance"]),
            exit_reason,
        )
        log_live_event(
            self.mode,
            "TRADE",
            "trade_closed",
            f"Contrato {contract_id} cerrado con P&L {profit:+.2f}",
            {"profit": profit, "profit_pct": profit_pct, "exit_reason": exit_reason},
        )
        # Evalúa protecciones inmediatamente después del cierre.
        self._risk_gate(_as_int(state.get("last_candle_epoch")))
