#!/usr/bin/env python3
"""
Test-Script für intelligente Asana-Aufgaben-Erstellung
"""

import os
from dotenv import load_dotenv
load_dotenv()

from agents.asana_agent import AsanaAgent

print("=" * 70)
print("TEST: INTELLIGENTE AUFGABEN-ERSTELLUNG")
print("=" * 70)

agent = AsanaAgent()

if not agent.is_connected():
    print("❌ Asana nicht verbunden")
    exit(1)

print("\n✅ Asana verbunden")

# Test 1: Titel-Parsing mit Doppelpunkt
print("\n" + "="*70)
print("TEST 1: Titel-Parsing mit Doppelpunkt")
print("="*70)

test_inputs = [
    "Erstelle Aufgabe: Meeting mit Dr. Herbert vorbereiten",
    "neue aufgabe: Präsentation für KHS erstellen",
    "Aufgabe erstellen: Dokumente prüfen"
]

for input_text in test_inputs:
    title = agent.parse_task_title_from_input(input_text)
    print(f"Input:  '{input_text}'")
    print(f"Titel:  '{title}'")
    print()

# Test 2: Titel-Parsing ohne Doppelpunkt
print("\n" + "="*70)
print("TEST 2: Titel-Parsing ohne Doppelpunkt")
print("="*70)

test_inputs = [
    "Erstelle eine Aufgabe Meeting vorbereiten",
    "neue aufgabe Dokumente prüfen",
    "lege eine task an Präsentation erstellen"
]

for input_text in test_inputs:
    title = agent.parse_task_title_from_input(input_text)
    print(f"Input:  '{input_text}'")
    print(f"Titel:  '{title}'")
    print()

# Test 3: Datums-Parsing
print("\n" + "="*70)
print("TEST 3: Relative Datums-Erkennung")
print("="*70)

date_inputs = [
    "Aufgabe für heute",
    "bis morgen",
    "nächsten Freitag",
    "in 3 Tagen",
    "diese Woche",
    "nächste Woche",
    "25.01.2026",
    "2026-01-30",
    "übermorgen"
]

for input_text in date_inputs:
    parsed_date = agent.parse_relative_date(input_text)
    print(f"Input: '{input_text}' → Datum: {parsed_date}")

# Test 4: Selbstzuweisungs-Erkennung
print("\n" + "="*70)
print("TEST 4: Selbstzuweisungs-Erkennung")
print("="*70)

assignee_inputs = [
    "Erstelle Aufgabe für mich",
    "mir zuweisen",
    "an mich",
    "ich soll das machen",
    "für Peter",  # sollte False sein
    "ohne Zuweisung"  # sollte False sein
]

for input_text in assignee_inputs:
    is_self = agent.detect_assignee_self(input_text)
    print(f"Input: '{input_text}' → Selbstzuweisung: {is_self}")

# Test 5: Komplette Smart-Erstellung (DRY-RUN ohne tatsächliche Erstellung)
print("\n" + "="*70)
print("TEST 5: Smart-Parsing (komplett)")
print("="*70)

complex_inputs = [
    "Erstelle Aufgabe: Präsentation fertigstellen, fällig morgen, mir zuweisen",
    "neue aufgabe Meeting vorbereiten für nächsten Montag",
    "Aufgabe: Dokumente prüfen bis übermorgen",
]

for input_text in complex_inputs:
    title = agent.parse_task_title_from_input(input_text)
    due_date = agent.parse_relative_date(input_text)
    is_self_assigned = agent.detect_assignee_self(input_text)

    print(f"\nInput: '{input_text}'")
    print(f"  → Titel: '{title}'")
    print(f"  → Datum: {due_date}")
    print(f"  → Selbstzuweisung: {is_self_assigned}")

# Test 6: create_task_smart mit fehlenden Infos
print("\n" + "="*70)
print("TEST 6: create_task_smart - Fehlende Infos")
print("="*70)

test_input = "Erstelle Aufgabe: Test-Aufgabe für Smart-Parsing, fällig morgen, mir zuweisen"
print(f"Input: '{test_input}'")

result = agent.create_task_smart(
    user_input=test_input,
    notes="Dies ist ein Test für intelligentes Parsing",
    project_gid=None  # Projekt fehlt absichtlich
)

print("\nErgebnis:")
print(f"  Success: {result.get('success')}")
print(f"  Needs User Input: {result.get('needs_user_input')}")
print(f"  Missing Info: {result.get('missing_info')}")
print(f"  Parsed Data: {result.get('parsed_data')}")

# Test 7: create_task_smart mit Projekt (echte Erstellung)
print("\n" + "="*70)
print("TEST 7: create_task_smart - Echte Erstellung")
print("="*70)

# Hole erstes Projekt
projects = agent.list_projects(limit=5)
if projects:
    first_project = projects[0]
    print(f"Verwende Projekt: {first_project['name']} (GID: {first_project['gid']})")

    test_input = "Erstelle Aufgabe: TEST - Smart Parsing Aufgabe, fällig morgen, mir zuweisen"

    result = agent.create_task_smart(
        user_input=test_input,
        notes="Dies ist eine Test-Aufgabe für intelligentes Parsing. Bitte löschen nach Test.",
        project_gid=first_project['gid']
    )

    print("\nErgebnis:")
    print(f"  Success: {result.get('success')}")
    if result.get('success'):
        print(f"  Task Name: {result.get('task_name')}")
        print(f"  Task GID: {result.get('task_gid')}")
        print(f"  Assignee: {result.get('assignee')}")
        print(f"  Permalink: {result.get('permalink_url')}")
    else:
        print(f"  Error: {result.get('error')}")
else:
    print("⚠️ Keine Projekte gefunden - überspringe echte Erstellung")

print("\n" + "="*70)
print("✅ ALLE TESTS ABGESCHLOSSEN")
print("="*70)
