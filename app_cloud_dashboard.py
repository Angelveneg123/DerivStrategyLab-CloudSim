import csv
import io
import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, Response, jsonify, render_template_string, request

DATA_ROOT = Path(os.getenv("SIM_DATA_DIR", "/data/cloud_sim"))
if not DATA_ROOT.exists():
    DATA_ROOT = Path(__file__).resolve().parent / "cloud_sim"

STATE_FILE = DATA_ROOT / "state.json"
TRADES_FILE = DATA_ROOT / "trades.jsonl"
EQUITY_FILE = DATA_ROOT / "equity.jsonl"

app = Flask(__name__)
LOCAL_TZ = ZoneInfo("America/Managua")
UTC_TZ = ZoneInfo("UTC")


def read_jsonl(path, limit=50):
    if not path.exists():
        return []
    rows = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except Exception:
        return []
    return rows[-limit:]


def read_all_jsonl(path):
    if not path.exists():
        return []
    rows = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except Exception:
        return []
    return rows


def _f(v, default=0.0):
    try:
        n = float(v)
        return n if math.isfinite(n) else default
    except Exception:
        return default


def _local_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC_TZ)
        return dt.astimezone(LOCAL_TZ)
    except Exception:
        return None


def _normalized_trades():
    rows = []
    for raw in read_all_jsonl(TRADES_FILE):
        dt = _local_dt(raw.get("exit_time") or raw.get("time") or raw.get("timestamp"))
        if not dt:
            continue
        t = dict(raw)
        t["_dt"] = dt
        t["_date"] = dt.date().isoformat()
        t["_p"] = _f(raw.get("profit"))
        t["_bal"] = _f(raw.get("balance_after"))
        t["_entry_dt"] = _local_dt(raw.get("entry_time"))
        if t["_entry_dt"]:
            t["_duration_seconds"] = max(0, int((dt - t["_entry_dt"]).total_seconds()))
        else:
            t["_duration_seconds"] = None
        rows.append(t)
    rows.sort(key=lambda x: x["_dt"])
    return rows


def _duration_text(seconds):
    if seconds is None:
        return "-"
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _public_trade(t):
    """Devuelve un trade listo para API, tabla y exportaciones."""
    row = {k: v for k, v in t.items() if not k.startswith("_")}
    row["entry_time_local"] = (
        t["_entry_dt"].strftime("%Y-%m-%d %H:%M:%S") if t.get("_entry_dt") else None
    )
    row["exit_time_local"] = t["_dt"].strftime("%Y-%m-%d %H:%M:%S")
    row["trade_date_local"] = t["_date"]
    row["duration_seconds"] = t.get("_duration_seconds")
    row["duration"] = _duration_text(t.get("_duration_seconds"))
    return row


def _filter_trade_rows(trades, start=None, end=None, reason="all", direction="all", outcome="all"):
    rows = trades
    if start:
        rows = [t for t in rows if t["_dt"].date() >= start]
    if end:
        rows = [t for t in rows if t["_dt"].date() <= end]

    reason = (reason or "all").lower()
    direction = (direction or "all").upper()
    outcome = (outcome or "all").lower()

    if reason != "all":
        rows = [t for t in rows if str(t.get("exit_reason", "")).lower() == reason]
    if direction != "ALL":
        rows = [t for t in rows if str(t.get("direction", "")).upper() == direction]
    if outcome == "win":
        rows = [t for t in rows if t["_p"] > 0]
    elif outcome == "loss":
        rows = [t for t in rows if t["_p"] < 0]

    return rows


def _date_range(mode, trades, start_arg="", end_arg=""):
    today = datetime.now(LOCAL_TZ).date()
    mode = (mode or "all").lower()
    if mode == "today":
        return today, today
    if mode in {"7", "15", "30"}:
        return today - timedelta(days=int(mode) - 1), today
    if mode == "custom":
        try:
            start = datetime.strptime(start_arg, "%Y-%m-%d").date()
            end = datetime.strptime(end_arg, "%Y-%m-%d").date()
        except Exception:
            return today, today
        return (end, start) if start > end else (start, end)

    dates = [t["_dt"].date() for t in trades]
    return (min(dates), max(dates)) if dates else (today, today)


def _pf(gp, gl):
    if gl <= 0:
        return None if gp <= 0 else 999999.0
    return gp / gl


def _stats(trades):
    trades = sorted(trades, key=lambda x: x["_dt"])
    total = len(trades)
    wins = sum(t["_p"] > 0 for t in trades)
    losses = sum(t["_p"] < 0 for t in trades)
    gp = sum(t["_p"] for t in trades if t["_p"] > 0)
    gl = abs(sum(t["_p"] for t in trades if t["_p"] < 0))
    net = sum(t["_p"] for t in trades)
    start = (trades[0]["_bal"] - trades[0]["_p"]) if trades else 0.0
    end = trades[-1]["_bal"] if trades else 0.0

    peak = start
    max_dd = 0.0
    for t in trades:
        bal = t["_bal"]
        if bal > peak:
            peak = bal
        if peak > 0:
            max_dd = max(max_dd, (peak - bal) / peak * 100.0)

    mw = ml = cw = cl = 0
    for t in trades:
        if t["_p"] > 0:
            cw += 1
            cl = 0
            mw = max(mw, cw)
        elif t["_p"] < 0:
            cl += 1
            cw = 0
            ml = max(ml, cl)
        else:
            cw = cl = 0

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / total * 100.0) if total else 0.0,
        "gross_profit": gp,
        "gross_loss": gl,
        "net": net,
        "start_balance": start,
        "end_balance": end,
        "profitability_pct": (net / start * 100.0) if start else 0.0,
        "profit_factor": _pf(gp, gl),
        "max_drawdown_pct": max_dd,
        "avg_win": (gp / wins) if wins else 0.0,
        "avg_loss": (gl / losses) if losses else 0.0,
        "best_trade": max((t["_p"] for t in trades), default=0.0),
        "worst_trade": min((t["_p"] for t in trades), default=0.0),
        "max_win_streak": mw,
        "max_loss_streak": ml,
    }


def _build_report():
    trades = _normalized_trades()
    today = datetime.now(LOCAL_TZ).date()
    mode = (request.args.get("range") or "15").lower()

    if mode == "today":
        start = end = today
    elif mode in {"7", "15", "30"}:
        end = today
        start = today - timedelta(days=int(mode) - 1)
    elif mode == "custom":
        try:
            start = datetime.strptime(request.args.get("start", ""), "%Y-%m-%d").date()
            end = datetime.strptime(request.args.get("end", ""), "%Y-%m-%d").date()
        except Exception:
            start = end = today
        if start > end:
            start, end = end, start
    else:
        dates = [t["_dt"].date() for t in trades]
        start = min(dates) if dates else today
        end = max(dates) if dates else today

    filt = [t for t in trades if start <= t["_dt"].date() <= end]

    by = {}
    for t in filt:
        by.setdefault(t["_date"], []).append(t)

    daily = []
    for d in sorted(by):
        st = _stats(by[d])
        st["date"] = d
        st["status"] = "positive" if st["net"] > 0 else ("negative" if st["net"] < 0 else "breakeven")
        daily.append(st)

    overall = _stats(filt)
    overall.update({
        "days": len(daily),
        "positive_days": sum(d["net"] > 0 for d in daily),
        "negative_days": sum(d["net"] < 0 for d in daily),
        "avg_daily_net": sum(d["net"] for d in daily) / len(daily) if daily else 0.0,
        "best_day": max(daily, key=lambda x: x["net"], default=None),
        "worst_day": min(daily, key=lambda x: x["net"], default=None),
    })

    return {
        "timezone": "America/Managua",
        "range": mode,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "generated_at": datetime.now(LOCAL_TZ).isoformat(),
        "overall": overall,
        "daily": daily,
    }


def _build_trade_report():
    """Reporte independiente para el historial; no modifica el reporte de rendimiento."""
    trades = _normalized_trades()
    mode = (request.args.get("range") or "all").lower()
    start, end = _date_range(
        mode,
        trades,
        request.args.get("start", ""),
        request.args.get("end", ""),
    )
    filtered = _filter_trade_rows(
        trades,
        start,
        end,
        request.args.get("reason", "all"),
        request.args.get("direction", "all"),
        request.args.get("outcome", "all"),
    )
    return {
        "timezone": "America/Managua",
        "range": mode,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "generated_at": datetime.now(LOCAL_TZ).isoformat(),
        "overall": _stats(filtered),
        "trades": [_public_trade(t) for t in reversed(filtered)],
    }


