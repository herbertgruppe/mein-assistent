#!/bin/bash
# Email Worker Starter Script

SCRIPT_DIR="/home/sherbert/mein-assistent"
PYTHON_BIN="$SCRIPT_DIR/venv/bin/python3"
WORKER_SCRIPT="$SCRIPT_DIR/email_worker.py"
LOG_FILE="$SCRIPT_DIR/email_worker.log"
PID_FILE="$SCRIPT_DIR/email_worker.pid"

cd "$SCRIPT_DIR"

# Prüfe ob Worker bereits läuft
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if ps -p "$OLD_PID" > /dev/null 2>&1; then
        echo "Worker läuft bereits mit PID $OLD_PID"
        exit 0
    fi
fi

# Starte Worker
nohup "$PYTHON_BIN" "$WORKER_SCRIPT" >> "$LOG_FILE" 2>&1 &
NEW_PID=$!

# Speichere PID
echo $NEW_PID > "$PID_FILE"

echo "Email Worker gestartet mit PID: $NEW_PID"
echo "Log: $LOG_FILE"
