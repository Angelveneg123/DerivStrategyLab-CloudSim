"""
Motor de estrategias — Módulo 4.

Este archivo NO descarga datos ni conoce a Deriv. Solo recibe listas de
precios ya calculadas y devuelve señales. Esa separación es a propósito:
el mismo motor de estrategias podrá usarse después tanto en backtesting
(datos históricos) como en el bot en vivo (datos en tiempo real) sin
cambiar una línea de este archivo.

Incluye dos estrategias:

1. generate_signals()
   Cruce de EMA rápida/lenta, filtrado por RSI y (opcionalmente) por ADX.
   (La que ya existía — no se tocó su comportamiento.)

2. generate_signals_soporte_resistencia()
   Estrategia nueva basada en niveles de soporte y resistencia:
   detecta zonas donde el precio rebotó varias veces en el pasado y
   genera señales cuando el precio vuelve a tocar esas zonas, confirmando
   con RSI que hay sobrecompra/sobreventa real (y no solo un toque de
   casualidad). Además respeta la tendencia de fondo (EMA rápida vs EMA
   lenta) y el "cambio de rol" de los niveles: una resistencia rota se
   convierte en soporte futuro, y viceversa — el mismo concepto que se ve
   en gráficos de TradingView cuando una zona que antes frenaba al precio
   pasa a sostenerlo después de ser perforada en la dirección de la
   tendencia.
"""

from indicators.ema import calculate_ema
from indicators.rsi import calculate_rsi
from indicators.adx import calculate_adx

# Señales posibles que puede devolver cualquier estrategia de este módulo.
BUY = "BUY"
SELL = "SELL"
HOLD = "HOLD"


def generate_signals(closes, highs=None, lows=None, ema_fast_period=9,
                      ema_slow_period=21, rsi_period=14, adx_period=14,
                      adx_minimo=25):
    """
    closes: lista de precios de cierre, de la vela más vieja a la más nueva.
    highs, lows: necesarias para calcular ADX. Si no se pasan, la
        estrategia funciona sin filtro de tendencia (comportamiento
        anterior).
    adx_minimo: qué tan fuerte debe estar la tendencia (según ADX) para
        que la estrategia tome la señal. 25 es el estándar de mercado
        para considerar "tendencia confirmada".

    Devuelve una lista del mismo largo que `closes`, con BUY / SELL / HOLD
    en cada posición (None donde todavía no hay suficientes datos).
    """
    ema_fast = calculate_ema(closes, period=ema_fast_period)
    ema_slow = calculate_ema(closes, period=ema_slow_period)
    rsi = calculate_rsi(closes, period=rsi_period)

    usar_filtro_adx = highs is not None and lows is not None
    if usar_filtro_adx:
        adx, _, _ = calculate_adx(highs, lows, closes, period=adx_period)

    n = len(closes)
    signals = [None] * n

    # El primer índice donde todos los indicadores en uso ya tienen valor.
    inicio = max(ema_fast_period, ema_slow_period, rsi_period) + 1
    if usar_filtro_adx:
        inicio = max(inicio, adx_period * 2)

    for i in range(inicio, n):
        cruce_hacia_arriba = (
            ema_fast[i - 1] <= ema_slow[i - 1] and ema_fast[i] > ema_slow[i]
        )
        cruce_hacia_abajo = (
            ema_fast[i - 1] >= ema_slow[i - 1] and ema_fast[i] < ema_slow[i]
        )

        # Si estamos usando el filtro, exige tendencia confirmada (ADX alto).
        tendencia_confirmada = (not usar_filtro_adx) or (adx[i] is not None and adx[i] > adx_minimo)

        if cruce_hacia_arriba and rsi[i] < 70 and tendencia_confirmada:
            signals[i] = BUY
        elif cruce_hacia_abajo and rsi[i] > 30 and tendencia_confirmada:
            signals[i] = SELL
        else:
            signals[i] = HOLD

    return signals


