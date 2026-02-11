"""
Email Database - Strikte Trennung zwischen UI und Backend

Die UI liest/schreibt NUR diese Datenbank.
Der Worker ist der EINZIGE, der mit Outlook/Asana kommuniziert.
"""

import sqlite3
import json
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class EmailDB:
    """
    SQLite-Datenbank für Email-Verwaltung

    Felder:
    - id: Outlook Email ID (Primary Key)
    - subject: Betreff
    - sender_name: Absendername
    - sender_email: Absender-Email
    - received_dt: Empfangsdatum (ISO format)
    - body_preview: Email-Vorschau (erste 500 Zeichen)
    - body_full: Vollständiger Email-Text
    - has_attachments: Boolean
    - attachments_json: JSON mit Anhang-Metadaten
    - priority: 1-5 (von LLM berechnet)
    - category: Kategorie (von LLM berechnet)
    - summary: KI-Zusammenfassung
    - action_items: JSON-Array von Handlungspunkten
    - deadline: Optional: YYYY-MM-DD
    - sentiment: positiv|neutral|negativ|dringend
    - instruction: 'none', 'archive', 'asana', 'forward' - Was soll der Worker tun?
    - instruction_payload: JSON mit zusätzlichen Daten für instruction
    - status: 'unread', 'processing', 'done', 'error'
    - error_message: Falls status='error'
    - created_at: Timestamp wann in DB eingefügt
    - updated_at: Timestamp letzte Änderung
    """

    def __init__(self, db_path: str = "data/email_store.db"):
        """
        Initialisiert Datenbank

        Args:
            db_path: Pfad zur SQLite-Datenbank
        """
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_connection(self):
        """Context Manager für DB-Verbindung"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        """Erstellt DB-Schema falls nicht vorhanden"""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS emails (
                    id TEXT PRIMARY KEY,
                    subject TEXT,
                    sender_name TEXT,
                    sender_email TEXT,
                    received_dt TEXT,
                    body_preview TEXT,
                    body_full TEXT,
                    has_attachments INTEGER DEFAULT 0,
                    attachments_json TEXT,
                    priority INTEGER DEFAULT 3,
                    category TEXT DEFAULT 'Sonstiges',
                    summary TEXT,
                    action_items TEXT,
                    deadline TEXT,
                    sentiment TEXT DEFAULT 'neutral',
                    instruction TEXT DEFAULT 'none',
                    instruction_payload TEXT,
                    status TEXT DEFAULT 'unread',
                    error_message TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Index für schnelle Abfragen
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_status
                ON emails(status)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_instruction
                ON emails(instruction)
            """)

            conn.commit()
            logger.info(f"[EmailDB] ✓ Datenbank initialisiert: {self.db_path}")

    def insert_email(self, email_data: Dict[str, Any]) -> bool:
        """
        Fügt neue Email ein

        Args:
            email_data: Dict mit id, subject, sender_name, sender_email,
                       received_dt, body_preview, body_full, has_attachments,
                       attachments_json, priority, category, summary,
                       action_items, deadline, sentiment

        Returns:
            True wenn erfolgreich, False wenn bereits vorhanden
        """
        try:
            with self._get_connection() as conn:
                # Prüfe ob Email bereits existiert
                cursor = conn.execute("SELECT id FROM emails WHERE id = ?",
                                     (email_data['id'],))
                if cursor.fetchone():
                    logger.debug(f"[EmailDB] Email bereits vorhanden: {email_data['id']}")
                    return False

                # Serialisiere action_items als JSON
                action_items_json = json.dumps(email_data.get('action_items', []))

                # Serialisiere attachments als JSON
                attachments_json_str = json.dumps(email_data.get('attachments', []))

                conn.execute("""
                    INSERT INTO emails (
                        id, subject, sender_name, sender_email, received_dt,
                        body_preview, body_full, has_attachments, attachments_json,
                        priority, category, summary, action_items,
                        deadline, sentiment, instruction, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'none', 'unread')
                """, (
                    email_data['id'],
                    email_data.get('subject', ''),
                    email_data.get('sender_name', ''),
                    email_data.get('sender_email', ''),
                    email_data.get('received_dt', ''),
                    email_data.get('body_preview', ''),
                    email_data.get('body_full', ''),
                    1 if email_data.get('has_attachments') else 0,
                    attachments_json_str,
                    email_data.get('priority', 3),
                    email_data.get('category', 'Sonstiges'),
                    email_data.get('summary', ''),
                    action_items_json,
                    email_data.get('deadline'),
                    email_data.get('sentiment', 'neutral')
                ))

                conn.commit()
                logger.info(f"[EmailDB] ✓ Neue Email eingefügt: {email_data.get('subject', 'N/A')}")
                return True

        except Exception as e:
            logger.error(f"[EmailDB] ❌ Fehler beim Einfügen: {e}")
            return False

    def get_unread_emails(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Holt alle ungelesenen Emails (status='unread')

        Args:
            limit: Max. Anzahl

        Returns:
            Liste von Email-Dicts
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    SELECT * FROM emails
                    WHERE status = 'unread'
                    ORDER BY received_dt DESC
                    LIMIT ?
                """, (limit,))

                emails = []
                for row in cursor.fetchall():
                    email = dict(row)
                    # Parse action_items JSON
                    email['action_items'] = json.loads(email['action_items'] or '[]')
                    emails.append(email)

                return emails

        except Exception as e:
            logger.error(f"[EmailDB] ❌ Fehler beim Abrufen: {e}")
            return []

    def set_instruction(self, email_id: str, instruction: str,
                       payload: Optional[Dict[str, Any]] = None) -> bool:
        """
        Setzt Instruction für Email (UI-Funktion)

        Args:
            email_id: Email ID
            instruction: 'archive', 'asana', 'forward', 'none'
            payload: Optional: Zusätzliche Daten (z.B. {"project_gid": "..."})

        Returns:
            True wenn erfolgreich
        """
        try:
            with self._get_connection() as conn:
                payload_json = json.dumps(payload) if payload else None

                conn.execute("""
                    UPDATE emails
                    SET instruction = ?,
                        instruction_payload = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (instruction, payload_json, email_id))

                conn.commit()
                logger.info(f"[EmailDB] ✓ Instruction gesetzt: {instruction} für {email_id}")
                return True

        except Exception as e:
            logger.error(f"[EmailDB] ❌ Fehler beim Setzen der Instruction: {e}")
            return False

    def hide_email(self, email_id: str) -> bool:
        """
        Versteckt Email in UI (setzt status='processing')

        Args:
            email_id: Email ID

        Returns:
            True wenn erfolgreich
        """
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    UPDATE emails
                    SET status = 'processing',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (email_id,))

                conn.commit()
                return True

        except Exception as e:
            logger.error(f"[EmailDB] ❌ Fehler beim Verstecken: {e}")
            return False

    def get_pending_instructions(self) -> List[Dict[str, Any]]:
        """
        Holt alle Emails mit instruction != 'none' (Worker-Funktion)

        Returns:
            Liste von Email-Dicts mit pending instructions
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    SELECT * FROM emails
                    WHERE instruction != 'none'
                    AND status != 'done'
                    ORDER BY updated_at ASC
                """)

                emails = []
                for row in cursor.fetchall():
                    email = dict(row)
                    email['action_items'] = json.loads(email['action_items'] or '[]')
                    if email['instruction_payload']:
                        email['instruction_payload'] = json.loads(email['instruction_payload'])
                    emails.append(email)

                return emails

        except Exception as e:
            logger.error(f"[EmailDB] ❌ Fehler beim Abrufen pending instructions: {e}")
            return []

    def mark_as_done(self, email_id: str) -> bool:
        """
        Markiert Email als erledigt (Worker-Funktion)

        Args:
            email_id: Email ID

        Returns:
            True wenn erfolgreich
        """
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    UPDATE emails
                    SET status = 'done',
                        instruction = 'none',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (email_id,))

                conn.commit()
                logger.info(f"[EmailDB] ✓ Email als erledigt markiert: {email_id}")
                return True

        except Exception as e:
            logger.error(f"[EmailDB] ❌ Fehler beim Markieren als erledigt: {e}")
            return False

    def mark_as_error(self, email_id: str, error_message: str) -> bool:
        """
        Markiert Email als fehlerhaft (Worker-Funktion)

        Args:
            email_id: Email ID
            error_message: Fehlermeldung

        Returns:
            True wenn erfolgreich
        """
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    UPDATE emails
                    SET status = 'error',
                        error_message = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (error_message, email_id))

                conn.commit()
                logger.warning(f"[EmailDB] ⚠️ Email als Fehler markiert: {email_id}")
                return True

        except Exception as e:
            logger.error(f"[EmailDB] ❌ Fehler beim Markieren als Fehler: {e}")
            return False

    def delete_email(self, email_id: str) -> bool:
        """
        Löscht Email aus DB

        Args:
            email_id: Email ID

        Returns:
            True wenn erfolgreich
        """
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM emails WHERE id = ?", (email_id,))
                conn.commit()
                logger.info(f"[EmailDB] ✓ Email gelöscht: {email_id}")
                return True

        except Exception as e:
            logger.error(f"[EmailDB] ❌ Fehler beim Löschen: {e}")
            return False

    def get_stats(self) -> Dict[str, int]:
        """
        Holt Statistiken

        Returns:
            Dict mit Zählern für verschiedene Status
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    SELECT status, COUNT(*) as count
                    FROM emails
                    GROUP BY status
                """)

                stats = {row['status']: row['count'] for row in cursor.fetchall()}
                return stats

        except Exception as e:
            logger.error(f"[EmailDB] ❌ Fehler beim Abrufen der Stats: {e}")
            return {}
