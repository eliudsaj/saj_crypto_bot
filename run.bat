@echo off
cd /d "%~dp0"

echo ========================================
echo   NEXUS TRADING BOT - Startup
echo ========================================
echo.

set "PY=python"
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    where py >nul 2>&1
    if %errorlevel%==0 (
        set "PY=py -3"
    ) else (
        where python3 >nul 2>&1
        if %errorlevel%==0 (
            set "PY=python3"
        ) else (
            where python >nul 2>&1
            if %errorlevel%==0 (
                set "PY=python"
            )
        )
    )
)

echo Using %PY%

echo [1] Installing dependencies...
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
    echo Error installing dependencies
    pause
    exit /b 1
)

echo.
echo [2] Starting dashboard...
echo Dashboard: http://localhost:5000
echo.

%PY% app.py

pause
