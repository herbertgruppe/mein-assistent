#!/usr/bin/env python3
"""
Test der clear/reset Befehle im interaktiven Modus
Simuliert Benutzereingaben
"""

import json
from utils.memory_manager import MemoryManager

def test_clear_command_logic():
    """Testet die Logik, die im interaktiven Modus verwendet wird"""

    print("=" * 70)
    print("TEST: clear/reset Befehl-Logik (wie in main.py)")
    print("=" * 70)

    # Simuliere den Code aus main.py:215-231
    memory = MemoryManager("user_profile.json")
    user_input = "clear"  # Simulierte Benutzereingabe

    if user_input.lower() in ["clear", "reset"]:
        conv_count = len(memory.memory.get("conversation_context", []))

        print(f"\n💭 Chat-Historie enthält {conv_count} Einträge")

        if conv_count == 0:
            print("✓ Chat-Historie ist bereits leer")
        else:
            # Simuliere: Benutzer drückt Enter (= ja)
            transfer_insights = True

            print("Wichtige Erkenntnisse in permanentes Gedächtnis übertragen? (ja)")
            transferred = memory.clear_conversation_history(transfer_insights=transfer_insights)

            if transfer_insights and transferred > 0:
                print(f"✓ Chat-Historie gelöscht ({transferred} Erkenntnisse übertragen)")
            else:
                print("✓ Chat-Historie gelöscht (ohne Übertragung)")

            # Validierung
            print("\n" + "─" * 70)
            print("VALIDIERUNG:")
            new_conv_count = len(memory.memory.get("conversation_context", []))
            print(f"  • Conversation Context nach Löschung: {new_conv_count}")
            print(f"  • Erfolgreich gelöscht: {'✅ JA' if new_conv_count == 0 else '❌ NEIN'}")
            print(f"  • Insights übertragen: {transferred}")

def test_reset_alias():
    """Testet ob 'reset' auch funktioniert"""
    print("\n" + "=" * 70)
    print("TEST: 'reset' als Alias")
    print("=" * 70)

    # Restore backup first
    import shutil
    shutil.copy("user_profile.json.backup", "user_profile.json")

    memory = MemoryManager("user_profile.json")
    user_input = "reset"  # Simulierte Benutzereingabe

    if user_input.lower() in ["clear", "reset"]:
        conv_count = len(memory.memory.get("conversation_context", []))
        print(f"✓ 'reset' Befehl erkannt")
        print(f"✓ Chat-Historie hat {conv_count} Einträge")

        # Test ohne Übertragung
        transferred = memory.clear_conversation_history(transfer_insights=False)
        print(f"✓ Chat-Historie gelöscht ohne Übertragung")

        new_conv_count = len(memory.memory.get("conversation_context", []))
        print(f"✓ Neue Anzahl: {new_conv_count}")
        print(f"✓ Erfolgreich: {'✅ JA' if new_conv_count == 0 else '❌ NEIN'}")

def test_relevance_check_in_prompt():
    """Testet ob der Relevanz-Check im ResearchAgent vorhanden ist"""
    print("\n" + "=" * 70)
    print("TEST: Relevanz-Check im ResearchAgent System-Prompt")
    print("=" * 70)

    with open("agents/research_agent.py", "r", encoding="utf-8") as f:
        content = f.read()

    checks = [
        "KONTEXT-VERSCHMUTZUNG VERMEIDEN",
        "Prüfe KRITISCH",
        "DIREKT relevant",
        "Bevorzuge IMMER aktuelle Tool-Ergebnisse"
    ]

    print("\nSuche nach kritischen Anweisungen:")
    all_found = True
    for check in checks:
        found = check in content
        status = "✅" if found else "❌"
        print(f"  {status} '{check}'")
        all_found = all_found and found

    if all_found:
        print("\n✅ SUCCESS: Alle Relevanz-Check Anweisungen vorhanden!")
    else:
        print("\n❌ FEHLER: Einige Anweisungen fehlen!")

if __name__ == "__main__":
    # Stelle sicher, dass Backup existiert
    import shutil
    shutil.copy("user_profile.json", "user_profile.json.backup")

    # Führe Tests durch
    test_clear_command_logic()
    test_reset_alias()
    test_relevance_check_in_prompt()

    # Restore original
    shutil.copy("user_profile.json.backup", "user_profile.json")
    print("\n" + "=" * 70)
    print("✅ ALLE TESTS ABGESCHLOSSEN - Original wiederhergestellt")
    print("=" * 70)
