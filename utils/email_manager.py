"""
Email Manager für Inbox Gatekeeper

Verwaltet E-Mail-Abruf, LLM-Analyse und Asana-Integration
"""

import os
import json
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


class EmailManager:
    """
    Verwaltet E-Mail-Operationen für den Inbox Gatekeeper
    """

    def __init__(self, outlook_tool, asana_agent, config_path: str = "config/mapping_config.json"):
        """
        Initialisiert EmailManager

        Args:
            outlook_tool: OutlookGraphTool Instanz
            asana_agent: AsanaAgent Instanz
            config_path: Pfad zur Mapping-Konfiguration
        """
        self.outlook_tool = outlook_tool
        self.asana_agent = asana_agent
        self.config_path = config_path
        self.config = self._load_config()

        # LLM initialisieren
        self.llm_provider = os.getenv("LLM_PROVIDER", "anthropic")
        self.llm_model = os.getenv("RESEARCH_MODEL", "claude-sonnet-4-5")
        self.llm = self._initialize_llm()

    def _load_config(self) -> Dict[str, Any]:
        """Lädt Mapping-Konfiguration"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                logger.info(f"[EmailManager] ✓ Config geladen: {self.config_path}")
                return config
        except Exception as e:
            logger.error(f"[EmailManager] ❌ Fehler beim Laden der Config: {e}")
            return {
                "people_mappings": {"mappings": []},
                "project_mappings": {"mappings": []},
                "forwarding_rules": {"rules": []},
                "email_categories": ["Sonstiges"]
            }

    def _initialize_llm(self):
        """Initialisiert LLM (analog zu ResearchAgent)"""
        try:
            if self.llm_provider.lower() == "anthropic":
                from langchain_anthropic import ChatAnthropic
                return ChatAnthropic(
                    model=self.llm_model,
                    temperature=0,
                    anthropic_api_key=os.getenv("ANTHROPIC_API_KEY")
                )
            elif self.llm_provider.lower() == "openai":
                from langchain_openai import ChatOpenAI
                return ChatOpenAI(
                    model=self.llm_model,
                    temperature=0,
                    openai_api_key=os.getenv("OPENAI_API_KEY")
                )
            else:
                logger.error(f"[EmailManager] ❌ Unbekannter LLM Provider: {self.llm_provider}")
                return None
        except Exception as e:
            logger.error(f"[EmailManager] ❌ Fehler beim Initialisieren des LLM: {e}")
            return None

    def fetch_unread_emails(self, max_count: int = 20) -> List[Dict[str, Any]]:
        """
        Holt ungelesene E-Mails

        Args:
            max_count: Maximale Anzahl

        Returns:
            Liste von E-Mail-Dicts
        """
        try:
            emails = self.outlook_tool.get_unread_emails(max_results=max_count)
            logger.info(f"[EmailManager] ✓ {len(emails)} ungelesene E-Mails abgerufen")
            return emails
        except Exception as e:
            logger.error(f"[EmailManager] ❌ Fehler beim Abrufen: {e}")
            return []

    def analyze_email_with_cache(self, email: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analysiert E-Mail (mit Cache aus Outlook Categories)

        Args:
            email: E-Mail-Dict im Graph API Format

        Returns:
            Analysis-Dict mit: summary, priority, category, action_items, deadline, sentiment
        """
        # Prüfe erst ob Analyse bereits in Outlook Categories vorhanden
        cached_analysis = self.outlook_tool.get_email_analysis_from_categories(email)

        if cached_analysis:
            logger.info(f"[EmailManager] ✓ Nutze gecachte Analyse aus Outlook Categories")
            return cached_analysis

        # Keine gecachte Analyse - führe LLM-Analyse durch
        logger.info(f"[EmailManager] 🔍 Führe neue LLM-Analyse durch...")
        analysis = self.analyze_email_with_llm(email)

        # Speichere Analyse in Outlook Categories für zukünftige Nutzung
        save_result = self.outlook_tool.save_email_analysis(email['id'], analysis)
        if save_result.get('success'):
            logger.info(f"[EmailManager] ✓ Analyse in Outlook Categories gespeichert")
        else:
            logger.warning(f"[EmailManager] ⚠️ Konnte Analyse nicht speichern: {save_result.get('error')}")

        return analysis

    def analyze_email_with_llm(self, email: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analysiert E-Mail mit LLM

        Args:
            email: E-Mail-Dict im Graph API Format

        Returns:
            Analysis-Dict mit: summary, priority, category, action_items, deadline, sentiment
        """
        if not self.llm:
            logger.warning("[EmailManager] ⚠️ LLM nicht verfügbar, nutze Fallback")
            return self._get_fallback_analysis(email)

        try:
            # Extrahiere Body (bevorzuge vollständigen Body)
            body_content = ""
            if 'body' in email and 'content' in email['body']:
                body_content = email['body']['content']
            elif 'bodyPreview' in email:
                body_content = email['bodyPreview']

            subject = email.get('subject', 'Kein Betreff')
            sender_name = email.get('from', {}).get('emailAddress', {}).get('name', 'Unbekannt')
            sender_email = email.get('from', {}).get('emailAddress', {}).get('address', '')

            # Kategorien aus Config
            categories = self.config.get('email_categories', ['Sonstiges'])
            categories_str = '", "'.join(categories)

            # System Prompt
            system_prompt = f"""Du bist ein intelligenter Email-Analyse-Assistent.
Analysiere die E-Mail und gib ein strukturiertes JSON zurück.

WICHTIG: Antworte NUR mit gültigem JSON, keine zusätzlichen Texte!

JSON-Format:
{{
  "summary": "Kurze Zusammenfassung in 1-2 Sätzen",
  "priority": 1-5 (1=niedrig, 5=kritisch),
  "category": "Eine der folgenden Kategorien: {categories_str}",
  "action_items": ["Handlungspunkt 1", "Handlungspunkt 2", ...],
  "deadline": "YYYY-MM-DD oder null",
  "sentiment": "positiv|neutral|negativ|dringend"
}}

Bewerte Priorität basierend auf:
- 5 = Kritisch (dringend + wichtig, z.B. Vertragsablauf, Eskalation)
- 4 = Dringend (schnelle Reaktion nötig, z.B. Meeting-Anfrage heute)
- 3 = Normal (reguläre Anfrage, kann innerhalb 1-2 Tagen bearbeitet werden)
- 2 = Niedrig (Information, keine Aktion nötig)
- 1 = Sehr niedrig (Newsletter, FYI)
"""

            user_prompt = f"""E-Mail analysieren:

Von: {sender_name} <{sender_email}>
Betreff: {subject}

Body:
{body_content[:2000]}
"""

            # LLM aufrufen
            from langchain_core.messages import SystemMessage, HumanMessage

            response = self.llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt)
            ])

            # Parse JSON
            response_text = response.content.strip()

            # Entferne mögliche Markdown-Code-Blöcke
            if response_text.startswith('```'):
                lines = response_text.split('\n')
                response_text = '\n'.join(lines[1:-1])
                if response_text.startswith('json'):
                    response_text = response_text[4:].strip()

            analysis = json.loads(response_text)

            logger.info(f"[EmailManager] ✓ E-Mail analysiert: Priority={analysis.get('priority')}, Category={analysis.get('category')}")
            return analysis

        except json.JSONDecodeError as e:
            logger.warning(f"[EmailManager] ⚠️ JSON-Parsing-Fehler: {e}, nutze Fallback")
            return self._get_fallback_analysis(email)
        except Exception as e:
            logger.error(f"[EmailManager] ❌ Fehler bei LLM-Analyse: {e}")
            import traceback
            traceback.print_exc()
            return self._get_fallback_analysis(email)

    def _get_fallback_analysis(self, email: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fallback-Analyse ohne LLM (regelbasiert)

        Args:
            email: E-Mail-Dict

        Returns:
            Analysis-Dict
        """
        importance = email.get('importance', 'normal')
        priority_map = {'high': 4, 'normal': 3, 'low': 2}
        priority = priority_map.get(importance, 3)

        subject = email.get('subject', 'Kein Betreff')
        body_preview = email.get('bodyPreview', '')

        # Einfache Kategorie-Erkennung
        category = "Sonstiges"
        if any(word in subject.lower() for word in ['rechnung', 'invoice']):
            category = "Rechnung"
        elif any(word in subject.lower() for word in ['meeting', 'termin', 'besprechung']):
            category = "Meeting"
        elif any(word in subject.lower() for word in ['auftrag', 'bestellung', 'order']):
            category = "Auftrag"

        return {
            "summary": body_preview[:150] + "..." if len(body_preview) > 150 else body_preview,
            "priority": priority,
            "category": category,
            "action_items": [],
            "deadline": None,
            "sentiment": "neutral"
        }

    def suggest_asana_target(self, email: Dict[str, Any], analysis: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Schlägt Asana-Projekt vor (Smart Resolver)

        Args:
            email: E-Mail-Dict
            analysis: Analysis-Dict

        Returns:
            Dict mit: project_gid, project_name, confidence, reason
            oder None wenn kein Match
        """
        sender_email = email.get('from', {}).get('emailAddress', {}).get('address', '').lower()
        subject = email.get('subject', '')
        body_preview = email.get('bodyPreview', '')

        # 1. People Mappings (exakter Match)
        people_mappings = self.config.get('people_mappings', {}).get('mappings', [])
        for mapping in people_mappings:
            email_pattern = mapping.get('email_pattern', '').lower()

            # Exakter Match
            if email_pattern == sender_email:
                return {
                    'project_gid': mapping.get('asana_project_gid'),
                    'project_name': mapping.get('name'),
                    'confidence': 1.0,
                    'reason': 'Exakter Absender-Match'
                }

            # Wildcard Match (z.B. *@domain.com)
            if email_pattern.startswith('*@'):
                domain = email_pattern[2:]
                if sender_email.endswith(domain):
                    return {
                        'project_gid': mapping.get('asana_project_gid'),
                        'project_name': mapping.get('name'),
                        'confidence': 0.95,
                        'reason': 'Domain-Match'
                    }

        # 2. Project Mappings (Keyword-Match)
        project_mappings = self.config.get('project_mappings', {}).get('mappings', [])
        for mapping in project_mappings:
            keywords = mapping.get('keywords', [])
            text_to_search = (subject + " " + body_preview).lower()

            for keyword in keywords:
                if keyword.lower() in text_to_search:
                    return {
                        'project_gid': mapping.get('asana_project_gid'),
                        'project_name': mapping.get('project_name'),
                        'confidence': 0.85,
                        'reason': f'Keyword-Match: "{keyword}"'
                    }

        # 3. Fuzzy-Match über alle Projekte
        try:
            all_projects = self.asana_agent.list_projects()
            best_match = None
            best_score = 0.0

            for project in all_projects:
                project_name = project.get('name', '')
                score = SequenceMatcher(None, subject.lower(), project_name.lower()).ratio()

                if score > best_score:
                    best_score = score
                    best_match = project

            if best_match and best_score >= 0.6:
                return {
                    'project_gid': best_match.get('gid'),
                    'project_name': best_match.get('name'),
                    'confidence': best_score,
                    'reason': 'Fuzzy-Match (Betreff-Ähnlichkeit)'
                }

        except Exception as e:
            logger.error(f"[EmailManager] ❌ Fehler beim Fuzzy-Match: {e}")

        # Kein Match gefunden
        return None

    def send_to_asana(self, email: Dict[str, Any], analysis: Dict[str, Any],
                      project_gid: str, project_name: str) -> Dict[str, Any]:
        """
        Erstellt Asana-Task aus E-Mail

        Args:
            email: E-Mail-Dict
            analysis: Analysis-Dict
            project_gid: Ziel-Projekt GID
            project_name: Ziel-Projekt Name

        Returns:
            Dict mit 'success' (bool) und optional 'error' (str) oder 'task_gid'
        """
        try:
            # Extrahiere Daten
            subject = email.get('subject', 'Kein Betreff')
            sender_name = email.get('from', {}).get('emailAddress', {}).get('name', 'Unbekannt')
            sender_email = email.get('from', {}).get('emailAddress', {}).get('address', '')
            received_dt = email.get('receivedDateTime', '')
            body_preview = email.get('bodyPreview', '')
            web_link = email.get('webLink', '')

            # Formatiere Datum
            try:
                received_datetime = datetime.fromisoformat(received_dt.replace('Z', '+00:00'))
                received_str = received_datetime.strftime('%d.%m.%Y %H:%M')
            except:
                received_str = received_dt

            # Task-Beschreibung
            description_parts = [
                f"**Von:** {sender_name} <{sender_email}>",
                f"**Empfangen:** {received_str}",
                f"**Kategorie:** {analysis.get('category', 'Sonstiges')}",
                f"**Priorität:** {analysis.get('priority', 3)}/5",
                "",
                "**Zusammenfassung:**",
                analysis.get('summary', 'Keine Zusammenfassung verfügbar'),
                "",
                "**Email-Vorschau:**",
                body_preview[:300] + "..." if len(body_preview) > 300 else body_preview,
            ]

            # Handlungspunkte
            action_items = analysis.get('action_items', [])
            if action_items:
                description_parts.append("")
                description_parts.append("**Handlungspunkte:**")
                for item in action_items:
                    description_parts.append(f"- {item}")

            # Link zur E-Mail
            if web_link:
                description_parts.append("")
                description_parts.append(f"[📧 Email in Outlook öffnen]({web_link})")

            description = "\n".join(description_parts)

            # Erstelle Task
            task_data = {
                'name': f"📧 {subject}",
                'notes': description,
                'projects': [project_gid]
            }

            # Deadline hinzufügen falls vorhanden
            deadline = analysis.get('deadline')
            if deadline:
                task_data['due_on'] = deadline

            result = self.asana_agent.create_task(task_data)

            if result.get('success'):
                task_gid = result.get('task', {}).get('gid')
                logger.info(f"[EmailManager] ✓ Task erstellt in '{project_name}': {task_gid}")
                return {'success': True, 'task_gid': task_gid}
            else:
                error = result.get('error', 'Unbekannter Fehler')
                logger.error(f"[EmailManager] ❌ Task-Erstellung fehlgeschlagen: {error}")
                return {'success': False, 'error': error}

        except Exception as e:
            error_msg = str(e)
            logger.error(f"[EmailManager] ❌ Fehler beim Senden an Asana: {error_msg}")
            import traceback
            traceback.print_exc()
            return {'success': False, 'error': error_msg}

    def archive_email(self, email_id: str) -> Dict[str, Any]:
        """
        Archiviert E-Mail (markiert als gelesen + verschiebt in Ordner)

        Args:
            email_id: E-Mail ID

        Returns:
            Dict mit 'success' (bool) und optional 'error' (str)
        """
        try:
            # Markiere als gelesen
            result1 = self.outlook_tool.mark_as_read(email_id)
            if not result1.get('success'):
                return result1

            # Verschiebe in Archiv-Ordner
            result2 = self.outlook_tool.move_to_folder(email_id, "Posteingang erledigt 2026")
            if not result2.get('success'):
                return result2

            logger.info(f"[EmailManager] ✓ E-Mail archiviert")
            return {'success': True}

        except Exception as e:
            error_msg = str(e)
            logger.error(f"[EmailManager] ❌ Fehler beim Archivieren: {error_msg}")
            return {'success': False, 'error': error_msg}

    def forward_email(self, email_id: str, to_recipients: List[str], comment: str = "") -> Dict[str, Any]:
        """
        Leitet E-Mail weiter

        Args:
            email_id: E-Mail ID
            to_recipients: Liste von E-Mail-Adressen
            comment: Optional: Kommentar

        Returns:
            Dict mit 'success' (bool) und optional 'error' (str)
        """
        return self.outlook_tool.forward_email(email_id, to_recipients, comment)

    def reply_email(self, email_id: str, comment: str, reply_all: bool = False) -> Dict[str, Any]:
        """
        Antwortet auf eine E-Mail

        Args:
            email_id: E-Mail ID
            comment: Der Antworttext
            reply_all: Falls True, wird an alle geantwortet (Reply All)

        Returns:
            Dict mit 'success' (bool) und optional 'error' (str)
        """
        return self.outlook_tool.reply_email(email_id, comment, reply_all)

    def clear_email_analysis(self, email_id: str) -> Dict[str, Any]:
        """
        Löscht gespeicherte Analyse aus Outlook Categories

        Args:
            email_id: E-Mail ID

        Returns:
            Dict mit 'success' (bool) und optional 'error' (str)
        """
        if not self.outlook_tool.access_token:
            return {'success': False, 'error': 'Keine Authentifizierung vorhanden'}

        try:
            import requests

            url = f"https://graph.microsoft.com/v1.0/me/messages/{email_id}"
            headers = {
                "Authorization": f"Bearer {self.outlook_tool.access_token}",
                "Content-Type": "application/json"
            }

            # Entferne alle AI_* Categories
            body = {"categories": []}

            response = requests.patch(url, headers=headers, json=body)

            if response.status_code == 200:
                logger.info(f"[EmailManager] ✓ Analyse-Categories entfernt")
                return {'success': True}
            else:
                error_msg = f"HTTP {response.status_code}: {response.text}"
                logger.error(f"[EmailManager] ❌ Fehler beim Löschen: {error_msg}")
                return {'success': False, 'error': error_msg}

        except Exception as e:
            error_msg = str(e)
            logger.error(f"[EmailManager] ❌ Fehler beim Löschen der Analyse: {error_msg}")
            return {'success': False, 'error': error_msg}

    def get_forwarding_rule(self, category: str) -> Optional[Dict[str, Any]]:
        """
        Findet Forwarding-Regel für Kategorie

        Args:
            category: E-Mail-Kategorie

        Returns:
            Rule-Dict oder None
        """
        rules = self.config.get('forwarding_rules', {}).get('rules', [])
        for rule in rules:
            if rule.get('category') == category:
                return rule
        return None

    def generate_draft_reply(self, email: Dict[str, Any], analysis: Dict[str, Any]) -> str:
        """
        Generiert Draft-Reply mit LLM

        Args:
            email: Email-Dict mit subject, body, sender
            analysis: Analysis-Dict mit priority, category, summary

        Returns:
            Draft-Reply als String
        """
        if not self.llm:
            logger.warning("[EmailManager] ⚠️ LLM nicht verfügbar für Draft-Generierung")
            return ""

        system_prompt = """Du bist ein Email-Assistent.
Schreibe einen kurzen, professionellen Antwortentwurf auf Deutsch.
Berücksichtige Priorität und Kategorie der Email.
Halte den Entwurf kurz (max 150 Wörter).
Beginne direkt mit der Anrede, kein Betreff."""

        subject = email.get('subject', '')
        sender_name = email.get('from', {}).get('emailAddress', {}).get('name', '')
        sender_email = email.get('from', {}).get('emailAddress', {}).get('address', '')
        body_preview = email.get('bodyPreview', '')[:500]

        user_prompt = f"""Email analysieren und Antwort-Entwurf erstellen:

Betreff: {subject}
Von: {sender_name} <{sender_email}>
Inhalt: {body_preview}

Priorität: {analysis.get('priority')}/5
Kategorie: {analysis.get('category')}
Zusammenfassung: {analysis.get('summary', '')}

Schreibe einen passenden Antwortentwurf."""

        try:
            from langchain_core.messages import SystemMessage, HumanMessage

            response = self.llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt)
            ])

            draft = response.content.strip()
            logger.info(f"[EmailManager] ✓ Draft-Reply generiert ({len(draft)} Zeichen)")
            return draft

        except Exception as e:
            logger.error(f"[EmailManager] ❌ Fehler bei Draft-Generierung: {e}")
            import traceback
            traceback.print_exc()
            return ""
