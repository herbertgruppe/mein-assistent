#!/usr/bin/env python3
"""
Debug-Skript: Prüft warum Termine für 02.02.26 nicht gefunden werden
"""

import os
import sys
from datetime import datetime, date
from pathlib import Path

# .env laden
from dotenv import load_dotenv
load_dotenv()

# Tools importieren
from tools.outlook_graph_tool import OutlookGraphTool

def main():
    print("=" * 70)
    print("KALENDER DEBUG - 02.02.2026")
    print("=" * 70)

    # Outlook Tool initialisieren
    outlook_tool = OutlookGraphTool()

    if not outlook_tool.is_authenticated():
        print("\n❌ Outlook nicht authentifiziert!")
        print("Bitte führen Sie zuerst die Authentifizierung durch.")
        return

    print("\n✓ Outlook authentifiziert")

    # Teste verschiedene Datums-Varianten
    test_date = date(2026, 2, 2)  # 02.02.2026

    print(f"\n📅 Test-Datum: {test_date.strftime('%d.%m.%Y')}")

    # Variante 1: Wie in app.py verwendet
    print("\n" + "-" * 70)
    print("VARIANTE 1: datetime.combine mit min/max time")
    print("-" * 70)

    start_of_day = datetime.combine(test_date, datetime.min.time())
    end_of_day = datetime.combine(test_date, datetime.max.time())

    print(f"Start: {start_of_day}")
    print(f"End:   {end_of_day}")
    print(f"Start ISO: {start_of_day.isoformat()}")
    print(f"End ISO:   {end_of_day.isoformat()}")
    print(f"Start ISO+Z: {start_of_day.isoformat()}Z")
    print(f"End ISO+Z:   {end_of_day.isoformat()}Z")

    # Rufe Termine ab
    events = outlook_tool.get_events_for_date_range(start_of_day, end_of_day)

    print(f"\nErgebnis: {len(events)} Termin(e) gefunden")

    if events:
        print("\nTermine:")
        for i, event in enumerate(events, 1):
            print(f"\n{i}. {event.get('title', 'Ohne Titel')}")
            print(f"   Start: {event.get('start')}")
            print(f"   Ende:  {event.get('end')}")
            print(f"   Ort:   {event.get('location', 'Kein Ort')}")
    else:
        print("\n⚠️ Keine Termine gefunden!")

        # Teste erweiterten Zeitraum
        print("\n" + "-" * 70)
        print("ERWEITERTER TEST: Komplette Woche")
        print("-" * 70)

        # Teste die ganze Woche um das Datum herum
        from datetime import timedelta

        week_start = test_date - timedelta(days=3)
        week_end = test_date + timedelta(days=3)

        start_dt = datetime.combine(week_start, datetime.min.time())
        end_dt = datetime.combine(week_end, datetime.max.time())

        print(f"Zeitraum: {week_start.strftime('%d.%m.%Y')} bis {week_end.strftime('%d.%m.%Y')}")

        week_events = outlook_tool.get_events_for_date_range(start_dt, end_dt)

        print(f"\nErgebnis: {len(week_events)} Termin(e) in dieser Woche gefunden")

        if week_events:
            print("\nTermine in dieser Woche:")
            for i, event in enumerate(week_events, 1):
                print(f"\n{i}. {event.get('title', 'Ohne Titel')}")
                print(f"   Start: {event.get('start')}")
                print(f"   Ende:  {event.get('end')}")

    # Teste ob der Kalender generell Termine für Februar 2026 hat
    print("\n" + "-" * 70)
    print("GENERELLER TEST: Kompletter Februar 2026")
    print("-" * 70)

    feb_start = datetime(2026, 2, 1, 0, 0, 0)
    feb_end = datetime(2026, 2, 28, 23, 59, 59)

    print(f"Zeitraum: {feb_start.strftime('%d.%m.%Y')} bis {feb_end.strftime('%d.%m.%Y')}")

    feb_events = outlook_tool.get_events_for_date_range(feb_start, feb_end)

    print(f"\nErgebnis: {len(feb_events)} Termin(e) im Februar 2026 gefunden")

    if feb_events:
        print("\nTermine im Februar 2026:")
        # Gruppiere nach Datum
        from collections import defaultdict
        by_date = defaultdict(list)

        for event in feb_events:
            start_str = event.get('start', '')
            if start_str:
                try:
                    # Parse das Start-Datum
                    if 'T' in start_str:
                        event_date = start_str.split('T')[0]
                    else:
                        event_date = start_str[:10]
                    by_date[event_date].append(event)
                except:
                    by_date['Unbekannt'].append(event)

        for event_date, day_events in sorted(by_date.items()):
            try:
                # Formatiere Datum schön
                dt = datetime.fromisoformat(event_date)
                date_str = dt.strftime('%d.%m.%Y')
            except:
                date_str = event_date

            print(f"\n📅 {date_str}: {len(day_events)} Termin(e)")
            for event in day_events:
                print(f"   - {event.get('title', 'Ohne Titel')}")

    print("\n" + "=" * 70)
    print("DEBUG ABGESCHLOSSEN")
    print("=" * 70)

if __name__ == "__main__":
    main()
