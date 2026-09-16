#!/bin/sh
set -e

python worker_keepalive.py &
exec python app_cloud_dashboard.py
