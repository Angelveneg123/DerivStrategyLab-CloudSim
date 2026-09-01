"""
Backtester — Módulo 5.

Simula qué hubiera pasado si hubieras seguido cada señal del motor de
estrategias con dinero ficticio. No conoce a Deriv ni a la base de
datos: solo recibe listas de precios y señales, y devuelve resultados.
Esa separación permite backtestear cualquier estrategia futura sin
tocar este archivo.

Gestión de riesgo
------------------
La versión anterior de este archivo arriesgaba un monto FIJO en dólares
en cada operación (`position_size=1000`), igual al balance inicial
completo, y solo cerraba la operación cuando aparecía la señal contraria
— sin stop-loss. Eso equivale a apostar el 100% del capital en cada
operación sin límite de pérdida, algo que ninguna cuenta real sobrevive
más de un par de operaciones perdedoras seguidas.

Esta versión implementa gestión de riesgo real, con 3 piezas:

1. Sizing por % de riesgo (no por monto fijo): en cada operación se
   arriesga solo `riesgo_por_operacion_pct` % del balance ACTUAL (por
   defecto 2%). El tamaño de la posición se calcula para que, si se
   toca el stop-loss, la pérdida sea exactamente ese % — ni más ni
   menos. Como el balance cambia con cada operación, el tamaño de
   posición se ajusta solo (interés compuesto, hacia arriba y hacia
   abajo).

2. Stop-loss / take-profit dinámicos por ATR: la distancia del stop no
   es un número inventado, se calcula con el ATR (volatilidad real del
   mercado en ese momento — Módulo de indicadores). Si el mercado está
   agitado, el stop se aleja; si está tranquilo, se acerca. El
   take-profit por defecto es el doble de ancho que el stop (relación
   riesgo:beneficio 1:2), para que una operación ganadora compense a
   más de una perdedora.

3. Circuit breaker de drawdown: si el drawdown acumulado llega a
   `max_drawdown_stop_pct` (por defecto 20%), el backtest deja de abrir
   operaciones nuevas — igual que un trader real que se detiene a
   revisar qué está pasando en vez de seguir apostando capital que ya
   no tiene margen para perder.

IMPORTANTE antes de ir a cuenta real: `apalancamiento_max` limita cuánto
puede crecer una posición sobre el balance disponible. El valor por
defecto (5x) es conservador y GENÉRICO — antes de operar con dinero real
verifica el apalancamiento real que tu cuenta de Deriv permite para cada
símbolo (Deriv > Configuración de la plataforma) y ajusta este parámetro
para que coincida. Poner aquí un número más alto del que tu bróker
permite hace que el backtest muestre resultados que después no vas a
poder replicar en producción.
"""

from indicators.atr import calculate_atr

from strategy.strategy_engine import BUY, SELL