def _equity_payload():
    """
    Devuelve una curva preparada para el frontend.
    Prioriza equity.jsonl, pero completa metadatos con trades.jsonl.
    """
    raw_points = read_all_jsonl(EQUITY_FILE)
    trades = _normalized_trades()

    # Balance inicial real derivado de la primera operación, si existe.
    if trades:
        start_balance = trades[0]["_bal"] - trades[0]["_p"]
    else:
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
        except Exception:
            state = {}
        start_balance = _f(state.get("initial_balance"), 100.0)

    points = []
    last_ts = None

    for i, raw in enumerate(raw_points):
        eq = _f(raw.get("equity"), None)
        if eq is None:
            continue

        dt = _local_dt(
            raw.get("time")
            or raw.get("timestamp")
            or raw.get("datetime")
            or raw.get("exit_time")
            or raw.get("updated_at")
        )

        # Si equity.jsonl no tiene tiempo, intenta usar el tiempo de la operación.
        if not dt and i < len(trades):
            dt = trades[i]["_dt"]

        # Fallback estable y creciente.
        if not dt:
            base = datetime(2026, 1, 1, tzinfo=UTC_TZ)
            dt = (base + timedelta(minutes=i)).astimezone(LOCAL_TZ)

        ts = int(dt.timestamp())
        if last_ts is not None and ts <= last_ts:
            ts = last_ts + 1
        last_ts = ts

        trade = trades[i] if i < len(trades) else None
        profit = raw.get("profit")
        if profit is None and trade is not None:
            profit = trade["_p"]

        points.append({
            "time": ts,
            "equity": eq,
            "profit": _f(profit),
            "trade_number": int(raw.get("trade_number") or (i + 1)),
            "date_local": dt.isoformat(),
        })

    # Si no existe equity.jsonl, construye la curva desde los trades cerrados.
    if not points and trades:
        for i, t in enumerate(trades):
            points.append({
                "time": int(t["_dt"].timestamp()),
                "equity": t["_bal"],
                "profit": t["_p"],
                "trade_number": i + 1,
                "date_local": t["_dt"].isoformat(),
            })

    values = [p["equity"] for p in points]
    current = values[-1] if values else start_balance
    max_eq = max([start_balance] + values) if values else start_balance
    min_eq = min([start_balance] + values) if values else start_balance

    peak = start_balance
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak * 100.0)

    net = current - start_balance
    pct = (net / start_balance * 100.0) if start_balance else 0.0

    return {
        "points": points,
        "meta": {
            "start_balance": start_balance,
            "current_equity": current,
            "max_equity": max_eq,
            "min_equity": min_eq,
            "net": net,
            "return_pct": pct,
            "max_drawdown_pct": max_dd,
            "operations": len(trades) if trades else len(points),
        },
    }


