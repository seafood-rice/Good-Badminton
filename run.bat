@echo off
REM ============================================================
REM  Good-Badminton - One-click Windows launcher
REM  Creates a venv, installs deps, downloads model weights,
REM  starts the Flask web UI, and opens it in your browser.
REM ============================================================
setlocal
cd /d "%~dp0"

echo.
echo === Good-Badminton setup ===
echo.

REM --- 1. Check Python ---
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found on PATH.
    echo Install Python 3.8+ from https://www.python.org/downloads/ and re-run.
    pause
    exit /b 1
)

REM --- 2. Check FFmpeg (needed for browser-playable H.264 output) ---
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [WARN] FFmpeg not found on PATH. Video output may not play in browser.
    echo        Install with:  winget install Gyan.FFmpeg   (then reopen this window)
    echo.
)

REM --- 3. Create virtual environment (first run only) ---
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment .venv ...
    python -m venv .venv
    if errorlevel 1 ( echo [ERROR] venv creation failed & pause & exit /b 1 )
)

set "PY=.venv\Scripts\python.exe"

REM --- 4. Install dependencies (first run only) ---
if not exist ".venv\.deps_installed" (
    echo Installing dependencies ^(this downloads ~1-2 GB, be patient^) ...
    "%PY%" -m pip install --upgrade pip
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 ( echo [ERROR] pip install failed & pause & exit /b 1 )
    echo done > ".venv\.deps_installed"
)

REM --- 5. Download model weights (first run only) ---
if not exist "weights" mkdir weights
if not exist "weights\yolo11s-ball.pt" (
    echo Downloading yolo11s-ball.pt ...
    curl -L -o "weights\yolo11s-ball.pt" "https://github.com/yo-WASSUP/Good-Badminton/releases/latest/download/yolo11s-ball.pt"
)
if not exist "weights\yolox_nano_8xb8-300e_humanart-40f6f0d0.onnx" (
    echo Downloading yolox_nano ... onnx ...
    curl -L -o "weights\yolox_nano_8xb8-300e_humanart-40f6f0d0.onnx" "https://github.com/yo-WASSUP/Good-Badminton/releases/latest/download/yolox_nano_8xb8-300e_humanart-40f6f0d0.onnx"
)

REM --- 6. Start the web UI and open the browser ---
echo.
echo Starting web UI at http://127.0.0.1:5050
start "" "http://127.0.0.1:5050"
"%PY%" app.py

pause
