# Reporte de validación — Crash 500 DEMO

Fecha de paquete: 5 de agosto de 2026.

## Alcance validado localmente

- Compilación completa del código Python.
- Estrategia compartida entre backtester, live simulation y motor conectado.
- Histórico incluido de Crash 500 en M1.
- Cálculo de riesgo con asignación persistente de US$100.
- Límite de stake de US$10 y riesgo monetario derivado del ATR.
- Flujo simulado de propuesta → compra → monitoreo → cierre.
- Soporte de BUY/MULTUP y SELL/MULTDOWN.
- Persistencia de P&L y actualización de balance virtual.
- Circuit breakers diarios, por drawdown y pérdidas consecutivas.
- Recuperación simulada después de una desconexión durante `buy`.
- Dashboard y endpoints de solo lectura.
- Candado de cuenta REAL.

## Pruebas ejecutadas

```text
python PRUEBAS_CORRECCIONES.py
python PRUEBAS_PRODUCCION_DEMO.py
python -m compileall -q .
```

Todas finalizaron correctamente en el entorno de construcción.

## Validación que debe ejecutarse en la PC de operación

El entorno de construcción no pudo resolver por DNS el host de Deriv, por lo que no fue posible validar aquí el PAT, la cuenta o una propuesta real. Ejecuta:

```text
VERIFICAR_DEMO.bat
```

La validación consulta la cuenta DEMO, saldo, símbolo, histórico, contratos y una propuesta, pero no compra. Debe finalizar mostrando:

```json
"purchase_sent": false
```

Solo después inicia:

```text
INICIAR_DASHBOARD.bat
INICIAR_BOT_DEMO.bat
```

El bot no fuerza una operación al arrancar; espera la siguiente señal válida de la estrategia.

## Estado de preparación

- **Backtesting:** disponible.
- **Live simulation:** disponible.
- **Trading conectado DEMO:** implementación completa, pendiente de la validación de red/PAT en la PC de operación.
- **Cuenta REAL:** implementación preparada, pero bloqueada intencionalmente hasta completar forward testing DEMO.
