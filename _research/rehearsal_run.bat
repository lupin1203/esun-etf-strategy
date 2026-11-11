@echo off
cd /d "%~dp0.."
set PYTHONUTF8=1
if not exist _research\rehearsal mkdir _research\rehearsal
python _research\esun_rehearsal.py >> _research\rehearsal\rehearsal.log 2>&1