def run_backtest(
    epochs,
    closes,
    signals,
    highs=None,
    lows=None,
    initial_balance=100,
    spread_pct=0.05,
    riesgo_por_operacion_pct=2.0,
    atr_period=14,
    sl_atr_mult=1.5,
    tp_atr_mult=3.0,
    apalancamiento_max=5,
    max_drawdown_stop_pct=20,
    stop_fijo_pct=1.0,
):
    """
    epochs, closes, signals: listas del mismo largo (salen de get_candles
        y generate_signals / generate_signals_soporte_resistencia).
    highs, lows: opcionales, pero MUY recomendadas. Sin ellas no se puede
        calcular ATR y el stop-loss cae a una distancia fija
        (`stop_fijo_pct`) en vez de adaptarse a la volatilidad real.
    initial_balance: capital ficticio inicial. Por defecto $100 — cambia
        este número al capital real que vas a arriesgar antes de sacar
        conclusiones sobre si la estrategia "sirve" o no; los porcentajes
        (win rate, drawdown, retorno %) no cambian con el capital, pero
        el balance final en dólares sí.
    spread_pct: costo de entrar y salir de una operación, como porcentaje
        del precio (ej. 0.05 = 0.05%). Verifica el valor real de tu
        símbolo en la plataforma de Deriv antes de confiar en el número
        final — 0.05% es una estimación conservadora, no un dato oficial.
    riesgo_por_operacion_pct: % del balance ACTUAL que se arriesga por
        operación si se toca el stop-loss. 1-2% es el rango típico que
        recomienda la gestión de riesgo profesional; más de 5% por
        operación es jugar, no invertir.
    atr_period, sl_atr_mult, tp_atr_mult: definen dónde va el stop-loss
        (entry - sl_atr_mult * ATR) y el take-profit
        (entry + tp_atr_mult * ATR).
    apalancamiento_max: tope de tamaño de posición como múltiplo del
        balance actual, para que un ATR muy chico (mercado muy tranquilo)
        no dispare un tamaño de posición absurdo. AJUSTA ESTO al
        apalancamiento real que permite tu cuenta antes de operar en vivo.
    max_drawdown_stop_pct: si el drawdown llega a este %, se dejan de
        abrir operaciones nuevas por el resto del backtest (protección
        de capital).
    stop_fijo_pct: distancia de stop-loss a usar SOLO si no se pasaron
        highs/lows (no hay ATR disponible).

    Devuelve un dict con la lista de operaciones (`trades`) y las
    métricas de rendimiento (`metrics`), incluyendo si el circuit
    breaker de drawdown llegó a activarse.
    """
    n = len(closes)
    usar_atr = highs is not None and lows is not None and n > atr_period
    atr = (
        calculate_atr(highs, lows, closes, period=atr_period)
        if usar_atr
        else [None] * n
    )

    trades = []
    balance = initial_balance
    pico_balance = initial_balance
    circuit_breaker_activado = False

    i = 0
    while i < n:
        senal = signals[i]

        drawdown_actual_pct = (
            (pico_balance - balance) / pico_balance * 100 if pico_balance > 0 else 0
        )
        if drawdown_actual_pct >= max_drawdown_stop_pct:
            circuit_breaker_activado = True

        if senal == BUY and not circuit_breaker_activado:
            entrada = _abrir_y_simular_operacion(
                i,
                epochs,
                closes,
                highs,
                lows,
                signals,
                atr,
                balance,
                spread_pct,
                riesgo_por_operacion_pct,
                sl_atr_mult,
                tp_atr_mult,
                apalancamiento_max,
                stop_fijo_pct,
            )
            if entrada is not None:
                trade, indice_salida = entrada
                trades.append(trade)
                balance += trade["profit"]
                pico_balance = max(pico_balance, balance)
                i = indice_salida
                continue

        i += 1

    metrics = _calculate_metrics(trades, initial_balance)
    metrics["circuit_breaker_activado"] = circuit_breaker_activado

    return {"trades": trades, "metrics": metrics}


