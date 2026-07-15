"""
Test-Skript für die verbesserte Kontakt-Suchfunktion
"""

from dotenv import load_dotenv
load_dotenv()

from tools.outlook_graph_tool import OutlookGraphTool

print("=" * 70)
print("Test: Verbesserte Kontakt-Suche mit Ranking")
print("=" * 70)
print()

# Initialisiere Tool
print("1. Initialisiere OutlookGraphTool...")
tool = OutlookGraphTool()
print()

# Prüfe Status
print("2. Prüfe Status...")
print(f"   - Konfiguriert: {tool.is_configured}")
print(f"   - Authentifiziert: {tool.access_token is not None}")
print()

if not tool.access_token:
    print("❌ Nicht authentifiziert!")
    print("Bitte führen Sie zuerst 'python authenticate_outlook.py' aus")
    exit(1)

# Test mit mehreren Suchbegriffen
test_queries = [
    "Frank Herbert",    # Vollständiger Name
    "Herbert",          # Nachname
    "Frank",            # Vorname
]

for query in test_queries:
    print("=" * 70)
    print(f"SUCHE: '{query}'")
    print("=" * 70)
    print()

    contacts = tool.search_contacts(query, max_results=5)

    if not contacts:
        print("❌ Keine Kontakte gefunden\n")
        continue

    print(f"✅ {len(contacts)} Kontakt(e) gefunden (nach Relevanz sortiert):\n")

    for idx, contact in enumerate(contacts, 1):
        print(f"{idx}. {contact.get('name')}")

        # E-Mail
        if contact.get('primary_email'):
            print(f"   📧 {contact.get('primary_email')}")
        elif contact.get('emails'):
            print(f"   📧 {', '.join(contact.get('emails'))}")
        else:
            print(f"   📧 Keine E-Mail verfügbar")

        # Firma
        if contact.get('company'):
            print(f"   🏢 {contact.get('company')}", end="")
            if contact.get('job_title'):
                print(f" - {contact.get('job_title')}")
            else:
                print()

        # Telefon
        if contact.get('phones'):
            print(f"   📞 {', '.join(contact.get('phones'))}")

        print()

print("=" * 70)
print("Test abgeschlossen!")
print("=" * 70)
