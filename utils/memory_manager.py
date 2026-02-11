"""
Memory Manager für persistentes Langzeitgedächtnis
"""

import json
import os
from datetime import datetime
from typing import Dict, Any, List, Optional


class MemoryManager:
    """Verwaltet das Langzeitgedächtnis des Assistenten"""

    def __init__(self, memory_file: str = "user_profile.json"):
        """
        Initialisiert den MemoryManager

        Args:
            memory_file: Pfad zur JSON-Datei für das Gedächtnis
        """
        self.memory_file = memory_file
        self.memory = self._load_memory()

    def _load_memory(self) -> Dict[str, Any]:
        """Lädt das Gedächtnis aus der Datei"""
        if os.path.exists(self.memory_file):
            try:
                with open(self.memory_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"⚠️ Warnung: Fehler beim Laden des Gedächtnisses: {e}")
                return self._create_empty_memory()
        else:
            return self._create_empty_memory()

    def _create_empty_memory(self) -> Dict[str, Any]:
        """Erstellt ein leeres Gedächtnis mit der Grundstruktur"""
        return {
            "user_profile": {
                "name": None,
                "interests": [],
                "preferred_writing_style": None,
                "profession": None,
                "languages": [],
                "custom_facts": []
            },
            "research_insights": [],
            "conversation_context": [],
            "preferences": {
                "default_workflow": "research_then_task",
                "auto_mode": True
            },
            "metadata": {
                "created_at": datetime.now().isoformat(),
                "last_updated": datetime.now().isoformat(),
                "version": "1.0"
            }
        }

    def _save_memory(self) -> None:
        """Speichert das Gedächtnis in die Datei"""
        try:
            self.memory["metadata"]["last_updated"] = datetime.now().isoformat()
            with open(self.memory_file, 'w', encoding='utf-8') as f:
                json.dump(self.memory, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"❌ Fehler beim Speichern des Gedächtnisses: {e}")

    def add_user_fact(self, fact: str, category: str = "custom") -> None:
        """
        Fügt eine Information über den Nutzer hinzu

        Args:
            fact: Die zu speichernde Information
            category: Kategorie der Information (custom, interest, profession, etc.)
        """
        if category == "interest":
            if fact not in self.memory["user_profile"]["interests"]:
                self.memory["user_profile"]["interests"].append(fact)
        elif category == "profession":
            self.memory["user_profile"]["profession"] = fact
        elif category == "name":
            self.memory["user_profile"]["name"] = fact
        elif category == "writing_style":
            self.memory["user_profile"]["preferred_writing_style"] = fact
        elif category == "language":
            if fact not in self.memory["user_profile"]["languages"]:
                self.memory["user_profile"]["languages"].append(fact)
        else:
            # Custom fact
            fact_entry = {
                "content": fact,
                "timestamp": datetime.now().isoformat(),
                "category": category
            }
            self.memory["user_profile"]["custom_facts"].append(fact_entry)

        self._save_memory()

    def add_research_insight(self, query: str, insight: str, sources: List[str] = None) -> None:
        """
        Speichert eine wichtige Erkenntnis aus einer Recherche

        Args:
            query: Die ursprüngliche Suchanfrage
            insight: Die gewonnene Erkenntnis
            sources: Optional, Liste von Quellen
        """
        insight_entry = {
            "query": query,
            "insight": insight,
            "sources": sources or [],
            "timestamp": datetime.now().isoformat()
        }
        self.memory["research_insights"].append(insight_entry)

        # Begrenze auf die letzten 50 Erkenntnisse
        if len(self.memory["research_insights"]) > 50:
            self.memory["research_insights"] = self.memory["research_insights"][-50:]

        self._save_memory()

    def add_conversation_context(self, query: str, workflow: str, summary: str) -> None:
        """
        Speichert Kontext aus einer Konversation

        Args:
            query: Die Benutzeranfrage
            workflow: Der verwendete Workflow
            summary: Zusammenfassung des Ergebnisses
        """
        context_entry = {
            "query": query,
            "workflow": workflow,
            "summary": summary,
            "timestamp": datetime.now().isoformat()
        }
        self.memory["conversation_context"].append(context_entry)

        # Begrenze auf die letzten 20 Konversationen
        if len(self.memory["conversation_context"]) > 20:
            self.memory["conversation_context"] = self.memory["conversation_context"][-20:]

        self._save_memory()

    def get_relevant_context(self, query: str) -> Dict[str, Any]:
        """
        Sucht relevanten Kontext für eine Anfrage

        Args:
            query: Die Benutzeranfrage

        Returns:
            Dict mit relevantem Kontext
        """
        query_lower = query.lower()

        context = {
            "user_profile": self.memory["user_profile"],
            "relevant_insights": [],
            "recent_context": []
        }

        # Suche nach relevanten Research-Insights
        for insight in self.memory["research_insights"]:
            # Einfache Keyword-Übereinstimmung
            if any(word in insight["query"].lower() for word in query_lower.split() if len(word) > 3):
                context["relevant_insights"].append(insight)

        # Füge die letzten 3 Konversationen hinzu
        if self.memory["conversation_context"]:
            context["recent_context"] = self.memory["conversation_context"][-3:]

        return context

    def get_user_profile(self) -> Dict[str, Any]:
        """Gibt das Nutzerprofil zurück"""
        return self.memory["user_profile"]

    def update_preference(self, key: str, value: Any) -> None:
        """
        Aktualisiert eine Präferenz

        Args:
            key: Der Präferenz-Schlüssel
            value: Der neue Wert
        """
        self.memory["preferences"][key] = value
        self._save_memory()

    def get_preference(self, key: str, default: Any = None) -> Any:
        """
        Holt eine Präferenz

        Args:
            key: Der Präferenz-Schlüssel
            default: Standardwert falls nicht vorhanden

        Returns:
            Der Präferenzwert oder default
        """
        return self.memory["preferences"].get(key, default)

    def format_context_for_agent(self) -> str:
        """
        Formatiert den gespeicherten Kontext für die Übergabe an Agenten

        Returns:
            Formatierter Kontext-String
        """
        profile = self.memory["user_profile"]
        context_parts = []

        # Nutzerprofil
        if profile["name"]:
            context_parts.append(f"Name des Nutzers: {profile['name']}")

        if profile["profession"]:
            context_parts.append(f"Beruf: {profile['profession']}")

        if profile["interests"]:
            context_parts.append(f"Interessen: {', '.join(profile['interests'])}")

        if profile["preferred_writing_style"]:
            context_parts.append(f"Bevorzugter Schreibstil: {profile['preferred_writing_style']}")

        if profile["languages"]:
            context_parts.append(f"Sprachen: {', '.join(profile['languages'])}")

        # Custom Facts
        if profile["custom_facts"]:
            recent_facts = profile["custom_facts"][-5:]  # Letzte 5 Facts
            facts_str = "\n".join([f"- {fact['content']}" for fact in recent_facts])
            context_parts.append(f"Weitere Informationen:\n{facts_str}")

        if not context_parts:
            return ""

        return "KONTEXT ÜBER DEN NUTZER:\n" + "\n".join(context_parts)

    def clear_conversation_history(self, transfer_insights: bool = True) -> int:
        """
        Löscht die Konversations-Historie mit optionaler Übertragung wichtiger Fakten

        Args:
            transfer_insights: Wenn True, werden wichtige Erkenntnisse vor dem Löschen
                             in die research_insights übertragen

        Returns:
            Anzahl der übertragenen Insights
        """
        transferred = 0

        if transfer_insights and self.memory["conversation_context"]:
            # Prüfe die letzten Konversationen auf wichtige Erkenntnisse
            for conv in self.memory["conversation_context"]:
                # Übertrage nur Konversationen mit substantiellem Inhalt (>100 Zeichen)
                if len(conv.get("summary", "")) > 100:
                    # Prüfe ob es sich um eine Recherche-Anfrage handelte
                    if "research" in conv.get("workflow", "").lower():
                        # Übertrage als Research Insight
                        self.add_research_insight(
                            query=conv["query"],
                            insight=conv["summary"][:500],  # Limitiere auf 500 Zeichen
                            sources=["Konversations-Historie"]
                        )
                        transferred += 1

        # Lösche die Konversations-Historie
        old_count = len(self.memory["conversation_context"])
        self.memory["conversation_context"] = []
        self._save_memory()

        return transferred

    def clear_memory(self) -> None:
        """Löscht das gesamte Gedächtnis (mit Bestätigung)"""
        self.memory = self._create_empty_memory()
        self._save_memory()

    def export_memory(self) -> str:
        """Exportiert das Gedächtnis als formatierten String"""
        output = []
        output.append("=" * 70)
        output.append("📚 GEDÄCHTNIS-EXPORT")
        output.append("=" * 70)
        output.append("")

        profile = self.memory["user_profile"]

        output.append("👤 NUTZERPROFIL")
        output.append("-" * 70)
        if profile["name"]:
            output.append(f"Name: {profile['name']}")
        if profile["profession"]:
            output.append(f"Beruf: {profile['profession']}")
        if profile["interests"]:
            output.append(f"Interessen: {', '.join(profile['interests'])}")
        if profile["preferred_writing_style"]:
            output.append(f"Schreibstil: {profile['preferred_writing_style']}")
        if profile["languages"]:
            output.append(f"Sprachen: {', '.join(profile['languages'])}")

        if profile["custom_facts"]:
            output.append("\nWeitere Informationen:")
            for fact in profile["custom_facts"]:
                output.append(f"  • {fact['content']}")

        output.append("")
        output.append(f"🔍 RECHERCHE-ERKENNTNISSE ({len(self.memory['research_insights'])} gespeichert)")
        output.append("-" * 70)
        if self.memory["research_insights"]:
            for insight in self.memory["research_insights"][-5:]:
                output.append(f"\nThema: {insight['query']}")
                output.append(f"Erkenntnis: {insight['insight'][:200]}...")
        else:
            output.append("Noch keine Erkenntnisse gespeichert.")

        output.append("")
        output.append("=" * 70)

        return "\n".join(output)
