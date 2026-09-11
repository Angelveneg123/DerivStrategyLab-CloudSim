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
    """Lee todo el historial para permitir paginación real en el dashboard."""
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
        rows.append(t)
    rows.sort(key=lambda x: x["_dt"])
    return rows


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
            max_dd = max(max_dd, (peak-bal)/peak*100.0)
    mw=ml=cw=cl=0
    for t in trades:
        if t["_p"] > 0:
            cw += 1; cl = 0; mw = max(mw,cw)
        elif t["_p"] < 0:
            cl += 1; cw = 0; ml = max(ml,cl)
        else:
            cw=cl=0
    return {
        "total": total, "wins": wins, "losses": losses,
        "win_rate": (wins/total*100.0) if total else 0.0,
        "gross_profit": gp, "gross_loss": gl, "net": net,
        "start_balance": start, "end_balance": end,
        "profitability_pct": (net/start*100.0) if start else 0.0,
        "profit_factor": _pf(gp,gl), "max_drawdown_pct": max_dd,
        "avg_win": (gp/wins) if wins else 0.0,
        "avg_loss": (gl/losses) if losses else 0.0,
        "best_trade": max((t["_p"] for t in trades), default=0.0),
        "worst_trade": min((t["_p"] for t in trades), default=0.0),
        "max_win_streak": mw, "max_loss_streak": ml,
    }


def _build_report():
    trades = _normalized_trades()
    today = datetime.now(LOCAL_TZ).date()
    mode = (request.args.get("range") or "15").lower()
    if mode == "today":
        start = end = today
    elif mode in {"7","15","30"}:
        end = today; start = today - timedelta(days=int(mode)-1)
    elif mode == "custom":
        try:
            start = datetime.strptime(request.args.get("start", ""), "%Y-%m-%d").date()
            end = datetime.strptime(request.args.get("end", ""), "%Y-%m-%d").date()
        except Exception:
            start = end = today
        if start > end: start, end = end, start
    else:
        dates=[t["_dt"].date() for t in trades]
        start=min(dates) if dates else today
        end=max(dates) if dates else today
    filt=[t for t in trades if start <= t["_dt"].date() <= end]
    by={}
    for t in filt: by.setdefault(t["_date"],[]).append(t)
    daily=[]
    for d in sorted(by):
        st=_stats(by[d]); st["date"]=d
        st["status"]="positive" if st["net"]>0 else ("negative" if st["net"]<0 else "breakeven")
        daily.append(st)
    overall=_stats(filt)
    overall.update({
        "days":len(daily),
        "positive_days":sum(d["net"]>0 for d in daily),
        "negative_days":sum(d["net"]<0 for d in daily),
        "avg_daily_net":sum(d["net"] for d in daily)/len(daily) if daily else 0.0,
        "best_day":max(daily,key=lambda x:x["net"],default=None),
        "worst_day":min(daily,key=lambda x:x["net"],default=None),
    })
    return {"timezone":"America/Managua","range":mode,"start_date":start.isoformat(),"end_date":end.isoformat(),
            "generated_at":datetime.now(LOCAL_TZ).isoformat(),"overall":overall,"daily":daily}


