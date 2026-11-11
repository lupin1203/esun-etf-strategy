@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
python esun_daily.py >> esun_logs\scheduler.log 2>&1