"""Compara la estrategia operativa actual contra QSL v0.1 en el mismo dataset."""
import json
from database.database import get_candles
from strategy.registry import run_registered_backtest

SYMBOL = "CRASH500"
GRANULARITY = 60
rows = get_candles(SYMBOL, GRANULARITY)
if len(rows) < 220:
    raise SystemExit("Histórico insuficiente")
epochs=[r[0] for r in rows]; opens=[r[1] for r in rows]
highs=[r[2] for r in rows]; lows=[r[3] for r in rows]; closes=[r[4] for r in rows]

out={}
for sid in ("hybrid", "quant_structure_liquidity_v01"):
    result=run_registered_backtest(sid,epochs,highs,lows,closes,GRANULARITY,opens=opens)
    out[sid]={"metrics":result["metrics"],"trades":len(result["trades"])}
print(json.dumps(out,indent=2,ensure_ascii=False))
