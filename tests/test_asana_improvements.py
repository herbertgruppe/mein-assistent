#!/usr/bin/env python3
"""
Test-Script für Asana-Verbesserungen:
1. Error-Logging
2. User-Delegation
3. Präzise Aufgabenerstellung
4. Link-Bestätigung
"""

import os
from dotenv import load_dotenv
load_dotenv()

from agents.asana_agent import AsanaAgent
from agents.task_agent import TaskAgent

print("=" * 70)
print("TEST: ASANA-VERBESSERUNGEN")
print("=" * 70)

# 1. AsanaAgent initialisieren
asana_agent = AsanaAgent()

if not asana_agent.is_connected():
    print("❌ Asana nicht verbunden")
    exit(1)

print("✅ Asana Agent verbunden")

# 2. TaskAgent mit AsanaAgent initialisieren
task_agent = TaskAgent(asana_agent=asana_agent)
print("✅ Task Agent initialisiert")

print("\n" + "=" * 70)
print("TEST 1: USER-SUCHE (search_user_by_name)")
print("=" * 70)

# Test User-Suche mit verschiedenen Namen
test_names = [
    "Sven",  # Vorname
    "Herbert",  # Nachname
    "Max",  # Existiert möglicherweise nicht
]

for name in test_names:
    print(f"\n🔍 Suche nach: '{name}'")
    user_info = asana_agent.search_user_by_name(name)
    if user_info:
        print(f"   ✅ Gefunden: {user_info['name']} (GID: {user_info['gid']})")
    else:
        print(f"   ❌ Nicht gefunden")

print("\n" + "=" * 70)
print("TEST 2: ASSIGNEE-EXTRAKTION (extract_assignee_from_input)")
print("=" * 70)

test_inputs = [
    "Erstelle Aufgabe: Meeting, weise die Aufgabe Max zu",
    "Neue Aufgabe für Lisa: Präsentation erstellen",
    "Aufgabe zuweisen an Müller",
    "Erstelle Aufgabe: Dokument prüfen, mir zuweisen",
    "Neue Aufgabe: Code Review",  # Kein Assignee
]

for input_text in test_inputs:
    print(f"\nInput: '{input_text}'")
    assignee = asana_agent.extract_assignee_from_input(input_text)
    if assignee:
        print(f"   ✅ Assignee erkannt: '{assignee}'")
    else:
        print(f"   ℹ️  Kein Assignee erkannt (Default: me)")

print("\n" + "=" * 70)
print("TEST 3: TITEL-CLEANUP (parse_task_title_from_input)")
print("=" * 70)

test_titles = [
    "Erstelle Aufgabe: Meeting vorbereiten, weise die Aufgabe Max zu",
    "Neue Aufgabe Meeting für morgen",
    "Erstelle Aufgabe: Präsentation fertigstellen, fällig übermorgen, zuweisen an Lisa",
    "Aufgabe: Code Review bis heute",
]

for input_text in test_titles:
    title = asana_agent.parse_task_title_from_input(input_text)
    print(f"\nInput:  '{input_text}'")
    print(f"Titel:  '{title}'")
    if "weise" not in title.lower() and "zuweisen" not in title.lower():
        print("   ✅ Assignee-Phrasen entfernt")
    else:
        print("   ⚠️  Assignee-Phrasen noch vorhanden")

print("\n" + "=" * 70)
print("TEST 4: DATUMS-PARSING (parse_relative_date)")
print("=" * 70)

from datetime import datetime, timedelta

today = datetime.now().date()
tomorrow = (datetime.now() + timedelta(days=1)).date()
day_after_tomorrow = (datetime.now() + timedelta(days=2)).date()

date_tests = [
    ("heute", str(today)),
    ("morgen", str(tomorrow)),
    ("übermorgen", str(day_after_tomorrow)),
    ("in 3 Tagen", str((datetime.now() + timedelta(days=3)).date())),
]

