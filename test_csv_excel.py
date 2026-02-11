"""
Test-Skript für CSV und Excel-Unterstützung
"""

import pandas as pd
from tools import DocumentTool


def create_test_excel():
    """Erstellt eine Test-Excel-Datei"""
    # Sheet 1: Mitarbeiter
    df1 = pd.DataFrame({
        'Name': ['Max Mustermann', 'Anna Schmidt', 'Tom Weber'],
        'Abteilung': ['IT', 'Marketing', 'Vertrieb'],
        'Gehalt': [75000, 65000, 70000]
    })

    # Sheet 2: Projekte
    df2 = pd.DataFrame({
        'Projekt': ['Projekt A', 'Projekt B', 'Projekt C'],
        'Budget': [100000, 150000, 80000],
        'Status': ['Aktiv', 'Abgeschlossen', 'Aktiv']
    })

    # Speichere als Excel mit mehreren Sheets
    with pd.ExcelWriter('input_docs/test_data.xlsx', engine='openpyxl') as writer:
        df1.to_excel(writer, sheet_name='Mitarbeiter', index=False)
        df2.to_excel(writer, sheet_name='Projekte', index=False)

    print("✓ Test-Excel-Datei erstellt: input_docs/test_data.xlsx")


def test_csv_excel_support():
    """Testet CSV und Excel-Unterstützung"""
    print("=" * 70)
    print("🧪 CSV & Excel Support Test")
    print("=" * 70)

    # Erstelle Test-Excel
    create_test_excel()

    # Tool initialisieren
    doc_tool = DocumentTool()

    # Alle Dokumente scannen
    print("\n1. Verfügbare Dokumente:")
    documents = doc_tool.scan_documents()
    for doc in documents:
        size_kb = doc['size'] / 1024
        print(f"   • {doc['name']} ({doc['type']}, {size_kb:.1f} KB)")

    # CSV testen
    print("\n2. CSV-Datei lesen:")
    csv_docs = [d for d in documents if d['type'] == '.csv']
    if csv_docs:
        text = doc_tool.extract_text(csv_docs[0]['path'])
        print(text[:500])
    else:
        print("   Keine CSV-Datei gefunden")

    # Excel testen
    print("\n3. Excel-Datei lesen:")
    excel_docs = [d for d in documents if d['type'] in ['.xlsx', '.xls']]
    if excel_docs:
        text = doc_tool.extract_text(excel_docs[0]['path'])
        print(text[:800])
    else:
        print("   Keine Excel-Datei gefunden")

    # Suchfunktion testen
    print("\n4. Suche in allen Dokumenten nach 'IT':")
    results = doc_tool.search_in_documents("IT")
    print(f"   Gefunden in {len(results)} Dokument(en)")
    for result in results:
        print(f"   • {result['document']}")

    print("\n" + "=" * 70)
    print("✅ Test abgeschlossen")


if __name__ == "__main__":
    test_csv_excel_support()
