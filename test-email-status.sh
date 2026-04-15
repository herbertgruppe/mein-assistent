#!/bin/bash
# Zeigt aktuelle Email-Stati und pending Actions

echo "=== Pending Actions ==="
python3 -c "
import sqlite3
conn = sqlite3.connect('data/email_cache.db')
cursor = conn.cursor()
cursor.execute('SELECT id, email_id, action_type, status FROM action_queue WHERE status=\"pending\" ORDER BY id DESC LIMIT 5')
actions = cursor.fetchall()
if actions:
    for a in actions:
        print(f'  Action {a[0]}: Email {a[1]} -> {a[2]} ({a[3]})')
else:
    print('  Keine pending Actions')

print()
print('=== Emails mit pending Status ===')
cursor.execute('SELECT id, subject, status FROM emails WHERE status LIKE \"pending_%\" ORDER BY id DESC LIMIT 5')
emails = cursor.fetchall()
if emails:
    for e in emails:
        print(f'  Email {e[0]}: {e[2]} - {e[1][:50]}')
else:
    print('  Keine Emails mit pending Status')

conn.close()
"
