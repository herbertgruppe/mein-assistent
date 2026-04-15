#!/bin/bash
# Email Worker Watchdog - prüft ob Worker läuft und startet ihn neu falls nötig

SCRIPT_DIR="/home/sherbert/mein-assistent"
PID_FILE="$SCRIPT_DIR/email_worker.pid"

cd "$SCRIPT_DIR"

# Prüfe ob Worker läuft
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        # Worker läuft noch
        exit 0
    fi
fi

# Worker läuft nicht, starte neu
echo "[$(date)] Worker nicht gefunden, starte neu..." >> "$SCRIPT_DIR/watchdog.log"
"$SCRIPT_DIR/start-email-worker.sh" >> "$SCRIPT_DIR/watchdog.log" 2>&1
