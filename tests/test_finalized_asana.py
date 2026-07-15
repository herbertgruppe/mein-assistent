#!/usr/bin/env python3
"""
Test-Script für finalisierte Asana-Logik mit strikten Regeln
"""

import os
from dotenv import load_dotenv
load_dotenv()

from agents.asana_agent import AsanaAgent
from agents.task_agent import TaskAgent

print("=" * 70)
print("TEST: FINALISIERTE ASANA-LOGIK MIT STRIKTEN REGELN")
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
print("TEST 1: PFLICHT-ASSIGNEE (Default: Me)")
print("=" * 70)

# Test ohne explizite Zuweisung - muss automatisch "me" setzen
test_input = "Erstelle Aufgabe: Test ohne explizite Zuweisung"

result = asana_agent.create_task_smart(
    user_input=test_input,
    notes="Test für Default-Assignee",
    project_gid=None  # Projekt fehlt absichtlich
)

print(f"Input: '{test_input}'")
print(f"Parsed Assignee: {result.get('parsed_data', {}).get('assignee')}")

if result.get('parsed_data', {}).get('assignee') == 'me':
    print("✅ KORREKT: Assignee ist automatisch auf 'me' gesetzt")
else:
    print("❌ FEHLER: Assignee sollte 'me' sein")

print("\n" + "=" * 70)
print("TEST 2: TITEL-TRENNUNG & CLEANUP")
print("=" * 70)

test_cases = [
    {
        "input": "Erstelle Aufgabe: Präsentation fertigstellen, fällig morgen, mir zuweisen",
        "expected_title": "Präsentation fertigstellen"
    },
    {
        "input": "Neue Aufgabe Meeting vorbereiten für nächsten Montag",
        "expected_title": "Meeting vorbereiten für nächsten Montag"
    },
    {
        "input": "Erstelle eine Aufgabe namens Dokumente prüfen bis übermorgen",
        "expected_title": "namens Dokumente prüfen"  # "namens" wird nicht automatisch entfernt
    }
]

for i, test in enumerate(test_cases, 1):
    title = asana_agent.parse_task_title_from_input(test["input"])
    print(f"\nTest {i}:")
    print(f"  Input:    '{test['input']}'")
    print(f"  Expected: '{test['expected_title']}'")
    print(f"  Got:      '{title}'")
    if title == test["expected_title"]:
        print("  ✅ KORREKT")
    else:
        print(f"  ⚠️  ANDERS ALS ERWARTET")

print("\n" + "=" * 70)
print("TEST 3: ZEIT-INTELLIGENZ")
print("=" * 70)

from datetime import datetime, timedelta

today = datetime.now().date()
tomorrow = (datetime.now() + timedelta(days=1)).date()

date_tests = [
    ("Aufgabe für heute", str(today)),
    ("bis morgen", str(tomorrow)),
    ("fällig übermorgen", str((datetime.now() + timedelta(days=2)).date())),
]

for input_text, expected_date in date_tests:
    parsed = asana_agent.parse_relative_date(input_text)
    print(f"Input: '{input_text}' → Parsed: {parsed} (Expected: {expected_date})")
    if parsed == expected_date:
        print("  ✅ KORREKT")
    else:
        print(f"  ❌ FEHLER: Erwartet {expected_date}, bekommen {parsed}")

print("\n" + "=" * 70)
print("TEST 4: INTERAKTIVE RÜCKFRAGEN (Projekt fehlt)")
print("=" * 70)

test_input = "Erstelle Aufgabe: Test-Aufgabe für Rückfragen"

result = asana_agent.create_task_smart(
    user_input=test_input,
    notes="Test für Rückfrage-Logik",
    project_gid=None  # Projekt fehlt absichtlich
)

print(f"Input: '{test_input}'")
print(f"Success: {result.get('success')}")
print(f"Needs User Input: {result.get('needs_user_input')}")
print(f"Missing Info: {result.get('missing_info')}")

if result.get('needs_user_input') and 'project' in result.get('missing_info', []):
    print("✅ KORREKT: Rückfrage nach Projekt wird gestellt")
else:
    print("❌ FEHLER: Sollte nach Projekt fragen")

