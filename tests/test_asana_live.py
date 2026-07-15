#!/usr/bin/env python3
"""
Test-Script für Live Asana-Integration
Prüft Verbindung, Projekte und Aufgaben
"""

import os
from dotenv import load_dotenv

# Lade .env
load_dotenv()

print("=" * 60)
print("ASANA LIVE-INTEGRATION TEST")
print("=" * 60)

# Prüfe Token
token = os.getenv("ASANA_ACCESS_TOKEN")
if token:
    print(f"✅ ASANA_ACCESS_TOKEN gefunden: {token[:10]}...{token[-5:]}")
else:
    print("❌ ASANA_ACCESS_TOKEN nicht gefunden in .env")
    exit(1)

print("\n" + "=" * 60)
print("1. INITIALISIERE ASANA AGENT")
print("=" * 60)

from agents.asana_agent import AsanaAgent

agent = AsanaAgent()

print("\n" + "=" * 60)
print("2. PRÜFE VERBINDUNGSSTATUS")
print("=" * 60)

if agent.is_connected():
    print("✅ Asana Agent erfolgreich verbunden")
    print(f"   Workspace GID: {agent.workspace_gid}")
else:
    print("❌ Asana Agent NICHT verbunden")
    exit(1)

print("\n" + "=" * 60)
print("3. LADE PROJEKTE")
print("=" * 60)

projects = agent.list_projects(limit=50)
print(f"Gefunden: {len(projects)} Projekte\n")

if projects:
    for i, project in enumerate(projects[:10], 1):
        print(f"{i}. {project['name']} (GID: {project['gid']})")
else:
    print("⚠️ Keine Projekte gefunden")

print("\n" + "=" * 60)
print("4. LADE ALLE MEINE AUFGABEN")
print("=" * 60)

tasks = agent.get_upcoming_tasks(days=7, limit=10)
print(f"Gefunden: {len(tasks)} Aufgaben in den nächsten 7 Tagen\n")

if tasks:
    for i, task in enumerate(tasks[:5], 1):
        print(f"{i}. {task['name']}")
        print(f"   Fällig: {task.get('due_on', 'Kein Datum')}")
        print(f"   Projekte: {', '.join(task.get('projects', []))}")
        print()
else:
    print("⚠️ Keine anstehenden Aufgaben gefunden")

print("\n" + "=" * 60)
print("5. LADE AUFGABEN EINES PROJEKTS")
print("=" * 60)

if projects:
    test_project = projects[0]
    print(f"Teste mit Projekt: {test_project['name']}")

    project_tasks = agent.get_project_tasks(test_project['gid'], limit=20)
    print(f"Gefunden: {len(project_tasks)} Aufgaben im Projekt\n")

    if project_tasks:
        for i, task in enumerate(project_tasks[:5], 1):
            print(f"{i}. {task['name']}")
            print(f"   Fällig: {task.get('due_on', 'Kein Datum')}")
            notes = task.get('notes', '')
            if notes:
                preview = notes[:100] + '...' if len(notes) > 100 else notes
                print(f"   Beschreibung: {preview}")
            print()
    else:
        print("⚠️ Keine Aufgaben in diesem Projekt")

print("=" * 60)
print("✅ TEST ABGESCHLOSSEN")
print("=" * 60)
