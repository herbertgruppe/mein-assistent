"""
Test-Skript für die Verbesserungen:
1. ResearchAgent mit lokalen Dokumenten
2. Fuzzy Matching für Dateinamen
3. Orchestrator-Kontext-Kennzeichnung
4. Logging
"""

from tools import DocumentTool


def test_fuzzy_matching():
    """Test des Fuzzy Matching für Dateinamen"""
    print("=" * 70)
    print("🧪 Test 1: Fuzzy Matching für Dateinamen")
    print("=" * 70)

    doc_tool = DocumentTool()

    # Test verschiedene Suchanfragen
    test_queries = [
        "KHS",  # Teilstring
        "Gesellschafterliste",  # Teilstring
        "khs gesellschafterliste",  # Mehrere Wörter
        "test_data",  # Exakt
        "test data",  # Mit Leerzeichen statt Underscore
        "Mitarbeiter",  # Sollte nichts finden (nur im Inhalt)
    ]

    for query in test_queries:
        print(f"\nSuche nach: '{query}'")
        results = doc_tool.search_in_documents(query)
        print(f"Gefunden: {len(results)} Ergebnis(se)")
        for result in results:
            print(f"  • {result['document']} (Match-Typ: {result['match_type']})")


def test_logging():
    """Test des Logging bei Text-Extraktion"""
    print("\n" + "=" * 70)
    print("🧪 Test 2: Logging bei Text-Extraktion")
    print("=" * 70)

    doc_tool = DocumentTool()
    documents = doc_tool.scan_documents()

    if documents:
        print(f"\nExtrahiere Text aus erstem Dokument:")
        text = doc_tool.extract_text(documents[0]['path'])
        print(f"Länge des extrahierten Textes: {len(text)} Zeichen")


def test_document_tool_invoke():
    """Test der LangChain-kompatiblen invoke Methode"""
    print("\n" + "=" * 70)
    print("🧪 Test 3: DocumentTool invoke() Methode")
    print("=" * 70)

    doc_tool = DocumentTool()

    # Simuliere einen Tool-Aufruf wie ihn der Agent machen würde
    print("\nSimuliere Tool-Aufruf mit Query: 'KHS'")
    result = doc_tool.invoke("KHS")
    print("\nTool-Ergebnis:")
    print(result[:500] + "..." if len(result) > 500 else result)


if __name__ == "__main__":
    test_fuzzy_matching()
    test_logging()
    test_document_tool_invoke()

    print("\n" + "=" * 70)
    print("✅ Alle Tests abgeschlossen")
    print("=" * 70)
