@echo off
:: MeznaQuantFX — MT5 Bridge Service Launcher
:: Run this file to start the bridge. MT5 terminal must be open and logged in.
::
:: Prerequisites:
::   pip install -r requirements.txt
::   Copy .env.example to .env and fill in MT5_ACCOUNT, MT5_PASSWORD, MT5_SERVER

title MeznaQuantFX MT5 Bridge

cd /d "%~dp0"

echo ========================================
echo  MeznaQuantFX MT5 Bridge Service
echo  Port: 8010
echo ========================================
echo.
echo IMPORTANT: MetaTrader 5 terminal must be running and logged in.
echo.

python -m uvicorn app.main:app --host 0.0.0.0 --port 8010 --reload

pause
