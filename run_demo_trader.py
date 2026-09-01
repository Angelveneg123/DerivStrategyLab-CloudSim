"""Entrada principal del bot conectado.

``cfd_standard`` y ``multiplier`` son productos distintos. La selección se
realiza con ``LIVE_EXECUTION_PRODUCT`` o ``--product`` y solo se inicia un
motor por proceso.
"""

import argparse
import json

from config import (
    LIVE_ALLOCATED_CAPITAL_USD,
    LIVE_EXECUTION_PRODUCT,
    LIVE_GRANULARITY,
    LIVE_STRATEGY,
    LIVE_SYMBOL,
    MT5_SYMBOL,
)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Trading conectado a Deriv: CFD Standard (MT5) o Multiplier"
    )
    parser.add_argument("--mode", choices=("demo", "real"), default="demo")
    parser.add_argument(
        "--product",
        choices=("cfd_standard", "multiplier"),
        default=LIVE_EXECUTION_PRODUCT,
        help="Motor de ejecución. Nunca se ejecutan ambos a la vez.",
    )
    parser.add_argument(
        "--symbol",
        default=None,
        help="Símbolo del broker. Por defecto usa MT5_SYMBOL o LIVE_SYMBOL.",
    )
    parser.add_argument("--granularity", type=int, default=LIVE_GRANULARITY)
    parser.add_argument("--strategy", default=LIVE_STRATEGY)
    parser.add_argument("--allocation", type=float, default=LIVE_ALLOCATED_CAPITAL_USD)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Valida cuenta, símbolo, riesgo y orden sin enviarla.",
    )
    parser.add_argument(
        "--reset-allocation",
        action="store_true",
        help="Reinicia el libro virtual; falla si existe una posición abierta.",
    )
    return parser


def _build_engine(args):
    if args.product == "cfd_standard":
        from live_trading.mt5_engine import MT5CFDTradingEngine

        cls = MT5CFDTradingEngine
        symbol = args.symbol or MT5_SYMBOL
    else:
        from live_trading.engine import LiveTradingEngine

        cls = LiveTradingEngine
        symbol = args.symbol or LIVE_SYMBOL
    return cls(
        mode=args.mode,
        symbol_query=symbol,
        granularity=args.granularity,
        strategy_id=args.strategy,
        allocation_capital=args.allocation,
        check_only=args.check,
    ), symbol


def main():
    args = build_parser().parse_args()
    engine, symbol = _build_engine(args)
    if args.reset_allocation:
        print(json.dumps(engine.reset_allocation(), indent=2, ensure_ascii=False))
        if not args.check:
            return
    if args.check:
        try:
            result = engine.check_connection()
        except Exception as exc:
            result = {
                "ok": False,
                "mode": args.mode,
                "execution_product": args.product,
                "symbol_query": symbol,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "purchase_sent": False,
            }
            print(json.dumps(result, indent=2, ensure_ascii=False))
            raise SystemExit(1)
        result["ok"] = True
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    print("=" * 72)
    print(" DERIV STRATEGY LAB — TRADING CONECTADO ")
    print("=" * 72)
    print(f"Modo: {args.mode.upper()} | Producto: {args.product}")
    print(f"Activo solicitado: {symbol} | Estrategia: {args.strategy}")
    print(f"Asignación máxima del libro de riesgo: ${args.allocation:.2f}")
    if args.product == "cfd_standard":
        print("Ejecución: Deriv MT5 Standard, lotes y margen; SL/TP por precio.")
    else:
        print("Ejecución: Deriv Options, contratos MULTUP/MULTDOWN.")
    print("Detener con Ctrl+C. El SL/TP queda alojado en el broker.\n")
    engine.run_forever()


if __name__ == "__main__":
    main()