HTML = r"""
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CFD Standard Simulation Dashboard</title>

<!-- Lightweight Charts standalone. API v5.x -->
<script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>

<style>
:root{
  color-scheme:dark;
  --bg:#0b1120;
  --panel:#111827;
  --panel2:#0b1220;
  --line:#273449;
  --line2:#334155;
  --text:#e2e8f0;
  --muted:#94a3b8;
  --blue:#60a5fa;
  --blue2:#2563eb;
  --green:#86efac;
  --greenStrong:#22c55e;
  --red:#fca5a5;
  --redStrong:#ef4444;
}
*{box-sizing:border-box}
body{
  font-family:Inter,Arial,sans-serif;
  background:var(--bg);
  color:var(--text);
  margin:0;
  padding:20px;
}
.wrap{max-width:1450px;margin:auto}
h1{margin:0 0 4px;font-size:28px}
.sub{color:#93c5fd;margin-bottom:18px}
.status{
  display:inline-flex;
  align-items:center;
  gap:8px;
  padding:7px 12px;
  border-radius:999px;
  background:#1e293b;
  margin-bottom:16px;
  font-weight:700;
  font-size:13px;
}
.grid{
  display:grid;
  grid-template-columns:repeat(auto-fit,minmax(165px,1fr));
  gap:12px;
}
.card{
  background:var(--panel);
  border:1px solid var(--line2);
  border-radius:14px;
  padding:16px;
}
.value{font-size:27px;font-weight:800;margin-top:8px}
.good{color:var(--green)!important}
.bad{color:var(--red)!important}
.muted{color:var(--muted)}
.small-value{font-size:18px;font-weight:700;margin-top:5px}
.trade-grid{
  display:grid;
  grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
  gap:10px;
  margin-top:10px;
}

/* =========================
   EQUITY / TRADINGVIEW STYLE
   ========================= */
.equity-card{
  margin-top:16px;
  padding:0;
  overflow:hidden;
}
.equity-top{
  padding:16px 18px 10px;
  border-bottom:1px solid rgba(51,65,85,.55);
}
.equity-header{
  display:flex;
  justify-content:space-between;
  align-items:flex-start;
  gap:18px;
  flex-wrap:wrap;
}
.equity-title{
  font-size:14px;
  color:var(--muted);
  margin-bottom:6px;
}
.equity-main{
  display:flex;
  align-items:baseline;
  gap:10px;
  flex-wrap:wrap;
}
.eq-current{
  font-size:27px;
  font-weight:850;
  letter-spacing:-.5px;
}
.eq-change{
  font-size:14px;
  font-weight:800;
}
.equity-kpis{
  display:flex;
  align-items:flex-start;
  gap:26px;
  flex-wrap:wrap;
}
.equity-kpi{
  display:flex;
  flex-direction:column;
  gap:4px;
  min-width:86px;
}
.equity-kpi span{
  color:var(--muted);
  font-size:11px;
  text-transform:uppercase;
  letter-spacing:.05em;
}
.equity-kpi strong{font-size:14px}
.equity-toolbar{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:10px;
  padding:8px 18px;
  background:#0d1526;
  border-bottom:1px solid rgba(51,65,85,.45);
  flex-wrap:wrap;
}
.eq-ranges{display:flex;gap:5px;flex-wrap:wrap}
.eq-range{
  border:1px solid transparent;
  background:transparent;
  color:var(--muted);
  padding:6px 10px;
  border-radius:7px;
  cursor:pointer;
  font-size:12px;
  font-weight:750;
}
.eq-range:hover{background:#172033;color:var(--text)}
.eq-range.active{
  background:#1d4ed8;
  border-color:#2563eb;
  color:white;
}
.eq-help{
  color:#64748b;
  font-size:11px;
}
.equity-chart-wrap{
  position:relative;
  width:100%;
  height:430px;
  min-height:300px;
  background:var(--panel2);
  overflow:hidden;
}
#equityTradingChart{width:100%;height:100%}
.equity-tooltip{
  position:absolute;
  pointer-events:none;
  z-index:30;
  min-width:176px;
  padding:10px 12px;
  background:rgba(15,23,42,.97);
  border:1px solid #475569;
  border-radius:9px;
  font-size:12px;
  line-height:1.6;
  box-shadow:0 12px 32px rgba(0,0,0,.45);
  backdrop-filter:blur(8px);
}
.tip-title{font-weight:800;font-size:13px;margin-bottom:2px}
.tip-row{display:flex;justify-content:space-between;gap:16px}
.equity-footer{
  display:flex;
  justify-content:space-between;
  gap:12px;
  flex-wrap:wrap;
  padding:8px 18px 12px;
  color:var(--muted);
  font-size:12px;
  background:#0d1526;
}

/* TABLES */
table{width:100%;border-collapse:collapse;margin-top:10px;font-size:14px}
th,td{
  padding:9px;
  border-bottom:1px solid var(--line2);
  text-align:left;
  white-space:nowrap;
}
th{color:#cbd5e1;font-size:12px}
.scroll{overflow-x:auto}
.pagination{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
  margin-top:14px;
  flex-wrap:wrap;
}
.pagination-info{color:var(--muted);font-size:14px}
.pagination-controls{display:flex;align-items:center;gap:8px}
.page-btn,.report-btn{
  background:#1e293b;
  color:var(--text);
  border:1px solid #475569;
  border-radius:8px;
  padding:8px 12px;
  cursor:pointer;
  font-weight:700;
}
.page-btn:hover:not(:disabled),.report-btn:hover{background:#334155}
.page-btn:disabled{opacity:.45;cursor:not-allowed}
.page-number{min-width:110px;text-align:center;color:#cbd5e1;font-size:14px}
.trade-header{
  display:flex;
  justify-content:space-between;
  align-items:center;
  gap:12px;
  flex-wrap:wrap;
}
.trade-filters{
  display:flex;
  gap:8px;
  flex-wrap:wrap;
  align-items:center;
}
.trade-filters select,.trade-filters input{
  background:#1e293b;
  color:var(--text);
  border:1px solid #475569;
  border-radius:8px;
  padding:8px 10px;
  font-weight:700;
}
.trade-summary{
  display:flex;
  gap:8px;
  flex-wrap:wrap;
  margin:12px 0 4px;
}
.trade-summary span{
  background:var(--panel2);
  border:1px solid var(--line);
  border-radius:999px;
  padding:6px 10px;
  color:#cbd5e1;
  font-size:12px;
  font-weight:700;
}

/* REPORT */
.report-title{
  display:flex;
  justify-content:space-between;
  align-items:center;
  gap:12px;
  flex-wrap:wrap;
}
.report-title h2{margin:0;font-size:21px}
.report-controls{display:flex;gap:8px;flex-wrap:wrap}
.report-controls select,.report-controls input{
  background:#1e293b;
  color:var(--text);
  border:1px solid #475569;
  border-radius:8px;
  padding:8px 10px;
  font-weight:700;
}
.report-grid{
  display:grid;
  grid-template-columns:repeat(auto-fit,minmax(155px,1fr));
  gap:10px;
  margin-top:14px;
}
.report-metric{
  background:var(--panel2);
  border:1px solid var(--line);
  border-radius:12px;
  padding:12px;
}
.report-metric .rv{font-size:21px;font-weight:800;margin-top:5px}
.report-charts{
  display:grid;
  grid-template-columns:1.4fr .8fr;
  gap:12px;
  margin-top:14px;
}
.report-chart-wrap{
  height:260px;
  background:var(--panel2);
  border-radius:12px;
  overflow:hidden;
  margin-top:8px;
}
.badge{
  display:inline-block;
  padding:4px 9px;
  border-radius:999px;
  font-size:12px;
  font-weight:700
}
.pos{background:#12311f;color:var(--green)}
.neg{background:#3a1717;color:var(--red)}
.be{background:#273449;color:#cbd5e1}
.hidden{display:none!important}
.print-only{display:none}

@media(max-width:900px){
  .equity-chart-wrap{height:340px}
  .report-charts{grid-template-columns:1fr}
}
@media(max-width:600px){
  body{padding:12px}
  h1{font-size:24px}
  .value{font-size:23px}
  .equity-top{padding:14px 12px 9px}
  .equity-toolbar{padding:7px 12px}
  .equity-footer{padding:7px 12px 10px}
  .eq-current{font-size:22px}
  .equity-kpis{
    width:100%;
    justify-content:space-between;
    gap:10px;
  }
  .equity-kpi{min-width:auto}
  .eq-help{display:none}
  .equity-chart-wrap{height:280px;min-height:240px}
  .report-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
  .report-chart-wrap{height:220px}
  .report-metric .rv{font-size:18px}
}
@media(max-width:380px){
  .equity-chart-wrap{height:245px;min-height:220px}
}
@media print{
  body{background:#fff;color:#111;padding:0}
  .wrap{max-width:none}
  .grid,#status,.sub,.equity-card,#openTradeCard,#tradesCard,#stateCard,
  .report-controls,.report-actions{display:none!important}
  #reportCard{background:#fff;color:#111;border:none;padding:0}
  .report-metric{background:#fff;border:1px solid #bbb}
  .muted{color:#444!important}
  .print-only{display:block}
  .report-charts{grid-template-columns:1fr 1fr}
  table{font-size:10px}
}
</style>
</head>
<body>

<div class="wrap">
  <h1>CFD Standard Simulation</h1>
  <div class="sub" id="subtitle">Crash 500 · hybrid_long_only · simulación sin órdenes MT5</div>
  <div id="status" class="status">Cargando...</div>

  <div class="grid">
    <div class="card"><div class="muted">Saldo</div><div id="balance" class="value">$0.00</div></div>
    <div class="card"><div class="muted">Equity</div><div id="equity" class="value">$0.00</div></div>
    <div class="card"><div class="muted">P&L cerrado</div><div id="pnl" class="value">$0.00</div></div>
    <div class="card"><div class="muted">P&L flotante</div><div id="floating" class="value">$0.00</div></div>
    <div class="card"><div class="muted">Trades</div><div id="trades" class="value">0</div></div>
    <div class="card"><div class="muted">Win Rate</div><div id="wr" class="value">0%</div></div>
    <div class="card"><div class="muted">Profit Factor</div><div id="pf" class="value">0.00</div></div>
    <div class="card"><div class="muted">Max DD</div><div id="dd" class="value">0%</div></div>
    <div class="card"><div class="muted">Precio actual</div><div id="price" class="value">-</div></div>
    <div class="card"><div class="muted">Última señal</div><div id="signal" class="value">HOLD</div></div>
  </div>

  <div class="card" id="openTradeCard" style="margin-top:16px">
    <div class="muted">Operación virtual abierta</div>
    <div id="openTrade" class="trade-grid"><div>Ninguna</div></div>
  </div>

  <!-- EQUITY PROFESIONAL -->
  <div class="card equity-card">
    <div class="equity-top">
      <div class="equity-header">
        <div>
          <div class="equity-title">Curva de equity</div>
          <div class="equity-main">
            <span id="eqCurrent" class="eq-current">$0.00</span>
            <span id="eqProfit" class="eq-change good">+$0.00</span>
            <span id="eqProfitPct" class="eq-change good">+0.00%</span>
          </div>
        </div>

        <div class="equity-kpis">
          <div class="equity-kpi">
            <span>Máximo</span>
            <strong id="eqMax">$0.00</strong>
          </div>
          <div class="equity-kpi">
            <span>Drawdown</span>
            <strong id="eqDrawdown">0.00%</strong>
          </div>
          <div class="equity-kpi">
            <span>Operaciones</span>
            <strong id="eqOperations">0</strong>
          </div>
        </div>
      </div>
    </div>

    <div class="equity-toolbar">
      <div class="eq-ranges">
        <button class="eq-range active" data-range="all">Todas</button>
        <button class="eq-range" data-range="1">1D</button>
        <button class="eq-range" data-range="7">7D</button>
        <button class="eq-range" data-range="15">15D</button>
        <button class="eq-range" data-range="30">30D</button>
      </div>
      <div class="eq-help">Rueda: zoom · arrastrar: mover · cursor: detalles</div>
    </div>

    <div class="equity-chart-wrap" id="equityChartWrap">
      <div id="equityTradingChart"></div>

      <div id="equityTooltip" class="equity-tooltip hidden">
        <div id="tipTrade" class="tip-title">Operación #0</div>
        <div id="tipDate" class="muted" style="margin-bottom:4px">-</div>
        <div class="tip-row"><span>Balance</span><strong id="tipBalance">$0.00</strong></div>
        <div class="tip-row"><span>Resultado</span><strong id="tipProfit">$0.00</strong></div>
      </div>
    </div>

    <div class="equity-footer">
      <span id="equityRange">Min $0.00 · Max $0.00</span>
      <span id="eqInitial">Saldo inicial: $100.00</span>
    </div>
  </div>

  <!-- REPORT -->
  <div class="card" id="reportCard" style="margin-top:16px">
    <div class="print-only">
      <h1>Reporte de simulación</h1>
      <div id="printMeta"></div>
      <hr>
    </div>

    <div class="report-title">
      <h2>Reporte de rendimiento</h2>
      <div class="report-controls">
        <select id="reportRange">
          <option value="today">Hoy</option>
          <option value="7">Últimos 7 días</option>
          <option value="15" selected>Últimos 15 días</option>
          <option value="30">Últimos 30 días</option>
          <option value="all">Todo</option>
          <option value="custom">Rango personalizado</option>
        </select>
        <input id="reportStart" type="date" class="hidden">
        <input id="reportEnd" type="date" class="hidden">
        <button class="report-btn" id="generateReport">Generar reporte</button>
      </div>
    </div>

    <div class="report-actions" style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
      <button class="report-btn" id="printReport">Imprimir / Guardar PDF</button>
      <button class="report-btn" id="csvReport">Exportar CSV</button>
    </div>

    <div class="report-grid">
      <div class="report-metric"><div class="muted">Periodo</div><div class="rv" id="rPeriod">-</div></div>
      <div class="report-metric"><div class="muted">Saldo inicial</div><div class="rv" id="rStart">$0.00</div></div>
      <div class="report-metric"><div class="muted">Saldo final</div><div class="rv" id="rEnd">$0.00</div></div>
      <div class="report-metric"><div class="muted">Resultado neto</div><div class="rv" id="rNet">$0.00</div></div>
      <div class="report-metric"><div class="muted">Rentabilidad</div><div class="rv" id="rReturn">0%</div></div>
      <div class="report-metric"><div class="muted">Operaciones</div><div class="rv" id="rTrades">0</div></div>
      <div class="report-metric"><div class="muted">Ganadas</div><div class="rv" id="rWins">0</div></div>
      <div class="report-metric"><div class="muted">Perdidas</div><div class="rv" id="rLosses">0</div></div>
      <div class="report-metric"><div class="muted">Win Rate</div><div class="rv" id="rWR">0%</div></div>
      <div class="report-metric"><div class="muted">Profit Factor</div><div class="rv" id="rPF">0</div></div>
      <div class="report-metric"><div class="muted">Ganancia bruta</div><div class="rv" id="rGP">$0</div></div>
      <div class="report-metric"><div class="muted">Pérdida bruta</div><div class="rv" id="rGL">$0</div></div>
      <div class="report-metric"><div class="muted">Max DD</div><div class="rv" id="rDD">0%</div></div>
      <div class="report-metric"><div class="muted">Días positivos</div><div class="rv" id="rPos">0</div></div>
      <div class="report-metric"><div class="muted">Días negativos</div><div class="rv" id="rNeg">0</div></div>
      <div class="report-metric"><div class="muted">Promedio diario</div><div class="rv" id="rAvg">$0</div></div>
      <div class="report-metric"><div class="muted">Mejor día</div><div class="rv" id="rBest">-</div></div>
      <div class="report-metric"><div class="muted">Peor día</div><div class="rv" id="rWorst">-</div></div>
      <div class="report-metric"><div class="muted">Racha ganadora</div><div class="rv" id="rWS">0</div></div>
      <div class="report-metric"><div class="muted">Racha perdedora</div><div class="rv" id="rLS">0</div></div>
    </div>

    <div class="report-charts">
      <div>
        <div class="muted">Ganancia / pérdida diaria</div>
        <div class="report-chart-wrap"><canvas id="dailyChart"></canvas></div>
      </div>
      <div>
        <div class="muted">Ganadas vs perdidas</div>
        <div class="report-chart-wrap"><canvas id="wlChart"></canvas></div>
      </div>
    </div>

    <div style="margin-top:14px">
      <div class="muted">Detalle diario</div>
      <div class="scroll">
        <table>
          <thead>
            <tr>
              <th>Fecha</th><th>Estado</th><th>Trades</th><th>Ganadas</th><th>Perdidas</th>
              <th>WR</th><th>Gan. bruta</th><th>Pérdida</th><th>Neto</th><th>PF</th>
              <th>Rentab.</th><th>DD</th><th>Saldo final</th>
            </tr>
          </thead>
          <tbody id="dailyRows"><tr><td colspan="13">Sin datos</td></tr></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- TRADES -->
  <div class="card" id="tradesCard" style="margin-top:16px">
    <div class="trade-header">
      <div class="muted">Historial de operaciones cerradas</div>
      <div class="trade-filters">
        <select id="tradeRange">
          <option value="today">Hoy</option>
          <option value="7">Últimos 7 días</option>
          <option value="15">Últimos 15 días</option>
          <option value="30">Últimos 30 días</option>
          <option value="all" selected>Todo</option>
          <option value="custom">Rango personalizado</option>
        </select>
        <input id="tradeStart" type="date" class="hidden">
        <input id="tradeEnd" type="date" class="hidden">
        <select id="tradeReason">
          <option value="all">TP y SL</option>
          <option value="take_profit">Take Profit</option>
          <option value="stop_loss">Stop Loss</option>
        </select>
        <select id="tradeDirection">
          <option value="all">BUY y SELL</option>
          <option value="BUY">BUY</option>
          <option value="SELL">SELL</option>
        </select>
        <select id="tradeOutcome">
          <option value="all">Todos</option>
          <option value="win">Ganadores</option>
          <option value="loss">Perdedores</option>
        </select>
        <button class="report-btn" id="applyTradeFilters" type="button">Filtrar</button>
        <button class="report-btn" id="resetTradeFilters" type="button">Limpiar</button>
        <button class="report-btn" id="exportTradesPdf" type="button">Guardar PDF</button>
      </div>
    </div>
    <div class="trade-summary">
      <span id="filteredTrades">0 trades</span>
      <span id="filteredWins">0 ganados</span>
      <span id="filteredLosses">0 perdidos</span>
      <span id="filteredWR">WR 0%</span>
      <span id="filteredNet">Neto $0.00</span>
    </div>
    <div class="scroll">
      <table>
        <thead>
          <tr>
            <th>Entrada (Managua)</th><th>Salida (Managua)</th><th>Duración</th><th>Dir.</th>
            <th>Precio entrada</th><th>Take Profit</th><th>Stop Loss</th><th>Precio salida</th>
            <th>Motivo</th><th>P&amp;L</th><th>Saldo</th>
          </tr>
        </thead>
        <tbody id="tradeRows"><tr><td colspan="11">Sin trades todavía</td></tr></tbody>
      </table>
    </div>

    <div class="pagination">
      <div id="paginationInfo" class="pagination-info">Mostrando 0 operaciones</div>
      <div class="pagination-controls">
        <button id="prevPage" class="page-btn" type="button">← Anterior</button>
        <span id="pageNumber" class="page-number">Página 1 de 1</span>
        <button id="nextPage" class="page-btn" type="button">Siguiente →</button>
      </div>
    </div>
  </div>

  <!-- STATE -->
  <div class="card" id="stateCard" style="margin-top:16px">
    <div class="muted">Estado</div>
    <div id="updated">-</div>
    <div id="note" class="muted" style="margin-top:8px"></div>
    <div id="error" class="bad" style="margin-top:8px"></div>
  </div>
</div>

<script>
function money(v){
  const n=Number(v||0);
  return (n>=0?'+$':'-$')+Math.abs(n).toFixed(2);
}
function num(v,d=3){
  const n=Number(v);
  return Number.isFinite(n)?n.toFixed(d):'-';
}
function card(label,value){
  return `<div><div class="muted">${label}</div><div class="small-value">${value}</div></div>`;
}
function fmtDate(s){
  if(!s)return '-';
  const p=s.split('-');
  return `${p[2]}/${p[1]}/${p[0]}`;
}
function fmtLocalDateTime(s){
  if(!s)return '-';
  const d=new Date(s);
  if(Number.isNaN(d.getTime()))return s;
  return d.toLocaleString('es-NI',{
    timeZone:'America/Managua',year:'numeric',month:'2-digit',day:'2-digit',
    hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false
  });
}
function reasonLabel(v){
  return v==='take_profit'?'Take Profit':v==='stop_loss'?'Stop Loss':(v||'-');
}
function reportPF(v){
  const n=Number(v);
  return v==null?'N/A':(!Number.isFinite(n)||n>=999999?'∞':n.toFixed(2));
}

/* =========================
   PAGINACIÓN
   ========================= */
const TRADES_PER_PAGE=30;
let currentTradePage=1;
let totalTradePages=1;

function updatePagination(meta){
  const total=Number(meta.total||0);
  const page=Number(meta.page||1);
  const perPage=Number(meta.per_page||TRADES_PER_PAGE);
  const totalPages=Math.max(1,Number(meta.total_pages||1));

  currentTradePage=page;
  totalTradePages=totalPages;

  const start=total?((page-1)*perPage)+1:0;
  const end=total?Math.min(page*perPage,total):0;

  document.getElementById('paginationInfo').textContent=
    total?`Mostrando ${start}–${end} de ${total} operaciones`:'Mostrando 0 operaciones';

  document.getElementById('pageNumber').textContent=`Página ${page} de ${totalPages}`;
  document.getElementById('prevPage').disabled=page<=1;
  document.getElementById('nextPage').disabled=page>=totalPages;
}

/* =========================
   EQUITY CHART - LIGHTWEIGHT CHARTS v5
   ========================= */
let equityChart=null;
let equitySeries=null;
let initialBalanceLine=null;
let maxEquityLine=null;
let equityRangeMode='all';
let equityPayload={points:[],meta:{}};
let visibleEquityPoints=[];
let firstEquityFit=true;

function initEquityChart(){
  if(equityChart)return;

  const container=document.getElementById('equityTradingChart');

  equityChart=LightweightCharts.createChart(container,{
    width:container.clientWidth,
    height:container.clientHeight,
    layout:{
      background:{type:'solid',color:'#0b1220'},
      textColor:'#94a3b8',
      fontFamily:'Inter, Arial, sans-serif'
    },
    grid:{
      vertLines:{color:'#152033'},
      horzLines:{color:'#1e293b'}
    },
    rightPriceScale:{
      visible:true,
      borderColor:'#334155',
      scaleMargins:{top:.10,bottom:.10}
    },
    leftPriceScale:{visible:false},
    timeScale:{
      borderColor:'#334155',
      timeVisible:true,
      secondsVisible:false,
      rightOffset:3,
      barSpacing:10,
      minBarSpacing:3,
      fixLeftEdge:false,
      fixRightEdge:false
    },
    crosshair:{
      mode:LightweightCharts.CrosshairMode.Normal,
      vertLine:{
        color:'#64748b',
        width:1,
        style:2,
        labelBackgroundColor:'#334155'
      },
      horzLine:{
        color:'#64748b',
        width:1,
        style:2,
        labelBackgroundColor:'#334155'
      }
    },
    handleScroll:{
      mouseWheel:true,
      pressedMouseMove:true,
      horzTouchDrag:true,
      vertTouchDrag:false
    },
    handleScale:{
      axisPressedMouseMove:true,
      mouseWheel:true,
      pinch:true
    }
  });

  equitySeries=equityChart.addSeries(
    LightweightCharts.AreaSeries,
    {
      lineColor:'#60a5fa',
      topColor:'rgba(96,165,250,.25)',
      bottomColor:'rgba(96,165,250,.015)',
      lineWidth:2,
      crosshairMarkerVisible:true,
      crosshairMarkerRadius:4,
      lastValueVisible:true,
      priceLineVisible:true,
      priceFormat:{
        type:'custom',
        formatter:value=>'$'+Number(value).toFixed(2)
      }
    }
  );

  equityChart.subscribeCrosshairMove(param=>{
    const tooltip=document.getElementById('equityTooltip');

    if(
      !param ||
      param.time===undefined ||
      !param.point ||
      param.point.x<0 ||
      param.point.y<0
    ){
      tooltip.classList.add('hidden');
      return;
    }

    const seriesPoint=param.seriesData.get(equitySeries);
    if(!seriesPoint){
      tooltip.classList.add('hidden');
      return;
    }

    const row=visibleEquityPoints.find(p=>Number(p.time)===Number(param.time));
    if(!row){
      tooltip.classList.add('hidden');
      return;
    }

    const balance=Number(row.equity||seriesPoint.value||0);
    const profit=Number(row.profit||0);

    document.getElementById('tipTrade').textContent=`Operación #${row.trade_number||'-'}`;
    document.getElementById('tipBalance').textContent='$'+balance.toFixed(2);
    document.getElementById('tipProfit').textContent=money(profit);
    document.getElementById('tipProfit').className=profit>=0?'good':'bad';

    const dt=new Date(Number(row.time)*1000);
    document.getElementById('tipDate').textContent=
      dt.toLocaleString('es-NI',{
        timeZone:'America/Managua',
        day:'2-digit',
        month:'2-digit',
        year:'numeric',
        hour:'2-digit',
        minute:'2-digit'
      });

    const wrap=document.getElementById('equityChartWrap');
    let x=param.point.x+15;
    let y=param.point.y+15;

    if(x>wrap.clientWidth-200)x=param.point.x-190;
    if(y>wrap.clientHeight-135)y=param.point.y-125;

    tooltip.style.left=Math.max(8,x)+'px';
    tooltip.style.top=Math.max(8,y)+'px';
    tooltip.classList.remove('hidden');
  });

  const ro=new ResizeObserver(entries=>{
    if(!entries.length||!equityChart)return;
    const rect=entries[0].contentRect;
    equityChart.applyOptions({
      width:Math.max(1,rect.width),
      height:Math.max(1,rect.height)
    });
  });
  ro.observe(container);
}

function filterEquityPoints(points){
  if(equityRangeMode==='all')return points;

  const days=Number(equityRangeMode);
  if(!Number.isFinite(days))return points;

  const cutoff=Math.floor(Date.now()/1000)-(days*86400);
  return points.filter(p=>Number(p.time)>=cutoff);
}

function calculateVisibleStats(points,meta){
  const startAll=Number(meta.start_balance||100);

  if(!points.length){
    return {
      start:startAll,current:startAll,min:startAll,max:startAll,
      net:0,pct:0,dd:0,operations:0
    };
  }

  // Para "Todas", usa el saldo inicial real.
  // Para un filtro de tiempo, usa el valor previo al primer punto visible si existe.
  let start=startAll;
  if(equityRangeMode!=='all'){
    const all=equityPayload.points||[];
    const firstIndex=all.findIndex(p=>Number(p.time)===Number(points[0].time));
    if(firstIndex>0)start=Number(all[firstIndex-1].equity||startAll);
  }

  const values=points.map(p=>Number(p.equity));
  const current=values[values.length-1];
  const max=Math.max(start,...values);
  const min=Math.min(start,...values);
  const net=current-start;
  const pct=start?(net/start*100):0;

  let peak=start;
  let dd=0;
  for(const value of values){
    peak=Math.max(peak,value);
    if(peak>0)dd=Math.max(dd,(peak-value)/peak*100);
  }

  return {start,current,min,max,net,pct,dd,operations:points.length};
}

function drawEquity(payload){
  initEquityChart();

  if(Array.isArray(payload)){
    // Compatibilidad por si el backend viejo devuelve solo un array.
    payload={
      points:payload.map((p,i)=>({
        time:Number(p.time||p.timestamp||Math.floor(Date.now()/1000)-(payload.length-i)*60),
        equity:Number(p.equity||0),
        profit:Number(p.profit||0),
        trade_number:i+1
      })),
      meta:{start_balance:100}
    };
  }

  equityPayload=payload||{points:[],meta:{}};

  const allPoints=(equityPayload.points||[])
    .filter(p=>Number.isFinite(Number(p.equity))&&Number.isFinite(Number(p.time)))
    .sort((a,b)=>Number(a.time)-Number(b.time));

  visibleEquityPoints=filterEquityPoints(allPoints);

  const chartData=visibleEquityPoints.map(p=>({
    time:Number(p.time),
    value:Number(p.equity)
  }));

  equitySeries.setData(chartData);

  const st=calculateVisibleStats(visibleEquityPoints,equityPayload.meta||{});

  document.getElementById('eqCurrent').textContent='$'+st.current.toFixed(2);
  document.getElementById('eqProfit').textContent=money(st.net);
  document.getElementById('eqProfit').className='eq-change '+(st.net>=0?'good':'bad');
  document.getElementById('eqProfitPct').textContent=(st.pct>=0?'+':'')+st.pct.toFixed(2)+'%';
  document.getElementById('eqProfitPct').className='eq-change '+(st.pct>=0?'good':'bad');
  document.getElementById('eqMax').textContent='$'+st.max.toFixed(2);
  document.getElementById('eqDrawdown').textContent=st.dd.toFixed(2)+'%';

  // "Operaciones" usa el total real cuando vemos Todo.
  const opCount=equityRangeMode==='all'
    ? Number((equityPayload.meta||{}).operations||visibleEquityPoints.length)
    : visibleEquityPoints.length;

  document.getElementById('eqOperations').textContent=opCount;
  document.getElementById('equityRange').textContent=
    `Min $${st.min.toFixed(2)} · Max $${st.max.toFixed(2)}`;
  document.getElementById('eqInitial').textContent=
    `Saldo inicial: $${st.start.toFixed(2)}`;

  if(initialBalanceLine){
    equitySeries.removePriceLine(initialBalanceLine);
    initialBalanceLine=null;
  }
  if(maxEquityLine){
    equitySeries.removePriceLine(maxEquityLine);
    maxEquityLine=null;
  }

  if(chartData.length){
    initialBalanceLine=equitySeries.createPriceLine({
      price:st.start,
      color:'#64748b',
      lineWidth:1,
      lineStyle:2,
      axisLabelVisible:true,
      title:'Inicial'
    });

    maxEquityLine=equitySeries.createPriceLine({
      price:st.max,
      color:'#22c55e',
      lineWidth:1,
      lineStyle:2,
      axisLabelVisible:true,
      title:'Máx.'
    });
  }

  // No resetea el zoom cada 5 s. Solo al inicio o cuando cambias de rango.
  if(firstEquityFit){
    equityChart.timeScale().fitContent();
    firstEquityFit=false;
  }
}

document.querySelectorAll('.eq-range').forEach(btn=>{
  btn.addEventListener('click',()=>{
    document.querySelectorAll('.eq-range').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    equityRangeMode=btn.dataset.range;
    firstEquityFit=true;
    drawEquity(equityPayload);
  });
});

/* =========================
   REPORTES
   ========================= */
function reportQuery(){
  const r=document.getElementById('reportRange').value;
  const q=new URLSearchParams({range:r});
  if(r==='custom'){
    q.set('start',document.getElementById('reportStart').value);
    q.set('end',document.getElementById('reportEnd').value);
  }
  return q.toString();
}

function setupReportCanvas(id){
  const c=document.getElementById(id);
  const w=c.parentElement.clientWidth;
  const h=c.parentElement.clientHeight;
  const d=Math.min(devicePixelRatio||1,2);
  c.width=w*d;
  c.height=h*d;
  c.style.width=w+'px';
  c.style.height=h+'px';
  const x=c.getContext('2d');
  x.setTransform(d,0,0,d,0,0);
  x.clearRect(0,0,w,h);
  return {x,w,h};
}

function drawDaily(d){
  const {x,w,h}=setupReportCanvas('dailyChart');
  if(!d.length){
    x.fillStyle='#94a3b8';
    x.fillText('Sin datos',20,30);
    return;
  }

  const vals=d.map(v=>Number(v.net||0));
  const m=Math.max(1,...vals.map(Math.abs));
  const L=44,R=12,T=16,B=34,pw=w-L-R,ph=h-T-B,z=T+ph/2;

  x.strokeStyle='#334155';
  x.beginPath();
  x.moveTo(L,z);
  x.lineTo(L+pw,z);
  x.stroke();

  const step=pw/d.length;
  const bw=Math.max(5,step*.62);
  x.font='10px Arial';
  x.textAlign='center';

  d.forEach((v,i)=>{
    const n=Number(v.net||0);
    const bh=Math.abs(n)/m*(ph/2-8);
    const xx=L+i*step+(step-bw)/2;
    const yy=n>=0?z-bh:z;
    x.fillStyle=n>=0?'#22c55e':'#ef4444';
    x.fillRect(xx,yy,bw,bh);

    if(d.length<=15){
      x.fillStyle='#94a3b8';
      x.fillText(v.date.slice(5),xx+bw/2,h-12);
    }
  });
}

function drawWL(wins,losses){
  const {x,w,h}=setupReportCanvas('wlChart');
  const t=wins+losses;

  if(!t){
    x.fillStyle='#94a3b8';
    x.fillText('Sin datos',20,30);
    return;
  }

  const cx=w/2,cy=h/2-4,r=Math.min(w,h)*.31,p=wins/t;
  let a=-Math.PI/2;

  x.beginPath();
  x.moveTo(cx,cy);
  x.arc(cx,cy,r,a,a+p*Math.PI*2);
  x.closePath();
  x.fillStyle='#22c55e';
  x.fill();

  a+=p*Math.PI*2;
  x.beginPath();
  x.moveTo(cx,cy);
  x.arc(cx,cy,r,a,a+(1-p)*Math.PI*2);
  x.closePath();
  x.fillStyle='#ef4444';
  x.fill();

  x.beginPath();
  x.arc(cx,cy,r*.58,0,Math.PI*2);
  x.fillStyle='#0b1220';
  x.fill();

  x.textAlign='center';
  x.fillStyle='#e2e8f0';
  x.font='700 22px Arial';
  x.fillText((p*100).toFixed(1)+'%',cx,cy);

  x.font='12px Arial';
  x.fillStyle='#94a3b8';
  x.fillText('Win Rate',cx,cy+19);
}

let lastReport=null;

async function loadReport(){
  try{
    const r=await fetch('/api/report?'+reportQuery(),{cache:'no-store'});
    const d=await r.json();
    const o=d.overall||{};
    lastReport=d;

    document.getElementById('rPeriod').textContent=`${fmtDate(d.start_date)} – ${fmtDate(d.end_date)}`;
    document.getElementById('rStart').textContent='$'+Number(o.start_balance||0).toFixed(2);
    document.getElementById('rEnd').textContent='$'+Number(o.end_balance||0).toFixed(2);
    document.getElementById('rNet').textContent=money(o.net);
    document.getElementById('rNet').className='rv '+(Number(o.net)>=0?'good':'bad');
    document.getElementById('rReturn').textContent=Number(o.profitability_pct||0).toFixed(2)+'%';
    document.getElementById('rTrades').textContent=o.total||0;
    document.getElementById('rWins').textContent=o.wins||0;
    document.getElementById('rLosses').textContent=o.losses||0;
    document.getElementById('rWR').textContent=Number(o.win_rate||0).toFixed(2)+'%';
    document.getElementById('rPF').textContent=reportPF(o.profit_factor);
    document.getElementById('rGP').textContent='+$'+Number(o.gross_profit||0).toFixed(2);
    document.getElementById('rGL').textContent='-$'+Number(o.gross_loss||0).toFixed(2);
    document.getElementById('rDD').textContent=Number(o.max_drawdown_pct||0).toFixed(2)+'%';
    document.getElementById('rPos').textContent=o.positive_days||0;
    document.getElementById('rNeg').textContent=o.negative_days||0;
    document.getElementById('rAvg').textContent=money(o.avg_daily_net||0);
    document.getElementById('rBest').textContent=o.best_day?fmtDate(o.best_day.date)+' '+money(o.best_day.net):'-';
    document.getElementById('rWorst').textContent=o.worst_day?fmtDate(o.worst_day.date)+' '+money(o.worst_day.net):'-';
    document.getElementById('rWS').textContent=o.max_win_streak||0;
    document.getElementById('rLS').textContent=o.max_loss_streak||0;

    const daily=d.daily||[];

    document.getElementById('dailyRows').innerHTML=daily.length
      ?daily.slice().reverse().map(v=>`
        <tr>
          <td>${fmtDate(v.date)}</td>
          <td>${
            v.status==='positive'
              ?'<span class="badge pos">POSITIVO</span>'
              :v.status==='negative'
                ?'<span class="badge neg">NEGATIVO</span>'
                :'<span class="badge be">BREAK-EVEN</span>'
          }</td>
          <td>${v.total}</td>
          <td>${v.wins}</td>
          <td>${v.losses}</td>
          <td>${Number(v.win_rate).toFixed(2)}%</td>
          <td class="good">+$${Number(v.gross_profit).toFixed(2)}</td>
          <td class="bad">-$${Number(v.gross_loss).toFixed(2)}</td>
          <td class="${Number(v.net)>=0?'good':'bad'}">${money(v.net)}</td>
          <td>${reportPF(v.profit_factor)}</td>
          <td>${Number(v.profitability_pct).toFixed(2)}%</td>
          <td>${Number(v.max_drawdown_pct).toFixed(2)}%</td>
          <td>$${Number(v.end_balance).toFixed(2)}</td>
        </tr>
      `).join('')
      :'<tr><td colspan="13">Sin datos</td></tr>';

    drawDaily(daily);
    drawWL(Number(o.wins||0),Number(o.losses||0));
    document.getElementById('printMeta').textContent=
      `Periodo ${fmtDate(d.start_date)} – ${fmtDate(d.end_date)} · America/Managua`;

  }catch(e){
    console.error('Reporte',e);
  }
}

document.getElementById('reportRange').addEventListener('change',e=>{
  const c=e.target.value==='custom';
  document.getElementById('reportStart').classList.toggle('hidden',!c);
  document.getElementById('reportEnd').classList.toggle('hidden',!c);
});
document.getElementById('generateReport').addEventListener('click',loadReport);
document.getElementById('printReport').addEventListener('click',()=>window.print());
document.getElementById('csvReport').addEventListener('click',()=>location.href='/api/report.csv?'+reportQuery());

function tradeQuery(includePage=true){
  const range=document.getElementById('tradeRange').value;
  const q=new URLSearchParams({
    range,
    reason:document.getElementById('tradeReason').value,
    direction:document.getElementById('tradeDirection').value,
    outcome:document.getElementById('tradeOutcome').value
  });
  if(range==='custom'){
    q.set('start',document.getElementById('tradeStart').value);
    q.set('end',document.getElementById('tradeEnd').value);
  }
  if(includePage){
    q.set('page',currentTradePage);
    q.set('per_page',TRADES_PER_PAGE);
  }
  return q.toString();
}

/* =========================
   ACTUALIZACIÓN PRINCIPAL
   ========================= */
async function refresh(){
  try{
    const [sr,tr,er]=await Promise.all([
      fetch('/api/state',{cache:'no-store'}),
      fetch('/api/trades?'+tradeQuery(true),{cache:'no-store'}),
      fetch('/api/equity',{cache:'no-store'})
    ]);

    const s=await sr.json();
    const tradeData=await tr.json();
    const equityData=await er.json();

    const trades=Array.isArray(tradeData)?tradeData:(tradeData.trades||[]);
    if(!Array.isArray(tradeData))updatePagination(tradeData);
    const fs=tradeData.summary||{};
    document.getElementById('filteredTrades').textContent=`${fs.total||0} trades`;
    document.getElementById('filteredWins').textContent=`${fs.wins||0} ganados`;
    document.getElementById('filteredLosses').textContent=`${fs.losses||0} perdidos`;
    document.getElementById('filteredWR').textContent=`WR ${Number(fs.win_rate||0).toFixed(2)}%`;
    document.getElementById('filteredNet').textContent=`Neto ${money(fs.net||0)}`;
    document.getElementById('filteredNet').className=Number(fs.net||0)>=0?'good':'bad';

    document.getElementById('status').textContent=s.running?'● SIMULADOR ACTIVO':'● RECONECTANDO';
    document.getElementById('subtitle').textContent=
      `${s.symbol||'Crash 500'} · ${s.strategy||'hybrid_long_only'} · ${s.granularity||60}s · sin órdenes`;

    document.getElementById('balance').textContent='$'+Number(s.balance||0).toFixed(2);
    document.getElementById('equity').textContent='$'+Number(s.equity||0).toFixed(2);

    for(const [id,val] of [['pnl',s.pnl],['floating',s.floating_pnl]]){
      const el=document.getElementById(id);
      el.textContent=money(val);
      el.className='value '+(Number(val||0)>=0?'good':'bad');
    }

    document.getElementById('trades').textContent=s.trades||0;
    document.getElementById('wr').textContent=Number(s.win_rate||0).toFixed(2)+'%';

    const pf=Number(s.profit_factor||0);
    document.getElementById('pf').textContent=pf>=999?'∞':pf.toFixed(2);

    document.getElementById('dd').textContent=Number(s.max_drawdown_pct||0).toFixed(2)+'%';
    document.getElementById('price').textContent=s.last_price==null?'-':num(s.last_price,3);
    document.getElementById('signal').textContent=s.last_signal||'HOLD';

    const p=s.open_trade;
    document.getElementById('openTrade').innerHTML=p
      ?card('Dirección',p.direction)
        +card('Hora de entrada',fmtLocalDateTime(p.entry_time))
        +card('Entrada',num(p.entry_price))
        +card('SL',num(p.stop_price))
        +card('TP',num(p.take_price))
        +card('Unidades virtuales',num(p.position_size,5))
        +card('Riesgo',Number(p.risk_pct||0).toFixed(2)+'%')
      :'<div>Ninguna</div>';

    const tbody=document.getElementById('tradeRows');
    tbody.innerHTML=trades.length
      ?trades.map(t=>`
        <tr>
          <td>${t.entry_time_local||fmtLocalDateTime(t.entry_time)}</td>
          <td>${t.exit_time_local||fmtLocalDateTime(t.exit_time)}</td>
          <td>${t.duration||'-'}</td>
          <td>${t.direction||'-'}</td>
          <td>${num(t.entry_price)}</td>
          <td class="good">${num(t.take_price)}</td>
          <td class="bad">${num(t.stop_price)}</td>
          <td>${num(t.exit_price)}</td>
          <td>${reasonLabel(t.exit_reason)}</td>
          <td class="${Number(t.profit)>=0?'good':'bad'}">${money(t.profit)}</td>
          <td>$${Number(t.balance_after||0).toFixed(2)}</td>
        </tr>
      `).join('')
      :'<tr><td colspan="11">Sin trades para estos filtros</td></tr>';

    drawEquity(equityData);

    document.getElementById('updated').textContent='Actualizado: '+(s.updated_at||'-');
    document.getElementById('note').textContent=s.note||'';
    document.getElementById('error').textContent=s.last_error?'Último error: '+s.last_error:'';

  }catch(e){
    console.error(e);
    document.getElementById('status').textContent='● SIN CONEXIÓN';
  }
}

/* =========================
   EVENTOS
   ========================= */
document.getElementById('prevPage').addEventListener('click',()=>{
  if(currentTradePage>1){
    currentTradePage--;
    refresh();
  }
});

document.getElementById('nextPage').addEventListener('click',()=>{
  if(currentTradePage<totalTradePages){
    currentTradePage++;
    refresh();
  }
});

document.getElementById('tradeRange').addEventListener('change',e=>{
  const custom=e.target.value==='custom';
  document.getElementById('tradeStart').classList.toggle('hidden',!custom);
  document.getElementById('tradeEnd').classList.toggle('hidden',!custom);
});
document.getElementById('applyTradeFilters').addEventListener('click',()=>{
  currentTradePage=1;
  refresh();
});
document.getElementById('resetTradeFilters').addEventListener('click',()=>{
  document.getElementById('tradeRange').value='all';
  document.getElementById('tradeReason').value='all';
  document.getElementById('tradeDirection').value='all';
  document.getElementById('tradeOutcome').value='all';
  document.getElementById('tradeStart').classList.add('hidden');
  document.getElementById('tradeEnd').classList.add('hidden');
  currentTradePage=1;
  refresh();
});
document.getElementById('exportTradesPdf').addEventListener('click',()=>{
  window.open('/trades/report?'+tradeQuery(false)+'&autoprint=1','_blank','noopener');
});

let resizeTimer=null;
window.addEventListener('resize',()=>{
  clearTimeout(resizeTimer);
  resizeTimer=setTimeout(()=>{
    if(lastReport){
      drawDaily(lastReport.daily||[]);
      drawWL(Number(lastReport.overall?.wins||0),Number(lastReport.overall?.losses||0));
    }
  },140);
});

refresh();
loadReport();
setInterval(refresh,5000);
setInterval(loadReport,30000);
</script>

<!-- Atribución requerida por Lightweight Charts / TradingView -->
<div style="max-width:1450px;margin:12px auto 0;color:#64748b;font-size:11px;text-align:right">
  Charts by TradingView Lightweight Charts
</div>
</body>
</html>
"""


