# MILLONARIO 2026 — CFD Standard DEMO

El proyecto conserva un solo núcleo de señales y dos motores de ejecución **separados**:

- `cfd_standard` — Deriv MT5 Standard: lotes, margen, bid/ask, SL/TP por precio.
- `multiplier` — Deriv Options: contratos `MULTUP`/`MULTDOWN`, stake y multiplicador x100.

El valor predeterminado ahora es:

```env
LIVE_EXECUTION_PRODUCT=cfd_standard
```

Nunca se ejecutan los dos motores a la vez. El historial anterior de Multiplier se conserva, pero las nuevas posiciones CFD se registran con `execution_product=cfd_standard`.

## Diferencia esencial

### Multiplier x100

- Se autentica con App ID y PAT de Deriv Options.
- La entrada es un contrato `MULTUP` o `MULTDOWN`.
- Usa stake, por ejemplo US$10, y multiplicador x100.
- SL/TP se expresan como importes monetarios del contrato.
- Tiene `contract_id`.

### CFD Standard

- Se ejecuta mediante el terminal Deriv MetaTrader 5.
- Usa login MT5 o una sesión ya abierta en el terminal.
- La posición se expresa en lotes, no en stake.
- El broker calcula margen según el símbolo y la cuenta.
- Compra al ask y vende al bid; el spread es real.
- SL/TP son niveles de precio.
- Tiene ticket de orden, deal y posición.
- No existe un parámetro “Multiplier x100”. El apalancamiento de la cuenta MT5 es otra cosa.

## Antes de iniciar CFD Standard DEMO

1. Instala Deriv MetaTrader 5 en Windows.
2. Crea o selecciona una cuenta **Demo Standard** en Deriv MT5.
3. Abre el terminal e inicia sesión en esa cuenta DEMO.
4. Comprueba que el servidor/empresa mostrado sea de Deriv.
5. Activa **Algo Trading / AutoTrading**.
6. Añade Crash 500 a Market Watch.
7. Mantén cerradas otras posiciones en esa cuenta mientras pruebas el bot.

MetaTrader expone login, servidor y modo DEMO/REAL, pero no garantiza que Python pueda leer la etiqueta comercial “Standard”. Por eso debes seleccionar manualmente la cuenta Standard correcta en el terminal.

## Instalación

```bat
INSTALAR_DEPENDENCIAS.bat
```

Instala también el paquete Python `MetaTrader5`. El bot CFD solo puede ejecutarse en el mismo Windows donde esté instalado y abierto el terminal MT5.

## Configuración CFD en `.env`

Puedes dejar las credenciales vacías y utilizar la sesión abierta en el terminal:

```env
LIVE_EXECUTION_PRODUCT=cfd_standard
TRADING_MODE=demo
MT5_TERMINAL_PATH=
MT5_LOGIN=
MT5_PASSWORD=
MT5_SERVER=
MT5_SYMBOL=Crash 500 Index
MT5_MAGIC=20260806
MT5_MAX_MARGIN_USD=100
LIVE_ALLOCATED_CAPITAL_USD=100
```

También puedes rellenar `MT5_LOGIN`, `MT5_PASSWORD` y `MT5_SERVER`. Es preferible no compartir ese `.env`.

El PAT conservado en el proyecto se utiliza únicamente cuando seleccionas `multiplier`; no autentica una cuenta MT5 CFD.

## Validación sin enviar orden

Con MT5 abierto y conectado a la cuenta Demo Standard:

```bat
VERIFICAR_DEMO.bat
```

Equivale a:

```bat
python run_demo_trader.py --product cfd_standard --mode demo --check
```

La prueba consulta cuenta, símbolo, velas, bid/ask, especificaciones de volumen, riesgo, margen y ejecuta `order_check`. No llama a `order_send`.

Debe terminar con:

```json
"purchase_sent": false
```

## Iniciar CFD Standard DEMO

Primera ventana:

```bat
INICIAR_DASHBOARD.bat
```

Abre:

```text
http://127.0.0.1:5000/live
```

Segunda ventana:

```bat
INICIAR_BOT_DEMO.bat
```

El bot espera una señal nueva. Cuando exista una entrada válida:

1. Lee bid/ask real desde MT5.
2. Calcula SL/TP por ATR.
3. Calcula el lote con `order_calc_profit`.
4. Comprueba el margen con `order_calc_margin`.
5. Ejecuta `order_check`.
6. Persiste la intención de orden.
7. Envía `order_send`.
8. Registra ticket, lote, margen, spread y slippage.
9. Al cerrar, registra profit, comisión, swap, fee y P&L neto reales.

