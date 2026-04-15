"""
Asana Agent für Task-Management und Projektorganisation
"""

import os
import asana
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from .base_agent import BaseAgent


class AsanaAgent(BaseAgent):
    """Agent für Asana Task-Management"""

    def __init__(self, api_key: str = None):
        super().__init__("AsanaAgent")

        # Asana API-Key laden
        self.api_key = api_key or os.getenv("ASANA_ACCESS_TOKEN")

        # Asana Client initialisieren
        self.client = None
        self.workspace_gid = None
        self.default_project_gid = None
        self.tasks_api = None
        self.workspaces_api = None
        self.attachments_api = None
        self.sections_api = None

        if self.api_key:
            try:
                print(f"[{self.name}] 🔄 Verbinde mit Asana API...")

                # Neue API-Struktur (Asana SDK 5.x)
                configuration = asana.Configuration()
                configuration.access_token = self.api_key
                self.client = asana.ApiClient(configuration)

                # API-Instanzen erstellen
                self.tasks_api = asana.TasksApi(self.client)
                self.workspaces_api = asana.WorkspacesApi(self.client)
                self.stories_api = asana.StoriesApi(self.client)
                self.projects_api = asana.ProjectsApi(self.client)
                self.users_api = asana.UsersApi(self.client)
                self.attachments_api = asana.AttachmentsApi(self.client)
                self.sections_api = asana.SectionsApi(self.client)

                # Hole aktuelle User-ID für Zuweisungen
                self.current_user_gid = None

                # Hole erstes Workspace - dies ist der echte Verbindungstest
                opts = {'opt_pretty': True}
                workspaces_response = list(self.workspaces_api.get_workspaces(opts))
                if workspaces_response and len(workspaces_response) > 0:
                    self.workspace_gid = workspaces_response[0]['gid']
                    workspace_name = workspaces_response[0].get('name', 'Unbenannt')
                    print(f"[{self.name}] ✅ ERFOLGREICH VERBUNDEN mit Workspace: '{workspace_name}'")
                    print(f"[{self.name}] Workspace GID: {self.workspace_gid}")
                else:
                    print(f"[{self.name}] ❌ VERBINDUNG FEHLGESCHLAGEN - Keine Workspaces gefunden")
                    print(f"[{self.name}] Prüfen Sie Ihr Token und Ihre Berechtigungen")
            except Exception as e:
                print(f"[{self.name}] ❌ VERBINDUNG FEHLGESCHLAGEN")
                print(f"[{self.name}] Fehlerdetails: {e}")
                import traceback
                traceback.print_exc()
                # Setze Client zurück bei Fehler
                self.client = None
                self.workspace_gid = None
        else:
            print(f"[{self.name}] ❌ ASANA_ACCESS_TOKEN nicht in .env gefunden")
            print(f"[{self.name}] Bitte fügen Sie 'ASANA_ACCESS_TOKEN=ihr_token' zur .env hinzu")

    def get_my_tasks(self, limit: int = 20, due_on_or_before: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Holt die eigenen Aufgaben aus Asana

        Args:
            limit: Maximale Anzahl Aufgaben
            due_on_or_before: Filter für Fälligkeit (Format: YYYY-MM-DD)

        Returns:
            Liste von Aufgaben-Dictionaries
        """
        if not self.tasks_api or not self.workspace_gid:
            print(f"[{self.name}] ⚠️ Asana nicht konfiguriert")
            return []

        try:
            # Hole Aufgaben für den aktuellen Nutzer
            # Neue API verwendet opts Dictionary
            opts = {
                'workspace': self.workspace_gid,
                'assignee': 'me',
                'opt_fields': 'name,due_on,due_at,notes,completed,projects.name',
                'completed_since': 'now',  # Nur unvollendete Aufgaben
                'limit': limit
            }

            if due_on_or_before:
                opts['due_on_or_before'] = due_on_or_before

            # Hole Tasks für den aktuellen User im Workspace
            tasks_response = list(self.tasks_api.get_tasks(opts))

            print(f"[{self.name}] ✓ {len(tasks_response)} Aufgaben geladen")

            # Formatiere Aufgaben
            formatted_tasks = []
            for task in tasks_response:
                formatted_task = {
                    'gid': task.get('gid'),
                    'name': task.get('name'),
                    'due_on': task.get('due_on'),
                    'due_at': task.get('due_at'),
                    'notes': task.get('notes', ''),
                    'completed': task.get('completed', False),
                    'projects': [p.get('name', '') for p in task.get('projects', [])]
                }
                formatted_tasks.append(formatted_task)

            return formatted_tasks

        except Exception as e:
            print(f"[{self.name}] ✗ Fehler beim Laden der Aufgaben: {e}")
            import traceback
            traceback.print_exc()
            return []

    def get_upcoming_tasks(self, days: int = 7, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Holt Aufgaben, die in den nächsten X Tagen fällig sind

        Args:
            days: Anzahl Tage in die Zukunft
            limit: Maximale Anzahl Aufgaben

        Returns:
            Liste von Aufgaben-Dictionaries
        """
        due_date = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')
        return self.get_my_tasks(limit=limit, due_on_or_before=due_date)

    def get_workspace_users(self, workspace_gid: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Holt alle Nutzer im Workspace.

        Args:
            workspace_gid: Workspace GID (optional, nutzt default)

        Returns:
            Liste von User-Dicts mit {gid, name, email}
        """
        if not self.users_api:
            print(f"[{self.name}] ⚠️ Users API nicht verfügbar")
            return []

        ws_gid = workspace_gid or self.workspace_gid
        if not ws_gid:
            print(f"[{self.name}] ⚠️ Kein Workspace verfügbar")
            return []

        try:
            opts = {'opt_fields': 'name,email'}
            users = list(self.users_api.get_users_for_workspace(ws_gid, opts))

            user_list = []
            for user in users:
                user_list.append({
                    'gid': user.get('gid'),
                    'name': user.get('name', 'Unbekannt'),
                    'email': user.get('email', '')
                })

            print(f"[{self.name}] ✓ {len(user_list)} Nutzer geladen")
            return user_list

        except Exception as e:
            print(f"[{self.name}] ✗ Fehler beim Laden der Nutzer: {e}")
            return []

    def find_user_by_name(self, name: str, workspace_gid: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Sucht einen Asana-Nutzer nach Namen im Workspace.

        Args:
            name: Name des Nutzers (Vorname, Nachname oder Email)
            workspace_gid: Workspace GID (optional, nutzt default)

        Returns:
            Dict mit user info {gid, name, email} oder None wenn nicht gefunden
        """
        if not self.users_api:
            print(f"[{self.name}] ⚠️ Users API nicht verfügbar")
            return None

        ws_gid = workspace_gid or self.workspace_gid
        if not ws_gid:
            print(f"[{self.name}] ⚠️ Kein Workspace verfügbar")
            return None

        try:
            # Hole alle Nutzer im Workspace
            opts = {'opt_fields': 'name,email'}
            users = list(self.users_api.get_users_for_workspace(ws_gid, opts))

            # Normalisiere Suchname
            search_name = name.lower().strip()

            # Suche nach exakten oder partiellen Übereinstimmungen
            for user in users:
                user_name = user.get('name', '').lower()
                user_email = user.get('email', '').lower()

                # Exakte Übereinstimmung (Name oder Email)
                if search_name == user_name or search_name == user_email:
                    print(f"[{self.name}] ✓ Nutzer gefunden (exakt): {user.get('name')}")
                    return {
                        'gid': user.get('gid'),
                        'name': user.get('name'),
                        'email': user.get('email')
                    }

                # Partielle Übereinstimmung (z.B. nur Vorname)
                if search_name in user_name or search_name in user_email:
                    print(f"[{self.name}] ✓ Nutzer gefunden (partiell): {user.get('name')}")
                    return {
                        'gid': user.get('gid'),
                        'name': user.get('name'),
                        'email': user.get('email')
                    }

            print(f"[{self.name}] ⚠️ Kein Nutzer gefunden für: {name}")
            return None

        except Exception as e:
            print(f"[{self.name}] ✗ Fehler bei Nutzersuche: {e}")
            return None

    def get_current_user(self) -> Optional[str]:
        """
        Holt die GID des aktuell angemeldeten Nutzers

        Returns:
            GID des aktuellen Nutzers oder None bei Fehler
        """
        if self.current_user_gid:
            return self.current_user_gid

        if not self.users_api:
            return None

        try:
            opts = {'opt_fields': 'gid,name,email'}
            user_response = self.users_api.get_user('me', opts)

            if user_response:
                self.current_user_gid = user_response.get('gid')
                print(f"[{self.name}] ✓ Aktueller Nutzer: {user_response.get('name')} (GID: {self.current_user_gid})")
                return self.current_user_gid

        except Exception as e:
            print(f"[{self.name}] ⚠️ Fehler beim Abrufen des aktuellen Nutzers: {e}")
            return None

    def search_user_by_name(self, name: str) -> Optional[Dict[str, str]]:
        """
        Sucht einen Asana-Nutzer anhand des Namens im aktuellen Workspace

        Args:
            name: Name oder Teil des Namens (z.B. "Max", "Müller", "Max Mustermann")

        Returns:
            Dictionary mit 'gid' und 'name' des Nutzers oder None wenn nicht gefunden
        """
        print(f"[{self.name}] 🔍 Suche Nutzer: '{name}'")

        if not self.users_api or not self.workspace_gid:
            print(f"[{self.name}] ⚠️ Nutzersuche nicht möglich (API nicht initialisiert)")
            return None

        try:
            # Hole alle Nutzer im Workspace
            opts = {
                'workspace': self.workspace_gid,
                'opt_fields': 'gid,name,email'
            }

            print(f"[{self.name}]   → Lade Nutzer aus Workspace {self.workspace_gid}...")
            users = self.users_api.get_users_for_workspace(self.workspace_gid, opts)

            name_lower = name.lower().strip()
            matched_users = []

            for user in users:
                user_name = user.get('name', '').lower()
                user_email = user.get('email', '').lower()

                # Prüfe ob Name im Nutzernamen oder Email vorkommt
                if name_lower in user_name or name_lower in user_email:
                    matched_users.append({
                        'gid': user.get('gid'),
                        'name': user.get('name'),
                        'email': user.get('email')
                    })
                    print(f"[{self.name}]     ✓ Match gefunden: {user.get('name')} ({user.get('email')})")

            if not matched_users:
                print(f"[{self.name}]   ❌ Kein Nutzer gefunden für: '{name}'")
                return None

            if len(matched_users) > 1:
                print(f"[{self.name}]   ⚠️ Mehrere Nutzer gefunden ({len(matched_users)}), verwende ersten Treffer")

            best_match = matched_users[0]
            print(f"[{self.name}]   ✅ Verwende: {best_match['name']} (GID: {best_match['gid']})")

            return best_match

        except Exception as e:
            print(f"[{self.name}] ❌ Fehler bei Nutzersuche: {e}")
            import traceback
            traceback.print_exc()
            return None

    def parse_relative_date(self, date_string: str) -> Optional[str]:
        """
        Wandelt relative Zeitangaben in YYYY-MM-DD Format um

        Args:
            date_string: Relative Zeitangabe wie "heute", "morgen", "nächsten Freitag"

        Returns:
            Datum im Format YYYY-MM-DD oder None wenn nicht erkannt
        """
        from datetime import datetime, timedelta
        import re

        date_string_lower = date_string.lower().strip()
        today = datetime.now()

        # Heute
        if 'heute' in date_string_lower:
            return today.strftime('%Y-%m-%d')

        # Morgen
        if 'morgen' in date_string_lower:
            return (today + timedelta(days=1)).strftime('%Y-%m-%d')

        # Übermorgen
        if 'übermorgen' in date_string_lower or 'uebermorgen' in date_string_lower:
            return (today + timedelta(days=2)).strftime('%Y-%m-%d')

        # Diese Woche / Nächste Woche
        if 'diese woche' in date_string_lower:
            # Ende dieser Woche (Freitag)
            days_until_friday = (4 - today.weekday()) % 7
            return (today + timedelta(days=days_until_friday)).strftime('%Y-%m-%d')

        if 'nächste woche' in date_string_lower or 'naechste woche' in date_string_lower:
            days_until_next_monday = (7 - today.weekday()) % 7 + 7
            return (today + timedelta(days=days_until_next_monday)).strftime('%Y-%m-%d')

        # Wochentage
        weekdays = {
            'montag': 0, 'dienstag': 1, 'mittwoch': 2,
            'donnerstag': 3, 'freitag': 4, 'samstag': 5, 'sonntag': 6
        }

        for weekday_name, weekday_num in weekdays.items():
            if weekday_name in date_string_lower:
                # Berechne nächsten Wochentag
                days_ahead = (weekday_num - today.weekday()) % 7
                if days_ahead == 0:
                    # Heute ist der Wochentag - nimm nächste Woche
                    days_ahead = 7
                return (today + timedelta(days=days_ahead)).strftime('%Y-%m-%d')

        # In X Tagen
        match = re.search(r'in (\d+) tag', date_string_lower)
        if match:
            days = int(match.group(1))
            return (today + timedelta(days=days)).strftime('%Y-%m-%d')

        # Direktes Datum (DD.MM.YYYY oder YYYY-MM-DD)
        date_patterns = [
            r'(\d{1,2})\.(\d{1,2})\.(\d{4})',  # DD.MM.YYYY
            r'(\d{4})-(\d{1,2})-(\d{1,2})',     # YYYY-MM-DD
        ]

        for pattern in date_patterns:
            match = re.search(pattern, date_string)
            if match:
                if '.' in pattern:
                    # DD.MM.YYYY
                    day, month, year = match.groups()
                    try:
                        date_obj = datetime(int(year), int(month), int(day))
                        return date_obj.strftime('%Y-%m-%d')
                    except:
                        pass
                else:
                    # YYYY-MM-DD (bereits im richtigen Format)
                    return match.group(0)

        return None

    def extract_assignee_from_input(self, user_input: str) -> Optional[str]:
        """
        Extrahiert den Assignee-Namen aus dem User-Input

        Erkennt Muster wie:
        - "Weise die Aufgabe [Name] zu"
        - "zuweisen an [Name]"
        - "für [Name]"
        - "assign to [Name]"

        Args:
            user_input: Komplette Nutzer-Eingabe

        Returns:
            Extrahierter Name oder None
        """
        import re

        user_input_lower = user_input.lower()

        # Muster für Assignee-Erkennung
        patterns = [
            r'weise\s+(?:die\s+)?(?:aufgabe\s+)?(?:an\s+)?([a-zäöüß\s]+?)\s+zu',
            r'zuweisen\s+an\s+([a-zäöüß\s]+?)(?:\s|,|$)',
            r'assign\s+to\s+([a-z\s]+?)(?:\s|,|$)',
            r'für\s+([a-zäöüß\s]+?)(?:\s|,|$)',
        ]

        for pattern in patterns:
            match = re.search(pattern, user_input_lower, re.IGNORECASE)
            if match:
                name = match.group(1).strip()
                # Filtere generische Begriffe
                if name not in ['mich', 'mir', 'me', 'myself']:
                    print(f"[{self.name}] 🎯 Assignee erkannt: '{name}'")
                    return name

        return None

    def parse_task_title_from_input(self, user_input: str) -> str:
        """
        Extrahiert den Aufgabentitel aus der Nutzer-Eingabe

        Logik:
        1. Wenn Doppelpunkt vorhanden → alles nach dem Doppelpunkt
        2. Sonst → Entferne Befehlswörter und nimm den Rest
        3. Entferne Zusatz-Infos wie "fällig morgen", "mir zuweisen"

        Args:
            user_input: Komplette Nutzer-Eingabe

        Returns:
            Bereinigter Aufgabentitel
        """
        import re

        # Prüfe auf Doppelpunkt
        if ':' in user_input:
            # Nimm alles nach dem ersten Doppelpunkt
            title = user_input.split(':', 1)[1].strip()
        else:
            # Entferne Befehlswörter
            # Liste von Befehlswörtern die entfernt werden sollen
            command_patterns = [
                r'^erstelle\s+(eine\s+)?(aufgabe|task)\s+',
                r'^neue\s+(aufgabe|task)\s+',
                r'^lege\s+(eine\s+)?(aufgabe|task)\s+an\s+',
                r'^mach\s+(eine\s+)?(aufgabe|task)\s+',
                r'^create\s+(a\s+)?task\s+',
            ]

            title = user_input.strip()

            for pattern in command_patterns:
                title = re.sub(pattern, '', title, flags=re.IGNORECASE)

        # Entferne häufige Zusatz-Phrasen
        cleanup_patterns = [
            # Datums-Phrasen
            r',?\s*fällig\s+(heute|morgen|übermorgen|nächste\s+woche|diese\s+woche|\w+tag)',
            r',?\s*bis\s+(heute|morgen|übermorgen|nächste\s+woche)',
            r',?\s*in\s+\d+\s+tag(en)?',
            # Assignee-Phrasen (an mich)
            r',?\s*(mir|mich)\s+zuweisen',
            r',?\s*für\s+(mich|mir)',
            r',?\s*an\s+mich',
            # Assignee-Phrasen (an andere)
            r',?\s*weise\s+(?:die\s+)?(?:aufgabe\s+)?(?:an\s+)?[a-zäöüß\s]+?\s+zu',
            r',?\s*zuweisen\s+an\s+[a-zäöüß\s]+',
            r',?\s*assign\s+to\s+[a-z\s]+',
            r',?\s*für\s+[a-zäöüß]+(?:\s+[a-zäöüß]+)?',
        ]

        for pattern in cleanup_patterns:
            title = re.sub(pattern, '', title, flags=re.IGNORECASE)

        return title.strip()

    def detect_assignee_self(self, user_input: str) -> bool:
        """
        Erkennt ob der Nutzer sich selbst als Verantwortlichen nennt

        Args:
            user_input: Nutzer-Eingabe

        Returns:
            True wenn "mich", "mir", "ich" erwähnt wird
        """
        import re

        patterns = [
            r'\bmich\b',
            r'\bmir\b',
            r'\bich\b',
            r'mir zuweisen',
            r'für mich',
            r'an mich',
        ]

        user_input_lower = user_input.lower()

        for pattern in patterns:
            if re.search(pattern, user_input_lower):
                return True

        return False

    def create_task(self, name: str, notes: str = "", due_on: Optional[str] = None,
                   project_gid: Optional[str] = None, assignee_gid: Optional[str] = None,
                   section_gid: Optional[str] = None) -> Dict[str, Any]:
        """
        Erstellt eine neue Aufgabe in Asana

        Args:
            name: Aufgabentitel
            notes: Beschreibung/Notizen
            due_on: Fälligkeitsdatum (Format: YYYY-MM-DD)
            project_gid: ID des Projekts (optional)
            assignee_gid: GID des Verantwortlichen (optional, "me" für aktuellen Nutzer)
            section_gid: GID des Ziel-Abschnitts (optional, platziert Task direkt in Section)

        Returns:
            Dictionary mit Aufgaben-Informationen oder Fehler
        """
        print(f"[{self.name}] 🔧 DEBUG: create_task aufgerufen")
        print(f"[{self.name}]   → Name: {name}")
        print(f"[{self.name}]   → Notes: {notes[:50]}..." if len(notes) > 50 else f"[{self.name}]   → Notes: {notes}")
        print(f"[{self.name}]   → Due: {due_on}")
        print(f"[{self.name}]   → Project GID: {project_gid}")
        print(f"[{self.name}]   → Assignee GID: {assignee_gid}")

        if not self.tasks_api or not self.workspace_gid:
            error_msg = "Asana nicht konfiguriert (tasks_api oder workspace_gid fehlt)"
            print(f"[{self.name}] ❌ FEHLER: {error_msg}")
            return {
                "success": False,
                "error": error_msg
            }

        try:
            print(f"[{self.name}] 📋 Erstelle task_data Dictionary...")
            task_data = {
                'name': name,
                'notes': notes,
                'workspace': self.workspace_gid
            }

            if due_on:
                print(f"[{self.name}]   ✓ Setze due_on: {due_on}")
                task_data['due_on'] = due_on

            effective_project_gid = project_gid or self.default_project_gid
            if effective_project_gid and section_gid:
                print(f"[{self.name}]   ✓ Setze memberships (project: {effective_project_gid}, section: {section_gid})")
                task_data['memberships'] = [{'project': effective_project_gid, 'section': section_gid}]
            elif effective_project_gid:
                print(f"[{self.name}]   ✓ Setze project: {effective_project_gid}")
                task_data['projects'] = [effective_project_gid]
            else:
                print(f"[{self.name}]   ⚠️  WARNUNG: Kein Projekt angegeben!")

            # Assignee setzen - nur wenn explizit angegeben
            if assignee_gid is not None:
                print(f"[{self.name}]   🎯 Verarbeite Assignee: {assignee_gid}")
                if assignee_gid == "me":
                    # Hole aktuelle User-GID
                    print(f"[{self.name}]     → Hole aktuelle User-GID für 'me'...")
                    current_user = self.get_current_user()
                    if current_user:
                        print(f"[{self.name}]     ✓ User-GID gefunden: {current_user}")
                        task_data['assignee'] = current_user
                    else:
                        print(f"[{self.name}]     ⚠️  WARNUNG: Konnte User-GID nicht abrufen!")
                else:
                    print(f"[{self.name}]     ✓ Verwende direkte GID: {assignee_gid}")
                    task_data['assignee'] = assignee_gid
            else:
                print(f"[{self.name}]   ℹ️  Kein Assignee angegeben - Aufgabe bleibt unzugewiesen")

            print(f"[{self.name}] 🚀 Sende API-Request an Asana...")
            print(f"[{self.name}]   → Task Data: {task_data}")

            # Neue API verwendet create_task mit body-Parameter
            opts = {'opt_pretty': True}
            result = self.tasks_api.create_task({'data': task_data}, opts)

            print(f"[{self.name}] ✅ API-Request erfolgreich!")
            print(f"[{self.name}]   → Result GID: {result.get('gid')}")
            print(f"[{self.name}]   → Permalink: {result.get('permalink_url', 'N/A')}")

            assignee_info = f" (Zugewiesen an: {'Mich' if assignee_gid == 'me' else 'Nutzer'})" if assignee_gid else ""
            print(f"[{self.name}] ✓ Aufgabe erstellt: {name}{assignee_info}")

            return {
                "success": True,
                "task_gid": result.get('gid'),
                "task_name": result.get('name'),
                "permalink_url": result.get('permalink_url', ''),
                "assignee": assignee_gid
            }

        except Exception as e:
            print(f"[{self.name}] ❌ EXCEPTION beim Erstellen der Aufgabe!")
            print(f"[{self.name}]   → Exception Type: {type(e).__name__}")
            print(f"[{self.name}]   → Exception Message: {str(e)}")

            # Detailliertes Error-Logging
            import traceback
            print(f"[{self.name}] 📋 FULL TRACEBACK:")
            traceback.print_exc()

            # Zusätzliche API-Error-Details (falls vorhanden)
            if hasattr(e, 'status'):
                print(f"[{self.name}]   → HTTP Status: {e.status}")
            if hasattr(e, 'reason'):
                print(f"[{self.name}]   → Reason: {e.reason}")
            if hasattr(e, 'body'):
                print(f"[{self.name}]   → Body: {e.body}")

            return {
                "success": False,
                "error": f"{type(e).__name__}: {str(e)}"
            }

    def create_task_smart(self, user_input: str, notes: str = "",
                          project_gid: Optional[str] = None) -> Dict[str, Any]:
        """
        Intelligente Aufgaben-Erstellung mit automatischem Parsing und strikten Regeln

        STRIKTE REGELN:
        1. Pflicht-Assignee: Jede Aufgabe MUSS einen Assignee haben (Default: "me")
        2. Titel-Trennung: Extrahiert sauberen Titel (mit Doppelpunkt-Logik)
        3. Zeit-Intelligenz: Wandelt relative Zeitangaben in YYYY-MM-DD
        4. Projekt-Pflicht: Fragt nach wenn nicht angegeben
        5. Keine Fantasie-Daten: Nur echte erkannte Werte verwenden

        Args:
            user_input: Komplette Nutzer-Eingabe
            notes: Zusätzliche Beschreibung (optional)
            project_gid: Projekt-GID (optional)

        Returns:
            Dictionary mit:
            - success: True/False
            - task_gid, task_name, permalink_url (bei Erfolg)
            - missing_info: Liste fehlender Informationen (bei Fehler)
            - parsed_data: Erkannte Daten
        """
        # Parse Titel
        title = self.parse_task_title_from_input(user_input)

        if not title or len(title.strip()) < 3:
            return {
                "success": False,
                "error": "Kein gültiger Aufgabentitel erkannt",
                "missing_info": ["title"],
                "parsed_data": {}
            }

        # Parse Datum (optional)
        due_on = self.parse_relative_date(user_input)

        # STRIKTE REGEL: Assignee ist IMMER gesetzt
        # Default: "me" (aktueller Nutzer)
        assignee_gid = "me"  # Default
        assignee_name = None  # Für spätere Ausgabe

        # Prüfe ob explizit "mir"/"mich" genannt wird
        if self.detect_assignee_self(user_input):
            print(f"[{self.name}] 🎯 Assignee: Explizit 'me' erkannt")
            assignee_gid = "me"
        else:
            # Prüfe ob ein anderer Nutzer genannt wird
            extracted_name = self.extract_assignee_from_input(user_input)
            if extracted_name:
                print(f"[{self.name}] 🔍 Suche Nutzer: '{extracted_name}'")
                user_info = self.search_user_by_name(extracted_name)
                if user_info:
                    assignee_gid = user_info['gid']
                    assignee_name = user_info['name']
                    print(f"[{self.name}] ✅ Assignee gefunden: {assignee_name} (GID: {assignee_gid})")
                else:
                    print(f"[{self.name}] ⚠️ Nutzer '{extracted_name}' nicht gefunden, verwende 'me' als Fallback")
                    assignee_gid = "me"
            else:
                # Kein Assignee explizit genannt → Default "me"
                print(f"[{self.name}] 🎯 Assignee: Kein Nutzer genannt, verwende Default 'me'")
                assignee_gid = "me"

        # Sammle erkannte Daten
        parsed_data = {
            "title": title,
            "due_on": due_on,
            "assignee": assignee_gid,  # IMMER gesetzt
            "assignee_name": assignee_name,  # Name falls gesucht
            "project_gid": project_gid
        }

        # Prüfe ob kritische Informationen fehlen
        missing_info = []

        # Titel muss vorhanden sein (bereits geprüft)
        # Projekt ist PFLICHT - muss nachgefragt werden wenn nicht angegeben
        if not project_gid:
            missing_info.append("project")

        # Wenn Informationen fehlen, gib sie zurück ohne zu erstellen
        if missing_info:
            return {
                "success": False,
                "needs_user_input": True,
                "missing_info": missing_info,
                "parsed_data": parsed_data,
                "message": "Bitte zusätzliche Informationen angeben"
            }

        # Erstelle Aufgabe mit PFLICHT-ASSIGNEE
        result = self.create_task(
            name=title,
            notes=notes,
            due_on=due_on,
            project_gid=project_gid,
            assignee_gid=assignee_gid  # IMMER gesetzt (mindestens "me")
        )

        # Füge parsed_data zum Ergebnis hinzu (für TaskAgent Output)
        if result.get('success'):
            result['parsed_data'] = parsed_data

        return result

    def update_task(self, task_gid: str, **kwargs) -> Dict[str, Any]:
        """
        Aktualisiert eine bestehende Aufgabe

        Args:
            task_gid: ID der Aufgabe
            **kwargs: Felder zum Aktualisieren (name, notes, completed, due_on, etc.)

        Returns:
            Dictionary mit Ergebnis
        """
        if not self.tasks_api:
            return {
                "success": False,
                "error": "Asana nicht konfiguriert"
            }

        try:
            # Neue API verwendet update_task mit body-Parameter
            opts = {'opt_pretty': True}
            result = self.tasks_api.update_task({'data': kwargs}, task_gid, opts)

            print(f"[{self.name}] ✓ Aufgabe aktualisiert: {task_gid}")

            return {
                "success": True,
                "task_gid": result.get('gid'),
                "task_name": result.get('name', '')
            }

        except Exception as e:
            print(f"[{self.name}] ✗ Fehler beim Aktualisieren: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e)
            }

    def complete_task(self, task_gid: str) -> Dict[str, Any]:
        """
        Markiert eine Aufgabe als erledigt

        Args:
            task_gid: ID der Aufgabe

        Returns:
            Dictionary mit Ergebnis
        """
        return self.update_task(task_gid, completed=True)

    def get_task_stories(self, task_gid: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Holt die letzten Kommentare/Stories einer Aufgabe

        Args:
            task_gid: ID der Aufgabe
            limit: Maximale Anzahl Kommentare

        Returns:
            Liste von Kommentar-Dictionaries
        """
        if not self.stories_api:
            return []

        try:
            # Hole Stories für die Aufgabe
            opts = {
                'opt_fields': 'text,created_by.name,created_at,type',
                'limit': limit
            }

            stories_response = list(self.stories_api.get_stories_for_task(task_gid, opts))

            # Filtere nur Kommentare (type='comment') und formatiere
            comments = []
            for story in stories_response:
                if story.get('type') == 'comment' and story.get('text'):
                    comment = {
                        'text': story.get('text', ''),
                        'author': story.get('created_by', {}).get('name', 'Unbekannt'),
                        'created_at': story.get('created_at', '')
                    }
                    comments.append(comment)

            return comments[:limit]

        except Exception as e:
            print(f"[{self.name}] ⚠️ Fehler beim Laden der Kommentare: {e}")
            return []

    def list_projects(self, limit: int = None) -> List[Dict[str, Any]]:
        """
        Lädt alle Projekte aus dem Workspace (mit automatischer Pagination)

        Args:
            limit: Maximale Anzahl Projekte (None = alle Projekte laden)

        Returns:
            Liste von Projekt-Dictionaries mit gid und name
        """
        if not self.projects_api or not self.workspace_gid:
            print(f"[{self.name}] ⚠️ Asana nicht konfiguriert für Projekt-Abfrage")
            return []

        try:
            # Hole ALLE Projekte im Workspace (keine Limitierung)
            # Die Asana API liefert automatisch alle Seiten über den Iterator
            opts = {
                'workspace': self.workspace_gid,
                'opt_fields': 'name,archived',
                'limit': 100  # Seiten-Größe (API-intern), aber wir laden alle Seiten
            }

            print(f"[{self.name}] 🔄 Lade alle Projekte aus Workspace...")

            # WICHTIG: Der Generator durchläuft automatisch alle Seiten
            # Asana SDK handhabt Pagination intern
            projects = []
            count = 0

            for project in self.projects_api.get_projects(opts):
                # Nur aktive Projekte
                if not project.get('archived', False):
                    projects.append({
                        'gid': project.get('gid'),
                        'name': project.get('name', 'Unbenannt')
                    })
                    count += 1

                    # Stoppe nur wenn explizites Limit gesetzt
                    if limit and count >= limit:
                        print(f"[{self.name}] ℹ️ Limit von {limit} erreicht, weitere Projekte verfügbar")
                        break

            print(f"[{self.name}] ✓ {len(projects)} aktive Projekte geladen")

            # Sortiere alphabetisch für bessere Übersicht
            projects.sort(key=lambda x: x['name'].lower())

            return projects

        except Exception as e:
            print(f"[{self.name}] ✗ Fehler beim Laden der Projekte: {e}")
            import traceback
            traceback.print_exc()
            return []

    def get_project_tasks(self, project_gid: str, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Lädt alle Aufgaben eines bestimmten Projekts

        Args:
            project_gid: GID des Projekts
            limit: Maximale Anzahl Aufgaben

        Returns:
            Liste von Aufgaben-Dictionaries
        """
        if not self.tasks_api:
            print(f"[{self.name}] ⚠️ Asana nicht konfiguriert")
            return []

        try:
            # Hole alle Aufgaben des Projekts
            opts = {
                'project': project_gid,
                'opt_fields': 'name,due_on,due_at,notes,completed,assignee.name,projects.name',
                'limit': limit
            }

            tasks_response = list(self.tasks_api.get_tasks(opts))

            # Formatiere Aufgaben
            formatted_tasks = []
            for task in tasks_response:
                # Nur unvollendete Aufgaben
                if not task.get('completed', False):
                    # Sicherer Zugriff auf assignee (kann None sein)
                    assignee = task.get('assignee')
                    assignee_name = assignee.get('name', '') if assignee and isinstance(assignee, dict) else ''

                    formatted_task = {
                        'gid': task.get('gid'),
                        'name': task.get('name'),
                        'due_on': task.get('due_on'),
                        'due_at': task.get('due_at'),
                        'notes': task.get('notes', ''),
                        'completed': task.get('completed', False),
                        'projects': [p.get('name', '') for p in task.get('projects', [])],
                        'assignee': assignee_name
                    }
                    formatted_tasks.append(formatted_task)

            print(f"[{self.name}] ✓ {len(formatted_tasks)} aktive Aufgaben aus Projekt geladen")
            return formatted_tasks

        except Exception as e:
            print(f"[{self.name}] ✗ Fehler beim Laden der Projekt-Aufgaben: {e}")
            import traceback
            traceback.print_exc()
            return []

    def create_subtask(
        self,
        parent_task_gid: str,
        name: str,
        notes: str = "",
        due_on: Optional[str] = None,
        assignee_gid: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Erstellt eine Unteraufgabe (Subtask) für eine bestehende Aufgabe.

        Args:
            parent_task_gid: GID der übergeordneten Aufgabe
            name: Titel der Unteraufgabe
            notes: Beschreibung/Notizen
            due_on: Fälligkeitsdatum (Format: YYYY-MM-DD)
            assignee_gid: GID des Verantwortlichen (optional, "me" für aktuellen Nutzer)

        Returns:
            Dictionary mit Aufgaben-Informationen oder Fehler
        """
        if not self.tasks_api:
            return {
                "success": False,
                "error": "Asana nicht konfiguriert"
            }

        try:
            # Erstelle zuerst die Subtask-Daten
            subtask_data = {
                'name': name,
                'notes': notes
            }

            if due_on:
                subtask_data['due_on'] = due_on

            if assignee_gid is not None:
                if assignee_gid == "me":
                    current_user = self.get_current_user()
                    if current_user:
                        subtask_data['assignee'] = current_user
                else:
                    subtask_data['assignee'] = assignee_gid

            # Erstelle Subtask über die API
            opts = {'opt_pretty': True}
            result = self.tasks_api.create_subtask_for_task(
                {'data': subtask_data},
                parent_task_gid,
                opts
            )

            print(f"[{self.name}] ✓ Subtask erstellt: {name} (Parent: {parent_task_gid})")

            return {
                "success": True,
                "task_gid": result.get('gid'),
                "task_name": result.get('name'),
                "parent_gid": parent_task_gid,
                "permalink_url": result.get('permalink_url', '')
            }

        except Exception as e:
            print(f"[{self.name}] ✗ Fehler beim Erstellen der Subtask: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e)
            }

    def attach_file_to_task(
        self,
        task_gid: str,
        file_path: str,
        file_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Hängt eine Datei an eine Asana-Aufgabe an.

        Args:
            task_gid: GID der Aufgabe
            file_path: Pfad zur anzuhängenden Datei
            file_name: Optionaler Name für die Datei (Standard: aus file_path)

        Returns:
            Dictionary mit Attachment-Informationen oder Fehler
        """
        if not self.attachments_api:
            return {
                "success": False,
                "error": "Asana Attachments API nicht verfügbar"
            }

        try:
            from pathlib import Path

            # Debug: Prüfe Typ von file_path
            print(f"[{self.name}] DEBUG: file_path type = {type(file_path)}, value = {file_path}")

            # Stelle sicher, dass file_path ein String/Path ist, kein Tupel
            if isinstance(file_path, tuple):
                return {
                    "success": False,
                    "error": f"file_path ist ein Tupel statt String: {file_path}"
                }

            file_path_obj = Path(file_path)
            if not file_path_obj.exists():
                return {
                    "success": False,
                    "error": f"Datei nicht gefunden: {file_path}"
                }

            # Verwende den angegebenen Namen oder den Dateinamen
            if not file_name:
                file_name = file_path_obj.name

            # Asana SDK v5: 'file' erwartet den Dateipfad als String (SDK öffnet die Datei selbst)
            opts = {
                'parent': task_gid,
                'file': str(file_path_obj),
                'name': file_name,
            }

            print(f"[{self.name}] Uploading attachment: {file_name} to task {task_gid}")
            result = self.attachments_api.create_attachment_for_object(opts)

            print(f"[{self.name}] ✓ Datei angehängt: {file_name} an Task {task_gid}")

            return {
                "success": True,
                "attachment_gid": result.get('gid'),
                "file_name": file_name,
                "task_gid": task_gid,
                "download_url": result.get('download_url', '')
            }

        except Exception as e:
            print(f"[{self.name}] ✗ Fehler beim Anhängen der Datei: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e)
            }

    def get_project_sections(self, project_gid: str) -> List[Dict[str, Any]]:
        """
        Holt alle Abschnitte (Sections) eines Projekts.

        Args:
            project_gid: GID des Projekts

        Returns:
            Liste von Section-Dictionaries mit gid und name
        """
        if not self.sections_api:
            print(f"[{self.name}] ⚠️ Sections API nicht verfügbar")
            return []

        try:
            opts = {
                'project': project_gid,
                'opt_fields': 'name',
                'opt_pretty': True
            }

            sections = list(self.sections_api.get_sections_for_project(project_gid, opts))

            formatted_sections = []
            for section in sections:
                formatted_sections.append({
                    'gid': section.get('gid'),
                    'name': section.get('name', 'Unbenannt')
                })

            print(f"[{self.name}] ✓ {len(formatted_sections)} Sections aus Projekt geladen")
            return formatted_sections

        except Exception as e:
            print(f"[{self.name}] ✗ Fehler beim Laden der Sections: {e}")
            import traceback
            traceback.print_exc()
            return []

    def add_task_to_section(
        self,
        task_gid: str,
        section_gid: str
    ) -> Dict[str, Any]:
        """
        Verschiebt eine Aufgabe in einen bestimmten Abschnitt (Section) eines Projekts.

        Args:
            task_gid: GID der Aufgabe
            section_gid: GID des Ziel-Abschnitts

        Returns:
            Dictionary mit Erfolgs-Status
        """
        if not self.sections_api:
            return {
                "success": False,
                "error": "Sections API nicht verfügbar"
            }

        try:
            opts = {'opt_pretty': True}
            body = {'data': {'task': task_gid}}

            self.sections_api.add_task_for_section(body, section_gid, opts)

            print(f"[{self.name}] ✓ Task {task_gid} zu Section {section_gid} hinzugefügt")

            return {
                "success": True,
                "task_gid": task_gid,
                "section_gid": section_gid
            }

        except Exception as e:
            print(f"[{self.name}] ✗ Fehler beim Verschieben in Section: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e)
            }

    def ensure_section_exists(
        self,
        project_gid: str,
        section_name: str
    ) -> Optional[str]:
        """
        Stellt sicher, dass ein Section mit dem gegebenen Namen existiert.
        Erstellt den Section falls er nicht existiert.

        Args:
            project_gid: GID des Projekts
            section_name: Name des gewünschten Sections

        Returns:
            GID des Sections (existierend oder neu erstellt) oder None bei Fehler
        """
        if not self.sections_api:
            print(f"[{self.name}] ⚠️ Sections API nicht verfügbar")
            return None

        try:
            # Prüfe ob Section bereits existiert
            existing_sections = self.get_project_sections(project_gid)

            for section in existing_sections:
                if section['name'].lower() == section_name.lower():
                    print(f"[{self.name}] ✓ Section '{section_name}' existiert bereits (GID: {section['gid']})")
                    return section['gid']

            # Section existiert nicht - erstelle ihn
            print(f"[{self.name}] 📝 Erstelle neuen Section '{section_name}'...")

            opts = {'opt_pretty': True}
            body = {
                'data': {
                    'name': section_name
                }
            }

            result = self.sections_api.create_section_for_project(body, project_gid, opts)

            section_gid = result.get('gid')
            print(f"[{self.name}] ✅ Section '{section_name}' erstellt (GID: {section_gid})")

            return section_gid

        except Exception as e:
            print(f"[{self.name}] ✗ Fehler bei ensure_section_exists: {e}")
            import traceback
            traceback.print_exc()
            return None

    def get_tasks_from_section(
        self,
        section_gid: str,
        limit: int = 50,
        include_completed: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Holt alle Aufgaben aus einem bestimmten Section.

        Args:
            section_gid: GID des Sections
            limit: Maximale Anzahl Aufgaben
            include_completed: Ob erledigte Aufgaben inkludiert werden sollen

        Returns:
            Liste von Aufgaben-Dictionaries
        """
        if not self.sections_api or not self.tasks_api:
            print(f"[{self.name}] ⚠️ API nicht verfügbar")
            return []

        try:
            print(f"[{self.name}] 🔍 get_tasks_from_section aufgerufen:")
            print(f"[{self.name}]   → Section GID: {section_gid}")
            print(f"[{self.name}]   → Limit: {limit}")
            print(f"[{self.name}]   → Include completed: {include_completed}")

            opts = {
                'section': section_gid,
                'limit': limit,
                'opt_fields': 'name,notes,due_on,completed,assignee.name'
            }

            print(f"[{self.name}]   → API-Call: tasks_api.get_tasks() mit section-Filter...")
            tasks_response = list(self.tasks_api.get_tasks(opts))
            print(f"[{self.name}]   → API-Response: {len(tasks_response)} Task(s) erhalten")

            formatted_tasks = []
            completed_count = 0
            for task in tasks_response:
                # Filtere erledigte Aufgaben falls gewünscht
                if not include_completed and task.get('completed', False):
                    completed_count += 1
                    continue

                assignee = task.get('assignee')
                assignee_name = assignee.get('name', '') if assignee and isinstance(assignee, dict) else ''

                formatted_task = {
                    'gid': task.get('gid'),
                    'name': task.get('name'),
                    'notes': task.get('notes', ''),
                    'due_on': task.get('due_on'),
                    'completed': task.get('completed', False),
                    'assignee': assignee_name
                }
                formatted_tasks.append(formatted_task)
                print(f"[{self.name}]     • {task.get('name')} (completed: {task.get('completed', False)})")

            if completed_count > 0:
                print(f"[{self.name}]   ℹ️ {completed_count} erledigte Task(s) ausgefiltert")

            print(f"[{self.name}] ✓ {len(formatted_tasks)} Aufgaben aus Section geladen (von {len(tasks_response)} total)")
            return formatted_tasks

        except Exception as e:
            print(f"[{self.name}] ✗ Fehler beim Laden der Section-Tasks: {e}")
            import traceback
            traceback.print_exc()
            return []

    def find_protocol_tasks_with_open_items(
        self,
        project_gid: str,
        protocol_section_name: str = "Protokolle"
    ) -> List[Dict[str, Any]]:
        """
        Findet Protokoll-Aufgaben und extrahiert deren offene Unteraufgaben.

        Args:
            project_gid: GID des Projekts
            protocol_section_name: Name des Protokoll-Sections

        Returns:
            Liste von Dicts mit Protokoll-Info und offenen Subtasks
        """
        if not self.tasks_api:
            return []

        try:
            # Hole Section
            sections = self.get_project_sections(project_gid)
            protocol_section_gid = None

            for section in sections:
                if section['name'].lower() == protocol_section_name.lower():
                    protocol_section_gid = section['gid']
                    break

            if not protocol_section_gid:
                print(f"[{self.name}] ℹ️ Kein '{protocol_section_name}'-Section gefunden")
                return []

            # Hole Protokoll-Aufgaben aus dem Section
            protocol_tasks = self.get_tasks_from_section(protocol_section_gid, limit=10, include_completed=False)

            results = []

            for protocol_task in protocol_tasks:
                # Hole Subtasks dieser Protokoll-Aufgabe
                try:
                    opts = {
                        'opt_fields': 'name,completed,assignee.name,due_on'
                    }

                    subtasks = list(self.tasks_api.get_subtasks_for_task(protocol_task['gid'], opts))

                    # Filtere nur offene Subtasks
                    open_subtasks = []
                    for subtask in subtasks:
                        if not subtask.get('completed', False):
                            assignee = subtask.get('assignee')
                            assignee_name = assignee.get('name', '') if assignee and isinstance(assignee, dict) else ''

                            open_subtasks.append({
                                'name': subtask.get('name'),
                                'assignee': assignee_name,
                                'due_on': subtask.get('due_on')
                            })

                    if open_subtasks:
                        results.append({
                            'protocol_name': protocol_task['name'],
                            'protocol_gid': protocol_task['gid'],
                            'open_items': open_subtasks
                        })

                except Exception as e:
                    print(f"[{self.name}] ⚠️ Fehler beim Laden von Subtasks für {protocol_task['name']}: {e}")
                    continue

            print(f"[{self.name}] ✓ {len(results)} Protokoll(e) mit offenen Punkten gefunden")
            return results

        except Exception as e:
            print(f"[{self.name}] ✗ Fehler bei find_protocol_tasks_with_open_items: {e}")
            import traceback
            traceback.print_exc()
            return []

    def is_connected(self) -> bool:
        """
        Prüft ob die Verbindung zu Asana erfolgreich hergestellt wurde

        Returns:
            True wenn verbunden, False sonst
        """
        return self.client is not None and self.workspace_gid is not None

    def format_tasks_for_display(self, tasks: List[Dict[str, Any]]) -> str:
        """
        Formatiert Aufgaben für Anzeige

        Args:
            tasks: Liste von Aufgaben

        Returns:
            Formatierter String
        """
        if not tasks:
            return "Keine Aufgaben gefunden."

        output = []
        for task in tasks:
            name = task.get('name', 'Unbenannt')
            due = task.get('due_on', 'Kein Datum')
            projects = ', '.join(task.get('projects', []))

            line = f"• {name}"
            if due != 'Kein Datum':
                line += f" (fällig: {due})"
            if projects:
                line += f" [{projects}]"

            output.append(line)

        return "\n".join(output)

    def process(self, input_data: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Verarbeitet Asana-bezogene Anfragen

        Args:
            input_data: Die Anfrage
            context: Zusätzlicher Kontext

        Returns:
            Dict mit Ergebnissen
        """
        print(f"\n[{self.name}] Verarbeite Asana-Anfrage...")

        if not self.client:
            return {
                "agent": self.name,
                "request": input_data,
                "result": """Asana ist nicht konfiguriert.

Um Asana zu nutzen, fügen Sie Ihren Access Token in die .env-Datei ein:
ASANA_ACCESS_TOKEN=your_token_here

Sie können einen Personal Access Token hier erstellen:
https://app.asana.com/0/my-apps""",
                "status": "error",
                "tasks": []
            }

        try:
            # Einfache Keyword-basierte Erkennung
            input_lower = input_data.lower()

            # Aufgaben abrufen
            if any(keyword in input_lower for keyword in ['liste', 'aufgaben', 'tasks', 'was steht an', 'to-do', 'todo']):
                # Bestimme Zeitraum
                if 'heute' in input_lower:
                    tasks = self.get_my_tasks(limit=10, due_on_or_before=datetime.now().strftime('%Y-%m-%d'))
                    title = "Heute fällige Aufgaben"
                elif 'woche' in input_lower or 'nächsten 7' in input_lower:
                    tasks = self.get_upcoming_tasks(days=7)
                    title = "Aufgaben der nächsten 7 Tage"
                else:
                    tasks = self.get_upcoming_tasks(days=14, limit=20)
                    title = "Anstehende Aufgaben"

                formatted = self.format_tasks_for_display(tasks)

                result = {
                    "agent": self.name,
                    "request": input_data,
                    "result": f"**{title}:**\n\n{formatted}",
                    "status": "success",
                    "tasks": tasks
                }

            # Aufgabe erstellen
            elif any(keyword in input_lower for keyword in ['erstelle', 'neue aufgabe', 'create task', 'hinzufügen']):
                # Extrahiere Aufgabentitel (sehr einfach)
                # In einer produktiven Version würde man hier ein LLM zur Extraktion nutzen
                task_name = input_data.strip()

                result_data = self.create_task(name=task_name, notes=f"Erstellt von Assistent am {datetime.now().strftime('%Y-%m-%d')}")

                if result_data['success']:
                    result = {
                        "agent": self.name,
                        "request": input_data,
                        "result": f"✓ Aufgabe erstellt: {result_data['task_name']}",
                        "status": "success",
                        "task_created": result_data
                    }
                else:
                    result = {
                        "agent": self.name,
                        "request": input_data,
                        "result": f"❌ Fehler beim Erstellen: {result_data.get('error', 'Unbekannter Fehler')}",
                        "status": "error"
                    }
            else:
                # Standardmäßig anstehende Aufgaben zeigen
                tasks = self.get_upcoming_tasks(days=7)
                formatted = self.format_tasks_for_display(tasks)

                result = {
                    "agent": self.name,
                    "request": input_data,
                    "result": f"**Anstehende Aufgaben:**\n\n{formatted}",
                    "status": "success",
                    "tasks": tasks
                }

            print(f"[{self.name}] ✓ Anfrage verarbeitet")

        except Exception as e:
            print(f"[{self.name}] ✗ Fehler: {e}")
            import traceback
            traceback.print_exc()
            result = {
                "agent": self.name,
                "request": input_data,
                "result": f"Fehler bei der Asana-Verarbeitung: {str(e)}",
                "status": "error"
            }

        self.add_to_memory(result)
        return result
