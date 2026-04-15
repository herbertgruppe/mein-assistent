#!/usr/bin/env python3
"""
Prüft die Outlook Master-Kategorien-Liste und erstellt ggf. die Kategorie "Protokoll"

Verwendung:
    python check_outlook_categories.py
"""

import sys
from pathlib import Path

# Füge Parent-Directory zum Path hinzu
sys.path.insert(0, str(Path(__file__).parent))

from tools.outlook_graph_tool import OutlookGraphTool
import requests


def check_and_create_categories():
    """Prüft und erstellt Outlook-Kategorien"""

    print("=" * 80)
    print("OUTLOOK KATEGORIEN CHECK")
    print("=" * 80)
    print()

    # 1. Initialisiere Outlook Tool
    print("1. Initialisiere Outlook Tool...")
    outlook = OutlookGraphTool()

    if not outlook.access_token:
        print("❌ FEHLER: Outlook ist nicht authentifiziert!")
        return

    print("✅ Outlook authentifiziert")
    print()

    # 2. Hole Master-Kategorien-Liste
    print("2. Hole Master-Kategorien-Liste...")
    print()

    url = "https://graph.microsoft.com/v1.0/me/outlook/masterCategories"
    headers = {
        "Authorization": f"Bearer {outlook.access_token}",
        "Content-Type": "application/json"
    }

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        data = response.json()
        categories = data.get('value', [])

        print(f"✅ {len(categories)} Kategorien in der Master-Liste gefunden:")
        print()

        for cat in categories:
            cat_name = cat.get('displayName')
            cat_color = cat.get('color', 'none')
            print(f"   - {cat_name} (Farbe: {cat_color})")

        print()

        # Prüfe ob "Protokoll" existiert
        protokoll_exists = any(cat.get('displayName') == 'Protokoll' for cat in categories)

        if protokoll_exists:
            print("✅ Kategorie 'Protokoll' existiert bereits!")
        else:
            print("⚠️ Kategorie 'Protokoll' existiert NICHT in der Master-Liste!")
            print()
            print("3. Erstelle Kategorie 'Protokoll'...")
            print()

            # Erstelle Kategorie
            create_url = "https://graph.microsoft.com/v1.0/me/outlook/masterCategories"
            create_data = {
                "displayName": "Protokoll",
                "color": "preset2"  # Blau
            }

            create_response = requests.post(create_url, headers=headers, json=create_data)

            if create_response.status_code == 201:
                print("✅ Kategorie 'Protokoll' erfolgreich erstellt!")
                print("   Farbe: Blau")
                print()
                print("WICHTIG: Outlook-Kategorien sollten jetzt funktionieren!")
            else:
                print(f"❌ Fehler beim Erstellen: {create_response.status_code}")
                print(f"   {create_response.text}")

    else:
        print(f"❌ Fehler beim Abrufen der Kategorien: {response.status_code}")
        print(f"   {response.text}")
        print()
        print("HINWEIS: Master-Kategorien sind möglicherweise nicht verfügbar.")
        print("   Kategorien können trotzdem direkt an Events gesetzt werden.")

    print()
    print("=" * 80)
    print("CHECK ABGESCHLOSSEN")
    print("=" * 80)
    print()
    print("NÄCHSTE SCHRITTE:")
    print("   1. Führen Sie 'python test_outlook_category.py' aus")
    print("   2. Prüfen Sie Outlook (Web oder Desktop)")
    print("   3. Aktualisieren Sie die Ansicht (F5)")


if __name__ == "__main__":
    try:
        check_and_create_categories()
    except KeyboardInterrupt:
        print("\n\nAbgebrochen.")
    except Exception as e:
        print(f"\n❌ FEHLER: {e}")
        import traceback
        traceback.print_exc()
