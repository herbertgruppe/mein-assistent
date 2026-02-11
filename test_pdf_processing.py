"""
Test-Skript für PDF-Transkript-Verarbeitung

Testet die neuen Funktionen:
1. PDF-Text-Extraktion
2. Datum/Zeit-Extraktion aus Transkript-Inhalt
3. Verarbeitung mit MeetingManager
"""

import os
import sys
from pathlib import Path
from datetime import datetime

# .env laden
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("python-dotenv nicht installiert")

from meeting_manager import MeetingManager

def test_pdf_extraction():
    """Test: PDF-Text-Extraktion"""
    print("\n=== Test 1: PDF-Text-Extraktion ===")

    pdf_path = Path("transcripts/incoming/01-26 Einführung einer agilen Arbeitsweise zur verbesserten Zielerreichung und Transparenz-transcript.pdf")

    if not pdf_path.exists():
        print(f"❌ PDF nicht gefunden: {pdf_path}")
        return False

    manager = MeetingManager()

    try:
        text = manager.extract_text_from_pdf(pdf_path)
        print(f"✓ Text extrahiert: {len(text)} Zeichen")
        print(f"\nErste 500 Zeichen:\n{text[:500]}")
        return True
    except Exception as e:
        print(f"❌ Fehler: {e}")
        return False


def test_datetime_extraction():
    """Test: Datum/Zeit-Extraktion aus Inhalt"""
    print("\n=== Test 2: Datum/Zeit-Extraktion ===")

    pdf_path = Path("transcripts/incoming/01-26 Einführung einer agilen Arbeitsweise zur verbesserten Zielerreichung und Transparenz-transcript.pdf")

    if not pdf_path.exists():
        print(f"❌ PDF nicht gefunden: {pdf_path}")
        return False

    manager = MeetingManager()

    try:
        # Text extrahieren
        text = manager.extract_text_from_pdf(pdf_path)

        # Datum/Zeit extrahieren
        dt = manager.extract_datetime_from_content(text)

        if dt:
            print(f"✓ Datum/Zeit gefunden: {dt}")
            print(f"  Format: {dt.strftime('%d.%m.%Y %H:%M:%S')}")
            return True
        else:
            print("❌ Kein Datum/Zeit gefunden")
            return False

    except Exception as e:
        print(f"❌ Fehler: {e}")
        return False


def test_full_processing():
    """Test: Komplette Verarbeitung"""
    print("\n=== Test 3: Komplette Verarbeitung ===")

    pdf_path = Path("transcripts/incoming/01-26 Einführung einer agilen Arbeitsweise zur verbesserten Zielerreichung und Transparenz-transcript.pdf")

    if not pdf_path.exists():
        print(f"❌ PDF nicht gefunden: {pdf_path}")
        return False

    manager = MeetingManager()

    try:
        print(f"\nVerarbeite: {pdf_path.name}")

        # Datum/Zeit ermitteln
        dt, source = manager.get_transcript_datetime(pdf_path)
        print(f"  Datum/Zeit: {dt.strftime('%d.%m.%Y %H:%M:%S')} (Quelle: {source})")

        # Meeting suchen
        meeting = manager.find_meeting_at_time(dt)
        if meeting:
            print(f"  ✓ Meeting gefunden: {meeting.get('title')}")
        else:
            print(f"  ⚠ Kein Meeting gefunden")

        # NICHT vollständig verarbeiten (nur testen)
        print("\n  Hinweis: Für vollständige Verarbeitung 'process_transcript()' aufrufen")

        return True

    except Exception as e:
        print(f"❌ Fehler: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("PDF-Transkript-Verarbeitung - Test Suite")
    print("=" * 60)

    results = []

    # Test 1: PDF-Extraktion
    results.append(("PDF-Extraktion", test_pdf_extraction()))

    # Test 2: Datum/Zeit-Extraktion
    results.append(("Datum/Zeit-Extraktion", test_datetime_extraction()))

    # Test 3: Komplette Verarbeitung
    results.append(("Komplette Verarbeitung", test_full_processing()))

    # Zusammenfassung
    print("\n" + "=" * 60)
    print("ZUSAMMENFASSUNG")
    print("=" * 60)

    for test_name, success in results:
        status = "✓ PASSED" if success else "❌ FAILED"
        print(f"{status} - {test_name}")

    print("\n" + "=" * 60)

    if all(result[1] for result in results):
        print("✓ ALLE TESTS ERFOLGREICH")
    else:
        print("❌ EINIGE TESTS FEHLGESCHLAGEN")