TRADES_REPORT_HTML = r"""
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Historial de operaciones cerradas</title>
  <style>
    :root{--bg:#07101f;--panel:#111a2b;--line:#334155;--text:#e5edf8;--muted:#93a4ba;--green:#4ade80;--red:#ff7373}
    *{box-sizing:border-box}
    body{margin:0;padding:24px;background:var(--bg);color:var(--text);font-family:Inter,Arial,sans-serif}
    .wrap{max-width:1500px;margin:auto}
    .top{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:16px}
    h1{font-size:26px;margin:0 0 7px}.muted{color:var(--muted)}
    .actions{display:flex;gap:8px;flex-wrap:wrap}
    .btn{display:inline-block;background:#1e293b;color:var(--text);border:1px solid #475569;border-radius:8px;padding:9px 13px;text-decoration:none;font-weight:750;cursor:pointer}
    .btn:hover{background:#334155}
    .summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px;margin-bottom:16px}
    .metric,.table-card{background:var(--panel);border:1px solid var(--line);border-radius:13px}
    .metric{padding:13px}.metric strong{display:block;font-size:21px;margin-top:5px}
    .table-card{padding:14px}.scroll{overflow-x:auto}
    table{width:100%;border-collapse:collapse;font-size:14px}
    th,td{padding:10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
    th{font-size:12px;color:#cbd5e1}.good{color:var(--green)}.bad{color:var(--red)}
    .empty{text-align:center;color:var(--muted);padding:30px}
    @media(max-width:600px){body{padding:12px}h1{font-size:22px}.summary{grid-template-columns:repeat(2,minmax(0,1fr))}}
    @page{size:A4 landscape;margin:8mm}
    @media print{
      html,body{width:100%;background:#fff;color:#111;padding:0;margin:0}
      .wrap{max-width:none;width:100%;margin:0}
      .top{margin-bottom:4mm;align-items:flex-start}
      .actions{display:none}
      h1{font-size:18px;margin-bottom:2mm}
      .muted{color:#444}
      .summary{grid-template-columns:repeat(6,1fr);gap:2.5mm;margin-bottom:4mm}
      .metric{background:#fff;border:1px solid #aaa;border-radius:2mm;padding:2.5mm;break-inside:avoid}
      .metric span{font-size:8px}
      .metric strong{font-size:13px;margin-top:1mm}
      .table-card{background:#fff;border:0;border-radius:0;padding:0}
      .scroll{overflow:visible}
      table{width:100%;table-layout:fixed;border-collapse:collapse;font-size:7px;margin:0}
      thead{display:table-header-group}
      tr{break-inside:avoid;page-break-inside:avoid}
      th,td{padding:1.35mm .8mm;border-bottom:1px solid #bbb;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      th{font-size:6.8px;background:#eef2f7;color:#111}
      th:nth-child(1),td:nth-child(1){width:12%}
      th:nth-child(2),td:nth-child(2){width:12%}
      th:nth-child(3),td:nth-child(3){width:6%}
      th:nth-child(4),td:nth-child(4){width:4%}
      th:nth-child(5),td:nth-child(5){width:10%}
      th:nth-child(6),td:nth-child(6){width:9%}
      th:nth-child(7),td:nth-child(7){width:9%}
      th:nth-child(8),td:nth-child(8){width:10%}
      th:nth-child(9),td:nth-child(9){width:9%}
      th:nth-child(10),td:nth-child(10){width:8%}
      th:nth-child(11),td:nth-child(11){width:8%}
      .good{color:#08783c}.bad{color:#b42318}
    }
  </style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div>
      <h1>Historial de operaciones cerradas</h1>
      <div class="muted">{{ report.start_date }} al {{ report.end_date }} · horario America/Managua</div>
    </div>
    <div class="actions">
      <button class="btn" onclick="window.print()">Guardar PDF</button>
      <a class="btn" href="/">Volver al dashboard</a>
    </div>
  </div>

  <div class="summary">
    <div class="metric"><span class="muted">Trades</span><strong>{{ report.overall.total }}</strong></div>
    <div class="metric"><span class="muted">Ganados</span><strong class="good">{{ report.overall.wins }}</strong></div>
    <div class="metric"><span class="muted">Perdidos</span><strong class="bad">{{ report.overall.losses }}</strong></div>
    <div class="metric"><span class="muted">Win Rate</span><strong>{{ '%.2f'|format(report.overall.win_rate) }}%</strong></div>
    <div class="metric"><span class="muted">Resultado neto</span><strong class="{{ 'good' if report.overall.net >= 0 else 'bad' }}">{{ '%+.2f'|format(report.overall.net) }} USD</strong></div>
    <div class="metric"><span class="muted">Profit Factor</span><strong>{% if report.overall.profit_factor is none %}N/A{% elif report.overall.profit_factor >= 999999 %}∞{% else %}{{ '%.2f'|format(report.overall.profit_factor) }}{% endif %}</strong></div>
  </div>

  <div class="table-card">
    <div class="scroll">
      <table>
        <thead><tr>
          <th>Entrada (Managua)</th><th>Salida (Managua)</th><th>Duración</th><th>Dir.</th>
          <th>Precio entrada</th><th>Take Profit</th><th>Stop Loss</th><th>Precio salida</th>
          <th>Motivo</th><th>P&amp;L</th><th>Saldo</th>
        </tr></thead>
        <tbody>
        {% for t in report.trades %}
          <tr>
            <td>{{ t.entry_time_local or '-' }}</td><td>{{ t.exit_time_local or '-' }}</td><td>{{ t.duration }}</td>
            <td>{{ t.direction or '-' }}</td><td>{{ '%.3f'|format(t.entry_price|float) }}</td>
            <td class="good">{{ '%.3f'|format(t.take_price|float) }}</td><td class="bad">{{ '%.3f'|format(t.stop_price|float) }}</td>
            <td>{{ '%.3f'|format(t.exit_price|float) }}</td>
            <td>{{ 'Take Profit' if t.exit_reason == 'take_profit' else ('Stop Loss' if t.exit_reason == 'stop_loss' else t.exit_reason) }}</td>
            <td class="{{ 'good' if t.profit|float >= 0 else 'bad' }}">${{ '%+.2f'|format(t.profit|float) }}</td>
            <td>${{ '%.2f'|format(t.balance_after|float) }}</td>
          </tr>
        {% else %}
          <tr><td class="empty" colspan="11">No hay operaciones para los filtros seleccionados.</td></tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>
{% if autoprint %}
<script>window.addEventListener('load',()=>setTimeout(()=>window.print(),250));</script>
{% endif %}
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/trades/report")
def trades_report():
    return render_template_string(
        TRADES_REPORT_HTML,
        report=_build_trade_report(),
        query_string=request.query_string.decode("utf-8", errors="ignore"),
        autoprint=request.args.get("autoprint") == "1",
    )


@app.route("/api/state")
def api_state():
    if not STATE_FILE.exists():
        return jsonify({
            "running": False,
            "balance": 100,
            "equity": 100,
            "pnl": 0,
            "floating_pnl": 0,
            "trades": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "max_drawdown_pct": 0,
            "last_signal": "HOLD",
            "open_trade": None,
            "updated_at": None,
            "note": "Worker todavía no ha creado el estado.",
        })

    try:
        return jsonify(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    except Exception as e:
        return jsonify({
            "running": False,
            "last_error": f"No se pudo leer state.json: {e}",
        }), 500


@app.route("/api/trades")
def api_trades():
    if "page" not in request.args and "per_page" not in request.args:
        return jsonify([_public_trade(t) for t in reversed(_normalized_trades()[-30:])])

    try:
        page=max(1,int(request.args.get("page",1)))
    except (TypeError,ValueError):
        page=1

    try:
        per_page=int(request.args.get("per_page",30))
    except (TypeError,ValueError):
        per_page=30

    per_page=min(100,max(1,per_page))

    all_rows=_normalized_trades()
    start_date,end_date=_date_range(
        request.args.get("range","all"),
        all_rows,
        request.args.get("start",""),
        request.args.get("end",""),
    )
    filtered=_filter_trade_rows(
        all_rows,
        start_date,
        end_date,
        request.args.get("reason","all"),
        request.args.get("direction","all"),
        request.args.get("outcome","all"),
    )
    summary=_stats(filtered)
    rows=list(reversed(filtered))
    total=len(rows)
    total_pages=max(1,(total+per_page-1)//per_page)
    page=min(page,total_pages)

    start=(page-1)*per_page
    page_rows=[_public_trade(t) for t in rows[start:start+per_page]]

    return jsonify({
        "trades":page_rows,
        "total":total,
        "page":page,
        "per_page":per_page,
        "total_pages":total_pages,
        "summary":summary,
    })


@app.route("/api/report")
def api_report():
    return jsonify(_build_report())


@app.route("/api/report.csv")
def api_report_csv():
    report=_build_report()
    buf=io.StringIO()
    w=csv.writer(buf)

    w.writerow([
        "Fecha","Estado","Trades","Ganadas","Perdidas","Win Rate %",
        "Ganancia bruta","Perdida bruta","Neto","Profit Factor",
        "Rentabilidad %","Max DD %","Saldo inicial","Saldo final"
    ])

    for d in report["daily"]:
        pf=d["profit_factor"]
        pf="N/A" if pf is None else ("INF" if pf>=999999 else f"{pf:.4f}")

        w.writerow([
            d["date"],d["status"],d["total"],d["wins"],d["losses"],
            f'{d["win_rate"]:.4f}',
            f'{d["gross_profit"]:.4f}',
            f'{d["gross_loss"]:.4f}',
            f'{d["net"]:.4f}',
            pf,
            f'{d["profitability_pct"]:.4f}',
            f'{d["max_drawdown_pct"]:.4f}',
            f'{d["start_balance"]:.4f}',
            f'{d["end_balance"]:.4f}',
        ])

    name=f'reporte_trading_{report["start_date"]}_a_{report["end_date"]}.csv'
    return Response(
        buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition":f'attachment; filename="{name}"'}
    )


@app.route("/api/trades.csv")
def api_trades_csv():
    """CSV del historial aplicando exactamente los filtros de la tabla."""
    report=_build_trade_report()
    buf=io.StringIO()
    w=csv.writer(buf)
    w.writerow([
        "Fecha","Hora entrada Managua","Hora salida Managua","Duracion",
        "Direccion","Precio entrada","Take Profit","Stop Loss","Precio salida",
        "Motivo salida","Ganancia/Perdida","Saldo final"
    ])
    for t in reversed(report["trades"]):
        w.writerow([
            t.get("trade_date_local", ""),t.get("entry_time_local", ""),
            t.get("exit_time_local", ""),t.get("duration", ""),
            t.get("direction", ""),f'{_f(t.get("entry_price")):.5f}',
            f'{_f(t.get("take_price")):.5f}',f'{_f(t.get("stop_price")):.5f}',
            f'{_f(t.get("exit_price")):.5f}',t.get("exit_reason", ""),
            f'{_f(t.get("profit")):.4f}',f'{_f(t.get("balance_after")):.4f}',
        ])
    name=f'operaciones_{report["start_date"]}_a_{report["end_date"]}.csv'
    return Response(
        "\ufeff"+buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition":f'attachment; filename="{name}"'}
    )


@app.route("/api/equity")
def api_equity():
    return jsonify(_equity_payload())


if __name__=="__main__":
    port=int(os.environ.get("PORT","8000"))
    app.run(host="0.0.0.0",port=port)
