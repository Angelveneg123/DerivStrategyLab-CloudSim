"""
Estructura de mercado — swing points, HH/HL/LH/LL, BOS y CHoCH.

Este módulo reemplaza la idea de "cruce de EMA" como disparador de
entrada por algo más cercano a cómo se lee un gráfico profesionalmente:
identificar los máximos y mínimos relevantes (swing points), etiquetar
si la estructura es alcista o bajista, y detectar cuándo esa estructura
se rompe (BOS) o cambia de carácter (CHoCH).

Definiciones que usamos aquí:
  - Swing high: una vela cuyo high es el más alto entre N velas antes y
    N velas después (un máximo local).
  - Swing low: lo mismo pero con mínimos.
  - HH (Higher High): un swing high más alto que el swing high anterior.
  - LH (Lower High): un swing high más bajo que el anterior.
  - HL (Higher Low): un swing low más alto que el anterior.
  - LL (Lower Low): un swing low más bajo que el anterior.
  - BOS (Break of Structure): el precio rompe el último swing relevante
    A FAVOR de la tendencia vigente — confirma que la tendencia sigue.
  - CHoCH (Change of Character): el precio rompe estructura EN CONTRA
    de la tendencia vigente — primera señal de posible reversión.
"""


def find_swing_points(highs, lows, lookback=3):
    """
    Encuentra los swing highs y swing lows de una serie de velas.

    lookback: cuántas velas antes y después debe superar/ser superado
        para contar como máximo/mínimo local. Más alto = swings más
        significativos pero menos frecuentes.

    Devuelve dos listas de tuplas (índice_confirmacion, precio). El swing
    nace en la vela central, pero solo queda disponible `lookback` velas
    después, cuando ya existen las velas de la derecha que lo confirman.
    Así el backtest nunca usa información futura.
    """
    swing_highs = []
    swing_lows = []
    n = len(highs)

    for i in range(lookback, n - lookback):
        ventana_high = highs[i - lookback : i + lookback + 1]
        if highs[i] == max(ventana_high) and ventana_high.count(highs[i]) == 1:
            swing_highs.append((i + lookback, highs[i]))

        ventana_low = lows[i - lookback : i + lookback + 1]
        if lows[i] == min(ventana_low) and ventana_low.count(lows[i]) == 1:
            swing_lows.append((i + lookback, lows[i]))

    return swing_highs, swing_lows


def label_structure(swing_highs, swing_lows):
    """
    Etiqueta cada swing point como HH/LH (para highs) o HL/LL (para lows),
    comparándolo contra el swing del mismo tipo inmediatamente anterior.

    Devuelve una lista de eventos ordenados cronológicamente, cada uno:
    {"index": i, "price": p, "type": "high"|"low", "label": "HH"|"LH"|"HL"|"LL"}
    El primer swing de cada tipo no tiene con qué compararse, así que se
    etiqueta simplemente como inicial (sin HH/LH/HL/LL).
    """
    eventos = []

    ultimo_high = None
    for i, price in swing_highs:
        if ultimo_high is None:
            label = "HIGH_INICIAL"
        else:
            label = "HH" if price > ultimo_high else "LH"
        eventos.append({"index": i, "price": price, "type": "high", "label": label})
        ultimo_high = price

    ultimo_low = None
    for i, price in swing_lows:
        if ultimo_low is None:
            label = "LOW_INICIAL"
        else:
            label = "HL" if price > ultimo_low else "LL"
        eventos.append({"index": i, "price": price, "type": "low", "label": label})
        ultimo_low = price

    eventos.sort(key=lambda e: e["index"])
    return eventos


def analizar_estructura(highs, lows, closes, lookback=3):
    """
    Función principal: encuentra los swing points, los etiqueta, y
    recorre las velas para detectar en qué momento exacto el precio de
    cierre rompe el último swing relevante — eso es lo que separa un
    BOS (ruptura a favor de la tendencia) de un CHoCH (ruptura en contra,
    posible cambio de tendencia).

    Devuelve un dict:
      - "eventos": todos los swing points etiquetados (HH/LH/HL/LL)
      - "rupturas": lista de BOS/CHoCH detectados, cada uno con el índice
        de la vela donde ocurrió, el tipo y la dirección
    """
    swing_highs, swing_lows = find_swing_points(highs, lows, lookback=lookback)
    eventos = label_structure(swing_highs, swing_lows)

    rupturas = []
    tendencia = None
    ultimo_swing_high = None
    ultimo_swing_low = None
    ya_roto_high = False  # evita marcar la misma ruptura varias veces seguidas
    ya_roto_low = False

    idx_evento = 0
    n = len(closes)

    for i in range(n):
        # Actualizar los swings conocidos hasta este punto.
        while idx_evento < len(eventos) and eventos[idx_evento]["index"] <= i:
            evento = eventos[idx_evento]
            if evento["label"] in ("HH", "HL"):
                tendencia = "alcista"
            elif evento["label"] in ("LH", "LL"):
                tendencia = "bajista"

            if evento["type"] == "high":
                ultimo_swing_high = evento["price"]
                ya_roto_high = False
            else:
                ultimo_swing_low = evento["price"]
                ya_roto_low = False

            idx_evento += 1

        # Ruptura hacia arriba: el cierre supera el último swing high.
        if (
            ultimo_swing_high is not None
            and not ya_roto_high
            and closes[i] > ultimo_swing_high
        ):
            tipo = "BOS" if tendencia == "alcista" else "CHoCH"
            rupturas.append(
                {
                    "index": i,
                    "price": closes[i],
                    "tipo": tipo,
                    "direccion": "alcista",
                    # Swing que dio inicio al impulso que rompió estructura.
                    # Lo necesita el módulo de pullback para calcular la zona
                    # institucional (retroceso de este swing al precio de ruptura).
                    "swing_origen_price": ultimo_swing_low,
                }
            )
            ya_roto_high = True
            if tipo == "CHoCH":
                tendencia = "alcista"  # el CHoCH marca el cambio de tendencia

        # Ruptura hacia abajo: el cierre rompe el último swing low.
        if (
            ultimo_swing_low is not None
            and not ya_roto_low
            and closes[i] < ultimo_swing_low
        ):
            tipo = "BOS" if tendencia == "bajista" else "CHoCH"
            rupturas.append(
                {
                    "index": i,
                    "price": closes[i],
                    "tipo": tipo,
                    "direccion": "bajista",
                    "swing_origen_price": ultimo_swing_high,
                }
            )
            ya_roto_low = True
            if tipo == "CHoCH":
                tendencia = "bajista"

    return {"eventos": eventos, "rupturas": rupturas}