# ---------------------------------------------------------------------------
# Estrategia 2: Soporte y Resistencia
# ---------------------------------------------------------------------------
#
# Idea de la estrategia
# ----------------------
# Un "soporte" es una zona de precio donde, en el pasado, el mercado bajó
# y rebotó hacia arriba más de una vez (los compradores defendieron ese
# nivel). Una "resistencia" es lo contrario: una zona donde el precio subió
# y fue rechazado hacia abajo más de una vez (los vendedores defendieron
# ese nivel).
#
# Esta estrategia combina TRES ideas sobre el mismo concepto:
#
#   1. Rebote en el nivel (mean reversion): si el precio toca un soporte
#      confiable y el RSI está en sobreventa, se espera un rebote hacia
#      arriba -> BUY. Si toca una resistencia confiable y el RSI está en
#      sobrecompra, se espera un rechazo -> SELL.
#
#   2. Cambio de rol (role reversal / polaridad): cuando un nivel se
#      rompe con convicción en la dirección de la tendencia, cambia de
#      función — una resistencia rota hacia arriba pasa a actuar como
#      soporte en el siguiente retroceso, y un soporte roto hacia abajo
#      pasa a actuar como resistencia. Es el mismo patrón que se marca a
#      mano en TradingView: la zona no desaparece, solo cambia de bando.
#
#   3. Filtro de tendencia (EMA rápida vs EMA lenta): igual que remarca
#      la imagen de referencia, "hay que respetar la tendencia alcista
#      para la compra, y lo contrario para la venta". En tendencia alcista
#      solo se buscan rebotes de continuación en soportes (incluyendo los
#      que antes eran resistencia y cambiaron de rol); en tendencia
#      bajista, solo rechazos en resistencias. Solo en mercado lateral
#      (sin tendencia clara) se opera en ambas direcciones.
#
# El RSI aquí no es adorno: sin él, "tocar un nivel" no alcanza para
# confiar en el rebote (el precio puede perforar el nivel sin más). El RSI
# en zona extrema es lo que sugiere que el movimiento reciente ya está
# agotado, justo donde históricamente el mercado giró.
#
# Cómo se detectan los niveles (sin trampa de "ver el futuro")
# --------------------------------------------------------------
# Un pivote (máximo o mínimo local) en la vela j solo se puede confirmar
# una vez que ya pasaron `lookback` velas después de j — porque hasta
# entonces no sabemos si el precio va a superar ese máximo/mínimo. Por
# eso el pivote de la vela j recién se confirma en la vela i = j + lookback.
# Esto es clave para que el backtest sea honesto: la estrategia solo usa,
# en cada vela, información que ya existía en ese momento.
#
# Los pivotes confirmados que caen cerca unos de otros (dentro de
# `tolerancia_pct`) se agrupan en un mismo "nivel", y cada agrupación
# cuenta cuántas veces fue tocada (`toques`). Solo se opera contra niveles
# que ya demostraron ser respetados al menos `min_toques` veces — un
# pivote aislado no es todavía un soporte/resistencia confiable.
#
# El cambio de rol se detecta y se aplica UNA sola vez por nivel (una vez
# que cambió de bando, no vuelve a cambiar). Es una simplificación
# deliberada para mantener el código legible: en la práctica evita que un
# nivel "parpadee" entre soporte y resistencia por ruido de precio.


def _es_pivote_alto(highs, j, lookback):
    """True si highs[j] es el máximo dentro de la ventana [j-lookback, j+lookback]."""
    ventana = highs[j - lookback:j + lookback + 1]
    return highs[j] == max(ventana)


def _es_pivote_bajo(lows, j, lookback):
    """True si lows[j] es el mínimo dentro de la ventana [j-lookback, j+lookback]."""
    ventana = lows[j - lookback:j + lookback + 1]
    return lows[j] == min(ventana)


def _registrar_nivel(niveles, precio, tolerancia_pct):
    """
    Agrega `precio` a la lista `niveles` (in-place). Si ya existe un nivel
    a menos de `tolerancia_pct` % de distancia, lo fusiona (promedia el
    precio y suma un toque) en vez de crear un nivel nuevo. Así, cuatro
    rebotes casi en el mismo punto cuentan como un nivel fuerte de 4
    toques, no como cuatro niveles distintos de 1 toque cada uno.

    Cada nivel es un dict: {"precio": float, "toques": int}.
    """
    tolerancia = tolerancia_pct / 100
    for nivel in niveles:
        distancia_pct = abs(precio - nivel["precio"]) / nivel["precio"]
        if distancia_pct <= tolerancia:
            # Fusiona: nuevo promedio ponderado por cantidad de toques previos.
            nivel["precio"] = (
                nivel["precio"] * nivel["toques"] + precio
            ) / (nivel["toques"] + 1)
            nivel["toques"] += 1
            return
    niveles.append({"precio": precio, "toques": 1})


