@echo off
setlocal
cd /d "%~dp0"

:: Guard loop: restart listener.py if it exits for any reason,
:: so card callbacks are never left without a receiver.
:loop
where py >nul 2>nul
if errorlevel 1 (
  python "%~dp0listener.py" >> "%~dp0listener.log" 2>&1
) else (
  py -3 "%~dp0listener.py" >> "%~dp0listener.log" 2>&1
)

echo [%date% %time%] listener exited, restarting in 5s... >> "%~dp0listener.log"
ping -n 6 127.0.0.1 >nul
goto loop
