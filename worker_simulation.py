
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from random import Random

STATE_DIR = Path(__file__).resolve().parent / "cloud_sim"
STATE_FILE = STATE_DIR / "state.json"
TRADES_FILE = STATE_DIR / "trades.jsonl"

SYMBOL = os.getenv("SIM_SYMBOL", "CRASH500")
STRATEGY = os.getenv("SIM_STRATEGY", "hybrid_long_only")
START_BALANCE = float(os.getenv("SIM_START_BALANCE", "100"))
POLL_SECONDS = int(os.getenv("SIM_POLL_SECONDS", "20"))

rng = Random(20260830)

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "mode": "CFD_STANDARD_SIMULATION",
        "symbol": SYMBOL,
        "strategy": STRATEGY,
        "running": True,
        "start_balance": START_BALANCE,
        "balance": START_BALANCE,
        "equity": START_BALANCE,
        "pnl": 0.0,
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "max_drawdown_pct": 0.0,
        "open_trade": None,
        "last_signal": "HOLD",
        "last_price": None,
        "updated_at": now_iso(),
        "note": "Simulación educativa: no envía órdenes a MT5."
    }

def save_state(state):
    STATE_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

def append_trade(trade):
    with TRADES_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(trade, ensure_ascii=False) + "\n")

def simulate_step(state):
    # Placeholder cloud-safe simulation loop.
    # Keeps process alive and dashboard updated without sending broker orders.
    # Real market-data integration can later replace this block.
    state["updated_at"] = now_iso()
    state["running"] = True
    state["mode"] = "CFD_STANDARD_SIMULATION"
    state["symbol"] = SYMBOL
    state["strategy"] = STRATEGY
    state["note"] = (
        "Simulación activa en la nube. No envía órdenes. "
        "Conecta aquí tu fuente de datos de Crash 500 cuando esté disponible."
    )
    return state

def main():
    state = load_state()
    while True:
        try:
            state = simulate_step(state)
            save_state(state)
            time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            state["running"] = False
            state["updated_at"] = now_iso()
            save_state(state)
            break
        except Exception as exc:
            state["running"] = False
            state["last_error"] = str(exc)
            state["updated_at"] = now_iso()
            save_state(state)
            time.sleep(max(10, POLL_SECONDS))

if __name__ == "__main__":
    main()
