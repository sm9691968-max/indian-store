@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Indian Store Launcher

set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

if /i "%~1"=="tests" goto tests
if /i "%~1"=="seed" goto seed
if /i "%~1"=="reset" goto reset
if /i "%~1"=="port" goto port

"%PY%" -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo [setup] installing dependencies...
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 goto fail
)

echo [setup] making sure the catalogue is seeded...
"%PY%" seed.py
if errorlevel 1 goto fail

netstat -ano | findstr /R /C:":5000 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo.
    echo Indian Store is already running at  http://127.0.0.1:5000
    start "" http://127.0.0.1:5000
    exit /b 0
)

echo.
echo Starting Indian Store at http://127.0.0.1:5000
start "" /b cmd /c "ping -n 3 127.0.0.1 >nul & start http://127.0.0.1:5000"
"%PY%" run.py
exit /b 0

:port
set "PORT=%~2"
if "%PORT%"=="" set "PORT=5001"
echo Starting Indian Store at http://127.0.0.1:%PORT%
start "" /b cmd /c "ping -n 3 127.0.0.1 >nul & start http://127.0.0.1:%PORT%"
"%PY%" -c "from app import create_app; create_app().run(port=%PORT%)"
exit /b 0

:tests
"%PY%" test_features.py
"%PY%" test_smoke.py
"%PY%" test_security.py
"%PY%" test_seo.py
exit /b 0

:seed
"%PY%" seed.py
exit /b 0

:reset
echo [warn] this drops all tables and reseeds from scratch.
choice /c YN /m "Continue"
if errorlevel 2 exit /b 1
"%PY%" seed.py --reset
exit /b 0

:fail
echo.
echo Startup failed. See the message above.
pause
exit /b 1