#!/bin/sh
set -e
python worker_simulation.py &
exec python app_cloud_dashboard.py
