@echo off
REM ============================================================
REM Mein Assistent - Windows Starter mit WSL
REM ============================================================
setlocal enabledelayedexpansion

REM Konfiguration
set "WSL_DISTRO=Ubuntu"
set "PROJECT_PATH=/home/sherbert/mein-assistent"
set "LOG_DIR=logs"
set "TIMESTAMP=%date:~-4%%date:~3,2%%date:~0,2%_%time:~0,2%%time:~3,2%%time:~6,2%"
set "TIMESTAMP=%TIMESTAMP: =0%"

echo ============================================================
echo    Mein Assistent - Starter
echo ============================================================
echo.

REM Erstelle Log-Verzeichnis falls nicht vorhanden
if not exist "%LOG_DIR%" (
    mkdir "%LOG_DIR%"
    echo [INFO] Log-Verzeichnis erstellt: %LOG_DIR%
)

REM ============================================================
REM 1. PRUEFE VORAUSSETZUNGEN
REM ============================================================
echo [1/5] Pruefe Voraussetzungen...

REM Prüfe ob WSL installiert ist
wsl --list --quiet >nul 2>&1
if errorlevel 1 (
    echo [ERROR] WSL ist nicht installiert!
    echo         Bitte installieren Sie WSL: https://aka.ms/wslinstall
    pause
    exit /b 1
)

REM Prüfe ob WSL-Distro verfügbar ist
wsl -d %WSL_DISTRO% --exec echo "" >nul 2>&1
if errorlevel 1 (
    echo [WARNING] WSL-Distro '%WSL_DISTRO%' nicht gefunden
    echo           Verwende Standard-Distro
    set "WSL_DISTRO="
)

REM Prüfe ob Projekt existiert
if defined WSL_DISTRO (
    wsl -d %WSL_DISTRO% --exec test -d %PROJECT_PATH%
) else (
    wsl --exec test -d %PROJECT_PATH%
)
if errorlevel 1 (
    echo [ERROR] Projekt nicht gefunden: %PROJECT_PATH%
    echo         Bitte passen Sie PROJECT_PATH in dieser Datei an
    pause
    exit /b 1
)

echo [OK] Voraussetzungen erfuellt
echo.

REM ============================================================
REM 2. PRUEFE OB SERVICES BEREITS LAUFEN
REM ============================================================
echo [2/5] Pruefe laufende Services...

REM Prüfe Email Worker
if defined WSL_DISTRO (
    wsl -d %WSL_DISTRO% --exec bash -c "cd %PROJECT_PATH% && [ -f email_worker.pid ] && ps -p $(cat email_worker.pid) >/dev/null 2>&1"
) else (
    wsl --exec bash -c "cd %PROJECT_PATH% && [ -f email_worker.pid ] && ps -p $(cat email_worker.pid) >/dev/null 2>&1"
)
if not errorlevel 1 (
    echo [WARNING] Email Worker laeuft bereits
    set "EMAIL_RUNNING=1"
) else (
    set "EMAIL_RUNNING=0"
)

REM Prüfe Streamlit
if defined WSL_DISTRO (
    wsl -d %WSL_DISTRO% --exec bash -c "pgrep -f 'streamlit run app.py' >/dev/null 2>&1"
) else (
    wsl --exec bash -c "pgrep -f 'streamlit run app.py' >/dev/null 2>&1"
)
if not errorlevel 1 (
    echo [WARNING] Streamlit laeuft bereits
    set "STREAMLIT_RUNNING=1"
) else (
    set "STREAMLIT_RUNNING=0"
)

if "%EMAIL_RUNNING%"=="1" if "%STREAMLIT_RUNNING%"=="1" (
    echo.
    echo [INFO] Alle Services laufen bereits!
    echo.
    choice /C JN /M "Neu starten"
    if errorlevel 2 (
        echo [INFO] Abgebrochen - Services laufen weiter
        pause
        exit /b 0
    )
    echo [INFO] Stoppe bestehende Services...
    if defined WSL_DISTRO (
        wsl -d %WSL_DISTRO% --exec bash -c "cd %PROJECT_PATH% && ./stop_email_system.sh >/dev/null 2>&1"
        wsl -d %WSL_DISTRO% --exec bash -c "pkill -f 'streamlit run app.py'"
    ) else (
        wsl --exec bash -c "cd %PROJECT_PATH% && ./stop_email_system.sh >/dev/null 2>&1"
        wsl --exec bash -c "pkill -f 'streamlit run app.py'"
    )
    timeout /t 2 /nobreak >nul
)

