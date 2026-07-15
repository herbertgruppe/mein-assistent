"""
Test-Skript für das DocumentTool
"""

from tools import DocumentTool


def test_document_tool():
    """Testet die Funktionen des DocumentTools"""
    print("=" * 70)
    print("🧪 DocumentTool Test")
    print("=" * 70)

    # Tool initialisieren
    doc_tool = DocumentTool()

    # 1. Dokumente scannen
    print("\n1. Dokumente scannen...")
    documents = doc_tool.scan_documents()
    print(f"   Gefunden: {len(documents)} Dokument(e)")

    for doc in documents:
        size_kb = doc['size'] / 1024
        print(f"   • {doc['name']} ({doc['type']}, {size_kb:.1f} KB)")

    # 2. Text aus einem Dokument extrahieren
    if documents:
        print("\n2. Text aus erstem Dokument extrahieren...")
        first_doc = documents[0]
        print(f"   Datei: {first_doc['name']}")

        text = doc_tool.extract_text(first_doc['path'])

        if text.startswith("❌"):
            print(f"   Fehler: {text}")
        else:
            print(f"   Extrahiert: {len(text)} Zeichen")
            print(f"   Erste 200 Zeichen:")
            print(f"   {text[:200]}...")

    # 3. Suchfunktion testen
    print("\n3. Suchfunktion testen...")
    results = doc_tool.search_in_documents("test")
    print(f"   Ergebnisse für 'test': {len(results)}")

    # 4. Namenssuche testen (für KHS-Liste)
    print("\n4. Namenssuche testen...")
    print("   Suche nach: 'Dr. Sven Herbert'")
    name_results = doc_tool.search_in_documents("Dr. Sven Herbert")
    print(f"   Ergebnisse: {len(name_results)}")

    if name_results:
        for result in name_results:
            print(f"   ✓ Gefunden in: {result['document']}")
            print(f"     Match-Typ: {result['match_type']}")
            if result.get('match_count'):
                print(f"     Anzahl Treffer: {result['match_count']}")
            print(f"     Snippet: {result['snippet'][:150]}...")
    else:
        print("   ✗ Keine Treffer gefunden")

    # 5. Test mit invoke-Methode (wie ResearchAgent es nutzt)
    print("\n5. Test mit invoke-Methode...")
    invoke_result = doc_tool.invoke("Dr. Sven Herbert")
    print("   Ergebnis:")
    print("   " + invoke_result.replace("\n", "\n   "))

    print("\n" + "=" * 70)
    print("✅ Test abgeschlossen")


if __name__ == "__main__":
    test_document_tool()
