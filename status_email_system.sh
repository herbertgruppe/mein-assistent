#!/bin/bash
# Zeigt Status des Email-Systems

echo "=================================================="
echo "Email System Status"
echo "=================================================="
echo ""

# Worker Status
if [ -f email_worker.pid ]; then
    PID=$(cat email_worker.pid)

    if ps -p $PID > /dev/null 2>&1; then
        echo "✅ Email Worker läuft (PID: $PID)"

        # Zeige letzte Log-Zeilen
        echo ""
        echo "📋 Letzte Log-Einträge:"
        echo "---"
        tail -n 10 email_worker.log
    else
        echo "❌ Email Worker läuft nicht (PID: $PID)"
    fi
else
    echo "❌ Email Worker läuft nicht (keine PID-Datei)"
fi

echo ""
echo "---"

# Datenbank-Statistiken
python3 << 'EOF'
from database.email_db import EmailDB

db = EmailDB()
stats = db.get_stats()

print("\n📊 Datenbank-Statistiken:")
print(f"   - Ungelesen:      {stats.get('unread', 0)}")
print(f"   - In Bearbeitung: {stats.get('processing', 0)}")
print(f"   - Erledigt:       {stats.get('done', 0)}")
print(f"   - Fehler:         {stats.get('error', 0)}")
EOF

echo ""
