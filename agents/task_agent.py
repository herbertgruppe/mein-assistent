"""
Task Agent für die Ausführung von Aufgaben
"""

import os
from typing import Dict, Any
from .base_agent import BaseAgent


class TaskAgent(BaseAgent):
    """Agent für die Ausführung von konkreten Aufgaben"""

    def __init__(self, api_key: str = None, llm_provider: str = None, asana_agent=None):
        super().__init__("TaskAgent")

        # LLM Provider bestimmen
        self.llm_provider = llm_provider or os.getenv("LLM_PROVIDER", "anthropic")

        # API-Key laden
        if self.llm_provider == "anthropic":
            self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        else:
            self.api_key = api_key or os.getenv("OPENAI_API_KEY")

        # LLM initialisieren
        self.llm = self._initialize_llm()

        # AsanaAgent für Aufgaben-Erstellung
        self.asana_agent = asana_agent

    def _initialize_llm(self):
        """Initialisiert das LLM basierend auf dem Provider"""
        # Prüfe ob API-Key vorhanden ist
        if not self.api_key:
            api_key_name = "ANTHROPIC_API_KEY" if self.llm_provider == "anthropic" else "OPENAI_API_KEY"
            print(f"\n❌ FEHLER: {api_key_name} fehlt in der .env-Datei!")
            print(f"Bitte füge deinen API-Key in die .env-Datei ein.")
            return None

        try:
            if self.llm_provider == "anthropic":
                from langchain_anthropic import ChatAnthropic
                model = os.getenv("TASK_MODEL", "claude-3-5-sonnet-latest")
                return ChatAnthropic(
                    api_key=self.api_key,
                    model=model,
                    temperature=float(os.getenv("TEMPERATURE", "0.7"))
                )
            else:
                from langchain_openai import ChatOpenAI
                model = os.getenv("TASK_MODEL", "gpt-4")
                return ChatOpenAI(
                    api_key=self.api_key,
                    model=model,
                    temperature=float(os.getenv("TEMPERATURE", "0.7"))
                )
        except ImportError as e:
            print(f"\n⚠️ Warnung: LLM-Provider nicht verfügbar ({e})")
            print("Installiere: pip install langchain-anthropic oder langchain-openai")
            return None
        except Exception as e:
            print(f"\n⚠️ Fehler bei LLM-Initialisierung: {e}")
            return None

    def _is_asana_command(self, input_data: str) -> bool:
        """
        Prüft ob die Eingabe ein Asana-Befehl ist

        Args:
            input_data: Nutzer-Eingabe

        Returns:
            True wenn Asana-Befehl erkannt wurde
        """
        import re

        asana_keywords = [
            r'\b(erstelle|lege|mach|create)\s+(eine\s+)?(aufgabe|task|asana)',
            r'\bneu(e|er)\s+(aufgabe|task)',
            r'\basana\s+(aufgabe|task)',
            r'\baufgabe\s+(erstellen|anlegen|hinzufügen)',
        ]

        input_lower = input_data.lower()

        for pattern in asana_keywords:
            if re.search(pattern, input_lower):
                return True

        return False

    def _handle_asana_task_creation(self, input_data: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Verarbeitet Asana-Aufgaben-Erstellung mit interaktiver Rückfrage-Logik

        Args:
            input_data: Nutzer-Eingabe
            context: Kontext (z.B. project_gid wenn bereits geklärt)

        Returns:
            Dict mit Ergebnis oder Rückfragen
        """
        print(f"[{self.name}] Erkenne Asana-Befehl, verarbeite mit AsanaAgent...")

        if not self.asana_agent:
            return {
                "agent": self.name,
                "task": input_data,
                "execution": "Asana nicht verfügbar",
                "status": "error",
                "output": "❌ Asana-Agent ist nicht verfügbar. Bitte konfigurieren Sie Asana."
            }

        if not self.asana_agent.is_connected():
            return {
                "agent": self.name,
                "task": input_data,
                "execution": "Asana nicht verbunden",
                "status": "error",
                "output": "❌ Asana-Verbindung fehlgeschlagen. Bitte prüfen Sie Ihr ASANA_ACCESS_TOKEN."
            }

        # Hole project_gid aus Kontext wenn vorhanden
        project_gid = context.get('project_gid') if context else None

        # Versuche Smart-Erstellung
        result = self.asana_agent.create_task_smart(
            user_input=input_data,
            notes="",
            project_gid=project_gid
        )

        # Wenn Rückfragen nötig sind
        if result.get('needs_user_input'):
            missing_info = result.get('missing_info', [])
            parsed_data = result.get('parsed_data', {})

            # Erstelle Rückfrage-Text
            if 'project' in missing_info:
                # Lade Projekte
                projects = self.asana_agent.list_projects()

                if projects:
                    output = f"**📋 In welches Projekt soll ich die Aufgabe '{parsed_data['title']}' erstellen?**\n\n"
                    output += "Verfügbare Projekte:\n"
                    for i, project in enumerate(projects, 1):
                        output += f"{i}. {project['name']}\n"

                    output += "\n_Bitte geben Sie die Nummer oder den Namen des Projekts an._"

                    return {
                        "agent": self.name,
                        "task": input_data,
                        "execution": "Warte auf Nutzer-Eingabe",
                        "status": "needs_input",
                        "output": output,
                        "needs_user_input": True,
                        "missing_info": missing_info,
                        "parsed_data": parsed_data,
                        "projects": projects
                    }
                else:
                    return {
                        "agent": self.name,
                        "task": input_data,
                        "execution": "Keine Projekte gefunden",
                        "status": "error",
                        "output": "❌ Keine Asana-Projekte gefunden. Bitte erstellen Sie zuerst ein Projekt in Asana."
                    }

        # Aufgabe erfolgreich erstellt
        if result.get('success'):
            task_name = result.get('task_name')
            task_gid = result.get('task_gid')
            permalink = result.get('permalink_url')
            parsed_data = result.get('parsed_data', {})
            due_on = parsed_data.get('due_on')
            assignee_name = parsed_data.get('assignee_name')

            # Formatierte Bestätigung mit Link (REGEL 5)
            output = f"✅ **Asana-Aufgabe erfolgreich erstellt!**\n\n"
            output += f"**Titel:** {task_name}\n"

            if due_on:
                output += f"**Fällig:** {due_on}\n"

            # Zeige korrekten Assignee-Namen
            if assignee_name:
                output += f"**Zugewiesen:** {assignee_name}\n\n"
            else:
                output += f"**Zugewiesen:** Dir (aktueller Nutzer)\n\n"

            # WICHTIG: Immer direkten Link anzeigen (REGEL 5)
            if permalink:
                output += f"🔗 **[Aufgabe in Asana öffnen]({permalink})**\n\n"
                output += f"_Direktlink: {permalink}_"
            else:
                output += f"_Task-ID: {task_gid}_"

            return {
                "agent": self.name,
                "task": input_data,
                "execution": "Asana-Aufgabe erstellt",
                "status": "success",
                "output": output,
                "asana_task": result
            }
        else:
            # Fehler bei Erstellung - DETAILLIERTES ERROR-LOGGING
            error = result.get('error', 'Unbekannter Fehler')
            print(f"[{self.name}] ❌ Asana-Fehler: {error}")

            # Gebe klare Fehlermeldung zurück
            output = f"❌ **Fehler beim Erstellen der Asana-Aufgabe:**\n\n"
            output += f"**Fehlerdetails:** {error}\n\n"

            # Hilfreiche Tipps je nach Fehlertyp
            if "project" in error.lower():
                output += "_Tipp: Bitte wählen Sie ein gültiges Projekt aus._"
            elif "assignee" in error.lower():
                output += "_Tipp: Der angegebene Nutzer wurde nicht gefunden._"
            elif "workspace" in error.lower():
                output += "_Tipp: Prüfen Sie Ihre Asana-Workspace-Konfiguration._"

            return {
                "agent": self.name,
                "task": input_data,
                "execution": "Fehler bei Asana-Erstellung",
                "status": "error",
                "output": output,
                "error_details": error
            }

    def process(self, input_data: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Führt eine Aufgabe basierend auf der Eingabe aus

        Args:
            input_data: Die auszuführende Aufgabe
            context: Kontext von vorherigen Agenten (z.B. Research-Ergebnisse, project_gid)

        Returns:
            Dict mit Ausführungsergebnissen
        """
        print(f"\n[{self.name}] Führe Aufgabe aus...")
        print(f"[{self.name}] Provider: {self.llm_provider}")

        # WICHTIG: Prüfe zuerst ob es ein Asana-Befehl ist
        if self._is_asana_command(input_data):
            return self._handle_asana_task_creation(input_data, context)

        # Prüfe auf Kontext von anderen Agenten
        research_data = None
        user_context = None
        memory_context = None

        if context:
            if "findings" in context:
                research_data = context["findings"]
                print(f"[{self.name}] Nutze Kontext vom ResearchAgent")

            if "user_context" in context:
                user_context = context["user_context"]
                print(f"[{self.name}] Nutze Nutzer-Kontext aus Gedächtnis")

            if "memory" in context:
                memory_context = context["memory"]

        if not self.llm:
            return {
                "agent": self.name,
                "task": input_data,
                "execution": "LLM nicht verfügbar",
                "used_context": research_data is not None,
                "status": "error",
                "output": "LLM nicht verfügbar - bitte API-Key konfigurieren"
            }

        try:
            # Erstelle Task-Prompt
            prompt = self._create_task_prompt(input_data, research_data, user_context)

            print(f"[{self.name}] Rufe LLM auf...")

            # Rufe LLM auf
            from langchain_core.messages import HumanMessage
            response = self.llm.invoke([HumanMessage(content=prompt)])

            output = response.content

            result = {
                "agent": self.name,
                "task": input_data,
                "execution": "Task erfolgreich ausgeführt",
                "used_context": research_data is not None,
                "status": "success",  # Geändert von "completed" zu "success" für Konsistenz
                "output": output
            }

            print(f"[{self.name}] ✓ Aufgabe abgeschlossen (Output-Länge: {len(output)} Zeichen)")

        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            print(f"[{self.name}] ✗ Fehler: {e}")
            print(f"[{self.name}] Traceback:\n{error_trace}")

            result = {
                "agent": self.name,
                "task": input_data,
                "execution": f"Fehler bei der Ausführung",
                "used_context": research_data is not None,
                "status": "error",
                "error": str(e),
                "output": f"Fehler beim Task Agent: {str(e)}\n\nDetails:\n{error_trace}"
            }

        self.add_to_memory(result)
        return result

    def _create_task_prompt(self, task: str, research_context: str = None, user_context: str = None) -> str:
        """Erstellt einen optimierten Prompt für Task-Ausführung"""
        prompt = """Du bist ein Task-Agent in einem Multi-Agenten-System.

Deine Aufgabe: Führe die folgende Aufgabe präzise und hilfreich aus.

SPEZIELLE FÄHIGKEITEN - LISTEN ZÄHLEN UND AUFBEREITEN:
Wenn der Nutzer nach einer Anzahl fragt oder eine Liste durchzählen will:
1. IDENTIFIZIERE Listen im Text:
   - Namen in aufeinanderfolgenden Zeilen
   - Bullet-Points oder nummerierte Listen
   - Tabellen mit Einträgen
   - Absätze mit mehreren Namen

2. ZÄHLE die Einträge:
   - Zähle jeden eindeutigen Eintrag/Namen einmal
   - Ignoriere Überschriften und Duplikate
   - Gib die GENAUE Anzahl an

3. FORMATIERE die Ausgabe:
   - Nummeriere alle Einträge durch (1., 2., 3., ...)
   - Gib am Ende die GESAMTZAHL an
   - Strukturiere übersichtlich

BEISPIEL:
Wenn der Text enthält:
  Dr. Max Müller
  Anna Schmidt
  Prof. Peter Weber

Dann antworte:
  "Gefunden: 3 Personen

  1. Dr. Max Müller
  2. Anna Schmidt
  3. Prof. Peter Weber

  GESAMT: 3 Personen"
"""

        # Füge Nutzer-Kontext hinzu falls vorhanden
        if user_context:
            prompt += f"""
{user_context}

---

"""

        prompt += f"""
Aufgabe: {task}
"""

        if research_context:
            # Prüfe ob es sich um lokale Dokumenteninhalte handelt
            if "LOKALER DOKUMENTEN-INHALT" in research_context:
                prompt += f"""
Recherche-Kontext (vom ResearchAgent):
{research_context}

⚠️ WICHTIG: Die oben stehenden Informationen stammen aus den lokalen Dokumenten des Nutzers.
Du hast bereits VOLLSTÄNDIGEN ZUGRIFF auf diese Daten - sie sind direkt hier im Kontext enthalten.
Du musst NICHT auf Dateien zugreifen oder nach weiteren Informationen suchen.
Nutze diese bereits extrahierten Informationen, um die Aufgabe zu erfüllen.
"""
            else:
                prompt += f"""
Recherche-Kontext (vom ResearchAgent):
{research_context}

Nutze diese Informationen, um die Aufgabe besser zu erfüllen.
"""

        prompt += """
Bitte liefere eine klare, strukturierte und hilfreiche Antwort."""

        return prompt

    def execute_action(self, action: str) -> str:
        """Führt eine spezifische Aktion aus"""
        return f"Aktion ausgeführt: {action}"

    def generate_content(self, prompt: str, context: Dict[str, Any] = None) -> str:
        """Generiert Content basierend auf einem Prompt"""
        return f"Generierter Content für: {prompt}"
