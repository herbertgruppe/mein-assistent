"""
Test-Skript für das Gedächtnis-System
"""

from utils import MemoryManager

def test_memory_system():
    print("=" * 70)
    print("🧪 TEST: Langzeitgedächtnis-System")
    print("=" * 70)

    # 1. MemoryManager initialisieren
    print("\n1️⃣  MemoryManager initialisieren...")
    memory = MemoryManager(memory_file="test_user_profile.json")
    print("✓ MemoryManager erstellt")

    # 2. Nutzer-Informationen hinzufügen
    print("\n2️⃣  Nutzer-Informationen speichern...")
    memory.add_user_fact("Max Mustermann", "name")
    memory.add_user_fact("Software-Entwickler", "profession")
    memory.add_user_fact("Python", "interest")
    memory.add_user_fact("Machine Learning", "interest")
    memory.add_user_fact("Technisch und präzise", "writing_style")
    memory.add_user_fact("Deutsch", "language")
    memory.add_user_fact("Arbeitet viel mit LangChain", "custom")
    print("✓ 7 Informationen gespeichert")

    # 3. Nutzerprofil anzeigen
    print("\n3️⃣  Nutzerprofil abrufen...")
    profile = memory.get_user_profile()
    print(f"   Name: {profile['name']}")
    print(f"   Beruf: {profile['profession']}")
    print(f"   Interessen: {', '.join(profile['interests'])}")
    print(f"   Schreibstil: {profile['preferred_writing_style']}")
    print(f"   Sprachen: {', '.join(profile['languages'])}")
    print(f"   Custom Facts: {len(profile['custom_facts'])}")

    # 4. Recherche-Erkenntnisse speichern
    print("\n4️⃣  Recherche-Erkenntnisse speichern...")
    memory.add_research_insight(
        query="Was ist LangChain?",
        insight="LangChain ist ein Framework zum Entwickeln von Anwendungen mit Large Language Models (LLMs). Es bietet Tools für Prompts, Chains und Agents.",
        sources=["https://langchain.com"]
    )
    memory.add_research_insight(
        query="Python Best Practices",
        insight="Python Best Practices umfassen: PEP 8 Style Guide, Type Hints, Dokumentation, Unit Tests und virtuelle Umgebungen.",
        sources=["https://python.org"]
    )
    print("✓ 2 Erkenntnisse gespeichert")

    # 5. Relevanten Kontext suchen
    print("\n5️⃣  Relevanten Kontext für Anfrage suchen...")
    query = "Wie nutze ich LangChain für einen Chatbot?"
    context = memory.get_relevant_context(query)
    print(f"   Anfrage: '{query}'")
    print(f"   Gefundene relevante Erkenntnisse: {len(context['relevant_insights'])}")
    for insight in context['relevant_insights']:
        print(f"   - {insight['query']}: {insight['insight'][:100]}...")

    # 6. Kontext für Agent formatieren
    print("\n6️⃣  Kontext für Agent formatieren...")
    agent_context = memory.format_context_for_agent()
    print("   Formatierter Kontext:")
    print("   " + "\n   ".join(agent_context.split("\n")[:10]))

    # 7. Gedächtnis exportieren
    print("\n7️⃣  Gedächtnis exportieren...")
    export = memory.export_memory()
    print(export)

    # 8. Cleanup
    print("\n8️⃣  Test-Datei bereinigen...")
    import os
    if os.path.exists("test_user_profile.json"):
        os.remove("test_user_profile.json")
        print("✓ Test-Datei gelöscht")

    print("\n" + "=" * 70)
    print("✅ ALLE TESTS ERFOLGREICH")
    print("=" * 70)


if __name__ == "__main__":
    test_memory_system()
