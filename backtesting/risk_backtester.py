"""
Motor de riesgo bidireccional — el "núcleo" del proyecto.

A diferencia de backtester.py (que cierra cada operación solo cuando
aparece la señal contraria), este simula stop-loss y take-profit vela a
vela: una vez que entras, cada vela nueva se revisa contra esos dos
niveles hasta que uno se activa. El tamaño de cada operación se calcula
para arriesgar siempre el mismo % del capital, sin importar qué tan
lejos esté el stop.

CORRECCIÓN IMPORTANTE (este archivo era el problema principal):
Antes, este motor solo sabía abrir operaciones de COMPRA (BUY). Una
señal SELL nunca abría nada — se ignoraba en silencio — y el cálculo de
stop/take/beneficio asumía siempre que ganar significaba "el precio
subió". Eso hacía que la mitad de las señales que genera
hybrid_strategy.py (las de venta) nunca se probaran en el backtest.

Ahora el motor es simétrico para ambos lados:

  BUY  → stop por DEBAJO de la entrada, take por ENCIMA.
         Se sale por stop si `low <= stop_price`.
         Se sale por take si `high >= take_price`.
         Beneficio = (precio_salida - precio_entrada) * tamaño.

  SELL → stop por ENCIMA de la entrada, take por DEBAJO.
         Se sale por stop si `high >= stop_price`.
         Se sale por take si `low <= take_price`.
         Beneficio = (precio_entrada - precio_salida) * tamaño.

La gestión de riesgo (stop-loss, take-profit, sizing por % de riesgo,
costo de spread) es IDÉNTICA para ambas direcciones — solo cambia el
signo con el que se mide la distancia y el beneficio.

Este es el motor pensado para reutilizarse tal cual en:
  - Backtesting (Módulo 6, este archivo)
  - Simulación en vivo (Módulo 7)
  - Cuenta demo (Módulo 8)
  - Cuenta real (Módulo 9)
Por eso la función central (`_procesar_vela`) no sabe nada de dónde
vienen los datos — solo recibe precios y señales, vela a vela, igual
que lo hará el feed en vivo más adelante.

No reemplaza a backtester.py — es una estrategia de salida distinta,
para comparar cuál gestiona mejor el riesgo real.
"""

import random

from strategy.strategy_engine import BUY, SELL
from strategy.risk_manager import calculate_stop_take, calculate_position_size
from backtesting.backtester import _calculate_metrics


