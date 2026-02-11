#!/usr/bin/env python3
"""
Test-Script für Outlook Graph API Authentifizierung
"""

import os
from dotenv import load_dotenv

# Lade .env
load_dotenv()

print("=" * 60)
print("OUTLOOK GRAPH API - AUTHENTIFIZIERUNGS-TEST")
print("=" * 60)
print()

# Prüfe Konfiguration
client_id = os.getenv("MICROSOFT_CLIENT_ID")
tenant_id = os.getenv("MICROSOFT_TENANT_ID")
client_secret = os.getenv("MICROSOFT_CLIENT_SECRET")

print(f"1. Konfiguration:")
print(f"   Client ID: {client_id[:8] if client_id else 'FEHLT'}...")
print(f"   Tenant ID: {tenant_id[:8] if tenant_id else 'FEHLT'}...")
print(f"   Client Secret: {'Vorhanden' if client_secret else 'Nicht gesetzt'}")
print()

if not client_id or not tenant_id:
    print("❌ FEHLER: Client ID oder Tenant ID fehlt in .env")
    exit(1)

# Test Device Code Flow
try:
    import msal

    print("2. Initialisiere MSAL...")
    app = msal.PublicClientApplication(
        client_id=client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}"
    )
    print("   ✓ MSAL App erstellt")

    # Starte Device Flow
    print()
    print("3. Starte Device Code Flow...")
    scopes = ["Calendars.Read", "Calendars.ReadWrite", "User.Read", "Mail.Send"]

    flow = app.initiate_device_flow(scopes=scopes)

    if "user_code" not in flow:
        print("   ❌ FEHLER: Device Flow konnte nicht initiiert werden")
        print(f"   Response: {flow}")
        exit(1)

    print("   ✓ Device Flow initiiert")
    print()
    print("=" * 60)
    print("BITTE FÜHREN SIE DIE ANMELDUNG DURCH:")
    print("=" * 60)
    print(f"\n1. Öffnen Sie: {flow['verification_uri']}")
    print(f"2. Geben Sie diesen Code ein: {flow['user_code']}")
    print(f"\n3. Drücken Sie Enter hier NACHDEM Sie sich angemeldet haben...")
    print("=" * 60)
    print()

    input(">>> Drücken Sie Enter nach der Anmeldung...")

    print()
    print("4. Hole Token...")
    result = app.acquire_token_by_device_flow(flow)

    if "access_token" in result:
        print("   ✅ TOKEN ERFOLGREICH ERHALTEN!")
        print(f"   Token (erste 20 Zeichen): {result['access_token'][:20]}...")
        print(f"   Token-Typ: {result.get('token_type', 'N/A')}")
        print(f"   Expires in: {result.get('expires_in', 'N/A')} Sekunden")

        # Test API-Call
        print()
        print("5. Teste API-Call...")
        import requests

        headers = {
            "Authorization": f"Bearer {result['access_token']}",
            "Content-Type": "application/json"
        }

        response = requests.get(
            "https://graph.microsoft.com/v1.0/me",
            headers=headers
        )

        if response.status_code == 200:
            user_data = response.json()
            print(f"   ✅ API-Call erfolgreich!")
            print(f"   User: {user_data.get('displayName', 'N/A')}")
            print(f"   Email: {user_data.get('mail', user_data.get('userPrincipalName', 'N/A'))}")
        else:
            print(f"   ⚠️ API-Call fehlgeschlagen: {response.status_code}")
            print(f"   Response: {response.text[:200]}")

        print()
        print("=" * 60)
        print("✅ AUTHENTIFIZIERUNG ERFOLGREICH!")
        print("=" * 60)

    else:
        print("   ❌ FEHLER: Kein Token erhalten")
        print(f"   Error: {result.get('error', 'N/A')}")
        print(f"   Error Description: {result.get('error_description', 'N/A')}")
        exit(1)

except ImportError as e:
    print(f"❌ FEHLER: Benötigtes Modul fehlt: {e}")
    print("Installieren Sie: pip install msal requests")
    exit(1)
except Exception as e:
    print(f"❌ FEHLER: {e}")
    import traceback
    traceback.print_exc()
    exit(1)
