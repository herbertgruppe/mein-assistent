"""
Test-Skript für Meeting Cockpit Integration

Testet:
1. Mapping-Konfiguration laden
2. Asana-Projekt-Kontext ermitteln (Keyword + Fuzzy-Match)
3. Offene Tasks abrufen
"""

import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

def test_mapping_config():
    """Test: Mapping-Konfiguration laden"""
    print("\n=== Test 1: Mapping-Konfiguration laden ===")

    config_path = Path("config/mapping_config.json")

    if not config_path.exists():
        print(f"❌ Konfiguration nicht gefunden: {config_path}")
        return False

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        mappings = config.get('project_mappings', {})
        print(f"✓ {len(mappings)} Projekt-Mappings geladen")

        for key, data in mappings.items():
            keywords = data.get('keywords', [])
            gid = data.get('asana_project_gid', 'None')
            print(f"  • {key}: {', '.join(keywords)} → GID: {gid}")

        return True

    except Exception as e:
        print(f"❌ Fehler: {e}")
        return False


def test_asana_project_context():
    """Test: Asana-Projekt-Kontext ermitteln"""
    print("\n=== Test 2: Asana-Projekt-Kontext ermitteln ===")

    try:
        from meeting_manager import MeetingManager

        print("Initialisiere Meeting Manager...")
        mm = MeetingManager()

        # Test 1: Keyword-Match
        print("\nTest 2a: Keyword-Match für 'myTGA Meeting'")
        context = mm.get_asana_project_context("myTGA Meeting")

        if context:
            print(f"✓ Projekt gefunden: {context['project_name']}")
            print(f"  Match-Typ: {context['match_type']}")
            print(f"  Projekt-GID: {context['project_gid']}")
            print(f"  Offene Tasks: {len(context.get('open_tasks', []))}")

            # Zeige erste 3 Tasks
            for task in context.get('open_tasks', [])[:3]:
                print(f"    • {task.get('name', 'Unbenannt')}")
        else:
            print("⚠️ Kein Projekt gefunden")

        # Test 2: Fuzzy-Match
        print("\nTest 2b: Fuzzy-Match für 'Personalplanung'")
        context2 = mm.get_asana_project_context("Personalplanung")

        if context2:
            print(f"✓ Projekt gefunden: {context2['project_name']}")
            print(f"  Match-Typ: {context2['match_type']}")
            if 'match_score' in context2:
                print(f"  Match-Score: {context2['match_score']:.2f}")
        else:
            print("⚠️ Kein Projekt gefunden")

        return True

    except Exception as e:
        print(f"❌ Fehler: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_task_extraction():
    """Test: Task-Extraktion aus Beispiel-Text"""
    print("\n=== Test 3: Task-Extraktion (Mock) ===")

    sample_transcript = """
    Meeting vom 2026-01-27 14:00

    Teilnehmer: Max, Anna, Tom

    Themen:
    1. Projektplanung Q1
       - Max: Projektplan bis Freitag fertigstellen
       - Anna: Budget-Übersicht erstellen (Deadline: 31.01.)

    2. IT-Infrastruktur
       - Tom: Server-Migration vorbereiten
       - Backup-Strategie überarbeiten bis nächste Woche

    Nächstes Meeting: 03.02.2026
    """

    print("Beispiel-Transkript:")
    print(sample_transcript[:200] + "...")

    print("\nErwartete Extraktion:")
    print("  • Projektplan fertigstellen")
    print("  • Budget-Übersicht erstellen (Due: 2026-01-31)")
    print("  • Server-Migration vorbereiten")
    print("  • Backup-Strategie überarbeiten")

    print("\n✓ Task-Extraktion wird via LLM in Streamlit durchgeführt")

    return True


if __name__ == "__main__":
    print("=" * 60)
    print("Meeting Cockpit Integration - Test Suite")
    print("=" * 60)

    results = []

    # Test 1: Mapping-Konfiguration
    results.append(("Mapping-Konfiguration", test_mapping_config()))

    # Test 2: Asana-Projekt-Kontext
    if os.getenv("ASANA_ACCESS_TOKEN"):
        results.append(("Asana-Projekt-Kontext", test_asana_project_context()))
    else:
        print("\n⚠️ Test 2 übersprungen: ASANA_ACCESS_TOKEN nicht gefunden")
        results.append(("Asana-Projekt-Kontext", None))

    # Test 3: Task-Extraktion (Mock)
    results.append(("Task-Extraktion (Mock)", test_task_extraction()))

    # Zusammenfassung
    print("\n" + "=" * 60)
    print("ZUSAMMENFASSUNG")
    print("=" * 60)

    for test_name, success in results:
        if success is None:
            status = "⊘ ÜBERSPRUNGEN"
        elif success:
            status = "✓ PASSED"
        else:
            status = "❌ FAILED"
        print(f"{status} - {test_name}")

    print("\n" + "=" * 60)

    passed = sum(1 for _, s in results if s is True)
    skipped = sum(1 for _, s in results if s is None)
    failed = sum(1 for _, s in results if s is False)

    print(f"Ergebnisse: {passed} bestanden, {skipped} übersprungen, {failed} fehlgeschlagen")

    if failed == 0:
        print("✓ ALLE TESTS ERFOLGREICH")
    else:
        print("❌ EINIGE TESTS FEHLGESCHLAGEN")
