#!/bin/bash
# Komplettes Debug-Script für Email-Flow

echo "=== 1. Worker Status ==="
if ps aux | grep -q "[e]mail_worker.py"; then
    echo "✓ Worker läuft"
    ps aux | grep "[e]mail_worker.py" | awk '{print "  PID: "$2", Laufzeit: "$10}'
else
    echo "✗ Worker läuft NICHT!"
fi

echo ""
echo "=== 2. Letzte Worker-Logs (5 Zeilen) ==="
tail -n 5 email_worker.log

echo ""
echo "=== 3. Pending Actions ==="
python3 -c "
import sqlite3
conn = sqlite3.connect('data/email_cache.db')
cursor = conn.cursor()
cursor.execute('SELECT id, email_id, action_type, status, created_at FROM action_queue WHERE status=\"pending\" ORDER BY id DESC')
actions = cursor.fetchall()
if actions:
    for a in actions:
        print(f'  #{a[0]}: Email {a[1]} -> {a[2]} | {a[4]}')
else:
    print('  Keine pending Actions')
conn.close()
"

echo ""
echo "=== 4. Emails nach Status ==="
python3 -c "
import sqlite3
from collections import Counter
conn = sqlite3.connect('data/email_cache.db')
cursor = conn.cursor()
cursor.execute('SELECT status FROM emails')
statuses = [row[0] for row in cursor.fetchall()]
counts = Counter(statuses)
for status, count in counts.most_common():
    print(f'  {status}: {count}')
conn.close()
"

echo ""
echo "=== 5. Streamlit läuft? ==="
if ps aux | grep -q "[s]treamlit.*app.py"; then
    echo "✓ Streamlit läuft"
    ps aux | grep "[s]treamlit.*app.py" | awk '{print "  PID: "$2}'
else
    echo "✗ Streamlit läuft NICHT!"
fi