HTML = r"""
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CFD Standard Simulation Dashboard</title>
<style>
:root{color-scheme:dark}
body{font-family:Arial,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:20px}
.wrap{max-width:1250px;margin:auto}
h1{margin:0 0 4px}.sub{color:#93c5fd;margin-bottom:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:12px}
.card{background:#111827;border:1px solid #334155;border-radius:14px;padding:16px}
.value{font-size:28px;font-weight:700;margin-top:8px}
.good{color:#86efac}.bad{color:#fca5a5}.muted{color:#94a3b8}
.status{display:inline-block;padding:7px 12px;border-radius:999px;background:#1e293b;margin-bottom:16px}
.trade-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin-top:10px}
.small-value{font-size:18px;font-weight:700;margin-top:5px}
table{width:100%;border-collapse:collapse;margin-top:10px;font-size:14px}
th,td{padding:9px;border-bottom:1px solid #334155;text-align:left;white-space:nowrap}
.scroll{overflow-x:auto}
.pagination{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:14px;flex-wrap:wrap}
.pagination-info{color:#94a3b8;font-size:14px}
.pagination-controls{display:flex;align-items:center;gap:8px}
.page-btn{background:#1e293b;color:#e2e8f0;border:1px solid #475569;border-radius:8px;padding:8px 12px;cursor:pointer;font-weight:700}
.page-btn:hover:not(:disabled){background:#334155}
.page-btn:disabled{opacity:.45;cursor:not-allowed}
.page-number{min-width:110px;text-align:center;color:#cbd5e1;font-size:14px}
.equity-card{margin-top:16px}
.equity-chart-wrap{position:relative;width:100%;height:340px;min-height:260px;margin-top:10px;overflow:hidden;border-radius:12px;background:#0b1220}
#equityChart{display:block;width:100%;height:100%;background:#0b1220;border-radius:12px}
.equity-stats{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-top:10px;color:#94a3b8;font-size:13px}
@media(max-width:900px){.equity-chart-wrap{height:280px}}
@media(max-width:600px){body{padding:12px}.value{font-size:24px}.equity-chart-wrap{height:220px;min-height:200px}.equity-card{padding:12px}.equity-stats{font-size:12px;gap:8px}}
@media(max-width:380px){.equity-chart-wrap{height:190px;min-height:180px}}

.report-title{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.report-title h2{margin:0;font-size:21px}.report-controls{display:flex;gap:8px;flex-wrap:wrap}
.report-controls select,.report-controls input,.report-btn{background:#1e293b;color:#e2e8f0;border:1px solid #475569;border-radius:8px;padding:8px 10px;font-weight:700}
.report-btn{cursor:pointer}.report-btn:hover{background:#334155}.report-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:10px;margin-top:14px}
.report-metric{background:#0b1220;border:1px solid #273449;border-radius:12px;padding:12px}.report-metric .rv{font-size:21px;font-weight:700;margin-top:5px}
.report-charts{display:grid;grid-template-columns:1.4fr .8fr;gap:12px;margin-top:14px}.report-chart-wrap{height:260px;background:#0b1220;border-radius:12px;overflow:hidden;margin-top:8px}
.badge{display:inline-block;padding:4px 9px;border-radius:999px;font-size:12px;font-weight:700}.pos{background:#12311f;color:#86efac}.neg{background:#3a1717;color:#fca5a5}.be{background:#273449;color:#cbd5e1}.hidden{display:none!important}.print-only{display:none}
@media(max-width:850px){.report-charts{grid-template-columns:1fr}}
@media(max-width:600px){.report-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.report-chart-wrap{height:220px}.report-metric .rv{font-size:18px}}
@media print{body{background:#fff;color:#111;padding:0}.wrap{max-width:none}.grid,#status,.sub,.equity-card,#openTradeCard,#tradesCard,#stateCard,.report-controls,.report-actions{display:none!important}#reportCard{background:#fff;color:#111;border:none;padding:0}.report-metric{background:#fff;border:1px solid #bbb}.muted{color:#444!important}.print-only{display:block}.report-charts{grid-template-columns:1fr 1fr}table{font-size:10px}}

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

  <div class="card equity-card">
    <div class="muted">Curva de equity</div>
    <div class="equity-chart-wrap" id="equityChartWrap">
      <canvas id="equityChart"></canvas>
    </div>
    <div class="equity-stats">
      <span id="equityRange">Rango: -</span>
      <span id="equityPoints">0 puntos</span>
    </div>
  </div>


  <div class="card" id="reportCard" style="margin-top:16px">
    <div class="print-only"><h1>Reporte de simulación</h1><div id="printMeta"></div><hr></div>
    <div class="report-title"><h2>Reporte de rendimiento</h2><div class="report-controls">
      <select id="reportRange"><option value="today">Hoy</option><option value="7">Últimos 7 días</option><option value="15" selected>Últimos 15 días</option><option value="30">Últimos 30 días</option><option value="all">Todo</option><option value="custom">Rango personalizado</option></select>
      <input id="reportStart" type="date" class="hidden"><input id="reportEnd" type="date" class="hidden">
      <button class="report-btn" id="generateReport">Generar reporte</button>
    </div></div>
    <div class="report-actions" style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px"><button class="report-btn" id="printReport">Imprimir / Guardar PDF</button><button class="report-btn" id="csvReport">Exportar CSV</button></div>
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
    <div class="report-charts"><div><div class="muted">Ganancia / pérdida diaria</div><div class="report-chart-wrap"><canvas id="dailyChart"></canvas></div></div><div><div class="muted">Ganadas vs perdidas</div><div class="report-chart-wrap"><canvas id="wlChart"></canvas></div></div></div>
    <div style="margin-top:14px"><div class="muted">Detalle diario</div><div class="scroll"><table><thead><tr><th>Fecha</th><th>Estado</th><th>Trades</th><th>Ganadas</th><th>Perdidas</th><th>WR</th><th>Gan. bruta</th><th>Pérdida</th><th>Neto</th><th>PF</th><th>Rentab.</th><th>DD</th><th>Saldo final</th></tr></thead><tbody id="dailyRows"><tr><td colspan="13">Sin datos</td></tr></tbody></table></div></div>
  </div>

  <div class="card" id="tradesCard" style="margin-top:16px">
    <div class="muted">Últimos trades cerrados</div>
    <div class="scroll">
      <table>
        <thead><tr><th>Hora UTC</th><th>Dir.</th><th>Entrada</th><th>Salida</th><th>Motivo</th><th>P&L</th><th>Saldo</th></tr></thead>
        <tbody id="tradeRows"><tr><td colspan="7">Sin trades todavía</td></tr></tbody>
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

  <div class="card" id="stateCard" style="margin-top:16px">
    <div class="muted">Estado</div>
    <div id="updated">-</div>
    <div id="note" class="muted" style="margin-top:8px"></div>
    <div id="error" class="bad" style="margin-top:8px"></div>
  </div>
</div>

<script>
function money(v){const n=Number(v||0);return (n>=0?'+$':'-$')+Math.abs(n).toFixed(2)}
function num(v,d=3){const n=Number(v);return Number.isFinite(n)?n.toFixed(d):'-'}
function card(label,value){return `<div><div class="muted">${label}</div><div class="small-value">${value}</div></div>`}

const TRADES_PER_PAGE = 30;
let currentTradePage = 1;
let totalTradePages = 1;
let lastEquityRows = [];
let resizeTimer = null;

function updatePagination(meta){
  const total=Number(meta.total||0);
  const page=Number(meta.page||1);
  const perPage=Number(meta.per_page||TRADES_PER_PAGE);
  const totalPages=Math.max(1,Number(meta.total_pages||1));
  currentTradePage=page;
  totalTradePages=totalPages;

  const start=total ? ((page-1)*perPage)+1 : 0;
  const end=total ? Math.min(page*perPage,total) : 0;
  document.getElementById('paginationInfo').textContent = total
    ? `Mostrando ${start}–${end} de ${total} operaciones`
    : 'Mostrando 0 operaciones';
  document.getElementById('pageNumber').textContent=`Página ${page} de ${totalPages}`;
  document.getElementById('prevPage').disabled=page<=1;
  document.getElementById('nextPage').disabled=page>=totalPages;
}

function drawEquity(rows){
  const canvas=document.getElementById('equityChart');
  const wrap=document.getElementById('equityChartWrap');
  const ctx=canvas.getContext('2d');

  const cssWidth=Math.max(280,wrap.clientWidth);
  const cssHeight=Math.max(180,wrap.clientHeight);
  const dpr=Math.min(window.devicePixelRatio||1,2);

  canvas.width=Math.round(cssWidth*dpr);
  canvas.height=Math.round(cssHeight*dpr);
  canvas.style.width=cssWidth+'px';
  canvas.style.height=cssHeight+'px';
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,cssWidth,cssHeight);

  const cleanRows=(rows||[]).filter(x=>Number.isFinite(Number(x.equity)));
  const vals=cleanRows.map(x=>Number(x.equity));

  document.getElementById('equityPoints').textContent=`${vals.length} punto${vals.length===1?'':'s'}`;

  if(!vals.length){
    document.getElementById('equityRange').textContent='Rango: -';
    ctx.fillStyle='#94a3b8';
    ctx.font='14px Arial';
    ctx.fillText('Sin datos de equity todavía',18,30);
    return;
  }

  const min=Math.min(...vals), max=Math.max(...vals);
  document.getElementById('equityRange').textContent=`Min $${min.toFixed(2)} · Max $${max.toFixed(2)}`;

  const rawSpan=max-min;
  const padValue=Math.max(rawSpan*0.12,0.50);
  const chartMin=min-padValue;
  const chartMax=max+padValue;
  const span=Math.max(0.01,chartMax-chartMin);

  const isMobile=cssWidth<600;
  const left=isMobile?42:58;
  const right=isMobile?12:20;
  const top=16;
  const bottom=isMobile?30:34;
  const plotW=Math.max(1,cssWidth-left-right);
  const plotH=Math.max(1,cssHeight-top-bottom);

  // Fondo y rejilla horizontal.
  ctx.strokeStyle='#1e293b';
  ctx.lineWidth=1;
  ctx.font=isMobile?'10px Arial':'11px Arial';
  ctx.fillStyle='#94a3b8';
  ctx.textAlign='right';
  ctx.textBaseline='middle';

  const yTicks=isMobile?3:5;
  for(let i=0;i<yTicks;i++){
    const ratio=i/(yTicks-1);
    const y=top+plotH*ratio;
    const value=chartMax-span*ratio;
    ctx.beginPath();
    ctx.moveTo(left,y);
    ctx.lineTo(left+plotW,y);
    ctx.stroke();
    ctx.fillText('$'+value.toFixed(0),left-7,y);
  }

  // Eje inferior tenue.
  ctx.beginPath();
  ctx.moveTo(left,top+plotH);
  ctx.lineTo(left+plotW,top+plotH);
  ctx.stroke();

  // Curva de equity.
  const gradient=ctx.createLinearGradient(0,top,0,top+plotH);
  gradient.addColorStop(0,'rgba(147,197,253,0.18)');
  gradient.addColorStop(1,'rgba(147,197,253,0.01)');

  const points=vals.map((v,i)=>({
    x:left+(vals.length===1?plotW/2:plotW*(i/(vals.length-1))),
    y:top+plotH*(1-(v-chartMin)/span)
  }));

  if(points.length>1){
    ctx.beginPath();
    ctx.moveTo(points[0].x,top+plotH);
    points.forEach((pt,i)=>i===0?ctx.lineTo(pt.x,pt.y):ctx.lineTo(pt.x,pt.y));
    ctx.lineTo(points[points.length-1].x,top+plotH);
    ctx.closePath();
    ctx.fillStyle=gradient;
    ctx.fill();
  }

  ctx.beginPath();
  points.forEach((pt,i)=>i===0?ctx.moveTo(pt.x,pt.y):ctx.lineTo(pt.x,pt.y));
  ctx.strokeStyle='#93c5fd';
  ctx.lineWidth=isMobile?2:2.5;
  ctx.lineJoin='round';
  ctx.lineCap='round';
  ctx.stroke();

  // Último valor resaltado.
  const last=points[points.length-1];
  ctx.beginPath();
  ctx.arc(last.x,last.y,isMobile?3:4,0,Math.PI*2);
  ctx.fillStyle='#bfdbfe';
  ctx.fill();

  ctx.textAlign='left';
  ctx.textBaseline='alphabetic';
  ctx.fillStyle='#cbd5e1';
  ctx.font=isMobile?'11px Arial':'12px Arial';
  const label=`$${vals[vals.length-1].toFixed(2)}`;
  const labelX=Math.min(last.x+7,cssWidth-right-ctx.measureText(label).width);
  const labelY=Math.max(top+12,last.y-8);
  ctx.fillText(label,labelX,labelY);
}



function reportPF(v){const n=Number(v);return v==null?'N/A':(!Number.isFinite(n)||n>=999999?'∞':n.toFixed(2))}
function fmtDate(s){if(!s)return '-';const p=s.split('-');return `${p[2]}/${p[1]}/${p[0]}`}
function reportQuery(){const r=document.getElementById('reportRange').value;const q=new URLSearchParams({range:r});if(r==='custom'){q.set('start',document.getElementById('reportStart').value);q.set('end',document.getElementById('reportEnd').value)}return q.toString()}
function setupReportCanvas(id){const c=document.getElementById(id),w=c.parentElement.clientWidth,h=c.parentElement.clientHeight,d=Math.min(devicePixelRatio||1,2);c.width=w*d;c.height=h*d;c.style.width=w+'px';c.style.height=h+'px';const x=c.getContext('2d');x.setTransform(d,0,0,d,0,0);x.clearRect(0,0,w,h);return {x,w,h}}
function drawDaily(d){const {x,w,h}=setupReportCanvas('dailyChart');if(!d.length){x.fillStyle='#94a3b8';x.fillText('Sin datos',20,30);return}const vals=d.map(v=>Number(v.net||0)),m=Math.max(1,...vals.map(Math.abs)),L=44,R=12,T=16,B=34,pw=w-L-R,ph=h-T-B,z=T+ph/2;x.strokeStyle='#334155';x.beginPath();x.moveTo(L,z);x.lineTo(L+pw,z);x.stroke();const step=pw/d.length,bw=Math.max(5,step*.62);x.font='10px Arial';x.textAlign='center';d.forEach((v,i)=>{const n=Number(v.net||0),bh=Math.abs(n)/m*(ph/2-8),xx=L+i*step+(step-bw)/2,yy=n>=0?z-bh:z;x.fillStyle=n>=0?'#22c55e':'#ef4444';x.fillRect(xx,yy,bw,bh);if(d.length<=15){x.fillStyle='#94a3b8';x.fillText(v.date.slice(5),xx+bw/2,h-12)}})}
function drawWL(wins,losses){const {x,w,h}=setupReportCanvas('wlChart'),t=wins+losses;if(!t){x.fillStyle='#94a3b8';x.fillText('Sin datos',20,30);return}const cx=w/2,cy=h/2-4,r=Math.min(w,h)*.31,p=wins/t;let a=-Math.PI/2;x.beginPath();x.moveTo(cx,cy);x.arc(cx,cy,r,a,a+p*Math.PI*2);x.closePath();x.fillStyle='#22c55e';x.fill();a+=p*Math.PI*2;x.beginPath();x.moveTo(cx,cy);x.arc(cx,cy,r,a,a+(1-p)*Math.PI*2);x.closePath();x.fillStyle='#ef4444';x.fill();x.beginPath();x.arc(cx,cy,r*.58,0,Math.PI*2);x.fillStyle='#0b1220';x.fill();x.textAlign='center';x.fillStyle='#e2e8f0';x.font='700 22px Arial';x.fillText((p*100).toFixed(1)+'%',cx,cy);x.font='12px Arial';x.fillStyle='#94a3b8';x.fillText('Win Rate',cx,cy+19)}
let lastReport=null;
async function loadReport(){try{const r=await fetch('/api/report?'+reportQuery()),d=await r.json(),o=d.overall||{};lastReport=d;document.getElementById('rPeriod').textContent=`${fmtDate(d.start_date)} – ${fmtDate(d.end_date)}`;document.getElementById('rStart').textContent='$'+Number(o.start_balance||0).toFixed(2);document.getElementById('rEnd').textContent='$'+Number(o.end_balance||0).toFixed(2);document.getElementById('rNet').textContent=money(o.net);document.getElementById('rNet').className='rv '+(Number(o.net)>=0?'good':'bad');document.getElementById('rReturn').textContent=Number(o.profitability_pct||0).toFixed(2)+'%';document.getElementById('rTrades').textContent=o.total||0;document.getElementById('rWins').textContent=o.wins||0;document.getElementById('rLosses').textContent=o.losses||0;document.getElementById('rWR').textContent=Number(o.win_rate||0).toFixed(2)+'%';document.getElementById('rPF').textContent=reportPF(o.profit_factor);document.getElementById('rGP').textContent='+$'+Number(o.gross_profit||0).toFixed(2);document.getElementById('rGL').textContent='-$'+Number(o.gross_loss||0).toFixed(2);document.getElementById('rDD').textContent=Number(o.max_drawdown_pct||0).toFixed(2)+'%';document.getElementById('rPos').textContent=o.positive_days||0;document.getElementById('rNeg').textContent=o.negative_days||0;document.getElementById('rAvg').textContent=money(o.avg_daily_net||0);document.getElementById('rBest').textContent=o.best_day?fmtDate(o.best_day.date)+' '+money(o.best_day.net):'-';document.getElementById('rWorst').textContent=o.worst_day?fmtDate(o.worst_day.date)+' '+money(o.worst_day.net):'-';document.getElementById('rWS').textContent=o.max_win_streak||0;document.getElementById('rLS').textContent=o.max_loss_streak||0;const daily=d.daily||[];document.getElementById('dailyRows').innerHTML=daily.length?daily.slice().reverse().map(v=>`<tr><td>${fmtDate(v.date)}</td><td>${v.status==='positive'?'<span class="badge pos">POSITIVO</span>':v.status==='negative'?'<span class="badge neg">NEGATIVO</span>':'<span class="badge be">BREAK-EVEN</span>'}</td><td>${v.total}</td><td>${v.wins}</td><td>${v.losses}</td><td>${Number(v.win_rate).toFixed(2)}%</td><td class="good">+$${Number(v.gross_profit).toFixed(2)}</td><td class="bad">-$${Number(v.gross_loss).toFixed(2)}</td><td class="${Number(v.net)>=0?'good':'bad'}">${money(v.net)}</td><td>${reportPF(v.profit_factor)}</td><td>${Number(v.profitability_pct).toFixed(2)}%</td><td>${Number(v.max_drawdown_pct).toFixed(2)}%</td><td>$${Number(v.end_balance).toFixed(2)}</td></tr>`).join(''):'<tr><td colspan="13">Sin datos</td></tr>';drawDaily(daily);drawWL(Number(o.wins||0),Number(o.losses||0));document.getElementById('printMeta').textContent=`Periodo ${fmtDate(d.start_date)} – ${fmtDate(d.end_date)} · America/Managua`; }catch(e){console.error('Reporte',e)}}
document.getElementById('reportRange').addEventListener('change',e=>{const c=e.target.value==='custom';document.getElementById('reportStart').classList.toggle('hidden',!c);document.getElementById('reportEnd').classList.toggle('hidden',!c)});
document.getElementById('generateReport').addEventListener('click',loadReport);document.getElementById('printReport').addEventListener('click',()=>window.print());document.getElementById('csvReport').addEventListener('click',()=>location.href='/api/report.csv?'+reportQuery());

async function refresh(){
  try{
    const [sr,tr,er]=await Promise.all([
      fetch('/api/state'),
      fetch(`/api/trades?page=${currentTradePage}&per_page=${TRADES_PER_PAGE}`),
      fetch('/api/equity')
    ]);
    const s=await sr.json(), tradeData=await tr.json(), equity=await er.json();
    const trades=Array.isArray(tradeData) ? tradeData : (tradeData.trades||[]);
    if(!Array.isArray(tradeData)) updatePagination(tradeData);

    document.getElementById('status').textContent=s.running?'SIMULADOR ACTIVO':'RECONECTANDO';
    document.getElementById('subtitle').textContent=`${s.symbol||'Crash 500'} · ${s.strategy||'hybrid_long_only'} · ${s.granularity||60}s · sin órdenes`;
    document.getElementById('balance').textContent='$'+Number(s.balance||0).toFixed(2);
    document.getElementById('equity').textContent='$'+Number(s.equity||0).toFixed(2);

    for(const [id,val] of [['pnl',s.pnl],['floating',s.floating_pnl]]){
      const el=document.getElementById(id);el.textContent=money(val);el.className='value '+(Number(val||0)>=0?'good':'bad');
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
      ? card('Dirección',p.direction)+card('Entrada',num(p.entry_price))+card('SL',num(p.stop_price))+card('TP',num(p.take_price))+card('Unidades virtuales',num(p.position_size,5))+card('Riesgo',Number(p.risk_pct||0).toFixed(2)+'%')
      : '<div>Ninguna</div>';

    const tbody=document.getElementById('tradeRows');
    tbody.innerHTML=trades.length ? trades.map(t=>
      `<tr><td>${t.exit_time||'-'}</td><td>${t.direction||'-'}</td><td>${num(t.entry_price)}</td><td>${num(t.exit_price)}</td><td>${t.exit_reason||'-'}</td><td class="${Number(t.profit)>=0?'good':'bad'}">${money(t.profit)}</td><td>$${Number(t.balance_after||0).toFixed(2)}</td></tr>`
    ).join('') : '<tr><td colspan="7">Sin trades todavía</td></tr>';

    lastEquityRows = Array.isArray(equity) ? equity : [];
    drawEquity(lastEquityRows);
    document.getElementById('updated').textContent='Actualizado: '+(s.updated_at||'-');
    document.getElementById('note').textContent=s.note||'';
    document.getElementById('error').textContent=s.last_error?'Último error: '+s.last_error:'';
  }catch(e){
    document.getElementById('status').textContent='SIN CONEXIÓN';
  }
}

document.getElementById('prevPage').addEventListener('click',()=>{
  if(currentTradePage>1){currentTradePage--;refresh();}
});
document.getElementById('nextPage').addEventListener('click',()=>{
  if(currentTradePage<totalTradePages){currentTradePage++;refresh();}
});

window.addEventListener('resize',()=>{
  clearTimeout(resizeTimer);
  resizeTimer=setTimeout(()=>{drawEquity(lastEquityRows);if(lastReport){drawDaily(lastReport.daily||[]);drawWL(Number(lastReport.overall?.wins||0),Number(lastReport.overall?.losses||0));}},120);
});

refresh();loadReport();setInterval(refresh,5000);setInterval(loadReport,30000);
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML)


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
    return jsonify(json.loads(STATE_FILE.read_text(encoding="utf-8")))


@app.route("/api/trades")
def api_trades():
    # Compatibilidad: sin parámetros conserva la respuesta antigua (últimos 30).
    if "page" not in request.args and "per_page" not in request.args:
        return jsonify(read_jsonl(TRADES_FILE, limit=30))

    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(request.args.get("per_page", 30))
    except (TypeError, ValueError):
        per_page = 30
    per_page = min(100, max(1, per_page))

    # El archivo está guardado cronológicamente. Invertimos para mostrar primero
    # las operaciones más recientes y luego paginamos.
    rows = list(reversed(read_all_jsonl(TRADES_FILE)))
    total = len(rows)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    start = (page - 1) * per_page
    page_rows = rows[start:start + per_page]

    # El frontend ya no necesita invertir la página porque aquí llega newest-first.
    return jsonify({
        "trades": page_rows,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
    })



@app.route("/api/report")
def api_report():
    return jsonify(_build_report())


@app.route("/api/report.csv")
def api_report_csv():
    report = _build_report()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Fecha","Estado","Trades","Ganadas","Perdidas","Win Rate %","Ganancia bruta","Perdida bruta","Neto","Profit Factor","Rentabilidad %","Max DD %","Saldo inicial","Saldo final"])
    for d in report["daily"]:
        pf = d["profit_factor"]
        pf = "N/A" if pf is None else ("INF" if pf >= 999999 else f"{pf:.4f}")
        w.writerow([d["date"],d["status"],d["total"],d["wins"],d["losses"],f'{d["win_rate"]:.4f}',f'{d["gross_profit"]:.4f}',f'{d["gross_loss"]:.4f}',f'{d["net"]:.4f}',pf,f'{d["profitability_pct"]:.4f}',f'{d["max_drawdown_pct"]:.4f}',f'{d["start_balance"]:.4f}',f'{d["end_balance"]:.4f}'])
    name=f'reporte_trading_{report["start_date"]}_a_{report["end_date"]}.csv'
    return Response(buf.getvalue(), mimetype="text/csv; charset=utf-8", headers={"Content-Disposition":f'attachment; filename="{name}"'})


@app.route("/api/equity")
def api_equity():
    return jsonify(read_jsonl(EQUITY_FILE, limit=200))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
