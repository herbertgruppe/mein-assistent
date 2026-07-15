"""
Test für LLM-basierte Email-Analyse
"""

import os
from dotenv import load_dotenv

load_dotenv()

from utils.email_manager import EmailManager
from tools.outlook_graph_tool import OutlookGraphTool
from agents.asana_agent import AsanaAgent

# Initialisiere Manager
print("🧪 Initialisiere EmailManager...")
outlook = OutlookGraphTool()
asana = AsanaAgent()
manager = EmailManager(outlook, asana)

# Prüfe ob LLM verfügbar
if not manager.llm:
    print("⚠️ LLM nicht verfügbar - überspringe LLM-Test")
    exit(0)

print(f"✓ LLM verfügbar: {manager.llm_provider} / {manager.llm_model}")

# Test-Email mit realistischem Inhalt
print("\n🧪 Teste LLM-Analyse mit realistischer E-Mail...")

test_email = {
    'id': 'test123',
    'subject': 'DRINGEND: Vertragsablauf Project Alpha am 15.02.2026',
    'from': {
        'emailAddress': {
            'name': 'Max Mustermann',
            'address': 'max.mustermann@wichtigpartner.de'
        }
    },
    'receivedDateTime': '2026-01-30T14:30:00Z',
    'body': {
        'content': """
Hallo Herr Herbert,

ich möchte Sie daran erinnern, dass unser Vertrag für Project Alpha am 15. Februar 2026 ausläuft.

Um eine reibungslose Fortsetzung unserer Zusammenarbeit zu gewährleisten, benötigen wir bis zum 05. Februar folgende Unterlagen:

1. Unterschriebene Vertragsverlängerung
2. Aktualisierte Preisliste für 2026
3. Bestätigung der neuen Projektleitung

Bitte lassen Sie mir bis spätestens nächsten Mittwoch eine Rückmeldung zukommen, damit wir die notwendigen Schritte einleiten können.

Mit freundlichen Grüßen
Max Mustermann

Wichtig Partner GmbH
Projektleitung
Tel: +49 123 456789
        """
    },
    'bodyPreview': 'ich möchte Sie daran erinnern, dass unser Vertrag für Project Alpha am 15. Februar 2026 ausläuft...',
    'importance': 'high',
    'hasAttachments': False,
    'webLink': 'https://outlook.office.com/mail/test'
}

try:
    analysis = manager.analyze_email_with_llm(test_email)

    print("✓ LLM-Analyse erfolgreich:")
    print(f"\n  📝 Summary:")
    print(f"     {analysis['summary']}")
    print(f"\n  🎯 Priority: {analysis['priority']}/5")
    print(f"  📂 Category: {analysis['category']}")
    print(f"  😊 Sentiment: {analysis['sentiment']}")

    if analysis.get('deadline'):
        print(f"  📅 Deadline: {analysis['deadline']}")

    if analysis.get('action_items'):
        print(f"\n  ✅ Action Items:")
        for item in analysis['action_items']:
            print(f"     - {item}")

    # Validiere Ergebnis
    assert 'summary' in analysis, "Missing summary"
    assert 'priority' in analysis, "Missing priority"
    assert 'category' in analysis, "Missing category"
    assert isinstance(analysis['priority'], int), "Priority muss int sein"
    assert 1 <= analysis['priority'] <= 5, "Priority muss zwischen 1-5 sein"

    print("\n✅ Alle LLM-Tests bestanden!")

except Exception as e:
    print(f"\n❌ Fehler bei LLM-Analyse: {e}")
    import traceback
    traceback.print_exc()
    exit(1)
