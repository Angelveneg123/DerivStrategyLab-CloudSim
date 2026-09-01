# Cambios QSL v0.1 — 29 de agosto de 2026

- Se preserva el motor CFD Standard / Deriv MT5 existente.
- Se preserva `hybrid` como estrategia predeterminada: QSL NO toma control del DEMO automáticamente.
- Nueva estrategia registrada: `quant_structure_liquidity_v01`.
- Nuevo módulo: `strategy/quant_structure_liquidity.py`.
- Implementa estructura fractal sin look-ahead, sesgo HTF, PDH/PDL, sweeps con ATR/mecha, MSS/BOS, FVG, OB, premium/discount y filtro de volatilidad.
- Se añadieron `open` reales al pipeline MT5 para que Order Blocks y anatomía de vela usen OHLC completo.
- Se añadieron batch dedicados para backtest y DEMO QSL.
- Riesgo recomendado de laboratorio bajado a 0.5%; límites sugeridos: 2% diario, 3 trades/día, 2 pérdidas consecutivas, DD 12%.
- La cuenta REAL sigue bloqueada.
- El `.env` privado fue excluido del ZIP de entrega. Copia tu `.env` local o rellena `.env.example` sin compartir credenciales.
