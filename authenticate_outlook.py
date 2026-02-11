"""
Outlook Authentication Script
Authentifiziert den Benutzer mit Microsoft Graph API und speichert das Token
"""

import os
from dotenv import load_dotenv
from tools.outlook_graph_tool import OutlookGraphTool

# Lade Umgebungsvariablen
load_dotenv()

def main():
    print("=" * 70)
    print("Microsoft Outlook Authentifizierung")
    print("=" * 70)
    print()

    # Prüfe Konfiguration
    client_id = os.getenv("MICROSOFT_CLIENT_ID")
    tenant_id = os.getenv("MICROSOFT_TENANT_ID")

    if not client_id or not tenant_id:
        print("❌ FEHLER: Microsoft Graph API nicht konfiguriert!")
        print()
        print("Bitte fügen Sie folgende Werte in Ihre .env-Datei ein:")
        print("  MICROSOFT_CLIENT_ID=...")
        print("  MICROSOFT_TENANT_ID=...")
        print()
        print("Diese Werte erhalten Sie von Ihrer IT-Abteilung nach der")
        print("App-Registrierung im Azure Portal.")
        return

    print("✓ Konfiguration gefunden:")
    print(f"  Client ID: {client_id[:20]}...")
    print(f"  Tenant ID: {tenant_id[:20]}...")
    print()

    # Initialisiere Outlook Tool
    outlook_tool = OutlookGraphTool()

    # Prüfe ob bereits authentifiziert
    if outlook_tool.access_token:
        print("✓ Sie sind bereits authentifiziert!")
        print(f"  Token-Datei: {outlook_tool.token_file}")
        print()

        # Test: Hole heutige Events
        try:
            events = outlook_tool.get_todays_events()
            print(f"✓ Verbindungstest erfolgreich!")
            print(f"  Gefundene Termine heute: {len(events) if events else 0}")
        except Exception as e:
            print(f"⚠️ Verbindungstest fehlgeschlagen: {e}")
            print()
            print("Token ist möglicherweise abgelaufen. Starte Neuauthentifizierung...")
            outlook_tool.access_token = None

    # Authentifizierung erforderlich
    if not outlook_tool.access_token:
        print("=" * 70)
        print("Starte Authentifizierungs-Prozess...")
        print("=" * 70)
        print()
        print("Sie werden jetzt zum Microsoft-Login weitergeleitet.")
        print("Bitte melden Sie sich mit Ihrem Geschäftskonto an.")
        print()

        # Starte Device Code Flow
        success = outlook_tool.authenticate_with_msal(
            use_device_flow=True,
            timeout=300  # 5 Minuten
        )

        if success:
            print()
            print("=" * 70)
            print("✓ AUTHENTIFIZIERUNG ERFOLGREICH!")
            print("=" * 70)
            print()
            print(f"Token wurde gespeichert in: {outlook_tool.token_file}")
            print()
            print("Sie können jetzt den CalendarEmailAgent verwenden:")
            print("  - Termine auflisten")
            print("  - E-Mails suchen")
            print("  - E-Mail-Entwürfe erstellen")
            print()

            # Test: Hole heutige Events
            try:
                events = outlook_tool.get_todays_events()
                print(f"✓ Test erfolgreich: {len(events) if events else 0} Termine heute gefunden")
            except Exception as e:
                print(f"⚠️ Test fehlgeschlagen: {e}")
        else:
            print()
            print("❌ Authentifizierung fehlgeschlagen!")
            print()
            print("Mögliche Gründe:")
            print("  - Timeout (5 Minuten)")
            print("  - Falsches Konto verwendet")
            print("  - Fehlende Berechtigungen")
            print()
            print("Bitte versuchen Sie es erneut oder kontaktieren Sie Ihre IT-Abteilung.")

    print()
    print("=" * 70)
    print("Fertig!")
    print("=" * 70)

if __name__ == "__main__":
    main()