def run_backtest_risk(
    epochs,
    highs,
    lows,
    closes,
    signals,
    atr_values,
    initial_balance=1000,
    risk_pct=1.0,
    stop_atr_mult=1.5,
    reward_ratio=2.0,
    spread_atr_frac=0.05,
    max_drawdown_stop_pct=None,
    slippage_atr_max=0.0,
    random_seed=20260806,
    commission_per_trade=0.0,
):
    """
    epochs, highs, lows, closes, signals, atr_values: listas del mismo
        largo (salen de get_candles, generate_signals y calculate_atr).
    initial_balance: capital ficticio inicial.
    risk_pct: % del capital arriesgado en cada operación.
    stop_atr_mult: distancia del stop-loss, en múltiplos de ATR.
    reward_ratio: relación riesgo:beneficio del take-profit.
    spread_atr_frac: costo del spread, como fracción del ATR del momento
        (no como % del precio total — con precios altos como los de
        R_75, usar % del precio inflaría el costo de forma irreal cuando
        el stop está cerca. 0.05 = el spread cuesta el 5% del ATR actual,
        una estimación conservadora; ajústala si consigues el spread
        real de Deriv para tu símbolo).
    max_drawdown_stop_pct: si se define, deja de abrir operaciones nuevas
        cuando el drawdown de operaciones cerradas alcanza ese porcentaje.

    Devuelve un dict con `trades` (incluye "direction" — BUY o SELL — y
    el "R múltiplo" de cada operación, cuántas veces el riesgo original
    se ganó o perdió) y `metrics`.
    """
    balance = initial_balance
    pico_balance = initial_balance
    rng = random.Random(random_seed)
    trades = []
    circuit_breaker_activado = False

    posicion = None  # dict con el estado de la operación abierta, o None
    n = len(closes)

    for i in range(n):
        # --- Revisar si la operación abierta toca stop o take ----------
        if posicion is not None and i > posicion["entry_index"]:
            salida = _revisar_salida(posicion, highs[i], lows[i])

            if salida is not None:
                exit_price_modelo, resultado_tipo = salida

                # Slippage adverso reproducible. Para BUY empeora la salida
                # hacia abajo; para SELL la empeora hacia arriba.
                exit_slippage = rng.uniform(
                    0.0, max(0.0, float(slippage_atr_max)) * posicion["entry_atr"]
                )
                if posicion["direction"] == BUY:
                    exit_price = max(1e-12, exit_price_modelo - exit_slippage)
                else:
                    exit_price = exit_price_modelo + exit_slippage

                # El spread histórico exacto no existe en los OHLC guardados;
                # se conserva la estimación ATR. En vivo MT5 usa bid/ask real.
                costo_puntos = posicion["entry_atr"] * spread_atr_frac
                spread_cost = costo_puntos * posicion["position_size"]
                commission_cost = max(0.0, float(commission_per_trade))

                profit_bruto = _calcular_profit_bruto(
                    posicion["direction"],
                    posicion["entry_price"],
                    exit_price,
                    posicion["position_size"],
                )
                profit = profit_bruto - spread_cost - commission_cost

                balance += profit
                pico_balance = max(pico_balance, balance)
                drawdown_actual_pct = (
                    (pico_balance - balance) / pico_balance * 100
                    if pico_balance > 0
                    else 0
                )
                if (
                    max_drawdown_stop_pct is not None
                    and drawdown_actual_pct >= max_drawdown_stop_pct
                ):
                    circuit_breaker_activado = True

                dinero_en_riesgo = posicion["dinero_en_riesgo"]
                r_multiple = profit / dinero_en_riesgo if dinero_en_riesgo else 0

                trades.append(
                    {
                        "direction": posicion["direction"],
                        "entry_epoch": posicion["entry_epoch"],
                        "signal_entry_price": posicion["signal_entry_price"],
                        "entry_price": posicion["entry_price"],
                        "entry_slippage": posicion["entry_slippage"],
                        "exit_epoch": epochs[i],
                        "exit_price_modelo": exit_price_modelo,
                        "exit_price": exit_price,
                        "exit_slippage": exit_slippage,
                        "gross_profit": profit_bruto,
                        "spread_cost": spread_cost,
                        "commission_cost": commission_cost,
                        "profit": profit,
                        "profit_pct": (profit / (balance - profit)) * 100,
                        "r_multiple": r_multiple,
                        "resultado_tipo": resultado_tipo,
                        # Alias legible que espera templates/index.html
                        # (la misma clave que usa backtester.py, para que
                        # ambos motores puedan alimentar la misma plantilla).
                        "motivo_salida": "Take Profit" if resultado_tipo == "TAKE" else "Stop Loss",
                    }
                )

                posicion = None

        # --- Buscar una nueva entrada (solo si no hay posición abierta) --
        if (
            posicion is None
            and not circuit_breaker_activado
            and signals[i] in (BUY, SELL)
            and atr_values[i] is not None
            and atr_values[i] > 0
        ):
            direction = signals[i]
            signal_entry_price = closes[i]
            entry_atr = atr_values[i]
            entry_slippage = rng.uniform(
                0.0, max(0.0, float(slippage_atr_max)) * entry_atr
            )
            entry_price = (
                signal_entry_price + entry_slippage
                if direction == BUY
                else max(1e-12, signal_entry_price - entry_slippage)
            )

            stop_price, take_price = calculate_stop_take(
                entry_price,
                entry_atr,
                direction=direction,
                stop_atr_mult=stop_atr_mult,
                reward_ratio=reward_ratio,
            )
            position_size = calculate_position_size(
                balance, risk_pct, entry_price, stop_price
            )

            if position_size > 0:
                posicion = {
                    "direction": direction,
                    "signal_entry_price": signal_entry_price,
                    "entry_price": entry_price,
                    "entry_slippage": entry_slippage,
                    "entry_atr": entry_atr,
                    "stop_price": stop_price,
                    "take_price": take_price,
                    "position_size": position_size,
                    "dinero_en_riesgo": balance * (risk_pct / 100),
                    "entry_epoch": epochs[i],
                    "entry_index": i,
                }

    metrics = _calculate_metrics(trades, initial_balance)
    metrics["circuit_breaker_activado"] = circuit_breaker_activado

    # Métricas extra, propias de este enfoque con stop/take.
    if trades:
        stops = sum(1 for t in trades if t["resultado_tipo"] == "STOP")
        takes = sum(1 for t in trades if t["resultado_tipo"] == "TAKE")
        r_promedio = sum(t["r_multiple"] for t in trades) / len(trades)
        compras = sum(1 for t in trades if t["direction"] == BUY)
        ventas = sum(1 for t in trades if t["direction"] == SELL)
        metrics["operaciones_cerradas_por_stop"] = stops
        metrics["operaciones_cerradas_por_take"] = takes
        metrics["r_multiple_promedio"] = r_promedio
        metrics["operaciones_compra"] = compras
        metrics["operaciones_venta"] = ventas
        metrics["slippage_total_puntos"] = sum(
            t.get("entry_slippage", 0) + t.get("exit_slippage", 0) for t in trades
        )
        metrics["spread_cost_total"] = sum(t.get("spread_cost", 0) for t in trades)
        metrics["commission_cost_total"] = sum(t.get("commission_cost", 0) for t in trades)

    return {"trades": trades, "metrics": metrics}


def _revisar_salida(posicion, high_actual, low_actual):
    """
    Revisa si la vela actual (high/low) activa el stop-loss o el
    take-profit de la posición abierta, respetando la dirección.

    Si en la misma vela se pudo haber tocado ambos niveles, asumimos el
    escenario más conservador: el stop se activó primero (peor caso, no
    el más optimista) — igual para BUY y para SELL.

    Devuelve (exit_price, "STOP"|"TAKE") o None si la vela no activó
    ninguno de los dos niveles todavía.
    """
    direction = posicion["direction"]
    stop_price = posicion["stop_price"]
    take_price = posicion["take_price"]

    if direction == BUY:
        if low_actual <= stop_price:
            return stop_price, "STOP"
        if high_actual >= take_price:
            return take_price, "TAKE"
    else:  # SELL
        if high_actual >= stop_price:
            return stop_price, "STOP"
        if low_actual <= take_price:
            return take_price, "TAKE"

    return None


def _calcular_profit_bruto(direction, entry_price, exit_price, position_size):
    """
    Beneficio (antes de costos) de una operación cerrada, según su
    dirección:

      BUY  → ganas si exit_price > entry_price.
      SELL → ganas si exit_price < entry_price (precio bajó).
    """
    if direction == SELL:
        return (entry_price - exit_price) * position_size
    return (exit_price - entry_price) * position_size
