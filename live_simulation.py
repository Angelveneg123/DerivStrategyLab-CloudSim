"""Simulación live sobre ticks reales de Deriv, sin comprar contratos.

Usa el mismo registro de estrategia que el backtester y el motor conectado.
Sirve como etapa intermedia: datos reales + ejecución ficticia + persistencia.

Ejemplos:
    python live_simulation.py
    python live_simulation.py "Crash 500 Index" 60 hybrid
    python live_simulation.py CRASH500 60 soporte_resistencia
"""

import json
import random
import sys
import time

from api.deriv import DerivClient
from config import (
    ATR_PERIOD,
    BACKTEST_COMMISSION_PER_TRADE_USD,
    BACKTEST_RANDOM_SEED,
    BACKTEST_SLIPPAGE_ATR_MAX,
    DERIV_API_BASE,
    DERIV_APP_ID,
    DERIV_PAT_TOKEN,
    INITIAL_BALANCE,
    LIVE_GRANULARITY,
    LIVE_STRATEGY,
    LIVE_SYMBOL,
    LIVE_WARMUP_CANDLES,
    MAX_DRAWDOWN_STOP_PCT,
    REWARD_RATIO,
    RIESGO_POR_OPERACION_PCT,
    SL_ATR_MULT,
    SPREAD_ATR_FRAC,
)
from database.database import (
    create_live_trades_table,
    create_tables,
    get_candles,
    save_candles,
    save_live_trade,
)
from indicators.atr import calculate_atr
from strategy.registry import generate_registered_signals, validate_strategy_id
from strategy.risk_manager import calculate_position_size, calculate_stop_take
from strategy.strategy_engine import BUY, SELL

SYMBOL_QUERY = sys.argv[1] if len(sys.argv) > 1 else LIVE_SYMBOL
GRANULARITY = int(sys.argv[2]) if len(sys.argv) > 2 else LIVE_GRANULARITY
STRATEGY_ID = sys.argv[3] if len(sys.argv) > 3 else LIVE_STRATEGY
MAX_CONTEXT = max(LIVE_WARMUP_CANDLES + 200, 4500)


def revisar_salida(direction, price, stop_price, take_profit_price):
    if direction == BUY:
        if price <= stop_price:
            return "stop_loss", stop_price
        if price >= take_profit_price:
            return "take_profit", take_profit_price
    elif direction == SELL:
        if price >= stop_price:
            return "stop_loss", stop_price
        if price <= take_profit_price:
            return "take_profit", take_profit_price
    return None


def calcular_profit(direction, entry_price, exit_price, units, entry_atr):
    gross = (
        (entry_price - exit_price) * units
        if direction == SELL
        else (exit_price - entry_price) * units
    )
    cost = entry_atr * SPREAD_ATR_FRAC * units
    return gross - cost - max(0.0, BACKTEST_COMMISSION_PER_TRADE_USD)


def cargar_contexto(client, symbol_code):
    rows = get_candles(symbol_code, GRANULARITY, limit=LIVE_WARMUP_CANDLES)
    if len(rows) < min(220, LIVE_WARMUP_CANDLES):
        candles = client.get_historical_candles(
            symbol_code,
            granularity=GRANULARITY,
            count=LIVE_WARMUP_CANDLES,
        )
        save_candles(symbol_code, GRANULARITY, candles)
        rows = get_candles(symbol_code, GRANULARITY, limit=LIVE_WARMUP_CANDLES)
    return (
        [row[0] for row in rows],
        [row[2] for row in rows],
        [row[3] for row in rows],
        [row[4] for row in rows],
    )


