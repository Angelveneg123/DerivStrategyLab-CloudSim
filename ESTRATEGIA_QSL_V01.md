# Quant Structure Liquidity v0.1

Estado: **hipótesis de laboratorio**. No se considera rentable hasta superar backtest, out-of-sample, walk-forward, live simulation y DEMO.

## Flujo
1. HTF: sesgo por HH/HL o LH/LL confirmados (fractal 5 velas).
2. LTF: mapa de liquidez con swings + PDH/PDL.
3. Sweep: penetración >= 0.10 ATR, cierre dentro del nivel y mecha >= 40%.
4. Confirmación: MSS/CHOCH/BOS posterior al sweep.
5. MTF/POI: FVG y OB derivados de ruptura estructural.
6. Premium/Discount: compras bajo 50% del dealing range; ventas sobre 50%.
7. Volatilidad: no operar si ATR14 < 0.75 x media ATR(50).
8. Entrada: solo cuando las capas anteriores coinciden.

## Temporalidades por defecto
- H4: dirección/contexto.
- H1: estructura/POI.
- M15: trigger.
- Datos fuente: M1 (se agregan sin mirar el futuro).

Las EMAs 50/100/200 se calculan como confirmación opcional. La estructura es el sesgo primario porque 200 velas H4 exigirían ~48,000 M1 para warmup.

## Gestión de riesgo DEMO por defecto
- 0.5% por operación.
- Máximo 3 operaciones/día.
- 2 pérdidas consecutivas -> pausa.
- Pérdida diaria máxima 2%.
- Circuit breaker por drawdown 12%.
- Una posición abierta máxima.
- Cuenta REAL deshabilitada.

## Qué falta validar
Los valores 40% de mecha, 0.10 ATR de penetración, 0.75 de filtro de volatilidad y la ventana de 3 velas son **parámetros de hipótesis**, no valores optimizados.

El SL/TP de ejecución sigue pasando por el `Risk Manager` actual (ATR + R:R) para no mezclar en la primera prueba la calidad de entrada con las 27 combinaciones MTF/SL/TP. Una vez exista edge base, se implementa la matriz de ablación.

## Resultado inicial encontrado en el histórico incluido
Prueba realizada sobre `CRASH500`, M1, 40,911 velas, usando el mismo motor de costos/riesgo del proyecto:

- QSL v0.1: 46 trades, WR 43.48%, PF 1.264, DD 4.10%, retorno +4.09%.
- Hybrid existente: 151 trades, WR 65.56%, PF 3.173, DD 2.83%, retorno +86.92%.

**Conclusión:** QSL v0.1 NO reemplaza todavía a Hybrid. Se añadió como estrategia experimental y debe mejorar mediante pruebas de ablación / out-of-sample antes de Live Simulation o DEMO automática.
