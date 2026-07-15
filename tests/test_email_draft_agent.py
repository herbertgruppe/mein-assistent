"""
Test-Skript um zu prüfen ob der CalendarEmailAgent E-Mail-Entwürfe erstellen kann
"""

from dotenv import load_dotenv
load_dotenv()

from agents.calendar_email_agent import CalendarEmailAgent

print("=" * 70)
print("Test: CalendarEmailAgent E-Mail-Entwurf")
print("=" * 70)
print()

# Initialisiere Agent
print("1. Initialisiere CalendarEmailAgent...")
agent = CalendarEmailAgent()
print()

# Prüfe Outlook Tool Status
print("2. Prüfe Outlook Tool Status...")
print(f"   - Tool vorhanden: {agent.outlook_tool is not None}")
if agent.outlook_tool:
    print(f"   - Konfiguriert: {agent.outlook_tool.is_configured}")
    print(f"   - Token vorhanden: {agent.outlook_tool.access_token is not None}")
    if agent.outlook_tool.access_token:
        print(f"   - Token-Länge: {len(agent.outlook_tool.access_token)}")
print()

# Teste E-Mail-Entwurf-Erstellung
print("3. Teste E-Mail-Entwurf-Erstellung...")
print()

request = "Erstelle einen E-Mail-Entwurf an test@example.com mit dem Betreff 'Test' und dem Text 'Dies ist ein Test'"

result = agent.process(request)

print()
print("=" * 70)
print("Ergebnis:")
print("=" * 70)
print(f"Status: {result.get('status')}")
print(f"Agent: {result.get('agent')}")
print()
print("Antwort:")
print(result.get('result'))
