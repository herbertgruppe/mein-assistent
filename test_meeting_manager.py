"""
Test-Skript für den Meeting Manager

Testet die verschiedenen Komponenten des Meeting Managers:
1. Ordner-Setup
2. Microsoft Graph API Verbindung
3. LLM-Integration
4. Dateiverarbeitung
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

# .env laden
load_dotenv()

# Farben für Terminal-Ausgabe
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'


def print_success(msg):
    print(f"{Colors.GREEN}✓ {msg}{Colors.RESET}")


def print_error(msg):
    print(f"{Colors.RED}✗ {msg}{Colors.RESET}")


def print_info(msg):
    print(f"{Colors.BLUE}ℹ {msg}{Colors.RESET}")


def print_warning(msg):
    print(f"{Colors.YELLOW}⚠ {msg}{Colors.RESET}")


def print_header(msg):
    print(f"\n{Colors.BLUE}{'='*60}")
    print(f"{msg}")
    print(f"{'='*60}{Colors.RESET}\n")


def test_environment():
    """Test 1: Umgebungsvariablen prüfen"""
    print_header("Test 1: Umgebungsvariablen")

    checks = [
        ("LLM_PROVIDER", os.getenv("LLM_PROVIDER")),
        ("ANTHROPIC_API_KEY", os.getenv("ANTHROPIC_API_KEY")),
        ("MICROSOFT_CLIENT_ID", os.getenv("MICROSOFT_CLIENT_ID")),
        ("MICROSOFT_TENANT_ID", os.getenv("MICROSOFT_TENANT_ID")),
    ]

    all_ok = True
    for var_name, var_value in checks:
        if var_value:
            # Nur erste 8 Zeichen von API Keys anzeigen
            if "KEY" in var_name or "SECRET" in var_name:
                display_value = f"{var_value[:8]}..." if len(var_value) > 8 else "***"
            else:
                display_value = var_value
            print_success(f"{var_name} = {display_value}")
        else:
            print_error(f"{var_name} nicht gesetzt")
            all_ok = False

    return all_ok


def test_imports():
    """Test 2: Python-Pakete importieren"""
    print_header("Test 2: Python-Pakete")

    packages = [
        ("watchdog", "watchdog.observers"),
        ("msal", "msal"),
        ("langchain", "langchain_core.messages"),
        ("langchain_anthropic", "langchain_anthropic"),
        ("requests", "requests"),
    ]

    all_ok = True
    for display_name, import_name in packages:
        try:
            __import__(import_name)
            print_success(f"{display_name} verfügbar")
        except ImportError:
            print_error(f"{display_name} fehlt - pip install {display_name}")
            all_ok = False

    return all_ok


def test_directories():
    """Test 3: Ordnerstruktur erstellen und prüfen"""
    print_header("Test 3: Ordnerstruktur")

    dirs = [
        Path("transcripts/incoming"),
        Path("transcripts/processed"),
    ]

    for dir_path in dirs:
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
            print_success(f"Ordner erstellt/gefunden: {dir_path}")
        except Exception as e:
            print_error(f"Fehler bei {dir_path}: {e}")
            return False

    return True


def test_outlook_tool():
    """Test 4: Microsoft Graph API Verbindung"""
    print_header("Test 4: Microsoft Graph API")

    try:
        from tools.outlook_graph_tool import OutlookGraphTool

        print_info("Initialisiere OutlookGraphTool...")
        outlook_tool = OutlookGraphTool()

        if not outlook_tool.is_configured:
            print_error("Nicht konfiguriert - MICROSOFT_CLIENT_ID und MICROSOFT_TENANT_ID fehlen")
            return False

        print_success("OutlookGraphTool initialisiert")

        # Prüfe Token
        if outlook_tool.access_token:
            print_success("Access Token vorhanden")

            # Teste API-Aufruf
            print_info("Teste Kalender-Zugriff (heutige Events)...")
            try:
                events = outlook_tool.get_todays_events()
                print_success(f"Kalender-Zugriff erfolgreich - {len(events)} Event(s) heute")

                if events:
                    print_info("Beispiel-Event:")
                    first_event = events[0]
                    print(f"  Titel: {first_event.get('title', 'Kein Titel')}")
                    print(f"  Start: {first_event.get('start', 'N/A')}")
                    print(f"  Ende: {first_event.get('end', 'N/A')}")

                return True
            except Exception as e:
                print_error(f"API-Aufruf fehlgeschlagen: {e}")
                print_warning("Möglicherweise ist das Token abgelaufen")
                print_info("Führe aus: python test_outlook_auth.py")
                return False
        else:
            print_error("Kein Access Token vorhanden")
            print_info("Führe aus: python test_outlook_auth.py")
            return False

    except Exception as e:
        print_error(f"Fehler beim Initialisieren: {e}")
        return False


def test_llm():
    """Test 5: LLM-Verbindung"""
    print_header("Test 5: LLM-Integration")

    try:
        llm_provider = os.getenv("LLM_PROVIDER", "anthropic")
        print_info(f"LLM Provider: {llm_provider}")

        if llm_provider == "anthropic":
            from langchain_anthropic import ChatAnthropic

            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                print_error("ANTHROPIC_API_KEY nicht gesetzt")
                return False

            model = os.getenv("RESEARCH_MODEL", "claude-sonnet-4-5")
            print_info(f"Teste Verbindung zu {model}...")

            llm = ChatAnthropic(
                api_key=api_key,
                model=model,
                temperature=0.7,
                max_tokens=100
            )

            from langchain_core.messages import HumanMessage
            response = llm.invoke([HumanMessage(content="Antworte mit: OK")])

            print_success(f"LLM-Verbindung erfolgreich")
            print_info(f"Antwort: {response.content[:50]}")
            return True

        elif llm_provider == "openai":
            from langchain_openai import ChatOpenAI

            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                print_error("OPENAI_API_KEY nicht gesetzt")
                return False

            model = os.getenv("RESEARCH_MODEL", "gpt-4")
            print_info(f"Teste Verbindung zu {model}...")

            llm = ChatOpenAI(
                api_key=api_key,
                model_name=model,
                temperature=0.7,
                max_tokens=100
            )

            from langchain_core.messages import HumanMessage
            response = llm.invoke([HumanMessage(content="Antworte mit: OK")])

            print_success(f"LLM-Verbindung erfolgreich")
            print_info(f"Antwort: {response.content[:50]}")
            return True

        else:
            print_error(f"Unbekannter LLM Provider: {llm_provider}")
            return False

    except Exception as e:
        print_error(f"LLM-Test fehlgeschlagen: {e}")
        return False


def test_meeting_manager():
    """Test 6: Meeting Manager initialisieren"""
    print_header("Test 6: Meeting Manager")

    try:
        from meeting_manager import MeetingManager

        print_info("Initialisiere Meeting Manager...")
        manager = MeetingManager()

        print_success("Meeting Manager erfolgreich initialisiert")
        print_info(f"  Incoming: {manager.incoming_dir}")
        print_info(f"  Processed: {manager.processed_dir}")
        print_info(f"  LLM: {manager.llm_provider} / {manager.llm_model}")

        return True

    except Exception as e:
        print_error(f"Fehler beim Initialisieren: {e}")
        import traceback
        print(traceback.format_exc())
        return False


def create_test_transcript():
    """Test 7: Test-Transkript erstellen und verarbeiten"""
    print_header("Test 7: Test-Transkript verarbeiten")

    try:
        from meeting_manager import MeetingManager

        # Test-Transkript erstellen
        test_file = Path("transcripts/incoming/test_meeting.txt")

        test_content = f"""Meeting Transcript - Test Meeting

