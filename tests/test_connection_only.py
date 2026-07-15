#!/usr/bin/env python3
"""Test nur Verbindung ohne Aufgaben-Erstellung"""

import os
import sys
from dotenv import load_dotenv
load_dotenv()

print("1. Dotenv geladen")

token = os.getenv("ASANA_ACCESS_TOKEN")
if not token:
    print("❌ Kein Token!")
    sys.exit(1)

print(f"2. Token gefunden: {token[:20]}...")

import asana
print("3. Asana importiert")

configuration = asana.Configuration()
configuration.access_token = token
client = asana.ApiClient(configuration)

print("4. Client erstellt")

workspaces_api = asana.WorkspacesApi(client)
print("5. WorkspacesApi erstellt")

print("6. Versuche Workspaces zu laden...")
try:
    opts = {'opt_pretty': True}
    workspaces_response = list(workspaces_api.get_workspaces(opts))
    print(f"7. Workspaces geladen: {len(workspaces_response)}")

    if workspaces_response:
        ws = workspaces_response[0]
        print(f"   ✅ Workspace: {ws['name']}")
        print(f"   ✅ GID: {ws['gid']}")
    else:
        print("   ❌ Keine Workspaces")
except Exception as e:
    print(f"   ❌ Fehler: {e}")
    import traceback
    traceback.print_exc()
