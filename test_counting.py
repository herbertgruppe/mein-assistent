#!/usr/bin/env python3
"""
Test-Script für die neuen Zähl- und Full-Read-Funktionen
"""

import os
from dotenv import load_dotenv

# Lade Umgebungsvariablen
load_dotenv()

def test_document_tool_full_read():
    """Testet die read_full_document Funktion"""
    print("\n" + "=" * 80)
    print("TEST 1: DocumentTool - read_full_document")
    print("=" * 80)

    from tools.document_tool import DocumentTool

    doc_tool = DocumentTool()

    # Zeige verfügbare Dokumente
    docs = doc_tool.scan_documents()
    print(f"\nVerfügbare Dokumente: {len(docs)}")
    for doc in docs:
        print(f"  • {doc['name']}")

    if not docs:
        print("❌ Keine Dokumente gefunden! Lege Testdokumente in input_docs/ ab.")
        return

    # Teste Full-Read mit dem ersten Dokument
    first_doc = docs[0]['name']
    print(f"\n🔍 Teste Full-Read mit: {first_doc}")

    result = doc_tool.read_full_document(first_doc)

    print(f"\n✓ Ergebnis ({len(result)} Zeichen):")
    print(result[:500] + "..." if len(result) > 500 else result)


def test_research_agent_counting():
    """Testet den ResearchAgent mit Zähl-Anfragen"""
    print("\n" + "=" * 80)
    print("TEST 2: ResearchAgent - Zähl-Anfragen")
    print("=" * 80)

    from agents.research_agent import ResearchAgent

    agent = ResearchAgent()

    # Teste mit einer Zähl-Anfrage
    query = "Wie viele Dokumente sind verfügbar?"
    print(f"\n🔍 Anfrage: {query}")

    result = agent.process(query)

    print(f"\n✓ Antwort:")
    print(result.get('findings', 'Keine Antwort'))


def test_task_agent_list_counting():
    """Testet den TaskAgent mit Listen-Zählen"""
    print("\n" + "=" * 80)
    print("TEST 3: TaskAgent - Listen zählen")
    print("=" * 80)

    from agents.task_agent import TaskAgent

    agent = TaskAgent()

    # Erstelle einen Test-Kontext mit einer Liste
    test_context = {
        "findings": """VOLLSTÄNDIGES DOKUMENT: Testliste.txt

Die folgenden Personen sind in der Liste:

Dr. Max Müller
Anna Schmidt
Prof. Peter Weber
Laura Klein
Thomas Groß
"""
    }

    task = "Zähle die Personen in der Liste und nummeriere sie durch"
    print(f"\n🔍 Aufgabe: {task}")

    result = agent.process(task, context=test_context)

    print(f"\n✓ Ergebnis:")
    print(result.get('output', 'Keine Ausgabe'))


def main():
    """Führt alle Tests aus"""
    print("\n" + "=" * 80)
    print("ZÄHL-FUNKTIONEN TEST-SUITE")
    print("=" * 80)

    # Prüfe ob API-Keys vorhanden sind
    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        print("\n❌ FEHLER: Kein API-Key gefunden!")
        print("Bitte setze ANTHROPIC_API_KEY oder OPENAI_API_KEY in der .env-Datei.")
        return

    # Führe Tests aus
    test_document_tool_full_read()
    test_research_agent_counting()
    test_task_agent_list_counting()

    print("\n" + "=" * 80)
    print("ALLE TESTS ABGESCHLOSSEN")
    print("=" * 80)


if __name__ == "__main__":
    main()