## Protección de US$100

`LIVE_ALLOCATED_CAPITAL_USD=100` es un libro virtual de riesgo. El bot no calcula el lote con todo el balance demo. Además:

- riesgo por operación configurado: 2%;
- margen máximo permitido: US$100;
- una posición como máximo;
- pérdida diaria máxima: 5%;
- máximo 20 operaciones diarias;
- pausa después de 4 pérdidas consecutivas;
- circuit breaker por drawdown;
- bloqueo si detecta posiciones externas.

Si el lote mínimo de Crash 500 supera el riesgo de US$2 o el margen permitido, el bot **no fuerza la operación**.

## Usar temporalmente Multiplier x100

No cambies código. Ejecuta:

```bat
python run_demo_trader.py --product multiplier --mode demo --check
python run_demo_trader.py --product multiplier --mode demo
```

O cambia:

```env
LIVE_EXECUTION_PRODUCT=multiplier
```

Eso vuelve al motor anterior de contratos Options. No lo confundas con CFD Standard.

## Backtest con slippage y reportes

```bat
BACKTEST_CON_REPORTE.bat
```

Cada ejecución crea una carpeta en `reports/backtests` con:

- `equity_curve.png`
- `trades.csv`
- `summary.json`
- `metrics.json`

Modelo configurado:

```env
BACKTEST_SLIPPAGE_ATR_MAX=0.30
BACKTEST_RANDOM_SEED=20260806
BACKTEST_COMMISSION_PER_TRADE_USD=0
```

El slippage se aplica en contra de la operación tanto al entrar como al salir. La semilla hace que el resultado sea reproducible.

El spread del histórico sigue modelado con `SPREAD_ATR_FRAC`, porque las velas OHLC antiguas no contienen bid y ask. En CFD DEMO el dashboard guarda el spread real observado en cada entrada. La comisión, swap y fee se leen del historial de deals de MT5 al cerrar.

## Comparar 100 DEMO contra 100 backtest

Después de cerrar al menos 100 posiciones CFD Standard DEMO:

```bat
COMPARAR_100_DEMO_VS_BACKTEST.bat
```

Genera un JSON en `reports/validation`. Compara muestras de 100 operaciones y reporta:

- win rate;
- profit factor;
- retorno;
- drawdown;
- beneficio promedio;
- spread y slippage observados;
- comisión, swap y fee reales.

Los contratos Multiplier anteriores no se incluyen en esta prueba. Si todavía no existen 100 CFDs cerrados, el reporte indica cuántos faltan.

## Live simulation

```bat
python live_simulation.py "Crash 500 Index" 60 hybrid
```

No compra. Aplica el mismo slippage adverso y comisión configurada, además del spread ATR estimado.

## Cambio futuro de índice sintético

En CFD Standard cambia:

```env
MT5_SYMBOL=Crash 500 Index
```

El nombre debe coincidir con un símbolo disponible en el Market Watch del terminal. El motor busca por nombre y descripción y valida volumen mínimo, step, stops y margen del símbolo seleccionado.

## Cuenta REAL

Permanece bloqueada:

```env
REAL_TRADING_ENABLED=false
REAL_TRADING_CONFIRMATION=
```

Antes de habilitarla, exige como mínimo:

1. 100 o más CFDs DEMO cerrados.
2. Concordancia del dashboard con MT5.
3. Reinicios probados con posición abierta.
4. Ninguna orden duplicada después de desconexión.
5. Costos reales incorporados en la calibración.
6. Comparación demo/backtest revisada, no solo un resultado aislado.
7. Prueba con el lote mínimo y capital que realmente usarás.

El software puede reducir errores operativos, pero no garantiza rentabilidad.

## Accesos directos explícitos

- `VERIFICAR_CFD_STANDARD_DEMO.bat` — valida MT5 sin enviar orden.
- `INICIAR_CFD_STANDARD_DEMO.bat` — opera únicamente CFDs Standard DEMO.
- `VERIFICAR_MULTIPLIER_DEMO.bat` — valida Options sin comprar.
- `INICIAR_MULTIPLIER_DEMO.bat` — opera únicamente Multiplier DEMO.

Los archivos genéricos `VERIFICAR_DEMO.bat` e `INICIAR_BOT_DEMO.bat` usan el producto definido en `.env`, que actualmente es `cfd_standard`.
