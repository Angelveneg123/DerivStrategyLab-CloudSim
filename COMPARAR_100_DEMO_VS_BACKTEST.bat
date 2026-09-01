@echo off
cd /d "%~dp0"
python compare_demo_backtest.py --count 100 --tolerance 5
pause