def _nivel_mas_cercano(niveles, precio_actual, min_toques):
    """
    De los niveles con al menos `min_toques` toques, devuelve el precio del
    más cercano a `precio_actual`. Devuelve None si no hay ninguno todavía.
    """
    candidatos = [n for n in niveles if n["toques"] >= min_toques]
    if not candidatos:
        return None
    mas_cercano = min(candidatos, key=lambda n: abs(n["precio"] - precio_actual))
    return mas_cercano["precio"]


def _actualizar_cambio_de_rol(niveles, tipo_actual, precio_actual, ruptura_pct):
    """
    Revisa una lista de niveles (todos del mismo `tipo_actual`, "soporte" o
    "resistencia") y cambia el tipo de los que fueron rotos con convicción
    por `precio_actual` (más allá de `ruptura_pct`). Devuelve dos listas
    nuevas: (niveles_que_siguen_igual, niveles_que_cambiaron_de_rol).

    Un nivel roto solo cambia de rol UNA vez (usa la clave "rol_cambiado"
    para no volver a evaluarlo después).
    """
    quedan_igual = []
    cambiaron = []

    for nivel in niveles:
        if nivel.get("rol_cambiado"):
            quedan_igual.append(nivel)
            continue

        precio_nivel = nivel["precio"]
        if tipo_actual == "resistencia" and precio_actual > precio_nivel * (1 + ruptura_pct / 100):
            nivel["rol_cambiado"] = True
            cambiaron.append(nivel)
        elif tipo_actual == "soporte" and precio_actual < precio_nivel * (1 - ruptura_pct / 100):
            nivel["rol_cambiado"] = True
            cambiaron.append(nivel)
        else:
            quedan_igual.append(nivel)

    return quedan_igual, cambiaron


