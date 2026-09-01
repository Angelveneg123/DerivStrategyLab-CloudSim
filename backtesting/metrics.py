"""
Métricas profesionales de validación — extensión del Módulo 5.

Estas métricas no reemplazan a Win Rate / Profit Factor / Drawdown
(backtester.py) — los complementan, respondiendo una pregunta distinta
cada una:

  - Sharpe / Sortino: ¿qué tan "parejo" fue el camino hacia el resultado?
  - Monte Carlo: ¿qué tan sensible es el resultado al orden en que
    ocurrieron las operaciones? (¿fue suerte de secuencia, o es robusto?)
  - Walk-Forward: ¿la estrategia es rentable de forma consistente en el
    tiempo, o solo un tramo particular "salvó" el promedio?

Todas trabajan sobre la lista de `trades` que ya devuelve run_backtest(),
así que no duplican la simulación — solo la analizan desde otro ángulo.
"""

import random
import statistics

from backtesting.backtester import calculate_metrics


def calculate_sharpe_ratio(trades):
    """
    Sharpe Ratio, calculado por operación (no anualizado).

    Fórmula: promedio de los retornos / desviación estándar de los
    retornos. Un valor más alto significa retornos más consistentes,
    no solo más grandes. No se anualiza aquí porque el intervalo real
    entre operaciones varía (algunas están separadas por minutos, otras
    por días) — anualizar sin esa información daría un número falso.

    Devuelve None si no hay suficientes operaciones para calcular una
    desviación estándar (mínimo 2).
    """
    if len(trades) < 2:
        return None

    retornos = [t["profit_pct"] / 100 for t in trades]
    promedio = statistics.mean(retornos)
    desviacion = statistics.stdev(retornos)  # stdev de muestra (n-1)

    if desviacion == 0:
        return None  # todas las operaciones dieron exactamente el mismo retorno

    return promedio / desviacion


def calculate_sortino_ratio(trades):
    """
    Sortino Ratio: como el Sharpe, pero solo penaliza la volatilidad de
    las operaciones perdedoras — no castiga que las ganancias varíen
    mucho en tamaño, solo que las pérdidas sean inconsistentes.

    Devuelve None si no hay al menos 2 operaciones perdedoras (no se
    puede calcular una desviación estándar de pérdidas con menos).
    """
    retornos = [t["profit_pct"] / 100 for t in trades]
    perdidas = [r for r in retornos if r < 0]

    if len(perdidas) < 2:
        return None

    promedio = statistics.mean(retornos)
    desviacion_bajista = statistics.stdev(perdidas)

    if desviacion_bajista == 0:
        return None

    return promedio / desviacion_bajista


def run_monte_carlo(trades, initial_balance=100, simulations=100, seed=None):
    """
    Reordena al azar la secuencia de operaciones (mismo conjunto de
    trades, distinto orden) miles de veces, y mide qué tan distinto pudo
    haber sido el CAMINO hacia el resultado — no el resultado final, que
    siempre es el mismo sin importar el orden (sumar las mismas
    ganancias/pérdidas en cualquier orden da el mismo total).

    Lo que sí cambia con el orden es qué tan profundo fue el peor
    momento (drawdown) y el balance más bajo que se llegó a tocar en el
    camino — eso es lo que responde: "¿el orden real fue benévolo, o
    pudiste haber pasado por algo mucho peor con las mismas operaciones?"
    """
    if len(trades) < 5:
        return {
            "advertencia": (
                f"Solo hay {len(trades)} operaciones — muy pocas para que "
                f"Monte Carlo diga algo confiable. Se recomienda un mínimo "
                f"de 20-30."
            ),
            "simulations": 0,
        }

    if seed is not None:
        random.seed(seed)

    profits = [t["profit"] for t in trades]
    balance_final_real = initial_balance + sum(
        profits
    )  # igual en todas las simulaciones

    drawdowns = []
    peores_balances = []

    for _ in range(simulations):
        orden_aleatorio = profits.copy()
        random.shuffle(orden_aleatorio)

        balance = initial_balance
        pico = initial_balance
        peor_balance = initial_balance
        max_dd = 0

        for profit in orden_aleatorio:
            balance += profit
            pico = max(pico, balance)
            peor_balance = min(peor_balance, balance)
            dd = (pico - balance) / pico * 100 if pico > 0 else 0
            max_dd = max(max_dd, dd)

        drawdowns.append(max_dd)
        peores_balances.append(peor_balance)

    drawdowns.sort()
    peores_balances.sort()

    def percentil(lista_ordenada, p):
        idx = int(len(lista_ordenada) * p / 100)
        idx = min(idx, len(lista_ordenada) - 1)
        return lista_ordenada[idx]

    return {
        "simulations": simulations,
        "balance_final": balance_final_real,  # no varía — es el resultado real
        "drawdown_mediana": percentil(drawdowns, 50),
        "drawdown_peor_5pct": percentil(drawdowns, 95),
        "peor_balance_mediana": percentil(peores_balances, 50),
        "peor_balance_peor_5pct": percentil(peores_balances, 5),
    }


def run_walk_forward(trades, initial_balance=1000, n_segments=4):
    """
    Divide las operaciones (ya en orden cronológico) en `n_segments`
    tramos consecutivos, y calcula las métricas de cada tramo por
    separado — cada uno arrancando con el mismo capital inicial, para
    que sean comparables entre sí.

    Si la estrategia es rentable en la mayoría de los tramos, es mucho
    más confiable que si el resultado general depende de uno solo.
    """
    total = len(trades)

    if total < n_segments * 3:
        return {
            "advertencia": (
                f"Solo hay {total} operaciones para dividir en {n_segments} "
                f"tramos — muy pocas por tramo para ser confiable. Se "
                f"recomienda al menos 3-5 operaciones por tramo, idealmente más."
            ),
            "tramos": [],
        }

    tamano_tramo = total // n_segments
    tramos = []

    for i in range(n_segments):
        inicio = i * tamano_tramo
        # El último tramo se lleva lo que sobre de la división.
        fin = total if i == n_segments - 1 else (i + 1) * tamano_tramo

        trades_tramo = trades[inicio:fin]
        metrics_tramo = calculate_metrics(trades_tramo, initial_balance)

        tramos.append(
            {
                "tramo": i + 1,
                "operaciones": len(trades_tramo),
                "fecha_inicio_epoch": (
                    trades_tramo[0]["entry_epoch"] if trades_tramo else None
                ),
                "fecha_fin_epoch": (
                    trades_tramo[-1]["exit_epoch"] if trades_tramo else None
                ),
                "metrics": metrics_tramo,
            }
        )

    tramos_rentables = sum(1 for t in tramos if t["metrics"]["total_return_pct"] > 0)

    return {
        "tramos": tramos,
        "tramos_rentables": tramos_rentables,
        "tramos_totales": n_segments,
    }
