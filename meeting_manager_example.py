"""
Meeting Manager - Beispiel-Skripte für verschiedene Anwendungsfälle

Zeigt verschiedene Möglichkeiten, den Meeting Manager zu verwenden.
"""

from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# .env laden
load_dotenv()

# Meeting Manager importieren
from meeting_manager import MeetingManager


def beispiel_1_einzelne_datei():
    """
    Beispiel 1: Einzelne Datei manuell verarbeiten
    """
    print("\n" + "="*60)
    print("Beispiel 1: Einzelne Datei verarbeiten")
    print("="*60 + "\n")

    # Manager erstellen
    manager = MeetingManager()

    # Datei verarbeiten
    file_path = Path("transcripts/incoming/meeting.txt")

    if file_path.exists():
        print(f"Verarbeite: {file_path.name}")
        success = manager.process_transcript(file_path)

        if success:
            print("✓ Erfolgreich verarbeitet!")
        else:
            print("✗ Fehler bei Verarbeitung")
    else:
        print(f"⚠ Datei nicht gefunden: {file_path}")
        print("  Erstelle zuerst eine Test-Datei in transcripts/incoming/")


def beispiel_2_alle_dateien():
    """
    Beispiel 2: Alle vorhandenen Dateien im incoming-Ordner verarbeiten
    """
    print("\n" + "="*60)
    print("Beispiel 2: Alle vorhandenen Dateien verarbeiten")
    print("="*60 + "\n")

    # Manager erstellen
    manager = MeetingManager()

    # Alle Dateien verarbeiten
    print("Verarbeite alle Dateien in transcripts/incoming/...")
    manager.process_existing_files()

    print("\n✓ Batch-Verarbeitung abgeschlossen!")


def beispiel_3_ordnerueberwachung():
    """
    Beispiel 3: Ordnerüberwachung im Dauerbetrieb
    """
    print("\n" + "="*60)
    print("Beispiel 3: Ordnerüberwachung starten")
    print("="*60 + "\n")

    # Manager erstellen
    manager = MeetingManager()

    print("Starte Ordnerüberwachung...")
    print("Lege neue .txt Dateien in transcripts/incoming/ ab")
    print("Drücke Ctrl+C zum Beenden\n")

    try:
        manager.start_watching()
    except KeyboardInterrupt:
        print("\n\n✓ Ordnerüberwachung beendet")


def beispiel_4_custom_ordner():
    """
    Beispiel 4: Eigene Ordner-Pfade verwenden
    """
    print("\n" + "="*60)
    print("Beispiel 4: Custom Ordner-Pfade")
    print("="*60 + "\n")

    # Manager mit Custom-Pfaden erstellen
    manager = MeetingManager(
        incoming_dir="meine_transkripte/neu",
        processed_dir="meine_transkripte/archiv"
    )

    print(f"Incoming: {manager.incoming_dir}")
    print(f"Processed: {manager.processed_dir}")

    # Ordner werden automatisch erstellt
    print("\n✓ Ordner erstellt/geprüft")


def beispiel_5_openai():
    """
    Beispiel 5: OpenAI GPT statt Claude verwenden
    """
    print("\n" + "="*60)
    print("Beispiel 5: OpenAI GPT verwenden")
    print("="*60 + "\n")

    # Manager mit OpenAI erstellen
    try:
        manager = MeetingManager(
            llm_provider="openai",
            llm_model="gpt-4"
        )

        print(f"✓ LLM: {manager.llm_provider} / {manager.llm_model}")

        # Beispiel: Titel generieren
        test_file = Path("transcripts/incoming/test.txt")
        if test_file.exists():
            title = manager.generate_title_from_transcript(test_file)
            print(f"Generierter Titel: {title}")
        else:
            print("⚠ Keine Test-Datei vorhanden")

    except ValueError as e:
        print(f"✗ Fehler: {e}")
        print("  Stelle sicher, dass OPENAI_API_KEY in .env gesetzt ist")


def beispiel_6_meeting_suche():
    """
    Beispiel 6: Meeting zu bestimmter Zeit suchen
    """
    print("\n" + "="*60)
    print("Beispiel 6: Meeting-Suche")
    print("="*60 + "\n")

    # Manager erstellen
    manager = MeetingManager()

    # Aktuelle Zeit
    now = datetime.now()
    print(f"Suche Meeting um: {now.strftime('%Y-%m-%d %H:%M')}")

    # Meeting suchen (±30 Minuten Toleranz)
    meeting = manager.find_meeting_at_time(now, tolerance_minutes=30)

    if meeting:
        print("\n✓ Meeting gefunden:")
        print(f"  Titel: {meeting.get('title', 'Kein Titel')}")
        print(f"  Start: {meeting.get('start', 'N/A')}")
        print(f"  Ende: {meeting.get('end', 'N/A')}")
        print(f"  Ort: {meeting.get('location', 'Kein Ort')}")

        attendees = meeting.get('attendees', [])
        if attendees:
            print(f"  Teilnehmer: {', '.join(attendees)}")
    else:
        print("\n⚠ Kein Meeting gefunden")
        print("  Tipp: Erhöhe die Toleranz (tolerance_minutes)")


