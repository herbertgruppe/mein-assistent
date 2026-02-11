#!/usr/bin/env python3
"""
Performance-Tests für Email-System
Testet UI-Ladezeit und Draft-Abruf-Geschwindigkeit
"""

import os
import sys
import time

# Setze Python Path für Imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.database import EmailDatabase

def test_ui_load_time():
    """UI sollte < 100ms laden"""
    print("\n[Test] UI-Ladezeit (50 Emails aus DB)...")

    db = EmailDatabase()

    start = time.time()
    emails = db.get_emails_by_status(['analyzed'], limit=50)
    duration = (time.time() - start) * 1000

    print(f"  → Ladezeit: {duration:.1f}ms")
    print(f"  → {len(emails)} Emails geladen")

    if duration < 100:
        print("  ✅ Performance OK (< 100ms)")
        return True
    else:
        print(f"  ❌ Performance zu langsam: {duration:.1f}ms")
        return False

def test_draft_retrieval():
    """Draft abrufen sollte < 10ms sein"""
    print("\n[Test] Draft-Retrieval-Geschwindigkeit...")

    db = EmailDatabase()

    # Hole erste Email mit draft_reply
    emails = db.get_emails_by_status(['analyzed'], limit=10)
    email_with_draft = None

    for email in emails:
        if email.get('draft_reply'):
            email_with_draft = email
            break

    if not email_with_draft:
        print("  ⚠ Keine Email mit draft_reply gefunden (Test übersprungen)")
        return True

    start = time.time()
    email = db.get_email_by_id(email_with_draft['id'])
    draft = email.get('draft_reply', '')
    duration = (time.time() - start) * 1000

    print(f"  → Ladezeit: {duration:.1f}ms")
    print(f"  → Draft-Länge: {len(draft)} Zeichen")

    if duration < 10:
        print("  ✅ Performance OK (< 10ms)")
        return True
    else:
        print(f"  ❌ Performance zu langsam: {duration:.1f}ms")
        return False

def test_queue_status():
    """Queue-Status sollte schnell abrufbar sein"""
    print("\n[Test] Queue-Status-Abfrage...")

    db = EmailDatabase()

    start = time.time()
    pending_actions = db.get_pending_actions(limit=100)
    synced_emails = db.get_emails_by_status(['synced'], limit=100)
    duration = (time.time() - start) * 1000

    print(f"  → Ladezeit: {duration:.1f}ms")
    print(f"  → {len(pending_actions)} pending Actions")
    print(f"  → {len(synced_emails)} synced Emails")

    if duration < 50:
        print("  ✅ Performance OK (< 50ms)")
        return True
    else:
        print(f"  ❌ Performance zu langsam: {duration:.1f}ms")
        return False

def main():
    """Führt alle Performance-Tests aus"""
    print("=" * 60)
    print("Email-System Performance-Tests")
    print("=" * 60)

    results = []

    # Test 1: UI-Ladezeit
    results.append(("UI-Ladezeit", test_ui_load_time()))

    # Test 2: Draft-Retrieval
    results.append(("Draft-Retrieval", test_draft_retrieval()))

    # Test 3: Queue-Status
    results.append(("Queue-Status", test_queue_status()))

    # Zusammenfassung
    print("\n" + "=" * 60)
    print("Zusammenfassung")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"  {name}: {status}")

    print(f"\n{passed}/{total} Tests bestanden")

    if passed == total:
        print("\n✅ Alle Performance-Tests erfolgreich!")
        return 0
    else:
        print(f"\n❌ {total - passed} Test(s) fehlgeschlagen")
        return 1

if __name__ == "__main__":
    sys.exit(main())
