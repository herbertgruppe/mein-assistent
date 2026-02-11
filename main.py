"""
Multi-Agenten-System Orchestrator
"""

import os
import re
from datetime import datetime
from typing import Dict, Any, List
from dotenv import load_dotenv

# WICHTIG: load_dotenv() MUSS vor dem Import der Agenten aufgerufen werden!
load_dotenv()

from agents import ResearchAgent, TaskAgent, CommunicationAgent
from utils import MemoryManager
from tools import DocumentTool


class AgentOrchestrator:
    """Orchestriert die Zusammenarbeit zwischen verschiedenen Agenten"""

    def __init__(self):
        # LLM Provider aus Umgebungsvariablen
        self.llm_provider = os.getenv("LLM_PROVIDER", "anthropic")

        # Memory Manager initialisieren
        self.memory = MemoryManager()

        # Document Tool initialisieren
        self.document_tool = DocumentTool()

        # Asana Agent initialisieren
        from agents.asana_agent import AsanaAgent
        self.asana_agent = AsanaAgent()

        # Outlook Graph Tool initialisieren
        from tools.outlook_graph_tool import OutlookGraphTool
        self.outlook_tool = OutlookGraphTool()

        # Email Tool initialisieren
        from tools.email_tool import EmailTool
        self.email_tool = EmailTool()

        # Agenten initialisieren (TaskAgent bekommt AsanaAgent)
        self.research_agent = ResearchAgent(llm_provider=self.llm_provider)
        self.task_agent = TaskAgent(llm_provider=self.llm_provider, asana_agent=self.asana_agent)
        self.communication_agent = CommunicationAgent(llm_provider=self.llm_provider)

        # Keywords für automatische Agent-Auswahl
        self.research_keywords = [
            "recherchiere", "suche", "finde", "informationen", "erkläre",
            "was ist", "wie funktioniert", "analyse", "vergleiche"
        ]

        self.task_keywords = [
            "schreibe", "erstelle", "generiere", "mache", "entwickle",
            "implementiere", "verfasse", "produziere", "baue"
        ]

    def detect_agent_type(self, user_input: str) -> str:
        """
        Erkennt automatisch welcher Agent basierend auf der Eingabe verwendet werden soll

        Returns:
            "research_only", "task_only" oder "research_then_task"
        """
        input_lower = user_input.lower()

        has_research_intent = any(keyword in input_lower for keyword in self.research_keywords)
        has_task_intent = any(keyword in input_lower for keyword in self.task_keywords)

        if has_research_intent and has_task_intent:
            return "research_then_task"
        elif has_research_intent:
            return "research_only"
        elif has_task_intent:
            return "task_only"
        else:
            # Default: Beide nutzen für umfassende Antwort
            return "research_then_task"

    def process_request(self, user_input: str, workflow: str = "research_then_task") -> Dict[str, Any]:
        """
        Verarbeitet eine Benutzeranfrage durch das Multi-Agenten-System

        Args:
            user_input: Die Benutzereingabe
            workflow: Der zu verwendende Workflow

        Returns:
            Dict mit den Ergebnissen aller Agenten
        """
        results = {
            "input": user_input,
            "workflow": workflow,
            "agents_used": []
        }

        # Hole relevanten Kontext aus dem Gedächtnis
        memory_context = self.memory.get_relevant_context(user_input)
        user_context_str = self.memory.format_context_for_agent()

        if workflow == "research_then_task":
            # Schritt 1: Recherche mit Gedächtnis-Kontext
            research_result = self.research_agent.process(
                user_input,
                context={"memory": memory_context, "user_context": user_context_str}
            )
            results["research"] = research_result
            results["agents_used"].append("ResearchAgent")

            # Speichere wichtige Erkenntnisse
            if research_result.get("status") == "success":
                insight = research_result.get("findings", "")[:500]  # Erste 500 Zeichen
                self.memory.add_research_insight(user_input, insight)

            # Schritt 2: Aufgabe mit Recherche-Kontext
            # Kennzeichne explizit ob lokale Dokumente verwendet wurden
            task_context = research_result.copy()
            if research_result.get("findings"):
                # Prüfe ob der ResearchAgent lokale Dokumente verwendet hat
                findings = research_result.get("findings", "")
                if any(doc["name"] in findings for doc in self.document_tool.scan_documents()):
                    task_context["source_type"] = "local_documents"
                    task_context["findings"] = f"""=== LOKALER DOKUMENTEN-INHALT ===
Diese Informationen stammen aus den lokalen Dokumenten des Nutzers im input_docs/ Ordner.
Du musst NICHT auf Dateien zugreifen - der Inhalt ist bereits hier verfügbar.

{findings}

=== ENDE LOKALER DOKUMENTEN-INHALT ==="""

            task_result = self.task_agent.process(user_input, context=task_context)
            results["task"] = task_result
            results["agents_used"].append("TaskAgent")

        elif workflow == "task_only":
            # Nur Task Agent mit Gedächtnis-Kontext
            task_result = self.task_agent.process(
                user_input,
                context={"memory": memory_context, "user_context": user_context_str}
            )
            results["task"] = task_result
            results["agents_used"].append("TaskAgent")

        elif workflow == "research_only":
            # Nur Research Agent mit Gedächtnis-Kontext
            research_result = self.research_agent.process(
                user_input,
                context={"memory": memory_context, "user_context": user_context_str}
            )
            results["research"] = research_result
            results["agents_used"].append("ResearchAgent")

            # Speichere wichtige Erkenntnisse
            if research_result.get("status") == "success":
                insight = research_result.get("findings", "")[:500]  # Erste 500 Zeichen
                self.memory.add_research_insight(user_input, insight)

        # Speichere Konversations-Kontext
        summary = ""
        if "research" in results:
            summary = results["research"].get("findings", "")[:200]
        elif "task" in results:
            summary = results["task"].get("output", "")[:200]

        self.memory.add_conversation_context(user_input, workflow, summary)

        return results

    def interactive_mode(self):
        """Startet den interaktiven Modus"""
        print("=" * 70)
        print("🤖 Multi-Agenten-System - Interaktiver Modus")
        print("=" * 70)
        print(f"\nLLM Provider: {self.llm_provider.upper()}")

        # Zeige Informationen über lokale Dokumente
        doc_count = self.document_tool.count_documents()
        if doc_count > 0:
            print(f"📁 Ich habe Zugriff auf {doc_count} lokale Dokument(e) im Ordner input_docs/")
            documents = self.document_tool.scan_documents()
            for doc in documents:
                size_kb = doc['size'] / 1024
                print(f"   • {doc['name']} ({size_kb:.1f} KB)")
        else:
            print("📁 Keine Dokumente im Ordner input_docs/ gefunden")

        print("\nModi:")
        print("  • AUTO: System wählt passenden Workflow (Standard)")
        print("  • MANUAL: Workflow manuell wählen")
        print("\nBefehle:")
        print("  • 'quit' - Beenden")
        print("  • 'help' - Hilfe anzeigen")
        print("  • 'mode' - Modus wechseln")
        print("  • 'remember' - Information merken")
        print("  • 'memory' - Gedächtnis anzeigen")
        print("  • 'clear' - Chat-Historie löschen")
        print("  • 'forget' - Gedächtnis löschen\n")

        auto_mode = True

        while True:
            try:
                user_input = input("\n💬 Ihre Anfrage: ").strip()

                if not user_input:
                    continue

                if user_input.lower() == "quit":
                    print("\n👋 Auf Wiedersehen!")
                    break

                if user_input.lower() == "help":
                    self.show_help()
                    continue

                if user_input.lower() == "mode":
                    auto_mode = not auto_mode
                    mode_str = "AUTO" if auto_mode else "MANUAL"
                    print(f"\n✓ Modus gewechselt zu: {mode_str}")
                    continue

                if user_input.lower() == "remember":
                    self._handle_remember_command()
                    continue

                if user_input.lower() == "memory":
                    print("\n" + self.memory.export_memory())
                    continue

                if user_input.lower() in ["clear", "reset"]:
                    # Intelligentes Löschen der Chat-Historie
                    conv_count = len(self.memory.memory.get("conversation_context", []))
                    if conv_count == 0:
                        print("\n✓ Chat-Historie ist bereits leer")
                        continue

                    print(f"\n💭 Chat-Historie enthält {conv_count} Einträge")
                    transfer = input("Wichtige Erkenntnisse in permanentes Gedächtnis übertragen? (ja/nein, Enter=ja): ").strip().lower()
                    transfer_insights = transfer != "nein"

                    transferred = self.memory.clear_conversation_history(transfer_insights=transfer_insights)

                    if transfer_insights and transferred > 0:
                        print(f"✓ Chat-Historie gelöscht ({transferred} Erkenntnisse übertragen)")
                    else:
                        print("✓ Chat-Historie gelöscht (ohne Übertragung)")
                    continue

                if user_input.lower() == "forget":
                    confirm = input("\n⚠️  WARNUNG: Gesamtes Gedächtnis löschen? (ja/nein): ").strip().lower()
                    if confirm == "ja":
                        self.memory.clear_memory()
                        print("✓ Gedächtnis wurde gelöscht")
                    else:
                        print("✓ Abgebrochen")
                    continue

                # Workflow bestimmen
                if auto_mode:
                    workflow = self.detect_agent_type(user_input)
                    print(f"🔍 Gewählter Workflow: {workflow}")
                else:
                    print("\nWorkflows: [1] research_then_task [2] research_only [3] task_only")
                    workflow_choice = input("   Workflow (Enter=1): ").strip()
                    workflow_map = {
                        "1": "research_then_task",
                        "2": "research_only",
                        "3": "task_only",
                        "": "research_then_task"
                    }
                    workflow = workflow_map.get(workflow_choice, "research_then_task")

                # Anfrage verarbeiten
                print("\n" + "─" * 70)
                results = self.process_request(user_input, workflow)

                # Ergebnisse anzeigen
                self._display_results(results)

                # Ergebnisse exportieren
                export_content = ""
                if "research" in results:
                    export_content += "# Recherche\n\n"
                    export_content += results['research']['findings'] + "\n\n"
                if "task" in results:
                    export_content += "# Ergebnis\n\n"
                    export_content += results['task']['output'] + "\n\n"

                if export_content:
                    filepath = self.save_result_to_file(export_content, user_input)
                    print(f"✅ Bericht erfolgreich gespeichert unter: {filepath}")

                    # Frage, ob Bericht per E-Mail versendet werden soll
                    self._ask_to_send_email_report(export_content, user_input)

            except KeyboardInterrupt:
                print("\n\n👋 Auf Wiedersehen!")
                break
            except Exception as e:
                print(f"\n❌ Fehler: {e}")
                import traceback
                traceback.print_exc()

    def _display_results(self, results: Dict[str, Any]):
        """Zeigt die Ergebnisse formatiert an"""
        print("\n" + "=" * 70)
        print("📊 ERGEBNISSE")
        print("=" * 70)
        print(f"Verwendete Agenten: {', '.join(results['agents_used'])}")
        print()

        if "research" in results:
            print("🔍 RESEARCH AGENT")
            print("─" * 70)
            print(results['research']['findings'])
            print()

        if "task" in results:
            print("⚙️  TASK AGENT")
            print("─" * 70)
            print(results['task']['output'])

        print("\n" + "─" * 70)

    def _handle_remember_command(self):
        """Verarbeitet den 'remember' Befehl zum Speichern von Informationen"""
        print("\n" + "=" * 70)
        print("📝 INFORMATION MERKEN")
        print("=" * 70)
        print("\nWas möchten Sie speichern?")
        print("\nKategorien:")
        print("  [1] Name")
        print("  [2] Beruf/Profession")
        print("  [3] Interesse")
        print("  [4] Schreibstil-Präferenz")
        print("  [5] Sprache")
        print("  [6] Freie Information")
        print()

        category_choice = input("Kategorie (1-6): ").strip()

        category_map = {
            "1": "name",
            "2": "profession",
            "3": "interest",
            "4": "writing_style",
            "5": "language",
            "6": "custom"
        }

        category = category_map.get(category_choice)

        if not category:
            print("❌ Ungültige Kategorie")
            return

        info = input("\nInformation: ").strip()

        if not info:
            print("❌ Keine Information eingegeben")
            return

        self.memory.add_user_fact(info, category)
        print(f"✓ Information gespeichert: {info}")

    def show_help(self):
        """Zeigt Hilfe-Informationen an"""
        print("\n" + "=" * 70)
        print("📖 HILFE")
        print("=" * 70)
        print("""
WORKFLOWS:
  • research_then_task: Recherche durchführen, dann Aufgabe ausführen
  • research_only: Nur Recherche durchführen
  • task_only: Nur Aufgabe ausführen

MODI:
  • AUTO: System erkennt automatisch den passenden Workflow
  • MANUAL: Sie wählen den Workflow manuell

BEFEHLE:
  • help: Diese Hilfe anzeigen
  • mode: Zwischen AUTO und MANUAL Modus wechseln
  • remember: Information über sich selbst speichern
  • memory: Gespeicherte Informationen anzeigen
  • clear/reset: Chat-Historie löschen (mit optionaler Übertragung wichtiger Fakten)
  • forget: Gesamtes Gedächtnis löschen
  • quit: Programm beenden

GEDÄCHTNIS-SYSTEM:
  Der Assistent merkt sich Informationen über Sie und nutzt diese
  als Kontext für zukünftige Anfragen. Verwenden Sie 'remember' um
  dem Assistenten wichtige Informationen mitzuteilen.

  Mit 'clear' können Sie die Chat-Historie löschen, um Kontext-Verschmutzung
  zu vermeiden. Wichtige Erkenntnisse werden dabei optional ins permanente
  Gedächtnis übertragen.

BEISPIELE:

  Research-Anfragen (lösen Research Agent aus):
    💬 Erkläre mir Quantencomputing
    💬 Was ist Machine Learning?
    💬 Vergleiche Python und JavaScript
    💬 Finde Informationen über Blockchain

  Task-Anfragen (lösen Task Agent aus):
    💬 Schreibe einen Blogpost über KI
    💬 Erstelle eine Produktbeschreibung
    💬 Generiere einen Python-Code für Fibonacci
    💬 Verfasse eine E-Mail

  Kombinierte Anfragen (beide Agenten):
    💬 Recherchiere React und schreibe ein Tutorial
    💬 Finde Infos über gesunde Ernährung und erstelle einen Meal Plan
        """)

    def save_result_to_file(self, content: str, topic: str) -> str:
        """
        Speichert den Inhalt als Markdown-Datei im newsletter_archiv Ordner

        Args:
            content: Der zu speichernde Inhalt
            topic: Das Thema für den Dateinamen

        Returns:
            Der Pfad zur gespeicherten Datei
        """
        # Ordner erstellen falls nicht vorhanden
        archive_dir = "newsletter_archiv"
        os.makedirs(archive_dir, exist_ok=True)

        # Thema für Dateinamen sanitizen
        safe_topic = re.sub(r'[^\w\s-]', '', topic)  # Sonderzeichen entfernen
        safe_topic = re.sub(r'[-\s]+', '-', safe_topic)  # Leerzeichen zu Bindestrichen
        safe_topic = safe_topic.strip('-')[:50]  # Begrenzen auf 50 Zeichen

        # Dateinamen erstellen
        today = datetime.now().strftime("%Y-%m-%d")
        filename = f"{today}_{safe_topic}.md"
        filepath = os.path.join(archive_dir, filename)

        # Inhalt speichern
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

        return filepath

    def _ask_to_send_email_report(self, content: str, topic: str):
        """
        Fragt den Benutzer, ob der Bericht per E-Mail versendet werden soll

        Args:
            content: Der Berichtsinhalt
            topic: Das Thema des Berichts
        """
        email_receiver = os.getenv("EMAIL_RECEIVER", "")

        if not email_receiver:
            print("\n⚠️  Hinweis: EMAIL_RECEIVER nicht in .env konfiguriert. E-Mail-Versand übersprungen.")
            return

        try:
            # Frage Benutzer
            response = input(f"\n📧 Möchten Sie diesen Bericht per E-Mail an {email_receiver} senden? (y/n): ").strip().lower()

            if response in ['y', 'yes', 'j', 'ja']:
                print("\n📤 Sende E-Mail...")

                # Erstelle Betreff
                subject = f"Recherche-Bericht: {topic[:50]}"

                # Sende E-Mail über CommunicationAgent
                result = self.communication_agent.send_email(
                    to=email_receiver,
                    subject=subject,
                    body=content
                )

                if result.get("status") == "success":
                    print(f"✅ {result.get('result', 'E-Mail erfolgreich versendet!')}")
                else:
                    print(f"❌ {result.get('result', 'E-Mail-Versand fehlgeschlagen')}")
            else:
                print("✓ E-Mail-Versand übersprungen")

        except Exception as e:
            print(f"❌ Fehler beim E-Mail-Versand: {e}")


def main():
    """Haupteinstiegspunkt des Programms"""
    orchestrator = AgentOrchestrator()
    orchestrator.interactive_mode()


if __name__ == "__main__":
    main()
