@echo off
REM ============================================================
REM Mein Assistent - Status Check
REM ============================================================
setlocal enabledelayedexpansion

REM Konfiguration
set "WSL_DISTRO=Ubuntu"
set "PROJECT_PATH=/home/sherbert/mein-assistent"

echo ============================================================
echo    Mein Assistent - Status
echo ============================================================
echo.

REM ============================================================
REM PRUEFE EMAIL WORKER
REM ============================================================
echo Email Worker:

if defined WSL_DISTRO (
    wsl -d %WSL_DISTRO% --exec bash -c "cd %PROJECT_PATH% && [ -f email_worker.pid ]"
) else (
    wsl --exec bash -c "cd %PROJECT_PATH% && [ -f email_worker.pid ]"
)

if errorlevel 1 (
    echo   Status:  [31mGestoppt[0m
    echo   PID:     Keine PID-Datei
) else (
    if defined WSL_DISTRO (
        for /f %%i in ('wsl -d %WSL_DISTRO% --exec bash -c "cd %PROJECT_PATH% && cat email_worker.pid"') do set "EMAIL_PID=%%i"
        wsl -d %WSL_DISTRO% --exec bash -c "cd %PROJECT_PATH% && ps -p $(cat email_worker.pid) >/dev/null 2>&1"
    ) else (
        for /f %%i in ('wsl --exec bash -c "cd %PROJECT_PATH% && cat email_worker.pid"') do set "EMAIL_PID=%%i"
        wsl --exec bash -c "cd %PROJECT_PATH% && ps -p $(cat email_worker.pid) >/dev/null 2>&1"
    )

    if errorlevel 1 (
        echo   Status:  [33mPID-Datei vorhanden, Prozess laeuft nicht[0m
        echo   PID:     !EMAIL_PID! ^(tot^)
    ) else (
        echo   Status:  [32mLaeuft[0m
        echo   PID:     !EMAIL_PID!

        REM Zeige Log-Info
        if defined WSL_DISTRO (
            for /f "delims=" %%i in ('wsl -d %WSL_DISTRO% --exec bash -c "cd %PROJECT_PATH% && tail -1 email_worker.log 2>/dev/null"') do set "LAST_LOG=%%i"
        ) else (
            for /f "delims=" %%i in ('wsl --exec bash -c "cd %PROJECT_PATH% && tail -1 email_worker.log 2>/dev/null"') do set "LAST_LOG=%%i"
        )
        if defined LAST_LOG (
            echo   Letzter Log: !LAST_LOG!
        )
    )
)

echo.

REM ============================================================
REM PRUEFE STREAMLIT
REM ============================================================
echo Streamlit Web-Interface:

if defined WSL_DISTRO (
    wsl -d %WSL_DISTRO% --exec bash -c "pgrep -f 'streamlit run app.py' >/dev/null 2>&1"
) else (
    wsl --exec bash -c "pgrep -f 'streamlit run app.py' >/dev/null 2>&1"
)

if errorlevel 1 (
    echo   Status:  [31mGestoppt[0m
    echo   URL:     http://localhost:8501 ^(nicht erreichbar^)
) else (
    if defined WSL_DISTRO (
        for /f %%i in ('wsl -d %WSL_DISTRO% --exec bash -c "pgrep -f \"streamlit run app.py\""') do set "STREAMLIT_PID=%%i"
    ) else (
        for /f %%i in ('wsl --exec bash -c "pgrep -f \"streamlit run app.py\""') do set "STREAMLIT_PID=%%i"
    )

    echo   Status:  [32mLaeuft[0m
    echo   PID:     !STREAMLIT_PID!

    REM Prüfe ob Port erreichbar ist
    if defined WSL_DISTRO (
        wsl -d %WSL_DISTRO% --exec bash -c "curl -s http://localhost:8501 >/dev/null 2>&1"
    ) else (
        wsl --exec bash -c "curl -s http://localhost:8501 >/dev/null 2>&1"
    )

    if errorlevel 1 (
        echo   URL:     http://localhost:8501 ^([33mnicht bereit[0m^)
    ) else (
        echo   URL:     http://localhost:8501 ^([32mbereit[0m^)
    )
)

echo.

REM ============================================================
REM SYSTEM-INFO
REM ============================================================
echo System:
echo   WSL-Distro: %WSL_DISTRO%
echo   Projekt:    %PROJECT_PATH%

if defined WSL_DISTRO (
    for /f "delims=" %%i in ('wsl -d %WSL_DISTRO% --exec bash -c "cd %PROJECT_PATH% && pwd"') do set "WSL_PWD=%%i"
) else (
    for /f "delims=" %%i in ('wsl --exec bash -c "cd %PROJECT_PATH% && pwd"') do set "WSL_PWD=%%i"
)
echo   WSL-Pfad:   !WSL_PWD!

echo.

REM ============================================================
REM LOGS
REM ============================================================
if exist "logs" (
    echo Aktuelle Logs:
    dir /b /o-d logs\*.log 2>nul | findstr /r ".*" >nul
    if not errorlevel 1 (
        for /f "delims=" %%i in ('dir /b /o-d logs\*.log 2^>nul') do (
            echo   - logs\%%i
            goto :log_shown
        )
        :log_shown
    ) else (
        echo   Keine Logs gefunden
    )
) else (
    echo Logs: Kein Log-Verzeichnis vorhanden
)

echo.
echo ============================================================
echo.
pause

exit /b 0
