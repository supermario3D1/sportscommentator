@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe goto :first_setup
if not exist .venv\.setup_complete goto :first_setup
goto :launch

:first_setup
echo ==============================================================
echo  First launch detected - starting one-time installation
echo  This downloads several GB and can take 10-60 minutes.
echo ==============================================================
call setup.bat
if errorlevel 1 (
  echo.
  echo Installation did not finish. Read the message above, then try run.bat again.
  pause
  exit /b 1
)

:launch
.venv\Scripts\python.exe -m app.main %*
if errorlevel 1 (
  echo.
  echo The application stopped with an error. See the message above.
  pause
  exit /b 1
)
exit /b 0
