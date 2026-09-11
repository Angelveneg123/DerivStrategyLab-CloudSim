import json
import os
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

DATA_ROOT = Path(os.getenv("SIM_DATA_DIR", "/data/cloud_sim"))
if not DATA_ROOT.exists():
    DATA_ROOT = Path(__file__).resolve().parent / "cloud_sim"

STATE_FILE = DATA_ROOT / "state.json"
TRADES_FILE = DATA_ROOT / "trades.jsonl"
EQUITY_FILE = DATA_ROOT / "equity.jsonl"

app = Flask(__name__)


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

  <div class="card" style="margin-top:16px">
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

  <div class="card" style="margin-top:16px">
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

  <div class="card" style="margin-top:16px">
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
  resizeTimer=setTimeout(()=>drawEquity(lastEquityRows),120);
});

refresh();setInterval(refresh,5000);
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


@app.route("/api/equity")
def api_equity():
    return jsonify(read_jsonl(EQUITY_FILE, limit=200))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
