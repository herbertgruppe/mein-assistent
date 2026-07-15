#!/usr/bin/env python3
"""Schneller Test für Aufgaben-Erstellung"""

import os
from dotenv import load_dotenv
load_dotenv()

from agents.asana_agent import AsanaAgent
from datetime import datetime, timedelta

print("=" * 70)
print("SCHNELLER ASANA CREATE_TASK TEST")
print("=" * 70)

agent = AsanaAgent()
if not agent.is_connected():
    print('❌ Nicht verbunden')
    exit(1)

print('\n✅ Agent verbunden')

# Hole erstes Projekt
projects = agent.list_projects(limit=1)
if not projects:
    print('❌ Keine Projekte')
    exit(1)

project = projects[0]
print(f'📋 Verwende Projekt: {project["name"][:50]}')
print(f'   GID: {project["gid"]}')

# Teste direkte Aufgaben-Erstellung
print('\n🚀 Erstelle Test-Aufgabe mit create_task()...\n')

tomorrow = str((datetime.now() + timedelta(days=1)).date())

result = agent.create_task(
    name='DEBUG TEST 2026-01-25 15:30',
    notes='Test für Fehlersuche - bitte ignorieren',
    project_gid=project['gid'],
    assignee_gid='me',
    due_on=tomorrow
)

print(f'\n📊 ERGEBNIS:')
print(f'   Success: {result.get("success")}')

if result.get('success'):
    print(f'\n   ✅ AUFGABE ERFOLGREICH ERSTELLT!')
    print(f'   GID: {result.get("task_gid")}')
    print(f'   Name: {result.get("task_name")}')
    print(f'   Assignee: {result.get("assignee")}')
    print(f'\n🔗 DIREKTER LINK:')
    print(f'   {result.get("permalink_url")}')
    print(f'\n✅ Bitte in Asana prüfen, ob die Aufgabe angekommen ist!')
else:
    print(f'\n   ❌ FEHLER: {result.get("error")}')
    print(f'\n⚠️  Siehe Debug-Ausgaben oben für Details!')

print("\n" + "=" * 70)
