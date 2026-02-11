"""
Asana Tool für Task-Management
"""

import os
from typing import Dict, Any, Optional
from datetime import datetime, timedelta


class AsanaTool:
    """Tool für Asana Task-Management Operationen"""

    def __init__(self):
        """Initialisiert das Asana Tool"""
        from agents.asana_agent import AsanaAgent

        self.asana_agent = AsanaAgent()
        self.is_configured = self.asana_agent.is_connected()

        if not self.is_configured:
            print("[AsanaTool] ⚠️ Asana nicht konfiguriert - Token fehlt oder ungültig")
        else:
            print("[AsanaTool] ✓ Asana Tool initialisiert und verbunden")

    def get_asana_tasks(self, days_ahead: int = 7, limit: int = 20) -> str:
        """
        Holt anstehende Asana-Aufgaben

        Args:
            days_ahead: Anzahl Tage in die Zukunft (Standard: 7)
            limit: Maximale Anzahl Aufgaben (Standard: 20)

        Returns:
            Formatierte Liste von Aufgaben
        """
        if not self.is_configured:
            return """❌ Asana ist nicht konfiguriert.

Um Asana zu nutzen, fügen Sie Ihren Personal Access Token in die .env-Datei ein:
ASANA_ACCESS_TOKEN=your_token_here

Sie können einen Token hier erstellen:
https://app.asana.com/0/my-apps"""

        try:
            tasks = self.asana_agent.get_upcoming_tasks(days=days_ahead, limit=limit)

            if not tasks:
                return f"Keine Aufgaben in den nächsten {days_ahead} Tagen gefunden."

            # Formatiere Aufgaben
            output = f"**Anstehende Aufgaben (nächste {days_ahead} Tage):**\n\n"

            for task in tasks:
                name = task.get('name', 'Unbenannt')
                due_on = task.get('due_on', 'Kein Datum')
                projects = ', '.join(task.get('projects', []))
                notes = task.get('notes', '')

                output += f"**• {name}**\n"

                if due_on != 'Kein Datum':
                    # Parse und formatiere Datum
                    try:
                        due_date = datetime.strptime(due_on, '%Y-%m-%d')
                        days_until = (due_date - datetime.now()).days

                        if days_until == 0:
                            output += f"   🔴 **Heute fällig!**\n"
                        elif days_until == 1:
                            output += f"   🟡 Morgen fällig\n"
                        elif days_until < 0:
                            output += f"   ⚠️ **Überfällig seit {abs(days_until)} Tag(en)!**\n"
                        else:
                            output += f"   📅 Fällig in {days_until} Tag(en) ({due_on})\n"
                    except:
                        output += f"   📅 Fällig: {due_on}\n"

                if projects:
                    output += f"   📁 Projekt: {projects}\n"

                if notes:
                    # Zeige erste 100 Zeichen der Notizen
                    notes_preview = notes[:100] + '...' if len(notes) > 100 else notes
                    output += f"   📝 {notes_preview}\n"

                output += "\n"

            return output

        except Exception as e:
            return f"❌ Fehler beim Abrufen der Aufgaben: {str(e)}"

    def create_asana_task(self, title: str, description: str = "", due_date: Optional[str] = None) -> str:
        """
        Erstellt eine neue Asana-Aufgabe

        Args:
            title: Titel der Aufgabe
            description: Beschreibung/Notizen
            due_date: Fälligkeitsdatum im Format YYYY-MM-DD (optional)

        Returns:
            Bestätigung oder Fehlermeldung
        """
        if not self.is_configured:
            return """❌ Asana ist nicht konfiguriert.

Um Asana zu nutzen, fügen Sie Ihren Personal Access Token in die .env-Datei ein:
ASANA_ACCESS_TOKEN=your_token_here

Sie können einen Token hier erstellen:
https://app.asana.com/0/my-apps"""

        if not title or not title.strip():
            return "❌ Fehler: Titel der Aufgabe ist erforderlich!"

        try:
            result = self.asana_agent.create_task(
                name=title.strip(),
                notes=description,
                due_on=due_date
            )

            if result.get('success'):
                output = f"✅ **Asana-Aufgabe erstellt:**\n\n"
                output += f"**Titel:** {result.get('task_name')}\n"

                if description:
                    output += f"**Beschreibung:** {description[:100]}{'...' if len(description) > 100 else ''}\n"

                if due_date:
                    output += f"**Fällig am:** {due_date}\n"

                if result.get('permalink_url'):
                    output += f"\n[In Asana öffnen]({result['permalink_url']})"

                return output
            else:
                return f"❌ Fehler beim Erstellen der Aufgabe: {result.get('error', 'Unbekannter Fehler')}"

        except Exception as e:
            return f"❌ Fehler beim Erstellen der Aufgabe: {str(e)}"

    def invoke(self, action: str, **kwargs) -> str:
        """
        LangChain-kompatible invoke-Methode

        Args:
            action: Aktion ('get_tasks' oder 'create_task')
            **kwargs: Zusätzliche Parameter

        Returns:
            Ergebnis der Aktion
        """
        if action == 'get_tasks':
            days = kwargs.get('days', kwargs.get('days_ahead', 7))
            limit = kwargs.get('limit', 20)
            return self.get_asana_tasks(days_ahead=int(days), limit=int(limit))

        elif action == 'create_task':
            title = kwargs.get('title', kwargs.get('name', ''))
            description = kwargs.get('description', kwargs.get('notes', ''))
            due_date = kwargs.get('due_date', kwargs.get('due_on', None))
            return self.create_asana_task(title=title, description=description, due_date=due_date)

        else:
            return f"❌ Unbekannte Aktion: {action}. Verfügbar: 'get_tasks', 'create_task'"
