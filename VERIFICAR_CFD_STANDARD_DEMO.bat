@echo off
cd /d "%~dp0"
python run_demo_trader.py --product cfd_standard --mode demo --check
pause
