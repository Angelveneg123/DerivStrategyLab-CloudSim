"""Configuración central de Deriv Strategy Lab.

El proyecto conserva el token en ``.env`` porque este paquete se entrega para
pruebas privadas. No lo publiques en Git ni lo distribuyas fuera de tu equipo.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


def _env_int(name, default):
    value = os.getenv(name)
    return int(value) if value not in (None, "") else int(default)


def _env_float(name, default):
    value = os.getenv(name)
    return float(value) if value not in (None, "") else float(default)


def _env_optional_int(name):
    value = os.getenv(name)
    return int(value) if value not in (None, "") else None


def _env_int_list(name, default):
    raw = os.getenv(name, default)
    values = []
    for item in raw.split(","):
        item = item.strip()
        if item:
            values.append(int(item))
    if not values:
        raise RuntimeError(f"{name} debe contener al menos un multiplicador")
    return values


DERIV_APP_ID = os.getenv("DERIV_APP_ID")
DERIV_PAT_TOKEN = os.getenv("DERIV_PAT_TOKEN")
DERIV_DEMO_ACCOUNT_ID = os.getenv("DERIV_DEMO_ACCOUNT_ID", "").strip() or None
DERIV_REAL_ACCOUNT_ID = os.getenv("DERIV_REAL_ACCOUNT_ID", "").strip() or None
DERIV_API_BASE = "https://api.derivws.com/trading/v1/options"

if not DERIV_APP_ID:
    raise RuntimeError("Falta DERIV_APP_ID en el archivo .env")
if not DERIV_PAT_TOKEN:
    raise RuntimeError("Falta DERIV_PAT_TOKEN en el archivo .env")

# ---------------------------------------------------------------------------
# Backtesting y gestión de riesgo compartida
# ---------------------------------------------------------------------------
INITIAL_BALANCE = _env_float("INITIAL_BALANCE", 100)
SPREAD_PCT = _env_float("SPREAD_PCT", 0.05)
RIESGO_POR_OPERACION_PCT = _env_float("RIESGO_POR_OPERACION_PCT", 0.5)
MAX_DRAWDOWN_STOP_PCT = _env_float("MAX_DRAWDOWN_STOP_PCT", 12.0)
HTF_GRANULARITY = _env_int("HTF_GRANULARITY", 900)
ATR_PERIOD = _env_int("ATR_PERIOD", 14)
SL_ATR_MULT = _env_float("SL_ATR_MULT", 1.5)
REWARD_RATIO = _env_float("REWARD_RATIO", 2.0)
SPREAD_ATR_FRAC = _env_float("SPREAD_ATR_FRAC", 0.05)

# ---------------------------------------------------------------------------
# Trading conectado. DEMO está habilitado; REAL permanece bloqueado.
# ---------------------------------------------------------------------------
TRADING_MODE = os.getenv("TRADING_MODE", "demo").strip().lower()
DEMO_TRADING_ENABLED = _env_bool("DEMO_TRADING_ENABLED", True)
REAL_TRADING_ENABLED = _env_bool("REAL_TRADING_ENABLED", False)
REAL_TRADING_CONFIRMATION = os.getenv("REAL_TRADING_CONFIRMATION", "")
REAL_TRADING_CONFIRMATION_PHRASE = "ACEPTO_RIESGO_DINERO_REAL"

# Producto de ejecución. Son motores distintos y nunca se ejecutan a la vez.
# - cfd_standard: cuenta Deriv MT5 Standard (lotes, margen, bid/ask, SL/TP por precio).
# - multiplier: cuenta Deriv Options (MULTUP/MULTDOWN, stake y multiplicador).
LIVE_EXECUTION_PRODUCT = os.getenv("LIVE_EXECUTION_PRODUCT", "cfd_standard").strip().lower()
if LIVE_EXECUTION_PRODUCT not in {"cfd_standard", "multiplier"}:
    raise RuntimeError("LIVE_EXECUTION_PRODUCT debe ser 'cfd_standard' o 'multiplier'")

# Se puede escribir el código exacto o un nombre mostrado por Deriv.
# El motor lo resuelve dinámicamente mediante active_symbols.
LIVE_SYMBOL = os.getenv("LIVE_SYMBOL", "Crash 500 Index").strip()
LIVE_GRANULARITY = _env_int("LIVE_GRANULARITY", 60)
LIVE_STRATEGY = os.getenv("LIVE_STRATEGY", "hybrid").strip()
LIVE_CURRENCY = os.getenv("LIVE_CURRENCY", "USD").strip().upper()
LIVE_MULTIPLIERS = _env_int_list("LIVE_MULTIPLIERS", "100,50,20,10")
LIVE_WARMUP_CANDLES = _env_int("LIVE_WARMUP_CANDLES", 4000)
LIVE_HISTORY_BLOCK_SIZE = min(1000, _env_int("LIVE_HISTORY_BLOCK_SIZE", 1000))

# El bot usa como máximo este capital virtual, aunque la cuenta demo tenga más.
LIVE_ALLOCATED_CAPITAL_USD = _env_float("LIVE_ALLOCATED_CAPITAL_USD", 100.0)
LIVE_REINVEST_PROFITS = _env_bool("LIVE_REINVEST_PROFITS", False)
LIVE_MIN_STAKE_USD = _env_float("LIVE_MIN_STAKE_USD", 1.0)
LIVE_MAX_STAKE_USD = _env_float("LIVE_MAX_STAKE_USD", 10.0)
LIVE_MAX_STAKE_PCT = _env_float("LIVE_MAX_STAKE_PCT", 10.0)
LIVE_MAX_SLIPPAGE_PCT = _env_float("LIVE_MAX_SLIPPAGE_PCT", 1.0)

# Deriv MT5 / CFD Standard. El PAT de Options no autentica MT5. Si login,
# password y server quedan vacíos, se usa la cuenta ya iniciada en el terminal.
MT5_TERMINAL_PATH = os.getenv("MT5_TERMINAL_PATH", "").strip() or None
MT5_LOGIN = _env_optional_int("MT5_LOGIN")
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "").strip() or None
MT5_SYMBOL = os.getenv("MT5_SYMBOL", LIVE_SYMBOL).strip()
MT5_MAGIC = _env_int("MT5_MAGIC", 20260806)
MT5_ORDER_COMMENT = os.getenv("MT5_ORDER_COMMENT", "MILLONARIO2026").strip()[:31]
MT5_DEVIATION_POINTS = _env_int("MT5_DEVIATION_POINTS", 50)
MT5_POLL_SECONDS = _env_float("MT5_POLL_SECONDS", 1.0)
MT5_MAX_MARGIN_USD = _env_float("MT5_MAX_MARGIN_USD", LIVE_ALLOCATED_CAPITAL_USD)
MT5_REQUIRE_DERIV_SERVER = _env_bool("MT5_REQUIRE_DERIV_SERVER", True)

# Realismo del backtest CFD. El deslizamiento se aplica en contra de la
# operación tanto al entrar como al salir, usando una semilla reproducible.
BACKTEST_SLIPPAGE_ATR_MAX = _env_float("BACKTEST_SLIPPAGE_ATR_MAX", 0.30)
BACKTEST_RANDOM_SEED = _env_int("BACKTEST_RANDOM_SEED", 20260806)
BACKTEST_COMMISSION_PER_TRADE_USD = _env_float(
    "BACKTEST_COMMISSION_PER_TRADE_USD", 0.0
)
BACKTEST_REPORTS_DIR = os.getenv(
    "BACKTEST_REPORTS_DIR", str(PROJECT_ROOT / "reports" / "backtests")
)

# Barreras operativas independientes de la señal de estrategia.
LIVE_DAILY_LOSS_LIMIT_PCT = _env_float("LIVE_DAILY_LOSS_LIMIT_PCT", 2.0)
LIVE_MAX_TRADES_PER_DAY = _env_int("LIVE_MAX_TRADES_PER_DAY", 3)
LIVE_MAX_CONSECUTIVE_LOSSES = _env_int("LIVE_MAX_CONSECUTIVE_LOSSES", 2)
LIVE_COOLDOWN_CANDLES = _env_int("LIVE_COOLDOWN_CANDLES", 1)
LIVE_MAX_OPEN_CONTRACTS = 1
LIVE_BLOCK_IF_OTHER_POSITIONS = _env_bool("LIVE_BLOCK_IF_OTHER_POSITIONS", True)
LIVE_MAX_SIGNAL_AGE_SECONDS = _env_int(
    "LIVE_MAX_SIGNAL_AGE_SECONDS", LIVE_GRANULARITY * 2
)

# Conexión, recuperación y observabilidad.
DERIV_HTTP_TIMEOUT_SECONDS = _env_int("DERIV_HTTP_TIMEOUT_SECONDS", 20)
DERIV_WS_TIMEOUT_SECONDS = _env_int("DERIV_WS_TIMEOUT_SECONDS", 45)
DERIV_REQUEST_TIMEOUT_SECONDS = _env_int("DERIV_REQUEST_TIMEOUT_SECONDS", 25)
DERIV_HEARTBEAT_SECONDS = _env_int("DERIV_HEARTBEAT_SECONDS", 25)
DERIV_MAX_RECONNECT_ATTEMPTS = _env_int("DERIV_MAX_RECONNECT_ATTEMPTS", 8)
DASHBOARD_REFRESH_SECONDS = _env_int("DASHBOARD_REFRESH_SECONDS", 5)


def validate_trading_mode(mode=None):
    """Valida los candados antes de conectar una cuenta de trading."""
    selected = (mode or TRADING_MODE).strip().lower()
    if selected not in {"demo", "real"}:
        raise RuntimeError("TRADING_MODE solo puede ser 'demo' o 'real'")

    if selected == "demo":
        if not DEMO_TRADING_ENABLED:
            raise RuntimeError("DEMO_TRADING_ENABLED está desactivado")
        return selected

    if not REAL_TRADING_ENABLED:
        raise RuntimeError(
            "Trading REAL bloqueado. Activa REAL_TRADING_ENABLED solo después "
            "de validar suficientemente la cuenta demo."
        )
    if REAL_TRADING_CONFIRMATION != REAL_TRADING_CONFIRMATION_PHRASE:
        raise RuntimeError(
            "Falta la frase exacta de confirmación para dinero real."
        )
    return selected
