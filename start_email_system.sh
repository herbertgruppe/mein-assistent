#!/bin/bash
# Startet das neue asynchrone Email-System

echo "=================================================="
echo "Email System - Asynchrone Architektur"
echo "=================================================="
echo ""

# Prüfe ob Worker bereits läuft
if [ -f email_worker.pid ]; then
    PID=$(cat email_worker.pid)
    if ps -p $PID > /dev/null 2>&1; then
        echo "⚠️  Email Worker läuft bereits (PID: $PID)"
        exit 1
    else
        echo "Alte PID-Datei gefunden, wird gelöscht..."
        rm email_worker.pid
    fi
fi

# Starte Worker im Hintergrund (mit venv Python)
echo "🚀 Starte Email Worker..."
nohup ./venv/bin/python email_worker.py > email_worker.log 2>&1 &
WORKER_PID=$!

# Speichere PID
echo $WORKER_PID > email_worker.pid

echo "✅ Email Worker gestartet (PID: $WORKER_PID)"
echo ""
echo "📋 Befehle:"
echo "   - Status prüfen:  ./status_email_system.sh"
echo "   - Logs anzeigen:  tail -f email_worker.log"
echo "   - Worker stoppen: ./stop_email_system.sh"
echo ""
