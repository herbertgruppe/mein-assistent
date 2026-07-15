#!/usr/bin/env python3
"""
Test-Skript für die clear_conversation_history Funktion
"""

import json
from utils.memory_manager import MemoryManager

def print_separator(title):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)

def main():
    # Initialisiere MemoryManager
    memory = MemoryManager("user_profile.json")

    # 1. Zeige Zustand VOR dem Löschen
    print_separator("ZUSTAND VOR DEM LÖSCHEN")
    conv_count = len(memory.memory.get("conversation_context", []))
    insight_count = len(memory.memory.get("research_insights", []))

    print(f"✓ Conversation Context Einträge: {conv_count}")
    print(f"✓ Research Insights: {insight_count}")

    # Zeige erste 3 Konversationen
    if conv_count > 0:
        print("\n📝 Erste 3 Konversations-Einträge:")
        for i, conv in enumerate(memory.memory["conversation_context"][:3]):
            print(f"\n  [{i+1}] Query: {conv['query'][:60]}...")
            print(f"      Workflow: {conv['workflow']}")
            print(f"      Summary Length: {len(conv['summary'])} Zeichen")

    # 2. Teste die clear_conversation_history Funktion MIT Übertragung
    print_separator("TEST: clear_conversation_history(transfer_insights=True)")

    transferred = memory.clear_conversation_history(transfer_insights=True)

    print(f"✓ Funktion ausgeführt")
    print(f"✓ Übertragene Insights: {transferred}")

    # 3. Zeige Zustand NACH dem Löschen
    print_separator("ZUSTAND NACH DEM LÖSCHEN")

    conv_count_after = len(memory.memory.get("conversation_context", []))
    insight_count_after = len(memory.memory.get("research_insights", []))

    print(f"✓ Conversation Context Einträge: {conv_count_after}")
    print(f"✓ Research Insights: {insight_count_after}")
    print(f"✓ Differenz Insights: +{insight_count_after - insight_count}")

    # Zeige neu übertragene Insights
    if transferred > 0:
        print(f"\n📚 Neu übertragene Insights (letzte {transferred}):")
        for insight in memory.memory["research_insights"][-transferred:]:
            print(f"\n  • Query: {insight['query'][:60]}...")
            print(f"    Insight: {insight['insight'][:80]}...")
            print(f"    Sources: {insight['sources']}")
            print(f"    Timestamp: {insight['timestamp']}")

    # 4. Prüfe, ob die Datei korrekt gespeichert wurde
    print_separator("VALIDIERUNG DER DATEI")

    with open("user_profile.json", 'r', encoding='utf-8') as f:
        saved_data = json.load(f)

    saved_conv_count = len(saved_data.get("conversation_context", []))
    saved_insight_count = len(saved_data.get("research_insights", []))

    print(f"✓ Gespeicherte Conversation Context: {saved_conv_count}")
    print(f"✓ Gespeicherte Research Insights: {saved_insight_count}")

    if saved_conv_count == 0:
        print("✅ SUCCESS: Conversation Context erfolgreich gelöscht!")
    else:
        print("❌ FEHLER: Conversation Context nicht gelöscht!")

    if transferred > 0 and saved_insight_count >= insight_count + transferred:
        print(f"✅ SUCCESS: {transferred} Insights erfolgreich übertragen!")
    elif transferred == 0:
        print("⚠️  INFO: Keine Insights erfüllen die Übertragungskriterien")
    else:
        print("❌ FEHLER: Insights nicht korrekt übertragen!")

    print_separator("TEST ABGESCHLOSSEN")

if __name__ == "__main__":
    main()