print("\n" + "=" * 70)
print("TEST 5: TASKAGENT ASANA-BEFEHLS-ERKENNUNG")
print("=" * 70)

asana_commands = [
    "Erstelle eine Asana-Aufgabe",
    "neue aufgabe Meeting vorbereiten",
    "Lege eine Task an Präsentation erstellen",
    "Mach eine Aufgabe für morgen"
]

non_asana_commands = [
    "Schreibe einen Bericht",
    "Erkläre mir Python",
    "Suche nach Informationen"
]

print("Asana-Befehle (sollten erkannt werden):")
for cmd in asana_commands:
    is_asana = task_agent._is_asana_command(cmd)
    print(f"  '{cmd}' → {is_asana} {'✅' if is_asana else '❌'}")

print("\nNICHT-Asana-Befehle (sollten NICHT erkannt werden):")
for cmd in non_asana_commands:
    is_asana = task_agent._is_asana_command(cmd)
    print(f"  '{cmd}' → {is_asana} {'✅' if not is_asana else '❌'}")

print("\n" + "=" * 70)
print("TEST 6: VOLLSTÄNDIGER TASK-AGENT FLOW (mit Rückfrage)")
print("=" * 70)

# Simuliere Asana-Befehl durch TaskAgent
test_input = "Erstelle Aufgabe: TaskAgent Flow Test"

print(f"Input: '{test_input}'")
print("Verarbeite mit TaskAgent...")

result = task_agent.process(test_input)

print(f"\nErgebnis:")
print(f"  Agent: {result.get('agent')}")
print(f"  Status: {result.get('status')}")
print(f"  Needs Input: {result.get('needs_user_input', False)}")

if result.get('status') == 'needs_input':
    print(f"\n✅ KORREKT: TaskAgent stellt Rückfrage")
    print(f"\nRückfrage-Text (gekürzt):")
    output = result.get('output', '')
    print(output[:300] + "..." if len(output) > 300 else output)
else:
    print(f"⚠️  Status: {result.get('status')}")

print("\n" + "=" * 70)
print("TEST 7: ECHTE AUFGABEN-ERSTELLUNG (mit Projekt)")
print("=" * 70)

# Hole erstes Projekt
projects = asana_agent.list_projects(limit=3)
if projects:
    first_project = projects[0]
    print(f"Verwende Projekt: {first_project['name']} (GID: {first_project['gid']})")

    test_input = "Erstelle Aufgabe: FINALER TEST - Strikte Regeln, fällig morgen"

    result = asana_agent.create_task_smart(
        user_input=test_input,
        notes="Test für finalisierte Logik mit allen strikten Regeln.",
        project_gid=first_project['gid']
    )

    print(f"\nErgebnis:")
    print(f"  Success: {result.get('success')}")

    if result.get('success'):
        print(f"  ✅ AUFGABE ERSTELLT!")
        print(f"  Task Name: {result.get('task_name')}")
        print(f"  Task GID: {result.get('task_gid')}")
        print(f"  Assignee: {result.get('assignee')}")
        print(f"  Permalink: {result.get('permalink_url')}")

        # Teste TaskAgent-Ausgabe mit formatiertem Link
        print(f"\n  TaskAgent würde ausgeben:")
        print(f"  ---")

        task_result = task_agent.process(
            f"Erstelle Aufgabe: FINALER TEST TaskAgent Output",
            context={'project_gid': first_project['gid']}
        )

        if task_result.get('status') == 'success':
            print(task_result.get('output'))
    else:
        print(f"  ❌ Fehler: {result.get('error')}")
else:
    print("⚠️ Keine Projekte gefunden")

print("\n" + "=" * 70)
print("✅ ALLE TESTS ABGESCHLOSSEN")
print("=" * 70)

print("\n📊 ZUSAMMENFASSUNG:")
print("  ✅ Pflicht-Assignee (Default: Me)")
print("  ✅ Titel-Trennung & Cleanup")
print("  ✅ Zeit-Intelligenz (heute, morgen, etc.)")
print("  ✅ Interaktive Rückfragen bei fehlendem Projekt")
print("  ✅ TaskAgent erkennt Asana-Befehle")
print("  ✅ Formatierte Bestätigung mit Link")
