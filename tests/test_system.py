"""
Test-Skript für das Multi-Agenten-System
"""

import os
from dotenv import load_dotenv
from agents import ResearchAgent, TaskAgent

def test_system():
    """Testet das Multi-Agenten-System"""
    load_dotenv()

    print("=" * 70)
    print("🧪 SYSTEM-TEST")
    print("=" * 70)

    # Test 1: ResearchAgent
    print("\n📋 TEST 1: ResearchAgent mit Tavily-Websuche")
    print("─" * 70)

    research_agent = ResearchAgent(llm_provider=os.getenv("LLM_PROVIDER", "anthropic"))

    test_query = "Was sind die neuesten Entwicklungen in der KI im Jahr 2025?"
    result = research_agent.process(test_query)

    print(f"\nStatus: {result['status']}")
    print(f"Websuche verwendet: {result.get('web_search_used', False)}")
    print(f"\nErgebnisse:\n{result['findings'][:500]}...")

    # Test 2: TaskAgent
    print("\n\n📋 TEST 2: TaskAgent")
    print("─" * 70)

    task_agent = TaskAgent(llm_provider=os.getenv("LLM_PROVIDER", "anthropic"))

    task_query = "Schreibe eine kurze Zusammenfassung über Python (3 Sätze)"
    result2 = task_agent.process(task_query, context=result)

    print(f"\nStatus: {result2['status']}")
    print(f"Kontext verwendet: {result2['used_context']}")
    print(f"\nErgebnis:\n{result2['output']}")

    print("\n" + "=" * 70)
    print("✓ TEST ABGESCHLOSSEN")
    print("=" * 70)

if __name__ == "__main__":
    test_system()
