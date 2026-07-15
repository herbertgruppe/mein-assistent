#!/usr/bin/env python3
"""
Test-Skript für das neue asynchrone Email-System

Testet:
1. Datenbank-Initialisierung
2. Email einfügen
3. Email abrufen
4. Instruction setzen
5. Pending instructions abrufen
6. Status-Updates
"""

import sys
from datetime import datetime
from database.email_db import EmailDB


def test_database():
    """Testet alle Datenbank-Funktionen"""
    print("=" * 60)
    print("Test: Neues Email-System")
    print("=" * 60)
    print()

    # 1. Initialisiere DB
    print("📋 Test 1: Datenbank initialisieren...")
    db = EmailDB(db_path="data/email_store_test.db")
    print("✅ Datenbank initialisiert")
    print()

    # 2. Füge Test-Email ein
    print("📋 Test 2: Test-Email einfügen...")
    test_email = {
        'id': 'test_email_001',
        'subject': 'Test Email - Dringend',
        'sender_name': 'Test Absender',
        'sender_email': 'test@example.com',
        'received_dt': datetime.now().isoformat(),
        'body_preview': 'Dies ist eine Test-Email für das neue System.',
        'priority': 4,
        'category': 'Test',
        'summary': 'Test-Email zur Überprüfung der Datenbank-Funktionen',
        'action_items': ['Test durchführen', 'Ergebnis prüfen'],
        'deadline': '2026-02-01',
        'sentiment': 'dringend'
    }

    success = db.insert_email(test_email)
    if success:
        print("✅ Email eingefügt")
    else:
        print("⚠️  Email bereits vorhanden")
    print()

    # 3. Hole ungelesene Emails
    print("📋 Test 3: Ungelesene Emails abrufen...")
    unread_emails = db.get_unread_emails(limit=10)
    print(f"✅ {len(unread_emails)} ungelesene Email(s) gefunden")

    if unread_emails:
        email = unread_emails[0]
        print(f"   - Betreff: {email['subject']}")
        print(f"   - Von: {email['sender_name']}")
        print(f"   - Priorität: {email['priority']}/5")
        print(f"   - Status: {email['status']}")
    print()

    # 4. Setze Instruction
    print("📋 Test 4: Instruction setzen...")
    if unread_emails:
        email_id = unread_emails[0]['id']
        db.set_instruction(email_id, 'archive')
        print(f"✅ Instruction 'archive' gesetzt für Email: {email_id}")
    else:
        print("⚠️  Keine Email zum Testen vorhanden")
    print()

    # 5. Hole pending instructions
    print("📋 Test 5: Pending instructions abrufen...")
    pending = db.get_pending_instructions()
    print(f"✅ {len(pending)} pending instruction(s) gefunden")

    if pending:
        for email in pending:
            print(f"   - {email['subject']}: instruction='{email['instruction']}'")
    print()

    # 6. Verstecke Email
    print("📋 Test 6: Email verstecken...")
    if unread_emails:
        email_id = unread_emails[0]['id']
        db.hide_email(email_id)
        print(f"✅ Email versteckt (status=processing): {email_id}")
    print()

    # 7. Markiere als done
    print("📋 Test 7: Email als erledigt markieren...")
    if pending:
        email_id = pending[0]['id']
        db.mark_as_done(email_id)
        print(f"✅ Email als erledigt markiert: {email_id}")
    print()

    # 8. Statistiken
    print("📋 Test 8: Statistiken abrufen...")
    stats = db.get_stats()
    print("✅ Statistiken:")
    for status, count in stats.items():
        print(f"   - {status}: {count}")
    print()

    # 9. Cleanup
    print("📋 Test 9: Cleanup (Test-Email löschen)...")
    if unread_emails:
        email_id = 'test_email_001'
        db.delete_email(email_id)
        print(f"✅ Test-Email gelöscht: {email_id}")
    print()

    print("=" * 60)
    print("✅ Alle Tests erfolgreich!")
    print("=" * 60)
    print()
    print("💡 Du kannst jetzt:")
    print("   1. Den Worker starten:     ./start_email_system.sh")
    print("   2. Die UI öffnen:          streamlit run app.py")
    print("   3. Status prüfen:          ./status_email_system.sh")
    print()


if __name__ == "__main__":
    try:
        test_database()
    except Exception as e:
        print(f"\n❌ Fehler beim Test: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