def beispiel_7_datei_metadaten():
    """
    Beispiel 7: Datei-Metadaten auslesen
    """
    print("\n" + "="*60)
    print("Beispiel 7: Datei-Metadaten auslesen")
    print("="*60 + "\n")

    # Manager erstellen
    manager = MeetingManager()

    # Test-Datei erstellen
    test_file = Path("transcripts/incoming/metadata_test.txt")
    test_file.parent.mkdir(parents=True, exist_ok=True)

    with open(test_file, 'w') as f:
        f.write(f"Test-Transkript erstellt um {datetime.now()}")

    print(f"Erstelle Test-Datei: {test_file.name}")

    # Metadaten auslesen
    creation_time = manager.get_file_creation_time(test_file)

    print(f"\nErstellungszeit: {creation_time}")
    print(f"Datum: {creation_time.strftime('%Y-%m-%d')}")
    print(f"Uhrzeit: {creation_time.strftime('%H:%M:%S')}")

    # Aufräumen
    test_file.unlink()
    print("\n✓ Test-Datei gelöscht")


def beispiel_8_fehlerbehandlung():
    """
    Beispiel 8: Fehlerbehandlung und Logging
    """
    print("\n" + "="*60)
    print("Beispiel 8: Fehlerbehandlung")
    print("="*60 + "\n")

    import logging

    # Debug-Logging aktivieren
    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger("meeting_manager")
    logger.setLevel(logging.DEBUG)

    print("Debug-Logging aktiviert\n")

    # Manager erstellen
    manager = MeetingManager()

    # Nicht-existierende Datei verarbeiten
    fake_file = Path("transcripts/incoming/nicht_vorhanden.txt")

    print(f"Versuche nicht-existierende Datei zu verarbeiten...")
    try:
        success = manager.process_transcript(fake_file)
        if not success:
            print("✓ Fehler korrekt behandelt")
    except Exception as e:
        print(f"Exception gefangen: {e}")


def menu():
    """
    Interaktives Menü zum Auswählen der Beispiele
    """
    beispiele = [
        ("Einzelne Datei verarbeiten", beispiel_1_einzelne_datei),
        ("Alle vorhandenen Dateien verarbeiten", beispiel_2_alle_dateien),
        ("Ordnerüberwachung starten", beispiel_3_ordnerueberwachung),
        ("Custom Ordner-Pfade", beispiel_4_custom_ordner),
        ("OpenAI GPT verwenden", beispiel_5_openai),
        ("Meeting-Suche", beispiel_6_meeting_suche),
        ("Datei-Metadaten auslesen", beispiel_7_datei_metadaten),
        ("Fehlerbehandlung", beispiel_8_fehlerbehandlung),
    ]

    print("\n" + "="*60)
    print("Meeting Manager - Beispiele")
    print("="*60)

    for i, (name, _) in enumerate(beispiele, 1):
        print(f"{i}. {name}")

    print("\n0. Alle Beispiele nacheinander ausführen")
    print("q. Beenden")

    auswahl = input("\nWähle ein Beispiel (0-8, q): ").strip()

    if auswahl == 'q':
        print("Auf Wiedersehen!")
        return

    if auswahl == '0':
        # Alle Beispiele außer Ordnerüberwachung (da blockierend)
        for name, func in beispiele:
            if func != beispiel_3_ordnerueberwachung:
                try:
                    func()
                    input("\nDrücke Enter für nächstes Beispiel...")
                except Exception as e:
                    print(f"\n✗ Fehler: {e}")
                    input("\nDrücke Enter für nächstes Beispiel...")
    else:
        try:
            idx = int(auswahl) - 1
            if 0 <= idx < len(beispiele):
                beispiele[idx][1]()
            else:
                print("Ungültige Auswahl")
        except ValueError:
            print("Ungültige Eingabe")


def main():
    """
    Hauptfunktion
    """
    print("""
╔════════════════════════════════════════════════════════════╗
║          Meeting Manager - Beispiele                       ║
║                                                            ║
║  Dieses Skript zeigt verschiedene Anwendungsfälle         ║
║  des Meeting Managers.                                     ║
╚════════════════════════════════════════════════════════════╝
    """)

    # Prüfe ob .env vorhanden
    if not Path(".env").exists():
        print("⚠ WARNUNG: .env Datei nicht gefunden")
        print("  Erstelle .env basierend auf .env.example\n")

    while True:
        try:
            menu()
            break
        except KeyboardInterrupt:
            print("\n\nAuf Wiedersehen!")
            break
        except Exception as e:
            print(f"\n✗ Fehler: {e}")
            import traceback
            traceback.print_exc()

            weiter = input("\nWeitermachen? (j/n): ").strip().lower()
            if weiter != 'j':
                break


if __name__ == "__main__":
    main()