def _abrir_y_simular_operacion(
    i,
    epochs,
    closes,
    highs,
    lows,
    signals,
    atr,
    balance,
    spread_pct,
    riesgo_por_operacion_pct,
    sl_atr_mult,
    tp_atr_mult,
    apalancamiento_max,
    stop_fijo_pct,
):
    """
    Abre una operación LONG en la vela `i` (siempre que haya señal BUY) y
    escanea hacia adelante vela por vela hasta que se cumple alguna salida:
    stop-loss, take-profit, señal SELL, o fin de los datos.

    Devuelve (trade_dict, indice_de_salida) o None si por algún motivo no
    se pudo calcular un stop válido (ej. ATR todavía sin datos).
    """
    n = len(closes)
    entry_price = closes[i]
    entry_epoch = epochs[i]

    # Distancia del stop: por ATR si está disponible, si no, distancia fija.
    if atr[i] is not None and atr[i] > 0:
        distancia_stop_pct = (atr[i] * sl_atr_mult) / entry_price
    else:
        distancia_stop_pct = stop_fijo_pct / 100

    if distancia_stop_pct <= 0:
        return None

    stop_price = entry_price * (1 - distancia_stop_pct)
    take_profit_price = entry_price * (
        1 + distancia_stop_pct * (tp_atr_mult / sl_atr_mult)
    )

    # Sizing por % de riesgo: cuánto dinero estoy dispuesto a perder si se
    # toca el stop, dividido entre qué tan lejos está el stop en %.
    riesgo_dinero = balance * (riesgo_por_operacion_pct / 100)
    position_size = riesgo_dinero / distancia_stop_pct
    position_size = min(position_size, balance * apalancamiento_max)

    costo_pct_entrada = spread_pct / 100 / 2  # la mitad del spread al entrar

    for k in range(i + 1, n):
        precio_bajo = lows[k] if lows is not None else closes[k]
        precio_alto = highs[k] if highs is not None else closes[k]

        toco_stop = precio_bajo <= stop_price
        toco_take_profit = precio_alto >= take_profit_price
        hay_senal_salida = signals[k] == SELL

        if not (toco_stop or toco_take_profit or hay_senal_salida) and k < n - 1:
            continue

        # Prioridad si dos cosas pasan en la misma vela: el stop manda,
        # porque en la vida real no sabemos si el precio tocó primero el
        # stop o el take-profit dentro de esa misma vela — asumir lo
        # peor es la manera conservadora (honesta) de backtestear.
        if toco_stop:
            exit_price = stop_price
            motivo_salida = "stop_loss"
        elif toco_take_profit:
            exit_price = take_profit_price
            motivo_salida = "take_profit"
        elif hay_senal_salida:
            exit_price = closes[k]
            motivo_salida = "señal"
        else:
            # Se acabaron los datos con la operación todavía abierta:
            # se cierra al último precio disponible para no dejarla "flotando".
            exit_price = closes[k]
            motivo_salida = "fin_de_datos"

        exit_epoch = epochs[k]
        costo_pct_salida = spread_pct / 100 / 2
        profit_pct_bruto = (exit_price - entry_price) / entry_price
        profit_pct = profit_pct_bruto - costo_pct_entrada - costo_pct_salida
        profit = profit_pct * position_size

        trade = {
            "entry_epoch": entry_epoch,
            "entry_price": entry_price,
            "exit_epoch": exit_epoch,
            "exit_price": exit_price,
            "profit": profit,
            "profit_pct": profit_pct * 100,
            "position_size": position_size,
            "stop_price": stop_price,
            "take_profit_price": take_profit_price,
            "motivo_salida": motivo_salida,
        }
        return trade, k

    return None


def calculate_metrics(trades, initial_balance):
    """API pública para calcular métricas sobre una lista de operaciones."""
    return _calculate_metrics(trades, initial_balance)


def _calculate_metrics(trades, initial_balance):
    """Calcula win rate, profit factor, drawdown máximo y retorno total."""
    total_trades = len(trades)

    if total_trades == 0:
        return {
            "total_trades": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "max_drawdown_pct": 0,
            "total_return_pct": 0,
            "final_balance": initial_balance,
        }

    ganadoras = [t for t in trades if t["profit"] > 0]
    perdedoras = [t for t in trades if t["profit"] <= 0]

    win_rate = len(ganadoras) / total_trades * 100

    ganancia_bruta = sum(t["profit"] for t in ganadoras)
    perdida_bruta = abs(sum(t["profit"] for t in perdedoras))
    profit_factor = (
        ganancia_bruta / perdida_bruta if perdida_bruta > 0 else float("inf")
    )

    # Curva de capital: cómo iba subiendo/bajando el balance operación a operación.
    balance = initial_balance
    pico = initial_balance
    max_drawdown_pct = 0

    for t in trades:
        balance += t["profit"]
        pico = max(pico, balance)
        drawdown_pct = (pico - balance) / pico * 100
        max_drawdown_pct = max(max_drawdown_pct, drawdown_pct)

    total_return_pct = (balance - initial_balance) / initial_balance * 100

    return {
        "total_trades": total_trades,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_drawdown_pct,
        "total_return_pct": total_return_pct,
        "final_balance": balance,
    }
