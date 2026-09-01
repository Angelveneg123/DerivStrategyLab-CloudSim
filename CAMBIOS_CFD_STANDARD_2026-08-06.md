# Cambios CFD Standard — 6 de agosto de 2026

- Se conservaron dos motores independientes: Multiplier y CFD Standard.
- `LIVE_EXECUTION_PRODUCT=cfd_standard` quedó como valor predeterminado.
- CFD Standard usa Deriv MT5, lotes, margen, bid/ask y SL/TP por precio.
- Se conserva el historial Multiplier existente, incluido el contrato #8235773199.
- El backtest ahora recibe slippage adverso reproducible y comisión configurable.
- La simulación live usa el mismo modelo de costos.
- CFD DEMO almacena spread, slippage, comisión, swap, fee y P&L neto devueltos por MT5.
- Se agregaron reportes automáticos de backtest.
- Se agregó comparación de 100 CFDs DEMO contra 100 operaciones de backtest.
- La cuenta REAL permanece bloqueada.

## Importante

Un Multiplier x100 no se convierte en CFD. Son productos, cuentas y APIs diferentes. El selector elige uno por proceso para evitar exposición duplicada.
- Al cambiar de producto, el libro virtual se reinicia en US$100 sin borrar el historial anterior.
- El dashboard filtra métricas, curva y operaciones por el producto configurado para no mezclar CFDs y Multipliers.
- Cada carga completada del backtester actualiza automáticamente los cuatro reportes en una carpeta `latest_*`.
- La comparación de 100 operaciones propone valores de calibración para spread ATR, slippage ATR y comisión por operación a partir de MT5.
