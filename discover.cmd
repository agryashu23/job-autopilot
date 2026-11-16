@echo off
set PYTHONUTF8=1
cd /d "%~dp0"
echo ==== %date% %time% ==== >> logs\scheduled.log
.venv\Scripts\python.exe run.py discover >> logs\scheduled.log 2>&1
