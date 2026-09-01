"""Servidor local de producción para el dashboard."""

import os

from waitress import serve

from app_web import app


if __name__ == "__main__":
    host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.getenv("DASHBOARD_PORT", "5000"))
    print(f"Dashboard disponible en http://{host}:{port}")
    serve(app, host=host, port=port, threads=6)
