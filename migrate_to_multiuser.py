"""
Migrationsskript: Single-User → Multi-User Struktur

Verschiebt bestehende Daten in das User-Verzeichnis des ersten Admin-Users (sherbert).

Nutzung:
    python migrate_to_multiuser.py [--dry-run]
"""

import shutil
import sys
from pathlib import Path


TARGET_USER = "sherbert"


# Mapping: alte Pfade → neue Pfade unter users/{TARGET_USER}/
MIGRATIONS = [
    ("transcripts/incoming", f"users/{TARGET_USER}/transcripts/incoming"),
    ("transcripts/processed", f"users/{TARGET_USER}/transcripts/processed"),
    ("transcripts/archive", f"users/{TARGET_USER}/transcripts/archive"),
    ("transcripts/protocols", f"users/{TARGET_USER}/transcripts/protocols"),
    ("transcripts/protocols_final", f"users/{TARGET_USER}/transcripts/protocols_final"),
    ("transcripts/meeting_prep", f"users/{TARGET_USER}/transcripts/meeting_prep"),
    ("transcripts/wip", f"users/{TARGET_USER}/transcripts/wip"),
    ("transcripts/protocol_cache", f"users/{TARGET_USER}/transcripts/protocol_cache"),
    ("input_docs", f"users/{TARGET_USER}/input_docs"),
    ("data", f"users/{TARGET_USER}/data"),
    ("auth", f"users/{TARGET_USER}/auth"),
]


def migrate(dry_run: bool = False):
    """Führt die Migration durch."""
    print(f"{'[DRY RUN] ' if dry_run else ''}Migration Single-User → Multi-User")
    print(f"Ziel-User: {TARGET_USER}")
    print(f"Ziel-Verzeichnis: users/{TARGET_USER}/")
    print("=" * 60)

    for src_rel, dst_rel in MIGRATIONS:
        src = Path(src_rel)
        dst = Path(dst_rel)

        if not src.exists():
            print(f"  SKIP  {src_rel} (nicht vorhanden)")
            continue

        files = list(src.rglob("*"))
        file_count = len([f for f in files if f.is_file()])

        if file_count == 0:
            print(f"  SKIP  {src_rel} (leer)")
            continue

        print(f"  COPY  {src_rel} → {dst_rel} ({file_count} Dateien)")

        if not dry_run:
            dst.mkdir(parents=True, exist_ok=True)
            for item in files:
                if item.is_file():
                    rel = item.relative_to(src)
                    target = dst / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, target)

    print("=" * 60)
    if dry_run:
        print("[DRY RUN] Keine Änderungen vorgenommen.")
    else:
        print("Migration abgeschlossen!")
        print(f"Bestehende Ordner wurden NICHT gelöscht (Sicherheit).")
        print(f"Nach Prüfung können Sie die alten Ordner manuell entfernen.")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    migrate(dry_run=dry_run)
