@echo off
cd /d "%~dp0"
echo ===============================================================
echo DERIV STRATEGY LAB - QSL v0.1 - CFD STANDARD DEMO
 echo Estrategia: Estructura + Liquidez + FVG/OB + MTF
 echo MODO DEMO. La cuenta REAL permanece deshabilitada.
echo ===============================================================
python run_demo_trader.py --product cfd_standard --mode demo --strategy quant_structure_liquidity_v01
pause
