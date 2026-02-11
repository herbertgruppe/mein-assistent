"""
Beispiel für die programmatische Verwendung des Multi-Agenten-Systems
"""

from main import AgentOrchestrator


def example_research():
    """Beispiel: Nur Research Agent verwenden"""
    print("\n" + "=" * 70)
    print("BEISPIEL 1: Research Agent")
    print("=" * 70)

    orchestrator = AgentOrchestrator()
    results = orchestrator.process_request(
        "Was ist maschinelles Lernen?",
        workflow="research_only"
    )

    print(f"\nAgent: {results['agents_used'][0]}")
    print(f"Ergebnis: {results['research']['findings'][:200]}...")


def example_task():
    """Beispiel: Nur Task Agent verwenden"""
    print("\n" + "=" * 70)
    print("BEISPIEL 2: Task Agent")
    print("=" * 70)

    orchestrator = AgentOrchestrator()
    results = orchestrator.process_request(
        "Schreibe ein Python-Programm, das die Fibonacci-Folge berechnet",
        workflow="task_only"
    )

    print(f"\nAgent: {results['agents_used'][0]}")
    print(f"Ergebnis:\n{results['task']['output']}")


def example_combined():
    """Beispiel: Beide Agenten kombinieren"""
    print("\n" + "=" * 70)
    print("BEISPIEL 3: Research + Task Agent (kombiniert)")
    print("=" * 70)

    orchestrator = AgentOrchestrator()
    results = orchestrator.process_request(
        "Recherchiere Best Practices für Python und erstelle eine Liste",
        workflow="research_then_task"
    )

    print(f"\nVerwendete Agenten: {', '.join(results['agents_used'])}")
    print(f"\nResearch:\n{results['research']['findings'][:200]}...")
    print(f"\nTask:\n{results['task']['output'][:200]}...")


def example_auto_detection():
    """Beispiel: Automatische Workflow-Erkennung"""
    print("\n" + "=" * 70)
    print("BEISPIEL 4: Automatische Workflow-Erkennung")
    print("=" * 70)

    orchestrator = AgentOrchestrator()

    # Test verschiedene Eingaben
    test_inputs = [
        "Erkläre mir Python",  # Research
        "Schreibe einen Haiku über KI",  # Task
        "Finde Infos über Docker und erstelle ein Tutorial"  # Kombiniert
    ]

    for user_input in test_inputs:
        detected_workflow = orchestrator.detect_agent_type(user_input)
        print(f"\nEingabe: {user_input}")
        print(f"Erkannter Workflow: {detected_workflow}")


def main():
    """Führt alle Beispiele aus"""
    print("\n" + "=" * 70)
    print("Multi-Agenten-System - Programmier-Beispiele")
    print("=" * 70)

    # Wähle ein Beispiel aus
    print("\nVerfügbare Beispiele:")
    print("1. Research Agent")
    print("2. Task Agent")
    print("3. Kombinierter Workflow")
    print("4. Automatische Erkennung")
    print("5. Alle Beispiele ausführen")

    try:
        choice = input("\nWähle ein Beispiel (1-5): ").strip()

        if choice == "1":
            example_research()
        elif choice == "2":
            example_task()
        elif choice == "3":
            example_combined()
        elif choice == "4":
            example_auto_detection()
        elif choice == "5":
            example_auto_detection()
            # example_research()  # Auskommentiert, da API-Calls kosten
            # example_task()
            # example_combined()
            print("\n✓ Alle Beispiele erfolgreich ausgeführt!")
        else:
            print("Ungültige Auswahl!")

    except Exception as e:
        print(f"\n❌ Fehler: {e}")
        print("\nStelle sicher, dass:")
        print("- Die .env Datei korrekt konfiguriert ist")
        print("- Ein gültiger API-Key eingetragen ist")
        print("- Die Dependencies installiert sind (pip install -r requirements.txt)")


if __name__ == "__main__":
    main()
