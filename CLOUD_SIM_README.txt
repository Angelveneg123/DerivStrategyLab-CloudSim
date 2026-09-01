CFD STANDARD SIMULATION CLOUD

Procesos:
1) worker_simulation.py
   - mantiene la simulación viva.
   - NO envía órdenes a MT5.
   - guarda estado en cloud_sim/state.json

2) app_cloud_dashboard.py
   - dashboard web.
   - endpoint /api/state

Variables opcionales:
SIM_SYMBOL=CRASH500
SIM_STRATEGY=hybrid_long_only
SIM_START_BALANCE=100
SIM_POLL_SECONDS=20
PORT=8000

Local:
pip install -r requirements-cloud.txt
python worker_simulation.py
python app_cloud_dashboard.py

Para nube:
- Crear un Web Service con: python app_cloud_dashboard.py
- Crear un Worker/Background Service con: python worker_simulation.py
- Ambos deben compartir el mismo volumen/disco persistente si el proveedor separa servicios.
