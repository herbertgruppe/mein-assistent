"""
StreamlitOrchestrator: Koordiniert alle Agenten und Tools für den Streamlit-UI.
"""
import os
import streamlit as st
from datetime import datetime
from utils import MemoryManager
from agents import ResearchAgent, TaskAgent, CommunicationAgent, AsanaAgent, CalendarEmailAgent
from tools import DocumentTool, AsanaTool, OutlookGraphTool


class StreamlitOrchestrator:
    """Orchestrator für Streamlit Web-Interface"""

    def __init__(self, user_ctx=None):
        """Initialisiere Orchestrator

        Args:
            user_ctx: Optional UserContext für Multi-User-Support.
                      Falls None, werden Legacy-Pfade verwendet.
        """
        self.user_ctx = user_ctx

        # LLM Provider aus Umgebungsvariablen
        self.llm_provider = os.getenv("LLM_PROVIDER", "anthropic")

        # Memory Manager initialisieren
        self.memory = MemoryManager()

        # Document Tool initialisieren
        self.document_tool = DocumentTool()

        # Asana Tool initialisieren
        self.asana_tool = AsanaTool()

        # Asana Agent zuerst initialisieren - mit per-User Token falls vorhanden
        asana_token = user_ctx.get_asana_token() if user_ctx else os.getenv("ASANA_ACCESS_TOKEN", "")
        self.asana_agent = AsanaAgent(api_key=asana_token) if asana_token else AsanaAgent()

        # Outlook Graph Tool initialisieren - mit per-User Token-Datei falls vorhanden
        from tools.outlook_graph_tool import OutlookGraphTool
        outlook_token_file = str(user_ctx.outlook_token_file) if user_ctx else None
        self.outlook_tool = OutlookGraphTool(token_file=outlook_token_file)

        # Email Tool initialisieren
        from tools.email_tool import EmailTool
        self.email_tool = EmailTool()

        # Agenten initialisieren (TaskAgent bekommt AsanaAgent)
        self.research_agent = ResearchAgent(llm_provider=self.llm_provider)
        self.task_agent = TaskAgent(llm_provider=self.llm_provider, asana_agent=self.asana_agent)
        self.communication_agent = CommunicationAgent(llm_provider=self.llm_provider)
        self.calendar_email_agent = CalendarEmailAgent(llm_provider=self.llm_provider, outlook_tool=self.outlook_tool)

        # Keywords für automatische Agent-Auswahl
        self.research_keywords = [
            "recherchiere", "suche", "finde", "informationen", "erkläre",
            "was ist", "wie funktioniert", "analyse", "vergleiche"
        ]

        self.task_keywords = [
            "schreibe", "erstelle", "generiere", "mache", "entwickle",
            "implementiere", "verfasse", "produziere", "baue"
        ]

        self.asana_keywords = [
            "aufgaben", "to-do", "todo", "was steht an", "termine", "deadlines",
            "aufgabe erstellen", "asana", "fällig", "erledigen"
        ]

        self.calendar_email_keywords = [
            "kalender", "termin", "meeting", "besprechung", "event", "events",
            "e-mail", "email", "mail", "nachricht", "entwurf", "draft",
            "sende email", "schicke email", "email suchen", "termine heute",
            "termine morgen", "kalendereinträge", "outlook"
        ]

    def detect_agent_type(self, user_input: str) -> str:
        """
        Erkennt automatisch welcher Agent basierend auf der Eingabe verwendet werden soll

        Returns:
            "research_only", "task_only", "research_then_task", "asana", or "calendar_email"
        """
        input_lower = user_input.lower()

        has_calendar_email_intent = any(keyword in input_lower for keyword in self.calendar_email_keywords)
        has_asana_intent = any(keyword in input_lower for keyword in self.asana_keywords)
        has_research_intent = any(keyword in input_lower for keyword in self.research_keywords)
        has_task_intent = any(keyword in input_lower for keyword in self.task_keywords)

        # Calendar/Email hat höchste Priorität bei direkten Anfragen
        if has_calendar_email_intent:
            return "calendar_email"
        # Asana hat Priorität bei direkten Anfragen
        elif has_asana_intent:
            return "asana"
        elif has_research_intent and has_task_intent:
            return "research_then_task"
        elif has_research_intent:
            return "research_only"
        elif has_task_intent:
            return "task_only"
        else:
            # Default: Beide nutzen für umfassende Antwort
            return "research_then_task"

    def process_request(self, user_input: str, workflow: str = "auto"):
        """Verarbeitet Anfrage und gibt strukturierte Ergebnisse zurück"""
        import traceback

        # Auto-detect workflow wenn gewünscht
        if workflow == "auto":
            workflow = self.detect_agent_type(user_input)

        results = {
            "input": user_input,
            "workflow": workflow,
            "agents_used": [],
            "timestamp": datetime.now().isoformat()
        }

        # Hole relevanten Kontext aus dem Gedächtnis
        memory_context = self.memory.get_relevant_context(user_input)
        user_context_str = self.memory.format_context_for_agent()

        try:
            if workflow == "research_then_task":
                # Schritt 1: Recherche
                with st.spinner("🔍 Research Agent arbeitet..."):
                    try:
                        research_result = self.research_agent.process(
                            user_input,
                            context={"memory": memory_context, "user_context": user_context_str}
                        )
                        results["research"] = research_result
                        results["agents_used"].append("ResearchAgent")

                        print(f"\n[DEBUG] Research Agent Status: {research_result.get('status')}")
                        print(f"[DEBUG] Research Agent Keys: {research_result.keys()}")

                    except Exception as e:
                        print(f"\n❌ [ERROR] Research Agent Fehler: {e}")
                        print(f"[ERROR] Traceback:\n{traceback.format_exc()}")
                        results["research"] = {
                            "status": "error",
                            "error": str(e),
                            "findings": f"Fehler beim Research Agent: {e}"
                        }

                # Speichere wichtige Erkenntnisse
                if results.get("research", {}).get("status") == "success":
                    insight = results["research"].get("findings", "")[:500]
                    self.memory.add_research_insight(user_input, insight)

                # Schritt 2: Aufgabe mit Task Agent
                try:
                    task_context = {
                        "memory": memory_context,
                        "user_context": user_context_str
                    }

                    if "research" in results and results["research"].get("status") == "success":
                        research_findings = results["research"].get("findings", "")

                        if any(doc["name"] in research_findings for doc in self.document_tool.scan_documents()):
                            task_context["findings"] = f"""=== LOKALER DOKUMENTEN-INHALT ===
Diese Informationen stammen aus den lokalen Dokumenten des Nutzers im input_docs/ Ordner.
Du musst NICHT auf Dateien zugreifen - der Inhalt ist bereits hier verfügbar.

{research_findings}

=== ENDE LOKALER DOKUMENTEN-INHALT ==="""
                        else:
                            task_context["findings"] = research_findings

                    print(f"\n[DEBUG] Task Agent wird aufgerufen...")
                    print(f"[DEBUG] Task Context Keys: {task_context.keys()}")

                    with st.spinner("⚙️ Task Agent arbeitet..."):
                        task_result = self.task_agent.process(user_input, context=task_context)
                        results["task"] = task_result
                        results["agents_used"].append("TaskAgent")

                        print(f"\n[DEBUG] Task Agent Status: {task_result.get('status')}")

                except Exception as e:
                    print(f"\n❌ [ERROR] Task Agent Fehler: {e}")
                    print(f"[ERROR] Traceback:\n{traceback.format_exc()}")
                    results["task"] = {
                        "status": "error",
                        "error": str(e),
                        "output": f"Fehler beim Task Agent: {e}\n\nDetails:\n{traceback.format_exc()}"
                    }

            elif workflow == "research_only":
                with st.spinner("🔍 Research Agent arbeitet..."):
                    try:
                        research_result = self.research_agent.process(
                            user_input,
                            context={"memory": memory_context, "user_context": user_context_str}
                        )
                        results["research"] = research_result
                        results["agents_used"].append("ResearchAgent")
                    except Exception as e:
                        print(f"\n❌ [ERROR] Research Agent Fehler: {e}")
                        results["research"] = {
                            "status": "error",
                            "error": str(e),
                            "findings": f"Fehler: {e}"
                        }

                if results.get("research", {}).get("status") == "success":
                    insight = results["research"].get("findings", "")[:500]
                    self.memory.add_research_insight(user_input, insight)

            elif workflow == "task_only":
                with st.spinner("⚙️ Task Agent arbeitet..."):
                    try:
                        task_result = self.task_agent.process(
                            user_input,
                            context={"memory": memory_context, "user_context": user_context_str}
                        )
                        results["task"] = task_result
                        results["agents_used"].append("TaskAgent")
                    except Exception as e:
                        print(f"\n❌ [ERROR] Task Agent Fehler: {e}")
                        results["task"] = {
                            "status": "error",
                            "error": str(e),
                            "output": f"Fehler: {e}"
                        }

            elif workflow == "asana":
                with st.spinner("✅ Asana Agent arbeitet..."):
                    try:
                        asana_result = self.asana_agent.process(user_input)
                        results["asana"] = asana_result
                        results["agents_used"].append("AsanaAgent")
                    except Exception as e:
                        print(f"\n❌ [ERROR] Asana Agent Fehler: {e}")
                        results["asana"] = {
                            "status": "error",
                            "error": str(e),
                            "result": f"Fehler: {e}"
                        }

            elif workflow == "calendar_email":
                with st.spinner("📅 CalendarEmail Agent arbeitet..."):
                    try:
                        calendar_email_result = self.calendar_email_agent.process(
                            user_input,
                            context={"memory": memory_context, "user_context": user_context_str}
                        )
                        results["calendar_email"] = calendar_email_result
                        results["agents_used"].append("CalendarEmailAgent")
                    except Exception as e:
                        print(f"\n❌ [ERROR] CalendarEmail Agent Fehler: {e}")
                        results["calendar_email"] = {
                            "status": "error",
                            "error": str(e),
                            "result": f"Fehler: {e}"
                        }

            # Speichere Konversations-Kontext
            summary = ""
            if "research" in results:
                summary = results["research"].get("findings", "")[:200]
            elif "task" in results:
                summary = results["task"].get("output", "")[:200]
            elif "asana" in results:
                summary = results["asana"].get("result", "")[:200]
            elif "calendar_email" in results:
                summary = results["calendar_email"].get("result", "")[:200]

            self.memory.add_conversation_context(user_input, workflow, summary)

        except Exception as e:
            print(f"\n❌ [CRITICAL ERROR] Unerwarteter Fehler in process_request: {e}")
            print(f"[ERROR] Traceback:\n{traceback.format_exc()}")
            results["error"] = str(e)
            st.error(f"Kritischer Fehler bei der Verarbeitung: {e}")

        return results
