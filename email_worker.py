#!/usr/bin/env python3
"""
Neuer Email Background Worker - Strikt asynchron

Das eiserne Gesetz:
- Dieser Worker ist der EINZIGE, der mit Outlook und Asana spricht
- Die UI (app.py) darf NIEMALS direkt auf Tools zugreifen
- Kommunikation nur über SQLite-Datenbank

Zwei Schleifen:
1. Fetch & Analyze Loop: Holt neue Mails von Outlook, analysiert mit LLM, schreibt in DB
2. Execute Instructions Loop: Verarbeitet pending instructions aus DB (archive, asana, etc.)
"""

import os
import sys
import time
import signal
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional

# Python Path für Imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

# Load .env
load_dotenv()

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('email_worker.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


class EmailWorker:
    """
    Background Worker mit 2 separaten Schleifen
    """

    def __init__(self):
        """Initialisiert Worker"""
        # Importiere EmailDB
        from database.email_db import EmailDB
        self.db = EmailDB()

        # Initialisiere Tools (lazy loading)
        self.outlook_tool = None
        self.asana_agent = None
        self.llm = None

        # Worker-Status
        self.running = False
        self.last_fetch = None
        self.last_execute = None

        # Signal-Handler
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        logger.info("[Worker] ✓ Worker initialisiert")

    def _signal_handler(self, signum, frame):
        """Graceful shutdown"""
        logger.info(f"[Worker] Signal {signum} empfangen, beende...")
        self.running = False
        sys.exit(0)

    def _init_outlook(self):
        """Initialisiert Outlook Tool (lazy)"""
        if self.outlook_tool is None:
            from tools.outlook_graph_tool import OutlookGraphTool
            self.outlook_tool = OutlookGraphTool()
            logger.info("[Worker] ✓ Outlook Tool initialisiert")
        return self.outlook_tool

    def _init_asana(self):
        """Initialisiert Asana Agent (lazy)"""
        if self.asana_agent is None:
            from agents.asana_agent import AsanaAgent
            self.asana_agent = AsanaAgent()
            logger.info("[Worker] ✓ Asana Agent initialisiert")
        return self.asana_agent

    def _init_llm(self):
        """Initialisiert LLM für Email-Analyse"""
        if self.llm is None:
            llm_provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
            llm_model = os.getenv("RESEARCH_MODEL", "claude-sonnet-4-5")

            try:
                if llm_provider == "anthropic":
                    from langchain_anthropic import ChatAnthropic
                    self.llm = ChatAnthropic(
                        model=llm_model,
                        temperature=0,
                        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY")
                    )
                elif llm_provider == "openai":
                    from langchain_openai import ChatOpenAI
                    self.llm = ChatOpenAI(
                        model=llm_model,
                        temperature=0,
                        openai_api_key=os.getenv("OPENAI_API_KEY")
                    )
                else:
                    logger.error(f"[Worker] ❌ Unbekannter LLM Provider: {llm_provider}")
                    return None

                logger.info(f"[Worker] ✓ LLM initialisiert: {llm_model}")
                return self.llm

            except Exception as e:
                logger.error(f"[Worker] ❌ Fehler beim Initialisieren des LLM: {e}")
                return None

        return self.llm

    def fetch_and_analyze_loop(self):
        """
        Schleife 1: Neue Mails von Outlook holen und analysieren
        """
        logger.info("[Worker] 🔄 Starte Fetch & Analyze Loop...")

        try:
            outlook = self._init_outlook()
            llm = self._init_llm()

            if not outlook or not llm:
                logger.error("[Worker] ❌ Tools nicht verfügbar, überspringe Fetch Loop")
                return

            # Authentifizierungs-Check
            if not outlook.is_authenticated():
                logger.error("[Worker] ❌ Outlook nicht authentifiziert")
                return

            # Hole ungelesene Emails von Outlook
            logger.info("[Worker] 📬 Hole neue Emails von Outlook...")
            emails = outlook.get_unread_emails(max_results=20)

            logger.info(f"[Worker] ✓ {len(emails)} ungelesene Emails gefunden")

            # Verarbeite jede Email
            for email in emails:
                email_id = email.get('id')
                subject = email.get('subject', 'Kein Betreff')

                # Prüfe ob bereits in DB
                existing_emails = self.db.get_unread_emails(limit=1000)
                if any(e['id'] == email_id for e in existing_emails):
                    logger.debug(f"[Worker] ⏭️ Email bereits in DB: {subject}")
                    continue

                # Analysiere mit LLM
                logger.info(f"[Worker] 🔍 Analysiere: {subject}")
                analysis = self._analyze_email_with_llm(email, llm)

                # Extrahiere vollständigen Body
                body_content = ""
                if 'body' in email and 'content' in email['body']:
                    body_content = email['body']['content']
                elif 'bodyPreview' in email:
                    body_content = email['bodyPreview']

                # Extrahiere Anhang-Metadaten
                attachments = []
                if email.get('hasAttachments'):
                    # Hole Anhang-Liste (ohne Content, nur Metadaten)
                    try:
                        outlook = self._init_outlook()
                        att_response = outlook.get_email_attachments(email_id)
                        if att_response.get('success'):
                            for att in att_response.get('attachments', []):
                                attachments.append({
                                    'id': att.get('id'),
                                    'name': att.get('name'),
                                    'size': att.get('size', 0),
                                    'contentType': att.get('contentType', 'application/octet-stream')
                                })
                    except Exception as e:
                        logger.warning(f"[Worker] ⚠️ Konnte Anhänge nicht laden: {e}")

                # Speichere in DB
                email_data = {
                    'id': email_id,
                    'subject': subject,
                    'sender_name': email.get('from', {}).get('emailAddress', {}).get('name', 'Unbekannt'),
                    'sender_email': email.get('from', {}).get('emailAddress', {}).get('address', ''),
                    'received_dt': email.get('receivedDateTime', ''),
                    'body_preview': email.get('bodyPreview', '')[:500],
                    'body_full': body_content,
                    'has_attachments': email.get('hasAttachments', False),
                    'attachments': attachments,
                    'priority': analysis.get('priority', 3),
                    'category': analysis.get('category', 'Sonstiges'),
                    'summary': analysis.get('summary', ''),
                    'action_items': analysis.get('action_items', []),
                    'deadline': analysis.get('deadline'),
                    'sentiment': analysis.get('sentiment', 'neutral')
                }

                if self.db.insert_email(email_data):
                    logger.info(f"[Worker] ✅ Email in DB gespeichert: {subject}")
                else:
                    logger.warning(f"[Worker] ⚠️ Email konnte nicht gespeichert werden: {subject}")

            self.last_fetch = datetime.now()
            logger.info("[Worker] ✅ Fetch & Analyze Loop abgeschlossen")

        except Exception as e:
            logger.error(f"[Worker] ❌ Fehler in Fetch Loop: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def execute_instructions_loop(self):
        """
        Schleife 2: Verarbeitet pending instructions aus DB
        """
        logger.info("[Worker] ⚙️ Starte Execute Instructions Loop...")

        try:
            # Hole alle pending instructions
            pending = self.db.get_pending_instructions()

            if not pending:
                logger.debug("[Worker] Keine pending instructions")
                return

            logger.info(f"[Worker] 📋 {len(pending)} pending instructions gefunden")

            for email in pending:
                email_id = email['id']
                instruction = email['instruction']
                payload = email.get('instruction_payload')

                logger.info(f"[Worker] ⚙️ Verarbeite instruction '{instruction}' für Email: {email.get('subject', 'N/A')}")

                try:
                    if instruction == 'archive':
                        self._execute_archive(email)
                    elif instruction == 'asana':
                        self._execute_asana(email, payload)
                    elif instruction == 'forward':
                        self._execute_forward(email, payload)
                    else:
                        logger.warning(f"[Worker] ⚠️ Unbekannte instruction: {instruction}")
                        self.db.mark_as_error(email_id, f"Unbekannte instruction: {instruction}")

                except Exception as e:
                    error_msg = f"Fehler bei {instruction}: {str(e)}"
                    logger.error(f"[Worker] ❌ {error_msg}")
                    self.db.mark_as_error(email_id, error_msg)

            self.last_execute = datetime.now()
            logger.info("[Worker] ✅ Execute Instructions Loop abgeschlossen")

        except Exception as e:
            logger.error(f"[Worker] ❌ Fehler in Execute Loop: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _analyze_email_with_llm(self, email: Dict[str, Any], llm) -> Dict[str, Any]:
        """
        Analysiert Email mit LLM

        Returns:
            Dict mit: summary, priority, category, action_items, deadline, sentiment
        """
        try:
            # Extrahiere Body
            body_content = ""
            if 'body' in email and 'content' in email['body']:
                body_content = email['body']['content']
            elif 'bodyPreview' in email:
                body_content = email['bodyPreview']

            subject = email.get('subject', 'Kein Betreff')
            sender_name = email.get('from', {}).get('emailAddress', {}).get('name', 'Unbekannt')
            sender_email = email.get('from', {}).get('emailAddress', {}).get('address', '')

            # System Prompt
            system_prompt = """Du bist ein intelligenter Email-Analyse-Assistent.
Analysiere die E-Mail und gib ein strukturiertes JSON zurück.

WICHTIG: Antworte NUR mit gültigem JSON, keine zusätzlichen Texte!

JSON-Format:
{
  "summary": "Kurze Zusammenfassung in 1-2 Sätzen",
  "priority": 1-5 (1=niedrig, 5=kritisch),
  "category": "Sonstiges",
  "action_items": ["Handlungspunkt 1", "Handlungspunkt 2", ...],
  "deadline": "YYYY-MM-DD oder null",
  "sentiment": "positiv|neutral|negativ|dringend"
}

Priorität:
- 5 = Kritisch (dringend + wichtig)
- 4 = Dringend (schnelle Reaktion nötig)
- 3 = Normal (reguläre Anfrage)
- 2 = Niedrig (Information)
- 1 = Sehr niedrig (Newsletter)
"""

            user_prompt = f"""E-Mail analysieren:

Von: {sender_name} <{sender_email}>
Betreff: {subject}

Body:
{body_content[:2000]}
"""

            # LLM aufrufen
            from langchain_core.messages import SystemMessage, HumanMessage
            import json

            response = llm.invoke([
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

            logger.info(f"[Worker] ✓ Analyse abgeschlossen: Priority={analysis.get('priority')}")
            return analysis

        except Exception as e:
            logger.error(f"[Worker] ❌ Fehler bei LLM-Analyse: {e}")
            # Fallback
            return {
                'summary': email.get('bodyPreview', '')[:150],
                'priority': 3,
                'category': 'Sonstiges',
                'action_items': [],
                'deadline': None,
                'sentiment': 'neutral'
            }

    def _execute_archive(self, email: Dict[str, Any]):
        """Archiviert Email in Outlook"""
        outlook = self._init_outlook()
        email_id = email['id']

        # Markiere als gelesen
        result = outlook.mark_as_read(email_id)
        if not result.get('success'):
            raise Exception(f"Konnte nicht als gelesen markieren: {result.get('error')}")

        # Verschiebe in Archiv-Ordner
        result = outlook.move_to_folder(email_id, "Posteingang erledigt 2026")
        if not result.get('success'):
            raise Exception(f"Konnte nicht verschieben: {result.get('error')}")

        # Markiere in DB als erledigt
        self.db.mark_as_done(email_id)
        logger.info(f"[Worker] ✅ Email archiviert: {email.get('subject')}")

    def _execute_asana(self, email: Dict[str, Any], payload: Optional[Dict]):
        """Erstellt Asana-Task aus Email"""
        asana = self._init_asana()

        # Hole project_gid aus payload
        project_gid = payload.get('project_gid') if payload else None

        if not project_gid or project_gid == 'default':
            # Nutze Default-Projekt (sollte aus Config kommen)
            project_gid = os.getenv('DEFAULT_ASANA_PROJECT_GID')

        if not project_gid:
            raise Exception("Kein Asana-Projekt angegeben")

        # Erstelle Task
        subject = email.get('subject', 'Kein Betreff')
        sender_name = email.get('sender_name', 'Unbekannt')
        sender_email = email.get('sender_email', '')
        summary = email.get('summary', '')
        body_preview = email.get('body_preview', '')

        task_data = {
            'name': f"📧 {subject}",
            'notes': f"""**Von:** {sender_name} <{sender_email}>

**Zusammenfassung:**
{summary}

**Email-Vorschau:**
{body_preview[:300]}
""",
            'projects': [project_gid]
        }

        # Deadline
        if email.get('deadline'):
            task_data['due_on'] = email.get('deadline')

        result = asana.create_task(task_data)

        if not result.get('success'):
            raise Exception(f"Asana-Task Erstellung fehlgeschlagen: {result.get('error')}")

        # Markiere in DB als erledigt
        self.db.mark_as_done(email['id'])
        logger.info(f"[Worker] ✅ Asana-Task erstellt: {subject}")

    def _execute_forward(self, email: Dict[str, Any], payload: Optional[Dict]):
        """Leitet Email weiter"""
        outlook = self._init_outlook()

        recipients = payload.get('recipients', []) if payload else []
        comment = payload.get('comment', '') if payload else ''

        if not recipients:
            raise Exception("Keine Empfänger angegeben")

        result = outlook.forward_email(email['id'], recipients, comment)

        if not result.get('success'):
            raise Exception(f"Weiterleitung fehlgeschlagen: {result.get('error')}")

        # Markiere in DB als erledigt
        self.db.mark_as_done(email['id'])
        logger.info(f"[Worker] ✅ Email weitergeleitet: {email.get('subject')}")

    def run(self):
        """
        Hauptloop: Führt beide Schleifen abwechselnd aus
        """
        logger.info("[Worker] 🚀 Starte Worker...")
        self.running = True

        # Intervalle (in Sekunden)
        FETCH_INTERVAL = 120  # 2 Minuten
        EXECUTE_INTERVAL = 30  # 30 Sekunden

        last_fetch_time = 0
        last_execute_time = 0

        while self.running:
            try:
                current_time = time.time()

                # Fetch & Analyze Loop (alle 2 Minuten)
                if current_time - last_fetch_time >= FETCH_INTERVAL:
                    self.fetch_and_analyze_loop()
                    last_fetch_time = current_time

                # Execute Instructions Loop (alle 30 Sekunden)
                if current_time - last_execute_time >= EXECUTE_INTERVAL:
                    self.execute_instructions_loop()
                    last_execute_time = current_time

                # Sleep 10 Sekunden
                time.sleep(10)

            except KeyboardInterrupt:
                logger.info("[Worker] 🛑 Worker gestoppt durch Benutzer")
                break
            except Exception as e:
                logger.error(f"[Worker] ❌ Fehler im Hauptloop: {e}")
                import traceback
                logger.error(traceback.format_exc())
                # Warte 60 Sekunden bei Fehler
                time.sleep(60)

        logger.info("[Worker] 🛑 Worker beendet")


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Email Worker - Strikt asynchrone Architektur")
    logger.info("=" * 60)

    worker = EmailWorker()
    worker.run()
