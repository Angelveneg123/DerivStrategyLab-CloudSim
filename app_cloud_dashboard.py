
import json
from pathlib import Path
from flask import Flask, jsonify, render_template_string

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "cloud_sim" / "state.json"
TRADES_FILE = BASE_DIR / "cloud_sim" / "trades.jsonl"

app = Flask(__name__)

HTML = """
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CFD Standard Simulation Dashboard</title>
<style>
body{font-family:Arial,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:20px}
.wrap{max-width:1100px;margin:auto}
h1{margin-bottom:4px}.sub{color:#94a3b8;margin-bottom:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.card{background:#111827;border:1px solid #334155;border-radius:14px;padding:16px}
.value{font-size:28px;font-weight:700;margin-top:8px}
.good{color:#86efac}.bad{color:#fca5a5}.muted{color:#94a3b8}
table{width:100%;border-collapse:collapse;margin-top:12px}
td,th{padding:10px;border-bottom:1px solid #334155;text-align:left}
.status{display:inline-block;padding:6px 10px;border-radius:999px;background:#1e293b}
</style>
</head>
<body>
<div class="wrap">
  <h1>CFD Standard Simulation</h1>
  <div class="sub">Crash 500 · hybrid_long_only · simulación sin órdenes MT5</div>
  <div id="status" class="status">Cargando...</div>
  <div class="grid" style="margin-top:16px">
    <div class="card"><div class="muted">Saldo</div><div id="balance" class="value">$0.00</div></div>
    <div class="card"><div class="muted">P&L</div><div id="pnl" class="value">$0.00</div></div>
    <div class="card"><div class="muted">Trades</div><div id="trades" class="value">0</div></div>
    <div class="card"><div class="muted">Win Rate</div><div id="wr" class="value">0%</div></div>
    <div class="card"><div class="muted">Max DD</div><div id="dd" class="value">0%</div></div>
    <div class="card"><div class="muted">Última señal</div><div id="signal" class="value">HOLD</div></div>
  </div>
  <div class="card" style="margin-top:16px">
    <div class="muted">Operación abierta</div>
    <pre id="openTrade">Ninguna</pre>
  </div>
  <div class="card" style="margin-top:16px">
    <div class="muted">Estado</div>
    <div id="updated">-</div>
    <div id="note" class="muted" style="margin-top:8px"></div>
  </div>
</div>
<script>
async function refresh(){
  try{
    const r = await fetch('/api/state');
    const s = await r.json();
    document.getElementById('status').textContent = s.running ? 'SIMULADOR ACTIVO' : 'DETENIDO';
    document.getElementById('balance').textContent = '$' + Number(s.balance||0).toFixed(2);
    const pnl = Number(s.pnl||0);
    const pnlEl = document.getElementById('pnl');
    pnlEl.textContent = (pnl>=0?'+$':'-$') + Math.abs(pnl).toFixed(2);
    pnlEl.className = 'value ' + (pnl>=0?'good':'bad');
    document.getElementById('trades').textContent = s.trades||0;
    document.getElementById('wr').textContent = Number(s.win_rate||0).toFixed(2)+'%';
    document.getElementById('dd').textContent = Number(s.max_drawdown_pct||0).toFixed(2)+'%';
    document.getElementById('signal').textContent = s.last_signal || 'HOLD';
    document.getElementById('openTrade').textContent = s.open_trade ? JSON.stringify(s.open_trade,null,2) : 'Ninguna';
    document.getElementById('updated').textContent = 'Actualizado: ' + (s.updated_at||'-');
    document.getElementById('note').textContent = s.note||'';
  }catch(e){
    document.getElementById('status').textContent = 'SIN CONEXIÓN';
  }
}
refresh();
setInterval(refresh,5000);
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
            "balance": 100.0,
            "pnl": 0.0,
            "trades": 0,
            "win_rate": 0.0,
            "max_drawdown_pct": 0.0,
            "last_signal": "HOLD",
            "open_trade": None,
            "updated_at": None,
            "note": "Worker todavía no ha creado state.json"
        })
    return jsonify(json.loads(STATE_FILE.read_text(encoding="utf-8")))

if __name__ == "__main__":
    port = int(__import__("os").environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
