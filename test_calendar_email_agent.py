"""
Test-Script für CalendarEmailAgent
Debuggt die Email und Kalender-Funktionalität
"""

import os
from dotenv import load_dotenv

# Lade Umgebungsvariablen
load_dotenv()

print("=" * 70)
print("CalendarEmailAgent Debug-Test")
print("=" * 70)
print()

# Test 1: Outlook Tool
print("TEST 1: OutlookGraphTool initialisieren...")
try:
    from tools.outlook_graph_tool import OutlookGraphTool
    outlook_tool = OutlookGraphTool()

    print(f"✓ OutlookGraphTool initialisiert")
    print(f"  - is_configured: {outlook_tool.is_configured}")
    print(f"  - access_token: {'✓ Vorhanden' if outlook_tool.access_token else '✗ Fehlend'}")
    print(f"  - client_id: {outlook_tool.client_id[:20]}..." if outlook_tool.client_id else "  - client_id: Nicht gesetzt")
except Exception as e:
    print(f"✗ Fehler: {e}")
    import traceback
    traceback.print_exc()

print()

# Test 2: Email Tool
print("TEST 2: EmailTool initialisieren...")
try:
    from tools.email_tool import EmailTool
    email_tool = EmailTool()

    print(f"✓ EmailTool initialisiert")
    print(f"  - email_address: {email_tool.email_address or '✗ Nicht konfiguriert'}")
    print(f"  - password: {'✓ Vorhanden' if email_tool.password else '✗ Fehlend'}")
except Exception as e:
    print(f"✗ Fehler: {e}")
    import traceback
    traceback.print_exc()

print()

# Test 3: CalendarEmailAgent initialisieren
print("TEST 3: CalendarEmailAgent initialisieren...")
try:
    from agents.calendar_email_agent import CalendarEmailAgent
    agent = CalendarEmailAgent()

    print(f"✓ CalendarEmailAgent initialisiert")
    print(f"  - LLM: {'✓ Vorhanden' if agent.llm else '✗ Fehlend'}")
    print(f"  - email_tool: {'✓ Vorhanden' if agent.email_tool else '✗ Fehlend'}")
    print(f"  - outlook_tool: {'✓ Vorhanden' if agent.outlook_tool else '✗ Fehlend'}")
except Exception as e:
    print(f"✗ Fehler: {e}")
    import traceback
    traceback.print_exc()

print()

# Test 4: Outlook Token testen
print("TEST 4: Outlook Token testen (API-Call)...")
try:
    from tools.outlook_graph_tool import OutlookGraphTool
    outlook_tool = OutlookGraphTool()

    if outlook_tool.access_token:
        # Teste API-Call
        from datetime import datetime
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        events = outlook_tool.get_events_for_date_range(today, today)

        print(f"✓ API-Call erfolgreich!")
        print(f"  - Termine heute: {len(events) if events else 0}")
    else:
        print(f"✗ Kein Access Token vorhanden")
except Exception as e:
    print(f"✗ Fehler: {e}")
    import traceback
    traceback.print_exc()

print()

# Test 5: create_email_draft direkt testen
print("TEST 5: create_email_draft direkt testen...")
try:
    from tools.outlook_graph_tool import OutlookGraphTool
    outlook_tool = OutlookGraphTool()

    if outlook_tool.access_token:
        result = outlook_tool.create_email_draft(
            subject="Test von Debug-Script",
            body="Dies ist ein Test-Entwurf",
            to_recipients=["test@example.com"]
        )

        print(f"Ergebnis: {result}")

        if result.get("success"):
            print(f"✓ E-Mail-Entwurf erfolgreich erstellt!")
            print(f"  - Draft ID: {result.get('draft_id', 'N/A')}")
        else:
            print(f"✗ Fehler: {result.get('error', 'Unbekannt')}")
    else:
        print(f"✗ Kein Access Token vorhanden")
except Exception as e:
    print(f"✗ Fehler: {e}")
    import traceback
    traceback.print_exc()

print()

# Test 6: Agent mit echter Anfrage testen
print("TEST 6: CalendarEmailAgent mit Test-Anfrage...")
try:
    from agents.calendar_email_agent import CalendarEmailAgent
    agent = CalendarEmailAgent()

    print("Anfrage: 'Zeige mir meine Termine heute'")
    result = agent.process("Zeige mir meine Termine heute")

    print()
    print("=" * 70)
    print("ERGEBNIS:")
    print("=" * 70)
    print(f"Status: {result.get('status')}")
    print(f"Tools verwendet: {result.get('tools_used', [])}")
    print()
    print("Antwort:")
    print(result.get('result', 'Keine Antwort'))
    print()

except Exception as e:
    print(f"✗ Fehler: {e}")
    import traceback
    traceback.print_exc()

print()
print("=" * 70)
print("Test abgeschlossen")
print("=" * 70)
