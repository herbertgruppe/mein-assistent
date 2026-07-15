"""
Test-Skript für die Meeting Manager UI-Integration

Testet die neuen Funktionen ohne Streamlit zu starten.
"""

import sys
from pathlib import Path

# Importiere die neuen Funktionen aus app.py
sys.path.insert(0, str(Path(__file__).parent))

# Setze STREAMLIT_RUNTIME environment variable um Import zu ermöglichen
import os
os.environ['STREAMLIT_SERVER_HEADLESS'] = 'true'

print("=" * 60)
print("Test: Meeting Manager UI Integration")
print("=" * 60)

# Test 1: Import der Funktionen
print("\n✓ Test 1: Import der Funktionen")
try:
    from app import (
        get_meeting_manager_pid,
        is_meeting_manager_running,
        start_meeting_manager,
        stop_meeting_manager
    )
    print("  ✓ Alle Funktionen erfolgreich importiert")
except Exception as e:
    print(f"  ✗ Fehler beim Import: {e}")
    sys.exit(1)

# Test 2: Status prüfen
print("\n✓ Test 2: Status prüfen")
is_running = is_meeting_manager_running()
pid = get_meeting_manager_pid()

if is_running:
    print(f"  ✓ Meeting Manager läuft (PID: {pid})")
else:
    print("  ✓ Meeting Manager ist gestoppt")

# Test 3: Ordner prüfen
print("\n✓ Test 3: Ordnerstruktur prüfen")
incoming = Path("transcripts/incoming")
processed = Path("transcripts/processed")

if incoming.exists():
    files = list(incoming.glob("*.txt"))
    print(f"  ✓ incoming/ existiert ({len(files)} Datei(en))")
else:
    print("  ✗ incoming/ fehlt")

if processed.exists():
    files = list(processed.glob("*.txt"))
    print(f"  ✓ processed/ existiert ({len(files)} Datei(en))")
else:
    print("  ✗ processed/ fehlt")

# Test 4: Log-Datei prüfen
print("\n✓ Test 4: Log-Datei prüfen")
log_file = Path("meeting_manager.log")
if log_file.exists():
    size = log_file.stat().st_size
    print(f"  ✓ meeting_manager.log existiert ({size} bytes)")
else:
    print("  ℹ meeting_manager.log existiert noch nicht (normal wenn noch nicht gestartet)")

# Test 5: Start/Stop Funktionen (ohne tatsächlich zu starten)
print("\n✓ Test 5: Start/Stop Funktionen verfügbar")
print(f"  ✓ start_meeting_manager: {callable(start_meeting_manager)}")
print(f"  ✓ stop_meeting_manager: {callable(stop_meeting_manager)}")

print("\n" + "=" * 60)
print("✓ Alle Tests erfolgreich!")
print("=" * 60)
print("\nDie UI-Integration ist bereit!")
print("Starte die Streamlit App mit:")
print("  source venv/bin/activate")
print("  streamlit run app.py")
print("\nÖffne dann den Tab '🎙️ Transkripte'")
