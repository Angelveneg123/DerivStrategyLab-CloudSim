"""Supervise the market worker and dashboard without changing trading signals."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv("SIM_DATA_DIR", "/data/cloud_sim"))
stopping = False

def stop_requested(signum, frame):
    global stopping
    stopping = True

def stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

def main():
    DATA.mkdir(parents=True, exist_ok=True)
    os.environ["SIM_DATA_DIR"] = str(DATA)
    os.environ["PYTHONUNBUFFERED"] = "1"
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop_requested)
    web = subprocess.Popen([sys.executable, "app_cloud_dashboard.py"], cwd=ROOT)
    worker = None
    try:
        while not stopping:
            if web.poll() is not None:
                raise RuntimeError("Dashboard termino; reiniciando servicio.")
            if worker is None:
                worker = subprocess.Popen([sys.executable, "worker_keepalive.py"], cwd=ROOT)
                started = time.time()
                print("[Supervisor] Worker iniciado.", flush=True)
            state = DATA / "state.json"
            last_write = state.stat().st_mtime if state.exists() else started
            stale = time.time() - max(started, last_write) > 180
            if worker.poll() is not None or stale:
                print("[Supervisor] Worker detenido o sin actualizar por 180s; reinicio.", flush=True)
                stop_process(worker)
                worker = None
            time.sleep(2)
    finally:
        if worker is not None:
            stop_process(worker)
        stop_process(web)

if __name__ == "__main__":
    main()

