"""
Interactive Asana Tool mit Rückfrage-Logik
"""

from typing import Dict, Any, Optional
from agents.asana_agent import AsanaAgent


class InteractiveAsanaTool:
    """
    Tool für interaktive Asana-Aufgaben-Erstellung mit Rückfragen
    """

    def __init__(self, asana_agent: AsanaAgent):
        """
        Initialisiert das Interactive Asana Tool

        Args:
            asana_agent: Instance des AsanaAgent
        """
        self.asana_agent = asana_agent

    def create_task_interactive(self, user_input: str, notes: str = "",
                                project_gid: Optional[str] = None) -> Dict[str, Any]:
        """
        Erstellt eine Aufgabe mit intelligenten Rückfragen

        Diese Methode:
        1. Versucht die Aufgabe zu erstellen
        2. Wenn Infos fehlen, gibt sie Rückfragen zurück
        3. Der Caller muss dann die fehlenden Infos nachliefern

        Args:
            user_input: Nutzer-Eingabe
            notes: Zusätzliche Beschreibung
            project_gid: Projekt-GID (optional)

        Returns:
            Dictionary mit:
            - success: True/False
            - needs_user_input: True wenn Rückfragen nötig
            - questions: Liste von Rückfragen
            - parsed_data: Erkannte Daten
            - task_gid, task_name, permalink_url (bei Erfolg)
        """
        # Versuche Smart-Erstellung
        result = self.asana_agent.create_task_smart(
            user_input=user_input,
            notes=notes,
            project_gid=project_gid
        )

        # Wenn Informationen fehlen, erstelle Rückfragen
        if result.get('needs_user_input'):
            missing_info = result.get('missing_info', [])
            parsed_data = result.get('parsed_data', {})

            questions = []

            # Projekt fehlt
            if 'project' in missing_info:
                projects = self.asana_agent.list_projects()
                if projects:
                    project_names = [p['name'] for p in projects[:20]]  # Erste 20 Projekte
                    questions.append({
                        'type': 'project',
                        'question': 'In welches Projekt soll die Aufgabe erstellt werden?',
                        'options': project_names,
                        'project_dict': {p['name']: p['gid'] for p in projects[:20]}
                    })
                else:
                    questions.append({
                        'type': 'project',
                        'question': 'In welches Projekt soll die Aufgabe erstellt werden?',
                        'options': [],
                        'message': 'Keine Projekte gefunden. Aufgabe wird ohne Projekt erstellt.'
                    })

            # Fälligkeit fehlt (optional - nur fragen wenn sinnvoll)
            if not parsed_data.get('due_on'):
                questions.append({
                    'type': 'due_date',
                    'question': 'Soll ein Fälligkeitsdatum gesetzt werden?',
                    'options': ['Heute', 'Morgen', 'Diese Woche', 'Kein Datum'],
                    'optional': True
                })

            # Assignee fehlt (optional)
            if not parsed_data.get('assignee'):
                questions.append({
                    'type': 'assignee',
                    'question': 'Soll die Aufgabe Ihnen zugewiesen werden?',
                    'options': ['Ja, mir zuweisen', 'Nein, nicht zuweisen'],
                    'optional': True
                })

            return {
                'success': False,
                'needs_user_input': True,
                'questions': questions,
                'parsed_data': parsed_data
            }

        # Erfolgreich erstellt
        return result

    def format_questions_for_user(self, questions: list) -> str:
        """
        Formatiert Rückfragen für die Ausgabe an den Nutzer

        Args:
            questions: Liste von Fragen-Dictionaries

        Returns:
            Formatierter String mit Rückfragen
        """
        output = "⚠️ **Bitte zusätzliche Informationen angeben:**\n\n"

        for i, q in enumerate(questions, 1):
            output += f"**{i}. {q['question']}**\n"

            if q.get('options'):
                for j, option in enumerate(q['options'][:10], 1):  # Max 10 Optionen
                    output += f"   {j}. {option}\n"

            if q.get('optional'):
                output += "   _(Optional - kann übersprungen werden)_\n"

            if q.get('message'):
                output += f"   ℹ️ {q['message']}\n"

            output += "\n"

        return output

    def complete_task_creation(self, parsed_data: Dict[str, Any],
                               user_answers: Dict[str, Any]) -> Dict[str, Any]:
        """
        Vervollständigt die Aufgaben-Erstellung mit Nutzer-Antworten

        Args:
            parsed_data: Bereits geparste Daten
            user_answers: Antworten des Nutzers auf Rückfragen

        Returns:
            Dictionary mit Ergebnis der Aufgaben-Erstellung
        """
        # Extrahiere Daten
        title = parsed_data.get('title')
        due_on = parsed_data.get('due_on')
        assignee = parsed_data.get('assignee')
        project_gid = parsed_data.get('project_gid')

        # Ergänze mit Nutzer-Antworten
        if 'project' in user_answers:
            project_name = user_answers['project']
            # Hole project_gid from project_dict
            if 'project_dict' in user_answers:
                project_gid = user_answers['project_dict'].get(project_name)

        if 'due_date' in user_answers:
            due_choice = user_answers['due_date']
            if due_choice != 'Kein Datum':
                due_on = self.asana_agent.parse_relative_date(due_choice)

        if 'assignee' in user_answers:
            assignee_choice = user_answers['assignee']
            if 'mir zuweisen' in assignee_choice.lower():
                assignee = 'me'

        # Erstelle Aufgabe
        return self.asana_agent.create_task(
            name=title,
            notes=user_answers.get('notes', ''),
            due_on=due_on,
            project_gid=project_gid,
            assignee_gid=assignee
        )
