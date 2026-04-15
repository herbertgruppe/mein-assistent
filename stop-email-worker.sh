#!/bin/bash
# Email Worker Stop Script

SCRIPT_DIR="/home/sherbert/mein-assistent"
PID_FILE="$SCRIPT_DIR/email_worker.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "Keine PID-Datei gefunden. Worker läuft vermutlich nicht."
    exit 1
fi

PID=$(cat "$PID_FILE")

if ps -p "$PID" > /dev/null 2>&1; then
    echo "Stoppe Worker mit PID $PID..."
    kill "$PID"
    sleep 2
    
    # Force kill falls noch läuft
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "Worker reagiert nicht, force kill..."
        kill -9 "$PID"
    fi
    
    rm "$PID_FILE"
    echo "✓ Worker gestoppt"
else
    echo "Worker mit PID $PID läuft nicht mehr"
    rm "$PID_FILE"
fi
