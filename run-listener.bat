@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  python "%~dp0listener.py" >> "%~dp0listener.log" 2>&1
) else (
  py -3 "%~dp0listener.py" >> "%~dp0listener.log" 2>&1
)
