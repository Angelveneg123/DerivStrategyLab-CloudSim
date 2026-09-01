"""
Gestión de riesgo — la pieza que le faltaba al sistema hasta ahora.

Hasta el Módulo 5, cada operación se cerraba solo cuando aparecía la
señal contraria — sin stop-loss ni take-profit. Este módulo agrega eso:

  - Stop-loss / take-profit dinámicos, basados en el ATR (la volatilidad
    real del momento, no un número fijo inventado).
  - Tamaño de posición basado en un % de riesgo del capital — así el
    tamaño de cada operación se ajusta solo según qué tan lejos está el
    stop, y una racha de pérdidas nunca compromete más del % definido
    por operación.

IMPORTANTE (corrección): las tres funciones de este módulo ahora son
bidireccionales — reciben un parámetro `direction` ("BUY" o "SELL") y
calculan el stop, el take y el tamaño de posición espejados
correctamente para cada lado. Antes, todo el módulo asumía en silencio
que la operación era siempre de compra (long); una operación de venta
(short) recibía un stop-loss puesto del lado equivocado del precio, lo
que rompía por completo la gestión de riesgo para SELL.
"""

from strategy.strategy_engine import BUY, SELL


def calculate_stop_take(entry_price, atr, direction=BUY, stop_atr_mult=1.5, reward_ratio=2.0):
    """
    Calcula el stop-loss y take-profit de una operación, ya sea de
    compra (BUY / long) o de venta (SELL / short), basados en el ATR
    del momento de la entrada.

    entry_price: precio de entrada.
    atr: valor del ATR en la vela de entrada (mide volatilidad reciente).
    direction: BUY o SELL (constantes de strategy.strategy_engine).
        - BUY:  el stop va POR DEBAJO del precio de entrada y el take
                POR ENCIMA (ganas si el precio sube).
        - SELL: el stop va POR ENCIMA del precio de entrada y el take
                POR DEBAJO (ganas si el precio baja).
    stop_atr_mult: a cuántos ATR de distancia poner el stop-loss. Más
        alto = stop más lejos = menos probabilidad de que te saque el
        ruido normal del mercado, pero pierdes más si se activa.
    reward_ratio: relación riesgo:beneficio. 2.0 significa que el
        take-profit está al doble de distancia que el stop (arriesgas 1
        para ganar 2).

    Devuelve (stop_price, take_price).
    """
    distancia_stop = atr * stop_atr_mult

    if direction == SELL:
        stop_price = entry_price + distancia_stop
        take_price = entry_price - (distancia_stop * reward_ratio)
    else:  # BUY (comportamiento anterior, sin cambios)
        stop_price = entry_price - distancia_stop
        take_price = entry_price + (distancia_stop * reward_ratio)

    return stop_price, take_price


def calculate_position_size(balance, risk_pct, entry_price, stop_price):
    """
    Calcula cuántas "unidades" del activo operar, para que si el stop
    se activa, la pérdida sea exactamente `risk_pct`% del balance actual
    — ni más, ni menos, sin importar qué tan lejos esté el stop ni si la
    operación es BUY o SELL.

    balance: capital actual.
    risk_pct: % del capital dispuesto a arriesgar en esta operación
        (ej. 1.0 = arriesgar el 1% del balance).
    entry_price, stop_price: para calcular la distancia del stop. Se usa
        el valor absoluto de la distancia a propósito: en un SELL el
        stop queda POR ENCIMA del precio de entrada (stop_price >
        entry_price), así que (entry_price - stop_price) daría negativo
        y rompería el sizing si no se corrige.

    Devuelve el tamaño de posición (unidades). Si la distancia al stop
    es 0 (no debería pasar con ATR real, pero por seguridad), devuelve 0
    para no dividir entre cero.
    """
    dinero_en_riesgo = balance * (risk_pct / 100)
    distancia = abs(entry_price - stop_price)

    if distancia <= 0:
        return 0

    return dinero_en_riesgo / distancia


def convertir_a_limites_monetarios(
    entry_price, stop_price, take_price, stake, multiplier
):
    """
    Convierte precios de stop-loss/take-profit (lo que ya calculamos con
    ATR) al formato que exige Deriv para contratos Multiplier: un MONTO
    EN DINERO, no un nivel de precio.

    Funciona para BUY y SELL: se usa el valor absoluto de cada
    distancia porque Deriv siempre espera "cuánto se pierde" y "cuánto
    se gana" como números positivos, sin importar el lado de la
    operación (en un SELL, stop_price > entry_price > take_price; en un
    BUY es al revés — abs() hace que el resultado sea correcto en
    ambos casos).

    La relación aproximada de un Multiplier: la ganancia/pérdida es
    stake × multiplier × (% de movimiento del precio). Esto es una
    aproximación razonable para planear la orden — Deriv puede ajustar
    ligeramente el monto exacto por comisiones; siempre confirma con el
    "proposal" real antes de comprar, no confíes ciegamente en este
    cálculo para nada crítico.

    Devuelve (stop_loss_dinero, take_profit_dinero), ambos como números
    positivos (así los espera la API: "cuánto se pierde" y "cuánto se
    gana", no un precio con signo).
    """
    pct_stop = abs(entry_price - stop_price) / entry_price
    pct_take = abs(take_price - entry_price) / entry_price

    stop_loss_dinero = stake * multiplier * pct_stop
    take_profit_dinero = stake * multiplier * pct_take

    return round(stop_loss_dinero, 2), round(take_profit_dinero, 2)