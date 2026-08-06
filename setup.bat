@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo === AI Sports Commentator Setup ===

python -c "import sys; assert sys.version_info >= (3,10), 'Python 3.10+ required'; print('Python', sys.version.split()[0], 'OK')" || goto :error

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo FFmpeg is missing. Attempting installation with winget...
  where winget >nul 2>nul || (echo Install FFmpeg manually and add it to PATH. & goto :error)
  winget install --id Gyan.FFmpeg --exact --accept-source-agreements --accept-package-agreements || goto :error
  echo Restart this terminal if ffmpeg is still not found after setup.
)

where ollama >nul 2>nul
if errorlevel 1 (
  echo Ollama is missing. Attempting installation with winget...
  where winget >nul 2>nul || (echo Install Ollama from https://ollama.com/download/windows & goto :error)
  winget install --id Ollama.Ollama --exact --accept-source-agreements --accept-package-agreements || goto :error
  set "PATH=%LOCALAPPDATA%\Programs\Ollama;%PATH%"
)

if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat || goto :error
python -m pip install --upgrade pip wheel setuptools || goto :error
echo Installing CPU-only PyTorch - no CUDA libraries...
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu || goto :error
python -m pip install -r requirements.txt || goto :error
python install_models.py || goto :error
python -m config.hardware_detect

echo.
echo Setup complete! Run: run.bat
exit /b 0

:error
echo.
echo Setup failed. Fix the message above, then run setup.bat again.
exit /b 1
