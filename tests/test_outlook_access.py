#!/usr/bin/env python3
"""
Test-Script für Microsoft Outlook Graph API Zugriff
"""

import os
from dotenv import load_dotenv
from tools.outlook_graph_tool import OutlookGraphTool

# Lade .env
load_dotenv()

print("=" * 60)
print("MICROSOFT OUTLOOK GRAPH API - VERBINDUNGSTEST")
print("=" * 60)
print()

# Initialisiere Outlook Tool
print("1. Initialisiere Outlook Graph Tool...")
outlook = OutlookGraphTool()

# Zeige Konfigurationsstatus
print("\n2. Prüfe Konfiguration...")
config = outlook.get_configuration_status()
print(f"   ✓ Konfiguriert: {config['configured']}")
print(f"   ✓ Client ID: {config['client_id']}")
print(f"   ✓ Tenant ID: {config['tenant_id']}")
print(f"   ✓ Authentifiziert: {config['authenticated']}")

if not config['configured']:
    print("\n❌ FEHLER: Microsoft Graph API nicht konfiguriert!")
    print(config['setup_instructions'])
    exit(1)

# Versuche Authentifizierung
print("\n3. Starte Authentifizierung...")
print("   (Sie werden aufgefordert, sich bei Microsoft anzumelden)")
print()

success = outlook.authenticate_with_msal()

if not success:
    print("\n❌ FEHLER: Authentifizierung fehlgeschlagen!")
    print("Bitte prüfen Sie die Fehlermeldungen oben.")
    exit(1)

print("\n✅ Authentifizierung erfolgreich!")

# Lade heutige Termine
print("\n4. Lade heutige Termine...")
events = outlook.get_todays_events()

if events:
    print(f"\n✅ {len(events)} Termin(e) gefunden:")
    print("=" * 60)
    for i, event in enumerate(events, 1):
        print(f"\n📅 TERMIN #{i}")
        print(f"   Titel:      {event['title']}")
        print(f"   Zeit:       {event['start']} - {event['end']}")
        print(f"   Ort:        {event['location'] or 'Kein Ort angegeben'}")
        print(f"   Teilnehmer: {', '.join(event['attendees']) if event['attendees'] else 'Keine'}")
        if event['preview']:
            print(f"   Vorschau:   {event['preview'][:100]}...")
else:
    print("\n📭 Keine Termine für heute gefunden.")

print("\n" + "=" * 60)
print("TEST ABGESCHLOSSEN")
print("=" * 60)
