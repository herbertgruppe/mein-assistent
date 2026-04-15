#!/bin/bash
# Email Worker Status Script

SCRIPT_DIR="/home/sherbert/mein-assistent"
PID_FILE="$SCRIPT_DIR/email_worker.pid"
LOG_FILE="$SCRIPT_DIR/email_worker.log"

echo "=== Email Worker Status ==="
echo

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "✓ Worker läuft mit PID: $PID"
        echo
        ps -p "$PID" -o pid,cmd,etime,%cpu,%mem
        echo
        echo "Letzte Log-Einträge:"
        tail -n 10 "$LOG_FILE"
    else
        echo "✗ Worker läuft nicht (PID-Datei existiert, aber Prozess nicht)"
        echo "  Alte PID: $PID"
    fi
else
    echo "✗ Worker läuft nicht (keine PID-Datei)"
fi
