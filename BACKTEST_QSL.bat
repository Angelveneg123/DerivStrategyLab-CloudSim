@echo off
cd /d "%~dp0"
python run_backtest_report.py --strategy quant_structure_liquidity_v01 --symbol "CRASH500" --granularity 60
pause
