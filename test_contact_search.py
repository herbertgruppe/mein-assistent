"""
Test-Skript für die Kontakt-Suchfunktion
"""

from dotenv import load_dotenv
load_dotenv()

from tools.outlook_graph_tool import OutlookGraphTool

print("=" * 70)
print("Test: Kontakt-Suche")
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

# Teste Kontakt-Suche
print("3. Teste Kontakt-Suche...")
print()

# Suche nach "Herbert" (sollte Ihren Kontakt finden wenn vorhanden)
search_query = input("Suchbegriff (Name oder E-Mail): ")
print(f"\nSuche nach: {search_query}")
print()

contacts = tool.search_contacts(search_query, max_results=5)

print()
print("=" * 70)
print(f"Ergebnis: {len(contacts)} Kontakt(e) gefunden")
print("=" * 70)
print()

for idx, contact in enumerate(contacts, 1):
    print(f"{idx}. {contact.get('name')}")
    if contact.get('primary_email'):
        print(f"   E-Mail: {contact.get('primary_email')}")
    if contact.get('company'):
        print(f"   Firma: {contact.get('company')}")
        if contact.get('job_title'):
            print(f"   Position: {contact.get('job_title')}")
    if contact.get('phones'):
        print(f"   Telefon: {', '.join(contact.get('phones'))}")
    print()
