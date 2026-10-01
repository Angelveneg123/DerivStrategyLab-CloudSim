# MT5 DEMO MIRROR

Modo experimental separado del motor `live_trading/mt5_engine.py`.

## Objetivo

Ejecutar en **Deriv MT5 DEMO** la lógica que actualmente usa el CloudSim de Crash 500:

- estrategia: `hybrid_long_only`
- velas: 60 segundos
- HTF: 15 minutos
- ATR: 14
- riesgo por operación: 0.5%
- stop-loss: 1.5 × ATR
- take-profit: 2 × la distancia del stop (RR 1:2)
- drawdown máximo: 12%
- una posición simultánea

No aplica los candados adicionales del motor live normal:
`LIVE_MAX_TRADES_PER_DAY`, `LIVE_MAX_CONSECUTIVE_LOSSES`,
`LIVE_DAILY_LOSS_LIMIT_PCT` ni `LIVE_COOLDOWN_CANDLES`.

## Fuente de señal

La señal sale del **WebSocket público de Deriv**, igual que
`worker_simulation.py`. Esto evita cambiar la estrategia por usar velas de MT5.

## Ejecución

La señal es la misma, pero la ejecución es real de DEMO:

1. aparece BUY en `hybrid_long_only`;
2. se toma el ATR(14) de la vela pública cerrada;
3. la entrada real es el `ask` actual de MT5;
4. SL = entrada real - 1.5 × ATR;
5. TP = entrada real + 2 × distancia SL;
6. el lote se calcula con `order_calc_profit()` para aproximar riesgo 0.5%;
7. se ejecuta con SL/TP alojados en el broker.

**No se añade el slippage aleatorio del simulador**, porque MT5 ya proporciona
spread y ejecución observables. Así se puede medir la diferencia real.

## Regla estricta de SL/TP

Este modo **NO ensancha ni mueve** SL/TP para satisfacer una restricción MT5.
Si el broker no acepta los niveles exactos, la señal queda registrada como
`SIGNAL_REJECTED` y no se opera.

También se rechaza la entrada si el lote mínimo de MT5 supera el 0.5% de riesgo.

## Seguridad

El código:

- rechaza cualquier cuenta que no sea DEMO;
- opcionalmente exige que servidor/compañía contengan `Deriv`;
- exige Algo Trading y API Python habilitados;
- usa `MT5_MIRROR_MAGIC=20261001`, separado del motor live normal;
- bloquea entradas si existen posiciones ajenas al mirror;
- nunca contiene opción `--real`.

Usar una **cuenta DEMO dedicada** al experimento.

## Instalación local/AWS Windows

Una vez clonado el repositorio:

```powershell
python -m pip install -r requirements.txt
```

Copiar `.env.mt5_demo_mirror.example` a `.env` o integrar sus variables en el
`.env` privado del servidor. Completar:

```env
MT5_LOGIN=
MT5_PASSWORD=
MT5_SERVER=
MT5_TERMINAL_PATH=
```

No subir `.env` a GitHub.

## Prueba obligatoria antes de operar

Con MT5 abierto e iniciado en la cuenta DEMO:

```powershell
python run_mt5_demo_mirror.py --check
```

Debe devolver:

```json
{
  "ok": true,
  "purchase_sent": false,
  "mode": "MT5_DEMO_MIRROR"
}
```

`--check` ejecuta `order_check` pero **no `order_send`**.

## Iniciar experimento

Solo después de aprobar el check:

```powershell
python run_mt5_demo_mirror.py
```

O:

```text
INICIAR_MT5_DEMO_MIRROR.bat
```

Los archivos del experimento se guardan en `mirror_data/`:

- `state.json`
- `trades.jsonl`
- `events.jsonl`

## Importante para comparar con Railway

Railway/CloudSim NO se modifica. Debe seguir ejecutándose como grupo de control.

La comparación posterior debe distinguir:

- misma señal / distinta ejecución;
- entrada simulada vs ask real MT5;
- SL/TP por la misma fórmula;
- riesgo virtual vs riesgo permitido por lotes MT5;
- slippage modelado vs slippage observado;
- operación rechazada por restricciones reales del broker.
