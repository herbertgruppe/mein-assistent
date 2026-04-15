#!/bin/bash
# Stoppt den Email Worker

echo "🛑 Stoppe Email Worker..."

if [ -f email_worker.pid ]; then
    PID=$(cat email_worker.pid)

    if ps -p $PID > /dev/null 2>&1; then
        kill $PID
        echo "✅ Email Worker gestoppt (PID: $PID)"
        rm email_worker.pid
    else
        echo "⚠️  Worker läuft nicht (PID: $PID)"
        rm email_worker.pid
    fi
else
    echo "⚠️  Keine PID-Datei gefunden"
fi
