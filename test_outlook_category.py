#!/usr/bin/env python3
"""
Test-Script für Outlook-Kategorie-Funktion

Verwendung:
    python test_outlook_category.py
"""

import sys
from pathlib import Path

# Füge Parent-Directory zum Path hinzu
sys.path.insert(0, str(Path(__file__).parent))

from tools.outlook_graph_tool import OutlookGraphTool
from datetime import datetime, timedelta


def test_category_function():
    """Testet die add_category_to_event Funktion"""

    print("=" * 80)
    print("OUTLOOK KATEGORIE TEST")
    print("=" * 80)
    print()

    # 1. Initialisiere Outlook Tool
    print("1. Initialisiere Outlook Tool...")
    outlook = OutlookGraphTool()

    if not outlook.access_token:
        print("❌ FEHLER: Outlook ist nicht authentifiziert!")
        print("   Bitte führen Sie zuerst die Authentifizierung durch:")
        print("   python authenticate_outlook.py")
        return

    print("✅ Outlook authentifiziert")
    print()

    # 2. Hole Events der nächsten 7 Tage
    print("2. Hole Termine der nächsten 7 Tage...")
    from datetime import datetime, timedelta

    start = datetime.now()
    end = start + timedelta(days=7)
    events = outlook.get_events_for_date_range(start, end)

    if not events:
        print("❌ Keine Termine in den nächsten 7 Tagen gefunden!")
        print("   Bitte erstellen Sie einen Test-Termin.")
        return

    print(f"✅ {len(events)} Termin(e) gefunden:")
    print()

    for i, event in enumerate(events, 1):
        event_id = event.get('id')
        title = event.get('subject', 'Ohne Titel')
        start = event.get('start', {})
        categories = event.get('categories', [])

        print(f"   {i}. {title}")
        print(f"      ID: {event_id[:30]}...")
        print(f"      Kategorien: {categories if categories else '(keine)'}")
        print()

    # 3. Wähle ersten Termin für Test
    test_event = events[0]
    test_event_id = test_event.get('id')
    test_title = test_event.get('subject', 'Ohne Titel')

    print("=" * 80)
    print(f"3. Teste Kategorie-Funktion mit: '{test_title}'")
    print("=" * 80)
    print()

    # 4. Füge Kategorie "Protokoll" hinzu
    print("4. Füge Kategorie 'Protokoll' hinzu...")
    print()

    result = outlook.add_category_to_event(
        event_id=test_event_id,
        category="Protokoll"
    )

    print()
    print("ERGEBNIS:")
    print("-" * 80)
    if result.get('success'):
        print(f"✅ ERFOLG: {result.get('message')}")
    else:
        print(f"❌ FEHLER: {result.get('error')}")
    print("-" * 80)
    print()

    # 5. Verifiziere: Lese Event erneut
    print("5. Verifiziere: Lese Termin erneut...")
    print()

    import requests
    url = f"https://graph.microsoft.com/v1.0/me/events/{test_event_id}"
    headers = {
        "Authorization": f"Bearer {outlook.access_token}",
        "Content-Type": "application/json"
    }
    params = {"$select": "subject,categories"}

    response = requests.get(url, headers=headers, params=params)

    if response.status_code == 200:
        event_data = response.json()
        current_categories = event_data.get('categories', [])

        print(f"Aktueller Status von '{test_title}':")
        print(f"   Kategorien: {current_categories}")
        print()

        if 'Protokoll' in current_categories:
            print("✅ ERFOLG: Kategorie 'Protokoll' ist jetzt gesetzt!")
            print()
            print("WICHTIG:")
            print("   - Aktualisieren Sie Ihre Outlook-Ansicht (F5)")
            print("   - In Outlook Desktop: Ansicht → Kategorien aktivieren")
            print("   - In Outlook Web: Kategorie sollte als farbiger Tag sichtbar sein")
        else:
            print("❌ FEHLER: Kategorie wurde NICHT gesetzt!")
            print(f"   Gefundene Kategorien: {current_categories}")
    else:
        print(f"❌ Fehler beim Verifizieren: {response.status_code}")
        print(f"   {response.text}")

    print()
    print("=" * 80)
    print("TEST ABGESCHLOSSEN")
    print("=" * 80)


if __name__ == "__main__":
    try:
        test_category_function()
    except KeyboardInterrupt:
        print("\n\nTest abgebrochen.")
    except Exception as e:
        print(f"\n❌ FEHLER: {e}")
        import traceback
        traceback.print_exc()