def main():
    validate_strategy_id(STRATEGY_ID)
    create_tables()
    create_live_trades_table()

    client = DerivClient(
        app_id=DERIV_APP_ID,
        access_token=DERIV_PAT_TOKEN,
        api_base=DERIV_API_BASE,
    )
    client.connect(account_type="demo")
    symbol_info = client.resolve_symbol(SYMBOL_QUERY)
    symbol_code = client.symbol_code(symbol_info)
    symbol_name = client.symbol_name(symbol_info)
    epochs, highs, lows, closes = cargar_contexto(client, symbol_code)

    required = 3200 if STRATEGY_ID == "hybrid" else 220
    if len(closes) < required:
        client.disconnect()
        raise RuntimeError(
            f"Histórico insuficiente: {len(closes)} velas; se requieren {required}."
        )

    print("=" * 68)
    print(" DERIV STRATEGY LAB — LIVE SIMULATION ")
    print("=" * 68)
    print(f"Activo: {symbol_name} ({symbol_code}) | {GRANULARITY}s")
    print(f"Estrategia: {STRATEGY_ID} | Balance ficticio: ${INITIAL_BALANCE:.2f}")
    print("No compra contratos. Usa ticks reales y guarda operaciones ficticias.\n")

    client.ws.send(json.dumps({"ticks": symbol_code, "subscribe": 1}))
    balance = INITIAL_BALANCE
    peak_balance = INITIAL_BALANCE
    circuit_breaker = False
    position = None
    current_candle = None
    rng = random.Random(BACKTEST_RANDOM_SEED)

    try:
        while True:
            message = json.loads(client.ws.recv())
            if message.get("error"):
                raise RuntimeError(message["error"].get("message", "Error de Deriv"))
            if message.get("msg_type") != "tick":
                continue

            tick = message["tick"]
            epoch = int(tick["epoch"])
            price = float(tick["quote"])
            bucket = epoch - (epoch % GRANULARITY)

            if position:
                exit_data = revisar_salida(
                    position["direction"],
                    price,
                    position["stop_price"],
                    position["take_price"],
                )
                if exit_data:
                    reason, exit_price_model = exit_data
                    exit_slippage = rng.uniform(
                        0.0, max(0.0, BACKTEST_SLIPPAGE_ATR_MAX) * position["entry_atr"]
                    )
                    exit_price = (
                        max(1e-12, exit_price_model - exit_slippage)
                        if position["direction"] == BUY
                        else exit_price_model + exit_slippage
                    )
                    before = balance
                    profit = calcular_profit(
                        position["direction"],
                        position["entry_price"],
                        exit_price,
                        position["position_size"],
                        position["entry_atr"],
                    )
                    balance += profit
                    peak_balance = max(peak_balance, balance)
                    profit_pct = profit / before * 100 if before else 0
                    save_live_trade(
                        symbol_code,
                        GRANULARITY,
                        position["entry_epoch"],
                        position["entry_price"],
                        epoch,
                        exit_price,
                        profit,
                        profit_pct,
                        direction=position["direction"],
                    )
                    print(
                        f"SALIDA {position['direction']} {reason} | "
                        f"P&L={profit:+.2f} | balance=${balance:.2f}"
                    )
                    position = None

            if current_candle is None:
                current_candle = {
                    "epoch": bucket,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                }
                continue

            if bucket == current_candle["epoch"]:
                current_candle["high"] = max(current_candle["high"], price)
                current_candle["low"] = min(current_candle["low"], price)
                current_candle["close"] = price
                continue

            closed = current_candle
            current_candle = {
                "epoch": bucket,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
            }
            if epochs and closed["epoch"] <= epochs[-1]:
                continue
            save_candles(symbol_code, GRANULARITY, [closed])
            epochs.append(closed["epoch"])
            highs.append(closed["high"])
            lows.append(closed["low"])
            closes.append(closed["close"])
            epochs = epochs[-MAX_CONTEXT:]
            highs = highs[-MAX_CONTEXT:]
            lows = lows[-MAX_CONTEXT:]
            closes = closes[-MAX_CONTEXT:]

            signals = generate_registered_signals(
                STRATEGY_ID,
                epochs,
                highs,
                lows,
                closes,
                source_granularity=GRANULARITY,
            )
            signal_value = signals[-1]
            atr = calculate_atr(highs, lows, closes, period=ATR_PERIOD)[-1]
            print(
                f"[{time.strftime('%H:%M:%S', time.localtime(closed['epoch']))}] "
                f"close={closed['close']:.5f} señal={signal_value}"
            )

            drawdown = (
                (peak_balance - balance) / peak_balance * 100
                if peak_balance > 0
                else 0
            )
            if drawdown >= MAX_DRAWDOWN_STOP_PCT:
                circuit_breaker = True

            if (
                signal_value in (BUY, SELL)
                and position is None
                and not circuit_breaker
                and atr is not None
                and atr > 0
            ):
                signal_entry = closed["close"]
                entry_slippage = rng.uniform(
                    0.0, max(0.0, BACKTEST_SLIPPAGE_ATR_MAX) * atr
                )
                entry = (
                    signal_entry + entry_slippage
                    if signal_value == BUY
                    else max(1e-12, signal_entry - entry_slippage)
                )
                stop, take = calculate_stop_take(
                    entry,
                    atr,
                    direction=signal_value,
                    stop_atr_mult=SL_ATR_MULT,
                    reward_ratio=REWARD_RATIO,
                )
                units = calculate_position_size(
                    balance,
                    RIESGO_POR_OPERACION_PCT,
                    entry,
                    stop,
                )
                if units > 0:
                    position = {
                        "direction": signal_value,
                        "signal_entry_price": signal_entry,
                        "entry_price": entry,
                        "entry_slippage": entry_slippage,
                        "entry_epoch": closed["epoch"],
                        "entry_atr": atr,
                        "stop_price": stop,
                        "take_price": take,
                        "position_size": units,
                    }
                    print(
                        f"ENTRADA FICTICIA {signal_value} señal={signal_entry:.5f} "
                        f"ejecutada={entry:.5f} SL={stop:.5f} TP={take:.5f}"
                    )
    except KeyboardInterrupt:
        print(f"\nSimulación detenida. Balance ficticio: ${balance:.2f}")
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
