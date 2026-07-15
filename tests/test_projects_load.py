#!/usr/bin/env python3
"""Test Projekt-Laden"""

import os
import sys
from dotenv import load_dotenv
load_dotenv()

from agents.asana_agent import AsanaAgent

print("=" * 70)
print("TEST: PROJEKTE LADEN")
print("=" * 70)

agent = AsanaAgent()
if not agent.is_connected():
    print("❌ Nicht verbunden")
    sys.exit(1)

print("✅ Verbunden\n")

print("Lade Projekte...")
try:
    projects = agent.list_projects(limit=5)
    print(f"✅ {len(projects)} Projekte geladen\n")

    for i, proj in enumerate(projects, 1):
        print(f"{i}. {proj['name']}")
        print(f"   GID: {proj['gid']}")
        print()

except Exception as e:
    print(f"❌ Fehler: {e}")
    import traceback
    traceback.print_exc()

print("=" * 70)
