@echo off
REM ============================================================
REM Mein Assistent - Stopper
REM ============================================================
setlocal enabledelayedexpansion

REM Konfiguration
set "WSL_DISTRO=Ubuntu"
set "PROJECT_PATH=/home/sherbert/mein-assistent"

echo ============================================================
echo    Mein Assistent - Stopper
echo ============================================================
echo.

REM ============================================================
REM 1. STOPPE EMAIL WORKER
REM ============================================================
echo [1/2] Stoppe Email Worker...

if defined WSL_DISTRO (
    wsl -d %WSL_DISTRO% --exec bash -c "cd %PROJECT_PATH% && [ -f email_worker.pid ]"
) else (
    wsl --exec bash -c "cd %PROJECT_PATH% && [ -f email_worker.pid ]"
)

if errorlevel 1 (
    echo [INFO] Email Worker laeuft nicht
) else (
    if defined WSL_DISTRO (
        wsl -d %WSL_DISTRO% --exec bash -c "cd %PROJECT_PATH% && ./stop_email_system.sh"
    ) else (
        wsl --exec bash -c "cd %PROJECT_PATH% && ./stop_email_system.sh"
    )
    echo [OK] Email Worker gestoppt
)

echo.

REM ============================================================
REM 2. STOPPE STREAMLIT
REM ============================================================
echo [2/2] Stoppe Streamlit...

if defined WSL_DISTRO (
    wsl -d %WSL_DISTRO% --exec bash -c "pgrep -f 'streamlit run app.py' >/dev/null 2>&1"
) else (
    wsl --exec bash -c "pgrep -f 'streamlit run app.py' >/dev/null 2>&1"
)

if errorlevel 1 (
    echo [INFO] Streamlit laeuft nicht
) else (
    if defined WSL_DISTRO (
        wsl -d %WSL_DISTRO% --exec bash -c "pkill -f 'streamlit run app.py'"
    ) else (
        wsl --exec bash -c "pkill -f 'streamlit run app.py'"
    )
    timeout /t 2 /nobreak >nul
    echo [OK] Streamlit gestoppt
)

echo.

REM ============================================================
REM ZUSAMMENFASSUNG
REM ============================================================
echo ============================================================
echo    Alle Services gestoppt
echo ============================================================
echo.
pause

exit /b 0
