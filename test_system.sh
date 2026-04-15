#!/bin/bash
# Quick-Test Script für Email-System
# Führt alle wichtigen Tests aus

set -e  # Exit bei Fehler

echo "============================================================"
echo "Email-System Test Suite"
echo "============================================================"
echo ""

# Farben
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test 1: Migration-Status
echo "📋 Test 1: Datenbank-Schema"
echo "-----------------------------------------------------------"
python3 -c "
import sqlite3
conn = sqlite3.connect('data/email_cache.db')
cursor = conn.cursor()
cursor.execute('PRAGMA table_info(emails)')
cols = [row[1] for row in cursor.fetchall()]
if 'draft_reply' in cols:
    print('✅ Spalte draft_reply vorhanden')
    exit(0)
else:
    print('❌ Spalte draft_reply fehlt!')
    exit(1)
"
echo ""

# Test 2: Worker-Import
echo "📋 Test 2: Worker-Imports"
echo "-----------------------------------------------------------"
python3 -c "
import sys
try:
    from utils.database import EmailDatabase
    print('✅ EmailDatabase importiert')
    from utils.email_manager import EmailManager
    print('✅ EmailManager importiert')
    # Note: email_worker kann nicht importiert werden ohne dotenv
    print('⚠️  email_worker.py übersprungen (benötigt Dependencies)')
except Exception as e:
    print(f'❌ Import-Fehler: {e}')
    sys.exit(1)
"
echo ""

# Test 3: Database-Methoden
echo "📋 Test 3: Neue Database-Methoden"
echo "-----------------------------------------------------------"
python3 -c "
from utils.database import EmailDatabase
db = EmailDatabase()

# Prüfe ob neue Methoden existieren
methods = ['insert_raw_email', 'update_email_analysis', 'increment_retry_count']
for method in methods:
    if hasattr(db, method):
        print(f'✅ Methode {method} vorhanden')
    else:
        print(f'❌ Methode {method} fehlt!')
        exit(1)
"
echo ""

# Test 4: EmailManager-Methoden
echo "📋 Test 4: EmailManager-Methoden"
echo "-----------------------------------------------------------"
python3 -c "
from utils.email_manager import EmailManager

# Prüfe ob neue Methoden existieren
if hasattr(EmailManager, 'generate_draft_reply'):
    print('✅ Methode generate_draft_reply vorhanden')
else:
    print('❌ Methode generate_draft_reply fehlt!')
    exit(1)
"
echo ""

# Test 5: Performance-Tests
echo "📋 Test 5: Performance-Tests"
echo "-----------------------------------------------------------"
python3 tests/test_performance.py
echo ""

# Test 6: DB-Status
echo "📋 Test 6: Datenbank-Status"
echo "-----------------------------------------------------------"
python3 -c "
from utils.database import EmailDatabase
db = EmailDatabase()

# Zähle Emails nach Status
statuses = ['synced', 'analyzed', 'pending_reply', 'pending_forward', 'archived', 'error']
total = 0
for status in statuses:
    emails = db.get_emails_by_status([status], limit=1000)
    count = len(emails)
    total += count
    if count > 0:
        print(f'  {status:15s}: {count:3d} Emails')

print(f'  {\"TOTAL\":15s}: {total:3d} Emails')

# Prüfe Worker-State
worker_state = db.get_worker_state()
last_poll = worker_state.get('last_successful_poll', 'Unbekannt')
print(f'\n  Letzter Poll: {last_poll}')
"
echo ""

# Test 7: Dateien vorhanden
echo "📋 Test 7: Dokumentation"
echo "-----------------------------------------------------------"
files=(
    "migrations/001_add_draft_reply.py"
    "tests/test_performance.py"
    "DEPLOYMENT_GUIDE.md"
    "REFACTORING_SUMMARY.md"
)

for file in "${files[@]}"; do
    if [ -f "$file" ]; then
        echo "✅ $file vorhanden"
    else
        echo "❌ $file fehlt!"
        exit 1
    fi
done
echo ""

# Zusammenfassung
echo "============================================================"
echo "Zusammenfassung"
echo "============================================================"
echo ""
echo -e "${GREEN}✅ Alle Tests erfolgreich!${NC}"
echo ""
echo "Nächste Schritte:"
echo "  1. Worker deployen: siehe DEPLOYMENT_GUIDE.md"
echo "  2. Streamlit testen: streamlit run app.py"
echo "  3. End-to-End Test: siehe DEPLOYMENT_GUIDE.md Abschnitt 3.2"
echo ""