echo [OK] Service-Check abgeschlossen
echo.

REM ============================================================
REM 3. STARTE EMAIL WORKER
REM ============================================================
echo [3/5] Starte Email Worker...

set "EMAIL_LOG=%LOG_DIR%\email_worker_%TIMESTAMP%.log"

if defined WSL_DISTRO (
    start /B wsl -d %WSL_DISTRO% --exec bash -c "cd %PROJECT_PATH% && ./start_email_system.sh" > "%EMAIL_LOG%" 2>&1
) else (
    start /B wsl --exec bash -c "cd %PROJECT_PATH% && ./start_email_system.sh" > "%EMAIL_LOG%" 2>&1
)

REM Warte kurz und prüfe ob gestartet
timeout /t 3 /nobreak >nul

if defined WSL_DISTRO (
    wsl -d %WSL_DISTRO% --exec bash -c "cd %PROJECT_PATH% && [ -f email_worker.pid ]"
) else (
    wsl --exec bash -c "cd %PROJECT_PATH% && [ -f email_worker.pid ]"
)
if errorlevel 1 (
    echo [ERROR] Email Worker konnte nicht gestartet werden
    echo         Siehe Log: %EMAIL_LOG%
    pause
    exit /b 1
)

echo [OK] Email Worker gestartet
echo      Log: %EMAIL_LOG%
echo.

REM ============================================================
REM 4. STARTE STREAMLIT WEB-INTERFACE
REM ============================================================
echo [4/5] Starte Streamlit Web-Interface...

set "STREAMLIT_LOG=%LOG_DIR%\streamlit_%TIMESTAMP%.log"

if defined WSL_DISTRO (
    start "Mein Assistent - Streamlit" wsl -d %WSL_DISTRO% --exec bash -c "cd %PROJECT_PATH% && source venv/bin/activate && streamlit run app.py --server.headless true" > "%STREAMLIT_LOG%" 2>&1
) else (
    start "Mein Assistent - Streamlit" wsl --exec bash -c "cd %PROJECT_PATH% && source venv/bin/activate && streamlit run app.py --server.headless true" > "%STREAMLIT_LOG%" 2>&1
)

REM Warte bis Streamlit bereit ist
echo      Warte auf Streamlit...
set /a counter=0
:wait_streamlit
timeout /t 2 /nobreak >nul
if defined WSL_DISTRO (
    wsl -d %WSL_DISTRO% --exec bash -c "curl -s http://localhost:8501 >/dev/null 2>&1"
) else (
    wsl --exec bash -c "curl -s http://localhost:8501 >/dev/null 2>&1"
)
if errorlevel 1 (
    set /a counter+=1
    if !counter! lss 15 (
        echo      Noch nicht bereit ^(!counter!/15^)...
        goto wait_streamlit
    )
    echo [WARNING] Streamlit antwortet nicht auf Port 8501
    echo           Service wurde gestartet, pruefe Log: %STREAMLIT_LOG%
) else (
    echo [OK] Streamlit ist bereit
)

echo      Log: %STREAMLIT_LOG%
echo.

REM ============================================================
REM 5. OEFFNE BROWSER
REM ============================================================
echo [5/5] Oeffne Browser...

timeout /t 2 /nobreak >nul
start http://localhost:8501

echo [OK] Browser geoeffnet
echo.

REM ============================================================
REM ZUSAMMENFASSUNG
REM ============================================================
echo ============================================================
echo    Mein Assistent gestartet!
echo ============================================================
echo.
echo Services:
echo   [32m✓[0m Email Worker    - Laeuft im Hintergrund
echo   [32m✓[0m Streamlit UI    - http://localhost:8501
echo.
echo Logs:
echo   - Email Worker: %EMAIL_LOG%
echo   - Streamlit:    %STREAMLIT_LOG%
echo.
echo Befehle:
echo   - Status pruefen: status_assistant.bat
echo   - Services stoppen: stop_assistant.bat
echo   - Logs anzeigen: logs\
echo.
echo ============================================================
echo.
echo Druecken Sie eine Taste zum Beenden...
echo Das Schliessen dieses Fensters beendet NICHT die Services!
pause >nul

exit /b 0
