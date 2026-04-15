#!/usr/bin/env python3
"""
Migriert die Email-Datenbank auf das neue Schema

Fügt hinzu:
- body_full
- has_attachments
- attachments_json
"""

import sqlite3
import sys


def migrate_db(db_path="data/email_store.db"):
    """Führt Migration durch"""
    print(f"Migriere Datenbank: {db_path}")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Prüfe welche Spalten existieren
    cursor.execute("PRAGMA table_info(emails)")
    columns = [row[1] for row in cursor.fetchall()]

    print(f"Existierende Spalten: {columns}")

    migrations_needed = []

    # body_full
    if 'body_full' not in columns:
        migrations_needed.append("ALTER TABLE emails ADD COLUMN body_full TEXT")
        print("  → Füge body_full hinzu")

    # has_attachments
    if 'has_attachments' not in columns:
        migrations_needed.append("ALTER TABLE emails ADD COLUMN has_attachments INTEGER DEFAULT 0")
        print("  → Füge has_attachments hinzu")

    # attachments_json
    if 'attachments_json' not in columns:
        migrations_needed.append("ALTER TABLE emails ADD COLUMN attachments_json TEXT")
        print("  → Füge attachments_json hinzu")

    if not migrations_needed:
        print("✅ Keine Migration nötig, Datenbank ist aktuell!")
        return

    # Führe Migrationen aus
    print("\nFühre Migrationen aus...")
    for sql in migrations_needed:
        print(f"  SQL: {sql}")
        cursor.execute(sql)

    conn.commit()
    conn.close()

    print("✅ Migration abgeschlossen!")


if __name__ == "__main__":
    migrate_db()
