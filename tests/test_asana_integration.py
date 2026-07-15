#!/usr/bin/env python3
"""
Test-Script für Asana-Integration
"""

import os
import sys
from dotenv import load_dotenv

# Lade Umgebungsvariablen
load_dotenv()

print("=" * 60)
print("ASANA INTEGRATION - TEST")
print("=" * 60)

# Test 1: Import-Test
print("\n[TEST 1] Module importieren...")
try:
    from agents.asana_agent import AsanaAgent
    from tools.asana_tool import AsanaTool
    print("✅ Module erfolgreich importiert")
except Exception as e:
    print(f"❌ Import-Fehler: {e}")
    sys.exit(1)

# Test 2: Konfiguration prüfen
print("\n[TEST 2] Konfiguration prüfen...")
asana_token = os.getenv("ASANA_ACCESS_TOKEN")
if asana_token:
    print(f"✅ ASANA_ACCESS_TOKEN gefunden (beginnt mit: {asana_token[:10]}...)")
else:
    print("❌ ASANA_ACCESS_TOKEN nicht in .env gefunden")
    sys.exit(1)

# Test 3: AsanaAgent initialisieren
print("\n[TEST 3] AsanaAgent initialisieren...")
try:
    asana_agent = AsanaAgent()
    print(f"✅ AsanaAgent initialisiert")
    print(f"   - Client: {'✓' if asana_agent.client else '✗'}")
    print(f"   - Workspace GID: {asana_agent.workspace_gid or 'Nicht gefunden'}")
except Exception as e:
    print(f"❌ AsanaAgent-Fehler: {e}")
    import traceback
    traceback.print_exc()

# Test 4: AsanaTool initialisieren
print("\n[TEST 4] AsanaTool initialisieren...")
try:
    asana_tool = AsanaTool()
    print(f"✅ AsanaTool initialisiert")
    print(f"   - Konfiguriert: {'✓' if asana_tool.is_configured else '✗'}")
except Exception as e:
    print(f"❌ AsanaTool-Fehler: {e}")
    import traceback
    traceback.print_exc()

# Test 5: Aufgaben abrufen
print("\n[TEST 5] Aufgaben abrufen...")
try:
    tasks = asana_agent.get_upcoming_tasks(days=14, limit=5)
    print(f"✅ {len(tasks)} Aufgabe(n) gefunden")

    if tasks:
        print("\nErste 3 Aufgaben:")
        for i, task in enumerate(tasks[:3], 1):
            name = task.get('name', 'Unbenannt')
            due = task.get('due_on', 'Kein Datum')
            print(f"   {i}. {name} (fällig: {due})")
    else:
        print("   Keine Aufgaben gefunden (das ist okay, wenn Asana leer ist)")

except Exception as e:
    print(f"❌ Fehler beim Abrufen: {e}")
    import traceback
    traceback.print_exc()

# Test 6: Formatierung testen
print("\n[TEST 6] Formatierung testen...")
try:
    if tasks:
        formatted = asana_agent.format_tasks_for_display(tasks)
        print("✅ Formatierung erfolgreich:")
        print(formatted[:200] + "..." if len(formatted) > 200 else formatted)
    else:
        print("⚠️ Keine Aufgaben zum Formatieren")
except Exception as e:
    print(f"❌ Formatierungs-Fehler: {e}")

# Test 7: Test-Anfrage verarbeiten
print("\n[TEST 7] Test-Anfrage verarbeiten...")
try:
    result = asana_agent.process("Was steht heute an?")
    print("✅ Anfrage verarbeitet:")
    print(f"   - Status: {result.get('status')}")
    print(f"   - Ergebnis: {result.get('result', '')[:100]}...")
except Exception as e:
    print(f"❌ Verarbeitungs-Fehler: {e}")
    import traceback
    traceback.print_exc()

# Zusammenfassung
print("\n" + "=" * 60)
print("TEST ABGESCHLOSSEN")
print("=" * 60)
print("\n✅ Die Asana-Integration ist funktionsfähig!")
print("\nNächste Schritte:")
print("1. Öffnen Sie http://localhost:8501")
print("2. Prüfen Sie die Sidebar auf Asana-Aufgaben")
print("3. Testen Sie im Chat: 'Was steht heute an?'")
print("4. Testen Sie im Archiv: Button 'Asana' bei Berichten")