def generate_signals_soporte_resistencia(closes, highs, lows, lookback=5,
                                          tolerancia_pct=0.15, rsi_period=14,
                                          rsi_sobrecompra=70, rsi_sobreventa=30,
                                          min_toques=2, ema_tendencia_rapida=50,
                                          ema_tendencia_lenta=200,
                                          ruptura_pct=0.10,
                                          usar_cambio_de_rol=True):
    """
    closes, highs, lows: listas del mismo largo, de la vela más vieja a la
        más nueva.
    lookback: cuántas velas antes Y después se exigen para confirmar un
        pivote (máximo o mínimo local). Más alto = niveles más importantes
        pero menos frecuentes. 5 es un punto de partida razonable en
        velas de 1 minuto.
    tolerancia_pct: qué tan cerca (en %) tiene que estar el precio de un
        nivel para considerar que lo "tocó". También se usa para agrupar
        pivotes cercanos en un mismo nivel. Ej: 0.15 = 0.15%.
    rsi_period, rsi_sobrecompra, rsi_sobreventa: confirmación de que el
        movimiento reciente ya está agotado (evita operar en cada simple
        toque del nivel).
    min_toques: cuántas veces debe haber sido respetado un nivel en el
        pasado para considerarlo confiable y operar contra él.
    ema_tendencia_rapida, ema_tendencia_lenta: definen la tendencia de
        fondo. Si el precio y la EMA rápida están por encima de la EMA
        lenta -> tendencia alcista (solo se buscan señales BUY). Si están
        por debajo -> tendencia bajista (solo señales SELL). Si no hay
        una relación clara -> mercado lateral (se buscan ambas).
    ruptura_pct: qué tan lejos tiene que cerrar el precio más allá de un
        nivel para considerar que lo rompió "con convicción" y activar el
        cambio de rol (no cualquier toque cuenta como ruptura).
    usar_cambio_de_rol: si es False, se desactiva el punto 2 (cambio de
        rol) y la estrategia se comporta como rebote puro en los niveles
        originales, sin reciclarlos cuando se rompen.

    Devuelve una lista del mismo largo que `closes`, con BUY / SELL / HOLD
    en cada posición (None donde todavía no hay suficientes datos).
    """
    n = len(closes)
    if not (len(highs) == n and len(lows) == n):
        raise ValueError("closes, highs y lows deben tener el mismo largo.")

    rsi = calculate_rsi(closes, period=rsi_period)
    ema_rapida = calculate_ema(closes, period=ema_tendencia_rapida)
    ema_lenta = calculate_ema(closes, period=ema_tendencia_lenta)

    # Se necesitan `lookback` velas antes Y después para confirmar el primer
    # pivote, `rsi_period` velas para el primer valor de RSI, y
    # `ema_tendencia_lenta` velas para que la EMA de tendencia arranque.
    inicio = max(2 * lookback + 1, rsi_period + 1, ema_tendencia_lenta)

    signals = [None] * n
    niveles_resistencia = []  # pivotes altos ya confirmados, agrupados
    niveles_soporte = []      # pivotes bajos ya confirmados, agrupados

    for i in range(inicio, n):
        precio_actual = closes[i]

        # Paso 1: confirmar el pivote que "madura" en esta vela, si hay uno.
        # El pivote candidato es el de la vela j = i - lookback, porque
        # recién en la vela i ya pasaron las `lookback` velas posteriores
        # a j necesarias para confirmarlo.
        j = i - lookback
        if j - lookback >= 0:
            if _es_pivote_alto(highs, j, lookback):
                _registrar_nivel(niveles_resistencia, highs[j], tolerancia_pct)
            if _es_pivote_bajo(lows, j, lookback):
                _registrar_nivel(niveles_soporte, lows[j], tolerancia_pct)

        # Paso 2: cambio de rol — un nivel roto con convicción "cruza de
        # lista" (una resistencia rota hacia arriba pasa a la lista de
        # soportes, y viceversa).
        if usar_cambio_de_rol:
            niveles_resistencia, pasan_a_soporte = _actualizar_cambio_de_rol(
                niveles_resistencia, "resistencia", precio_actual, ruptura_pct
            )
            niveles_soporte, pasan_a_resistencia = _actualizar_cambio_de_rol(
                niveles_soporte, "soporte", precio_actual, ruptura_pct
            )
            niveles_soporte.extend(pasan_a_soporte)
            niveles_resistencia.extend(pasan_a_resistencia)

        # Paso 3: tendencia de fondo, a partir de EMA rápida vs EMA lenta.
        if ema_rapida[i] is not None and ema_lenta[i] is not None:
            if precio_actual > ema_lenta[i] and ema_rapida[i] > ema_lenta[i]:
                tendencia = "alcista"
            elif precio_actual < ema_lenta[i] and ema_rapida[i] < ema_lenta[i]:
                tendencia = "bajista"
            else:
                tendencia = "lateral"
        else:
            tendencia = "lateral"

        permite_buy = tendencia in ("alcista", "lateral")
        permite_sell = tendencia in ("bajista", "lateral")

        # Paso 4: buscar el soporte/resistencia confiable más cercano al
        # precio actual (solo entre los que ya tienen `min_toques` toques).
        soporte = _nivel_mas_cercano(niveles_soporte, precio_actual, min_toques)
        resistencia = _nivel_mas_cercano(niveles_resistencia, precio_actual, min_toques)

        toco_soporte = (
            soporte is not None
            and abs(precio_actual - soporte) / soporte <= tolerancia_pct / 100
        )
        toco_resistencia = (
            resistencia is not None
            and abs(precio_actual - resistencia) / resistencia <= tolerancia_pct / 100
        )

        # Paso 5: señal solo si el toque del nivel viene acompañado de una
        # condición de agotamiento en el RSI Y la tendencia de fondo no la
        # contradice.
        if toco_soporte and permite_buy and rsi[i] is not None and rsi[i] <= rsi_sobreventa:
            signals[i] = BUY
        elif toco_resistencia and permite_sell and rsi[i] is not None and rsi[i] >= rsi_sobrecompra:
            signals[i] = SELL
        else:
            signals[i] = HOLD

    return signals