Erstellt: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Teilnehmer: Max Mustermann, Anna Schmidt, Thomas Müller

[10:00] Max: Willkommen zum Test Meeting für den Meeting Manager.
[10:02] Anna: Vielen Dank! Ich freue mich auf die Diskussion über die neue Funktion.
[10:05] Thomas: Ich habe die Anforderungen vorbereitet. Sollen wir direkt einsteigen?
[10:07] Max: Ja, perfekt. Thomas, kannst du uns einen Überblick geben?
[10:10] Thomas: Natürlich. Das Hauptziel ist die automatische Verarbeitung von Transkripten...

[Weiterer Meeting-Inhalt...]

[10:55] Anna: Danke für die produktive Diskussion!
[10:57] Max: Vielen Dank euch beiden. Bis zum nächsten Meeting!

Meeting Ende: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

        print_info(f"Erstelle Test-Transkript: {test_file.name}")
        with open(test_file, 'w', encoding='utf-8') as f:
            f.write(test_content)

        print_success("Test-Transkript erstellt")

        # Manager initialisieren
        manager = MeetingManager()

        # Datei verarbeiten
        print_info("Verarbeite Transkript...")
        success = manager.process_transcript(test_file)

        if success:
            print_success("Transkript erfolgreich verarbeitet!")

            # Prüfe ob Datei verschoben wurde
            processed_files = list(manager.processed_dir.glob("*.txt"))
            if processed_files:
                latest_file = max(processed_files, key=lambda p: p.stat().st_mtime)
                print_info(f"Verarbeitete Datei: {latest_file.name}")

                # Zeige ersten Teil der Datei
                print_info("Inhalt (erste 200 Zeichen):")
                with open(latest_file, 'r', encoding='utf-8') as f:
                    content = f.read(200)
                    print(f"  {content}...")

            return True
        else:
            print_error("Transkript-Verarbeitung fehlgeschlagen")
            return False

    except Exception as e:
        print_error(f"Fehler beim Test: {e}")
        import traceback
        print(traceback.format_exc())
        return False


def main():
    """Hauptfunktion - führt alle Tests durch"""
    print(f"{Colors.BLUE}")
    print("╔════════════════════════════════════════════════════════════╗")
    print("║          Meeting Manager - Test Suite                     ║")
    print("╚════════════════════════════════════════════════════════════╝")
    print(Colors.RESET)

    tests = [
        ("Umgebungsvariablen", test_environment),
        ("Python-Pakete", test_imports),
        ("Ordnerstruktur", test_directories),
        ("Microsoft Graph API", test_outlook_tool),
        ("LLM-Integration", test_llm),
        ("Meeting Manager", test_meeting_manager),
        ("Transkript-Verarbeitung", create_test_transcript),
    ]

    results = []

    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print_error(f"Unerwarteter Fehler in {test_name}: {e}")
            results.append((test_name, False))

    # Zusammenfassung
    print_header("Zusammenfassung")

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        if result:
            print_success(f"{test_name}")
        else:
            print_error(f"{test_name}")

    print(f"\n{Colors.BLUE}{'='*60}{Colors.RESET}")
    if passed == total:
        print_success(f"Alle Tests bestanden! ({passed}/{total})")
        print_info("\nDer Meeting Manager ist einsatzbereit!")
        print_info("Starte mit: python meeting_manager.py")
    else:
        print_error(f"Einige Tests fehlgeschlagen ({passed}/{total} bestanden)")
        print_warning("\nBitte behebe die Fehler bevor du den Meeting Manager startest.")

    print(f"{Colors.BLUE}{'='*60}{Colors.RESET}\n")

    return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
