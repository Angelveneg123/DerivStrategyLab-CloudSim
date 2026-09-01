# Cambios — Producción DEMO

## Núcleo unificado

Se agregó `strategy/registry.py`. Backtester, live simulation y bot conectado invocan la misma estrategia y los mismos parámetros registrados.

## Cuenta DEMO conectada

Se agregó un motor que:

- conecta usando el OTP de la cuenta seleccionada;
- resuelve el símbolo mediante `active_symbols`;
- descarga contexto histórico;
- escucha ticks y construye velas cerradas;
- pide una propuesta `MULTUP` o `MULTDOWN`;
- compra por `proposal_id`;
- monitorea `proposal_open_contract`;
- persiste apertura, cierre, P&L y eventos.

## Asignación de US$100

La asignación se guarda en `live_bot_state`. Reiniciar el proceso no vuelve a regalar otros US$100 ni ignora las pérdidas anteriores. Por defecto los beneficios no incrementan el capital base de riesgo.

## Dashboard

Nueva ruta `/live` y APIs de solo lectura:

- `/api/live/status`
- `/api/live/trades`
- `/api/live/equity`
- `/api/live/events`

## Protecciones

- cuenta REAL bloqueada por doble confirmación;
- una posición como máximo;
- límite de stake;
- pérdida diaria máxima;
- operaciones diarias máximas;
- pérdidas consecutivas máximas;
- drawdown máximo;
- cooldown;
- bloqueo si hay posiciones externas;
- recuperación de contrato persistido;
- heartbeat, reconexión exponencial y log rotativo.

## Compatibilidad

El `.env` original y su token de prueba se conservaron. Se añadieron variables sin imprimir ni registrar el token.

## Persistencia antes de comprar

La intención de compra se escribe en SQLite antes de llamar al endpoint `buy`. Los estados `proposal_ready`, `buy_submitted` y `purchase_unknown` permiten reconciliar el proceso después de una caída y evitan abrir una segunda operación cuando el resultado remoto es incierto.

Se agregó una prueba local que simula una desconexión durante `buy`, confirma el circuit breaker y recupera el contrato desde `portfolio` al reiniciar.

## Dashboard sin CDN

La gráfica del balance virtual ahora usa Canvas nativo. La vista DEMO no depende de Chart.js, Google Fonts ni otros recursos externos.
