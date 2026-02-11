"""
Einfacher Test für EmailManager
"""

import os
from dotenv import load_dotenv

# Lade .env
load_dotenv()

# Teste Import
print("🧪 Teste EmailManager Import...")
try:
    from utils.email_manager import EmailManager
    print("✓ EmailManager importiert")
except Exception as e:
    print(f"❌ Import-Fehler: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

# Teste Config-Loading
print("\n🧪 Teste Config-Loading...")
try:
    from tools.outlook_graph_tool import OutlookGraphTool
    from agents.asana_agent import AsanaAgent

    outlook = OutlookGraphTool()
    asana = AsanaAgent()
    manager = EmailManager(outlook, asana)

    print("✓ EmailManager initialisiert")
    print(f"  - Config geladen: {len(manager.config)} Keys")
    print(f"  - Email-Kategorien: {manager.config.get('email_categories', [])}")
    print(f"  - LLM Provider: {manager.llm_provider}")
    print(f"  - LLM Model: {manager.llm_model}")
    print(f"  - LLM verfügbar: {manager.llm is not None}")

except Exception as e:
    print(f"❌ Fehler: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

# Teste Fallback-Analyse
print("\n🧪 Teste Fallback-Analyse...")
try:
    test_email = {
        'id': 'test123',
        'subject': 'Test-Rechnung #12345',
        'from': {
            'emailAddress': {
                'name': 'Max Mustermann',
                'address': 'max@example.com'
            }
        },
        'receivedDateTime': '2026-01-30T10:00:00Z',
        'bodyPreview': 'Bitte finden Sie anbei die Rechnung für den Monat Januar.',
        'importance': 'normal',
        'hasAttachments': True,
        'webLink': 'https://outlook.office.com/mail/test'
    }

    analysis = manager._get_fallback_analysis(test_email)
    print("✓ Fallback-Analyse erfolgreich:")
    print(f"  - Summary: {analysis['summary'][:50]}...")
    print(f"  - Priority: {analysis['priority']}")
    print(f"  - Category: {analysis['category']}")
    print(f"  - Sentiment: {analysis['sentiment']}")

except Exception as e:
    print(f"❌ Fehler: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

# Teste Asana-Target-Suggestion (ohne echte Projekte)
print("\n🧪 Teste Asana-Target-Suggestion...")
try:
    # Erstelle Dummy-Analyse
    dummy_analysis = {
        'summary': 'Test',
        'priority': 3,
        'category': 'Rechnung',
        'action_items': [],
        'deadline': None,
        'sentiment': 'neutral'
    }

    target = manager.suggest_asana_target(test_email, dummy_analysis)
    print(f"✓ Suggestion abgeschlossen: {target}")

except Exception as e:
    print(f"❌ Fehler: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

print("\n✅ Alle Tests bestanden!")
