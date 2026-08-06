@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WinGet\Links;%LOCALAPPDATA%\Programs\Ollama"
set "PYTHON_EXE=python"

echo ==============================================================
echo  AI Sports Commentator - Windows setup
echo ==============================================================
echo The first setup downloads several GB and may take 10-60 minutes.
echo.

echo [1/6] Checking Python, FFmpeg, and Ollama...
"%PYTHON_EXE%" -c "import sys; assert sys.version_info >= (3,10)" >nul 2>nul
if errorlevel 1 goto :install_python
goto :python_ready

:install_python
where winget >nul 2>nul
if errorlevel 1 goto :no_winget
echo Python 3.10 or newer was not found. Installing Python 3.12...
winget install --id Python.Python.3.12 --exact --scope user --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto :error
set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PYTHON_EXE%" (
  echo Python was installed, but Windows has not refreshed PATH yet.
  echo Close this window and double-click run.bat again.
  exit /b 1
)

:python_ready
"%PYTHON_EXE%" -c "import sys,shutil,pathlib; assert sys.version_info >= (3,10), 'Python 3.10+ required'; free=shutil.disk_usage(pathlib.Path.cwd()).free/1024**3; print('Python',sys.version.split()[0],'OK -',round(free,1),'GiB free'); assert free >= 15, 'At least 15 GiB free disk space is required'"
if errorlevel 1 goto :error

where ffmpeg >nul 2>nul
if errorlevel 1 (
  where winget >nul 2>nul
  if errorlevel 1 goto :no_winget
  echo Installing FFmpeg...
  winget install --id Gyan.FFmpeg --exact --accept-source-agreements --accept-package-agreements
  if errorlevel 1 goto :error
  set "PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WinGet\Links"
)

where ollama >nul 2>nul
if errorlevel 1 (
  where winget >nul 2>nul
  if errorlevel 1 goto :no_winget
  echo Installing Ollama...
  winget install --id Ollama.Ollama --exact --accept-source-agreements --accept-package-agreements
  if errorlevel 1 goto :error
  set "PATH=%PATH%;%LOCALAPPDATA%\Programs\Ollama;%LOCALAPPDATA%\Microsoft\WinGet\Links"
)

echo.
echo [2/6] Creating an isolated Python environment...
if not exist .venv\Scripts\python.exe "%PYTHON_EXE%" -m venv .venv
if errorlevel 1 goto :error

set "VENV_PYTHON=.venv\Scripts\python.exe"
"%VENV_PYTHON%" -m pip install --upgrade pip wheel setuptools
if errorlevel 1 goto :error

echo.
echo [3/6] Installing CPU-only AI and application packages...
"%VENV_PYTHON%" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 goto :error
"%VENV_PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo [4/6] Confirming local services...
where ffmpeg >nul 2>nul
if errorlevel 1 goto :refresh_path
where ollama >nul 2>nul
if errorlevel 1 goto :refresh_path

echo.
echo [5/6] Downloading and verifying YOLO, Piper, Phi-3, and TinyLlama...
"%VENV_PYTHON%" install_models.py
if errorlevel 1 goto :error

echo.
echo [6/6] Checking this computer...
"%VENV_PYTHON%" -m config.hardware_detect
if errorlevel 1 goto :error
type nul > .venv\.setup_complete

echo.
echo ==============================================================
echo  Setup complete
echo ==============================================================
echo The application will start now when setup was launched by run.bat.
echo For later launches, double-click run.bat.
echo Then open http://localhost:7860
exit /b 0

:refresh_path
echo.
echo Windows installed a program but has not refreshed PATH yet.
echo Close this window and double-click run.bat again. Setup will continue.
exit /b 1

:no_winget
echo.
echo ERROR: Windows Package Manager ^(winget^) was not found.
echo Install "App Installer" from Microsoft Store, restart Windows, and run run.bat again.
echo Manual installation instructions are in INSTALL.md.
exit /b 1

:error
echo.
echo SETUP FAILED. Fix the message above, then run run.bat again.
echo Completed packages and model downloads will be reused.
exit /b 1
