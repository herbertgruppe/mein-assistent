"""
Email Cache Database - SQLite Abstraction Layer

Verwaltet persistente Email-Daten, Action-Queue und Worker-Status
"""

import sqlite3
import json
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class EmailDatabase:
    """
    Abstraction Layer für Email-Cache-Datenbank
    """

    def __init__(self, db_path: str = "data/email_cache.db"):
        """
        Initialisiert Database Connection

        Args:
            db_path: Pfad zur SQLite-Datenbank
        """
        self.db_path = db_path
        logger.info(f"[EmailDatabase] Initialisiere Datenbank: {db_path}")

    @contextmanager
    def get_connection(self):
        """Context Manager für sichere DB-Verbindungen"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Dict-like access
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"[EmailDatabase] Transaktion fehlgeschlagen: {e}")
            raise
        finally:
            conn.close()

    def initialize_schema(self):
        """
        Erstellt Schema (Tabellen + Indexes) falls noch nicht vorhanden
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Tabelle: emails
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS emails (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT UNIQUE NOT NULL,
                    subject TEXT,
                    sender_name TEXT,
                    sender_email TEXT,
                    received_at TEXT,
                    body_preview TEXT,
                    body_full TEXT,
                    has_attachments INTEGER DEFAULT 0,
                    web_link TEXT,
                    priority INTEGER DEFAULT 3,
                    category TEXT,
                    summary TEXT,
                    sentiment TEXT,
                    action_items TEXT,
                    deadline TEXT,
                    suggested_board_gid TEXT,
                    suggested_board_name TEXT,
                    suggestion_confidence REAL,
                    suggestion_reason TEXT,
                    status TEXT DEFAULT 'new',
                    error_message TEXT,
                    retry_count INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    analyzed_at TEXT,
                    processed_at TEXT
                )
            """)

            # Tabelle: action_queue
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS action_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email_id INTEGER NOT NULL,
                    action_type TEXT NOT NULL,
                    action_data TEXT,
                    status TEXT DEFAULT 'pending',
                    error_message TEXT,
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 3,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    completed_at TEXT,
                    FOREIGN KEY (email_id) REFERENCES emails(id) ON DELETE CASCADE
                )
            """)

            # Tabelle: worker_state
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS worker_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    last_poll_time TEXT,
                    last_successful_poll TEXT,
                    last_error TEXT,
                    total_emails_processed INTEGER DEFAULT 0,
                    is_running INTEGER DEFAULT 0,
                    pid INTEGER,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Initialisiere worker_state mit Default-Wert
            cursor.execute("""
                INSERT OR IGNORE INTO worker_state (id, is_running)
                VALUES (1, 0)
            """)

            # Tabelle: audit_log
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email_message_id TEXT,
                    event_type TEXT NOT NULL,
                    event_data TEXT,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_emails_status ON emails(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_emails_received ON emails(received_at DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_emails_priority ON emails(priority DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_emails_message_id ON emails(message_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_actions_status ON action_queue(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_actions_email_id ON action_queue(email_id)")

            conn.commit()
            logger.info("[EmailDatabase] ✓ Schema initialisiert")

    # ==================== Email Operations ====================

    def insert_raw_email(self, email: Dict[str, Any]) -> int:
        """
        Fügt rohe Email ein ohne LLM-Analyse (status='synced')
        Für Harvester-Task

        Args:
            email: Email-Dict (Graph API Format)

        Returns:
            ID der eingefügten Email
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Extrahiere Email-Daten
            message_id = email.get('id')
            subject = email.get('subject', 'Kein Betreff')
            sender_name = email.get('from', {}).get('emailAddress', {}).get('name', '')
            sender_email = email.get('from', {}).get('emailAddress', {}).get('address', '')
            received_at = email.get('receivedDateTime', '')
            body_preview = email.get('bodyPreview', '')
            body_full = email.get('body', {}).get('content', '')
            has_attachments = 1 if email.get('hasAttachments', False) else 0
            web_link = email.get('webLink', '')

            cursor.execute("""
                INSERT INTO emails (
                    message_id, subject, sender_name, sender_email, received_at,
                    body_preview, body_full, has_attachments, web_link,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'synced')
            """, (
                message_id, subject, sender_name, sender_email, received_at,
                body_preview, body_full, has_attachments, web_link
            ))

            email_id = cursor.lastrowid
            logger.info(f"[EmailDatabase] ✓ Raw Email eingefügt: ID={email_id}, Subject='{subject[:50]}'")
            return email_id

    def insert_email(self, email: Dict[str, Any], analysis: Dict[str, Any],
                     suggested_project: Optional[Dict[str, Any]] = None) -> int:
        """
        Fügt neue Email in Datenbank ein

        Args:
            email: Email-Dict (Graph API Format)
            analysis: Analysis-Dict (LLM-Output)
            suggested_project: Suggested Asana Project Dict

        Returns:
            ID der eingefügten Email
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Extrahiere Email-Daten
            message_id = email.get('id')
            subject = email.get('subject', 'Kein Betreff')
            sender_name = email.get('from', {}).get('emailAddress', {}).get('name', '')
            sender_email = email.get('from', {}).get('emailAddress', {}).get('address', '')
            received_at = email.get('receivedDateTime', '')
            body_preview = email.get('bodyPreview', '')
            body_full = email.get('body', {}).get('content', '')
            has_attachments = 1 if email.get('hasAttachments', False) else 0
            web_link = email.get('webLink', '')

            # Extrahiere Analysis-Daten
            priority = analysis.get('priority', 3)
            category = analysis.get('category', 'Sonstiges')
            summary = analysis.get('summary', '')
            sentiment = analysis.get('sentiment', 'neutral')
            action_items = json.dumps(analysis.get('action_items', []), ensure_ascii=False)
            deadline = analysis.get('deadline')

            # Suggested Project
            suggested_gid = suggested_project.get('project_gid') if suggested_project else None
            suggested_name = suggested_project.get('project_name') if suggested_project else None
            suggested_confidence = suggested_project.get('confidence') if suggested_project else None
            suggested_reason = suggested_project.get('reason') if suggested_project else None

            cursor.execute("""
                INSERT INTO emails (
                    message_id, subject, sender_name, sender_email, received_at,
                    body_preview, body_full, has_attachments, web_link,
                    priority, category, summary, sentiment, action_items, deadline,
                    suggested_board_gid, suggested_board_name,
                    suggestion_confidence, suggestion_reason,
                    status, analyzed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'analyzed', ?)
            """, (
                message_id, subject, sender_name, sender_email, received_at,
                body_preview, body_full, has_attachments, web_link,
                priority, category, summary, sentiment, action_items, deadline,
                suggested_gid, suggested_name, suggested_confidence, suggested_reason,
                datetime.now().isoformat()
            ))

            email_id = cursor.lastrowid
            logger.info(f"[EmailDatabase] ✓ Email eingefügt: ID={email_id}, Subject='{subject[:50]}'")
            return email_id

    def update_email_analysis(self, email_id: int, analysis: Dict[str, Any],
                              suggested_project: Optional[Dict[str, Any]] = None,
                              draft_reply: Optional[str] = None):
        """
        Updated Email mit LLM-Analysis-Daten
        Für Enrichment-Task

        Args:
            email_id: Email ID
            analysis: Analysis-Dict (LLM-Output)
            suggested_project: Suggested Asana Project Dict
            draft_reply: Vorgenerierter Draft-Reply Text
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Extrahiere Analysis-Daten
            priority = analysis.get('priority', 3)
            category = analysis.get('category', 'Sonstiges')
            summary = analysis.get('summary', '')
            sentiment = analysis.get('sentiment', 'neutral')
            action_items = json.dumps(analysis.get('action_items', []), ensure_ascii=False)
            deadline = analysis.get('deadline')

            # Suggested Project
            suggested_gid = suggested_project.get('project_gid') if suggested_project else None
            suggested_name = suggested_project.get('project_name') if suggested_project else None
            suggested_confidence = suggested_project.get('confidence') if suggested_project else None
            suggested_reason = suggested_project.get('reason') if suggested_project else None

            cursor.execute("""
                UPDATE emails
                SET priority = ?, category = ?, summary = ?, sentiment = ?,
                    action_items = ?, deadline = ?, draft_reply = ?,
                    suggested_board_gid = ?, suggested_board_name = ?,
                    suggestion_confidence = ?, suggestion_reason = ?,
                    status = 'analyzed', analyzed_at = ?, updated_at = ?
                WHERE id = ?
            """, (
                priority, category, summary, sentiment, action_items, deadline, draft_reply,
                suggested_gid, suggested_name, suggested_confidence, suggested_reason,
                datetime.now().isoformat(), datetime.now().isoformat(), email_id
            ))

            logger.info(f"[EmailDatabase] ✓ Email {email_id} analysiert: Priority={priority}, Category={category}")

    def increment_retry_count(self, email_id: int):
        """
        Erhöht retry_count für Email (Error-Handling)

        Args:
            email_id: Email ID
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE emails
                SET retry_count = retry_count + 1, updated_at = ?
                WHERE id = ?
            """, (datetime.now().isoformat(), email_id))

            logger.info(f"[EmailDatabase] ✓ Retry-Count erhöht für Email {email_id}")

    def update_email_status(self, email_id: int, status: str, error: Optional[str] = None):
        """
        Aktualisiert Email-Status

        Args:
            email_id: Email ID
            status: Neuer Status (synced|analyzed|pending_asana|pending_forward|pending_reply|archived|error)
            error: Optional: Error-Message
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            if error:
                cursor.execute("""
                    UPDATE emails
                    SET status = ?, error_message = ?, updated_at = ?, retry_count = retry_count + 1
                    WHERE id = ?
                """, (status, error, datetime.now().isoformat(), email_id))
            else:
                cursor.execute("""
                    UPDATE emails
                    SET status = ?, updated_at = ?, processed_at = ?
                    WHERE id = ?
                """, (status, datetime.now().isoformat(), datetime.now().isoformat(), email_id))

            logger.info(f"[EmailDatabase] ✓ Email {email_id} Status: {status}")

    def get_email_by_id(self, email_id: int) -> Optional[Dict[str, Any]]:
        """
        Holt Email anhand ID

        Args:
            email_id: Email ID

        Returns:
            Email-Dict oder None
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM emails WHERE id = ?", (email_id,))
            row = cursor.fetchone()

            if row:
                return self._row_to_dict(row)
            return None

    def get_email_by_message_id(self, message_id: str) -> Optional[Dict[str, Any]]:
        """
        Holt Email anhand Message-ID

        Args:
            message_id: Outlook Message ID

        Returns:
            Email-Dict oder None
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM emails WHERE message_id = ?", (message_id,))
            row = cursor.fetchone()

            if row:
                return self._row_to_dict(row)
            return None

    def get_emails_by_status(self, status_list: List[str], limit: int = 50,
                             order_by: str = "priority DESC, received_at DESC") -> List[Dict[str, Any]]:
        """
        Holt Emails nach Status

        Args:
            status_list: Liste von Status-Strings
            limit: Max. Anzahl
            order_by: SQL ORDER BY Clause

        Returns:
            Liste von Email-Dicts
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            placeholders = ','.join('?' * len(status_list))
            query = f"""
                SELECT * FROM emails
                WHERE status IN ({placeholders})
                ORDER BY {order_by}
                LIMIT ?
            """

            cursor.execute(query, (*status_list, limit))
            rows = cursor.fetchall()

            return [self._row_to_dict(row) for row in rows]

    def email_exists(self, message_id: str) -> bool:
        """
        Prüft ob Email bereits existiert

        Args:
            message_id: Outlook Message ID

        Returns:
            True wenn vorhanden
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM emails WHERE message_id = ? LIMIT 1", (message_id,))
            return cursor.fetchone() is not None

    def delete_old_emails(self, days: int = 30):
        """
        Löscht alte archivierte Emails

        Args:
            days: Emails älter als X Tage
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cutoff_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            cutoff_date = cutoff_date.replace(day=cutoff_date.day - days)

            cursor.execute("""
                DELETE FROM emails
                WHERE status = 'archived'
                AND received_at < ?
            """, (cutoff_date.isoformat(),))

            deleted_count = cursor.rowcount
            logger.info(f"[EmailDatabase] ✓ {deleted_count} alte Emails gelöscht")

    # ==================== Action Queue Operations ====================

    def create_action(self, email_id: int, action_type: str, action_data: Dict[str, Any]) -> int:
        """
        Erstellt neue Action in Queue

        Args:
            email_id: Email ID
            action_type: Action-Typ (asana|forward|archive)
            action_data: Action-Daten (JSON)

        Returns:
            Action ID
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO action_queue (email_id, action_type, action_data)
                VALUES (?, ?, ?)
            """, (email_id, action_type, json.dumps(action_data, ensure_ascii=False)))

            action_id = cursor.lastrowid
            logger.info(f"[EmailDatabase] ✓ Action erstellt: ID={action_id}, Type={action_type}")
            return action_id

    def get_pending_actions(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Holt pending Actions

        Args:
            limit: Max. Anzahl

        Returns:
            Liste von Action-Dicts
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT a.*, e.message_id, e.subject
                FROM action_queue a
                JOIN emails e ON a.email_id = e.id
                WHERE a.status = 'pending'
                AND a.retry_count < a.max_retries
                ORDER BY a.created_at ASC
                LIMIT ?
            """, (limit,))

            rows = cursor.fetchall()
            return [self._row_to_dict(row) for row in rows]

    def update_action_status(self, action_id: int, status: str, error: Optional[str] = None):
        """
        Aktualisiert Action-Status

        Args:
            action_id: Action ID
            status: Neuer Status (pending|processing|completed|failed)
            error: Optional: Error-Message
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            if status == 'completed':
                cursor.execute("""
                    UPDATE action_queue
                    SET status = ?, updated_at = ?, completed_at = ?
                    WHERE id = ?
                """, (status, datetime.now().isoformat(), datetime.now().isoformat(), action_id))
            elif status == 'failed':
                cursor.execute("""
                    UPDATE action_queue
                    SET status = ?, error_message = ?, retry_count = retry_count + 1, updated_at = ?
                    WHERE id = ?
                """, (status, error, datetime.now().isoformat(), action_id))
            else:
                cursor.execute("""
                    UPDATE action_queue
                    SET status = ?, updated_at = ?
                    WHERE id = ?
                """, (status, datetime.now().isoformat(), action_id))

            logger.info(f"[EmailDatabase] ✓ Action {action_id} Status: {status}")

    def mark_action_completed(self, action_id: int):
        """
        Markiert Action als completed

        Args:
            action_id: Action ID
        """
        self.update_action_status(action_id, 'completed')

    # ==================== Worker State Operations ====================

    def update_worker_state(self, is_running: bool, last_poll_time: Optional[str] = None,
                           error: Optional[str] = None, pid: Optional[int] = None):
        """
        Aktualisiert Worker-Status

        Args:
            is_running: Worker läuft
            last_poll_time: Letzter Poll-Zeitpunkt
            error: Optional: Error-Message
            pid: Process ID
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            if error:
                cursor.execute("""
                    UPDATE worker_state
                    SET is_running = ?, last_poll_time = ?, last_error = ?, pid = ?, updated_at = ?
                    WHERE id = 1
                """, (1 if is_running else 0, last_poll_time or datetime.now().isoformat(),
                      error, pid, datetime.now().isoformat()))
            else:
                cursor.execute("""
                    UPDATE worker_state
                    SET is_running = ?, last_poll_time = ?, last_successful_poll = ?,
                        last_error = NULL, pid = ?, updated_at = ?
                    WHERE id = 1
                """, (1 if is_running else 0, last_poll_time or datetime.now().isoformat(),
                      datetime.now().isoformat(), pid, datetime.now().isoformat()))

            logger.info(f"[EmailDatabase] ✓ Worker-Status aktualisiert: running={is_running}")

    def increment_processed_count(self):
        """Erhöht Counter für verarbeitete Emails"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE worker_state
                SET total_emails_processed = total_emails_processed + 1
                WHERE id = 1
            """)

    def get_worker_state(self) -> Dict[str, Any]:
        """
        Holt Worker-Status

        Returns:
            Worker-State-Dict
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM worker_state WHERE id = 1")
            row = cursor.fetchone()

            if row:
                return self._row_to_dict(row)
            return {}

    # ==================== Audit Log Operations ====================

    def log_event(self, message_id: Optional[str], event_type: str, event_data: Dict[str, Any]):
        """
        Schreibt Audit-Log-Eintrag

        Args:
            message_id: Email Message ID (optional)
            event_type: Event-Typ
            event_data: Event-Daten (JSON)
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO audit_log (email_message_id, event_type, event_data)
                VALUES (?, ?, ?)
            """, (message_id, event_type, json.dumps(event_data, ensure_ascii=False)))

            logger.debug(f"[EmailDatabase] Audit-Log: {event_type} für {message_id}")

    def get_recent_audit_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Holt letzte Audit-Log-Einträge

        Args:
            limit: Max. Anzahl

        Returns:
            Liste von Log-Dicts
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT * FROM audit_log
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,))

            rows = cursor.fetchall()
            return [self._row_to_dict(row) for row in rows]

    # ==================== Helper Methods ====================

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """
        Konvertiert SQLite Row zu Dict

        Args:
            row: SQLite Row

        Returns:
            Dict mit allen Columns
        """
        result = dict(row)

        # Parse JSON-Fields
        if 'action_items' in result and result['action_items']:
            try:
                result['action_items'] = json.loads(result['action_items'])
            except:
                result['action_items'] = []

        if 'action_data' in result and result['action_data']:
            try:
                result['action_data'] = json.loads(result['action_data'])
            except:
                result['action_data'] = {}

        if 'event_data' in result and result['event_data']:
            try:
                result['event_data'] = json.loads(result['event_data'])
            except:
                result['event_data'] = {}

        return result