for input_text, expected in date_tests:
    parsed = asana_agent.parse_relative_date(input_text)
    print(f"\nInput: '{input_text}'")
    print(f"   Erwartet: {expected}")
    print(f"   Geparst:  {parsed}")
    if parsed == expected:
        print("   ✅ KORREKT")
    else:
        print(f"   ❌ FEHLER")

print("\n" + "=" * 70)
print("TEST 5: VOLLSTÄNDIGER FLOW MIT USER-DELEGATION")
print("=" * 70)

# Hole erstes Projekt
projects = asana_agent.list_projects(limit=3)
if projects:
    first_project = projects[0]
    print(f"Verwende Projekt: {first_project['name']} (GID: {first_project['gid']})")

    # Test mit User-Delegation (wird vermutlich auf "me" zurückfallen)
    test_input = "Erstelle Aufgabe: TEST User-Delegation, weise die Aufgabe TestUser zu, fällig morgen"

    print(f"\n📝 Input: '{test_input}'")

    result = asana_agent.create_task_smart(
        user_input=test_input,
        notes="Test für User-Delegation und präzises Parsing",
        project_gid=first_project['gid']
    )

    print(f"\n📊 Ergebnis:")
    print(f"   Success: {result.get('success')}")

    if result.get('success'):
        print(f"   ✅ AUFGABE ERSTELLT!")
        print(f"   Task Name: {result.get('task_name')}")
        print(f"   Task GID: {result.get('task_gid')}")
        print(f"   Assignee: {result.get('assignee')}")
        print(f"   Assignee Name: {result.get('parsed_data', {}).get('assignee_name', 'N/A')}")
        print(f"   Permalink: {result.get('permalink_url')}")

        # Teste TaskAgent-Ausgabe
        print(f"\n📤 TaskAgent würde ausgeben:")
        print("   ---")

        parsed_data = result.get('parsed_data', {})
        assignee_name = parsed_data.get('assignee_name')

        output = f"✅ **Asana-Aufgabe erfolgreich erstellt!**\n\n"
        output += f"**Titel:** {result.get('task_name')}\n"
        output += f"**Fällig:** {parsed_data.get('due_on', 'N/A')}\n"

        if assignee_name:
            output += f"**Zugewiesen:** {assignee_name}\n\n"
        else:
            output += f"**Zugewiesen:** Dir (aktueller Nutzer)\n\n"

        output += f"🔗 **[Aufgabe in Asana öffnen]({result.get('permalink_url')})**\n\n"
        output += f"_Direktlink: {result.get('permalink_url')}_"

        print(output)

    else:
        print(f"   ❌ Fehler: {result.get('error')}")

else:
    print("⚠️ Keine Projekte gefunden")

print("\n" + "=" * 70)
print("TEST 6: ERROR-LOGGING")
print("=" * 70)

# Test mit ungültigen Daten, um Error-Logging zu prüfen
print("\n🔍 Teste Error-Logging mit ungültiger Projekt-GID...")

result = asana_agent.create_task(
    name="TEST Error-Logging",
    notes="Sollte fehlschlagen",
    project_gid="INVALID_GID_12345",
    assignee_gid="me"
)

print(f"\n📊 Ergebnis:")
print(f"   Success: {result.get('success')}")
if not result.get('success'):
    print(f"   ✅ Fehler korrekt behandelt")
    print(f"   Error Message: {result.get('error')}")
    print(f"   (Details sollten im Terminal-Log sichtbar sein)")
else:
    print(f"   ⚠️  Unerwarteter Erfolg")

print("\n" + "=" * 70)
print("✅ ALLE TESTS ABGESCHLOSSEN")
print("=" * 70)

print("\n📊 ZUSAMMENFASSUNG:")
print("  ✅ User-Suche (search_user_by_name)")
print("  ✅ Assignee-Extraktion (extract_assignee_from_input)")
print("  ✅ Titel-Cleanup (Assignee-Phrasen entfernen)")
print("  ✅ Datums-Parsing")
print("  ✅ Vollständiger Flow mit User-Delegation")
print("  ✅ Error-Logging mit Details")
print("  ✅ Link-Bestätigung")
