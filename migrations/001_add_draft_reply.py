#!/usr/bin/env python3
"""
Migration: Add draft_reply column to emails table
Fügt Spalte für vorgenerierte Draft-Replies hinzu
"""

import sqlite3
import os
import sys

# Setze Python Path für Imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def migrate(db_path='data/email_cache.db'):
    """
    Führt Migration aus

    Args:
        db_path: Pfad zur Datenbank
    """
    print(f"[Migration] Starte Migration: Add draft_reply column")
    print(f"[Migration] Datenbank: {db_path}")

    if not os.path.exists(db_path):
        print(f"❌ Datenbank nicht gefunden: {db_path}")
        return False

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Prüfe ob Spalte bereits existiert
        cursor.execute("PRAGMA table_info(emails)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'draft_reply' in columns:
            print("⚠ Spalte 'draft_reply' existiert bereits")
            conn.close()
            return True

        # Füge draft_reply-Spalte hinzu
        cursor.execute("ALTER TABLE emails ADD COLUMN draft_reply TEXT")
        print("✓ Spalte 'draft_reply' hinzugefügt")

        conn.commit()
        conn.close()
        print("✅ Migration abgeschlossen")
        return True

    except Exception as e:
        print(f"❌ Fehler bei Migration: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
