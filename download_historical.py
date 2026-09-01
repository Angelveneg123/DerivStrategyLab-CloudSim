"""
Módulo 2: descarga de datos históricos, con paginación.

Deriv limita cada petición a 1000 velas, así que para juntar una muestra
más grande pedimos varios bloques seguidos: el primero trae las velas
más recientes, y cada bloque siguiente pide las 1000 velas justo
anteriores a las que ya tenemos. Se puede correr varias veces sin
duplicar datos: `save_candles` ignora las velas que ya existen.
"""

import sys
import time

from config import DERIV_APP_ID, DERIV_PAT_TOKEN, DERIV_API_BASE
from api.deriv import DerivClient
from database.database import create_tables, save_candles, count_candles

# --- Configuración de esta descarga -----------------------------------
# Puedes elegir el símbolo desde la terminal sin tocar este archivo:
#   python download_historical.py R_75
#   python download_historical.py frxEURUSD 300   (300 = velas de 5 min)
SYMBOL_QUERY = sys.argv[1] if len(sys.argv) > 1 else "Crash 500 Index"
GRANULARITY = int(sys.argv[2]) if len(sys.argv) > 2 else 60
VELAS_POR_BLOQUE = 1000  # Límite real de Deriv por petición.
BLOQUES = 120
# 120 bloques x 1000 = hasta 120,000 velas (~84 días).
# ------------------------------------------------------------------------


def main():
    print("=" * 40)
    print(" DERIV STRATEGY LAB — Módulo 2 ")
    print("=" * 40)

    create_tables()

    deriv = DerivClient(
        app_id=DERIV_APP_ID,
        access_token=DERIV_PAT_TOKEN,
        api_base=DERIV_API_BASE,
    )

    deriv.connect(account_type="demo")
    symbol_info = deriv.resolve_symbol(SYMBOL_QUERY)
    symbol_code = deriv.symbol_code(symbol_info)
    symbol_name = deriv.symbol_name(symbol_info)
    print(f"Activo resuelto: {symbol_name} ({symbol_code})")

    end = "latest"
    total_recibidas = 0

    for bloque in range(1, BLOQUES + 1):
        print(
            f"Bloque {bloque}/{BLOQUES} — pidiendo {VELAS_POR_BLOQUE} velas "
            f"(end={end})..."
        )

        try:
            candles = deriv.get_candles(
                symbol_code, granularity=GRANULARITY, count=VELAS_POR_BLOQUE, end=end
            )
        except Exception as error:
            print(
                f"  ✗ Falló este bloque ({error}). Se detiene la descarga aquí, "
                f"pero lo ya descargado quedó guardado."
            )
            break

        if not candles:
            print("Deriv no devolvió más velas — llegamos al límite de historial.")
            break

        save_candles(symbol_code, GRANULARITY, candles)
        total_recibidas += len(candles)
        print(f"  ✓ {len(candles)} velas recibidas y guardadas")

        # El siguiente bloque debe terminar justo antes de la vela más
        # antigua que acabamos de recibir.
        epoch_mas_antiguo = candles[0]["epoch"]
        end = epoch_mas_antiguo - GRANULARITY

        # Pausa corta para no saturar la conexión con Deriv.
        time.sleep(1)

    total_guardadas = count_candles(symbol_code, GRANULARITY)
    print("-" * 40)
    print(f"Total recibido en esta corrida: {total_recibidas} velas")
    print(f"Total acumulado en la base de datos: {total_guardadas} velas")

    deriv.disconnect()


if __name__ == "__main__":
    main()
