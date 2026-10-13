"""
Protocols Database — DB-Layer für den Protokoll-Review-Workflow.

Der Protokoll-Agent "Mara" legt Drafts per API ab, Reviewer bearbeiten sie
im Web-Editor (/review/{token}) und geben sie frei. Die Finalisierung
(PDF, Outlook, Asana) läuft als Hintergrund-Task.

Pattern wie database/email_db.py: Raw SQLite, kein ORM.
Schema: migrations/003_protocols_table.sql (wird beim Init automatisch
ausgeführt — CREATE TABLE IF NOT EXISTS, manuelle Migration optional).
"""

import json
import logging
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Gültige Status-Werte (siehe Schema)
#
# pending_assignment/assigned gehören zur Zuordnungs-Stufe (zweistufiger
# Workflow): Sven ordnet Termin, Asana-Board und Teilnehmer zu und korrigiert
# die Sprecher in Plaud, BEVOR das Transkript gezogen wird. Vorher war die
# Reihenfolge umgekehrt, wodurch jede Sprecherkorrektur in Plaud folgenlos
# blieb — das Transkript war zu dem Zeitpunkt schon geholt.
VALID_STATUSES = {
    "pending_assignment",
    "assigned",
    "draft",
    "in_review",
    "approved",
    "rejected",
    "discarded",
    "finalized",
}

# Status, in denen noch kein Protokolltext existiert und die Review-Seite im
# Zuordnungs-Modus rendert.
ASSIGNMENT_STATUSES = {"pending_assignment", "assigned"}

# Token-Gültigkeit für Reviewer-Links
TOKEN_TTL_DAYS = 30

_MIGRATION_FILE = (
    Path(__file__).resolve().parent.parent / "migrations" / "003_protocols_table.sql"
)

# Fallback falls das Migrations-File im Deployment fehlt — identisch zu
# migrations/003_protocols_table.sql.
_SCHEMA_FALLBACK = """
CREATE TABLE IF NOT EXISTS protocols (
  id                TEXT PRIMARY KEY,
  source            TEXT NOT NULL,
  recording_id      TEXT,
  audio_ref         TEXT,
  meeting_name      TEXT NOT NULL,
  meeting_datetime  TEXT NOT NULL,
  event_id          TEXT,
  asana_board_gid   TEXT,
  asana_section_gid TEXT,
  create_asana_task INTEGER NOT NULL DEFAULT 1,
  ablageort         TEXT,
  teilnehmer        TEXT,
  draft_markdown    TEXT NOT NULL,
  current_markdown  TEXT NOT NULL,
  status            TEXT NOT NULL DEFAULT 'draft',
  reviewer_token    TEXT NOT NULL UNIQUE,
  reviewer_emails   TEXT,
  expires_at        TEXT NOT NULL,
  rejection_reason  TEXT,
  asana_task_gid    TEXT,
  asana_task_url    TEXT,
  last_modified     TEXT NOT NULL,
  last_modified_by  TEXT,
  created_at        TEXT NOT NULL,
  approved_at       TEXT,
  finalized_at      TEXT,
  finalization_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_protocols_status ON protocols(status);
CREATE INDEX IF NOT EXISTS idx_protocols_token ON protocols(reviewer_token);
CREATE INDEX IF NOT EXISTS idx_protocols_finalized_at ON protocols(finalized_at);
"""


def _utcnow_iso() -> str:
    """Aktueller UTC-Zeitstempel als ISO-8601-String."""
    return datetime.now(timezone.utc).isoformat()


class ProtocolsDB:
    """
    SQLite-Datenbank für Protokoll-Drafts und deren Review-Lifecycle.

    Status-Flow:
        draft → in_review → approved → finalized
                    ↑           |
                    +-- Fehler bei Finalisierung (finalization_error gesetzt)
        draft/in_review → rejected
    """

    def __init__(self, db_path: str = "data/protocols.db"):
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
        """Erstellt DB-Schema falls nicht vorhanden (aus Migrations-File)."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        if _MIGRATION_FILE.exists():
            schema = _MIGRATION_FILE.read_text(encoding="utf-8")
        else:
            schema = _SCHEMA_FALLBACK
        with self._get_connection() as conn:
            conn.executescript(schema)
            self._migrate(conn)
            conn.commit()
        logger.info(f"[ProtocolsDB] ✓ Datenbank initialisiert: {self.db_path}")

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """
        Additive Spalten-Migrationen für bestehende Datenbanken.

        executescript() mit CREATE TABLE IF NOT EXISTS legt bei einer bereits
        existierenden Tabelle keine neuen Spalten an — deshalb hier einzeln,
        idempotent, nach dem im Repo etablierten try/except-Muster.
        """
        for column, ddl in (
            # Der Plaud-Aufnahmename. Ohne ihn lässt sich die Aufnahme in der
            # Plaud-App nicht wiederfinden, um die Sprecher zu korrigieren.
            ("recording_title", "ALTER TABLE protocols ADD COLUMN recording_title TEXT"),
            ("recording_duration", "ALTER TABLE protocols ADD COLUMN recording_duration TEXT"),
            # Eingeladene laut Outlook-Termin — bewusst getrennt von teilnehmer.
            # Eingeladen ist nicht anwesend; die Vermischung beider Begriffe war
            # die Ursache falscher Teilnehmerlisten.
            ("eingeladene", "ALTER TABLE protocols ADD COLUMN eingeladene TEXT"),
            # Das Rohtranskript. Lebte bisher ausschliesslich in Plaud — ist die
            # Aufnahme dort weg, laesst sich das Protokoll nicht mehr gegen die
            # Quelle pruefen. Ausserdem musste Mara am 21.09. auf eine
            # schlechtere Quelle ausweichen, weil sie nicht an Plaud kam; mit
            # lokaler Kopie waere das nicht passiert.
            ("transcript", "ALTER TABLE protocols ADD COLUMN transcript TEXT"),
            ("transcript_saved_at", "ALTER TABLE protocols ADD COLUMN transcript_saved_at TEXT"),
            ("assigned_at", "ALTER TABLE protocols ADD COLUMN assigned_at TEXT"),
            ("reminder_sent_at", "ALTER TABLE protocols ADD COLUMN reminder_sent_at TEXT"),
            (
                "reminder_count",
                "ALTER TABLE protocols ADD COLUMN reminder_count INTEGER NOT NULL DEFAULT 0",
            ),
        ):
            try:
                conn.execute(ddl)
                logger.info("[ProtocolsDB] Migration: Spalte %s ergänzt", column)
            except sqlite3.OperationalError:
                pass  # Spalte existiert bereits

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        """Konvertiert Row zu Dict und parst JSON-Felder."""
        protocol = dict(row)
        for json_field in ("teilnehmer", "eingeladene", "reviewer_emails"):
            raw = protocol.get(json_field)
            try:
                protocol[json_field] = json.loads(raw) if raw else []
            except (TypeError, json.JSONDecodeError):
                protocol[json_field] = []
        protocol["create_asana_task"] = bool(protocol.get("create_asana_task", 1))
        return protocol

    @staticmethod
    def is_expired(protocol: Dict[str, Any]) -> bool:
        """Prüft ob der Reviewer-Token des Protokolls abgelaufen ist."""
        try:
            expires = datetime.fromisoformat(protocol["expires_at"])
        except (KeyError, TypeError, ValueError):
            return True
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires <= datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------
    def create_draft(
        self,
        markdown: str,
        meeting_name: str,
        meeting_datetime: str,
        source: str,
        teilnehmer: Optional[List[str]] = None,
        reviewer_emails: Optional[List[str]] = None,
        ablageort: Optional[str] = None,
        recording_id: Optional[str] = None,
        event_id: Optional[str] = None,
        asana_board_gid: Optional[str] = None,
        asana_section_gid: Optional[str] = None,
        audio_ref: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Legt einen neuen Protokoll-Draft an.

        Returns:
            Dict mit id, reviewer_token, expires_at
        """
        draft_id = str(uuid.uuid4())
        reviewer_token = secrets.token_urlsafe(32)
        now = _utcnow_iso()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=TOKEN_TTL_DAYS)
        ).isoformat()

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO protocols (
                    id, source, recording_id, audio_ref,
                    meeting_name, meeting_datetime,
                    event_id, asana_board_gid, asana_section_gid,
                    ablageort, teilnehmer,
                    draft_markdown, current_markdown,
                    status, reviewer_token, reviewer_emails, expires_at,
                    last_modified, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    source,
                    recording_id,
                    audio_ref,
                    meeting_name,
                    meeting_datetime,
                    event_id,
                    asana_board_gid,
                    asana_section_gid,
                    ablageort,
                    json.dumps(teilnehmer or [], ensure_ascii=False),
                    markdown,
                    markdown,
                    reviewer_token,
                    json.dumps(reviewer_emails or [], ensure_ascii=False),
                    expires_at,
                    now,
                    now,
                ),
            )
            conn.commit()

        logger.info(f"[ProtocolsDB] ✓ Draft angelegt: {draft_id} ({meeting_name})")
        return {
            "id": draft_id,
            "reviewer_token": reviewer_token,
            "expires_at": expires_at,
        }

    def create_assignment(
        self,
        meeting_name: str,
        meeting_datetime: str,
        source: str,
        recording_id: Optional[str] = None,
        recording_title: Optional[str] = None,
        recording_duration: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Legt die Zuordnungs-Stufe an — Stufe 1 des zweistufigen Workflows.

        Anders als create_draft() entsteht hier noch kein Protokolltext: die
        Zeile hält nur fest, dass eine Aufnahme existiert und auf Svens
        Zuordnung wartet. Erst nach confirm_assignment() zieht Mara das
        Transkript — bis dahin kann in Plaud die Sprecherzuordnung korrigiert
        werden, ohne dass die Korrektur ins Leere läuft.

        draft_markdown/current_markdown sind im Schema NOT NULL und bleiben
        deshalb vorerst leer.
        """
        draft_id = str(uuid.uuid4())
        reviewer_token = secrets.token_urlsafe(32)
        now = _utcnow_iso()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=TOKEN_TTL_DAYS)
        ).isoformat()

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO protocols (
                    id, source, recording_id, recording_title, recording_duration,
                    meeting_name, meeting_datetime,
                    teilnehmer, draft_markdown, current_markdown,
                    status, reviewer_token, reviewer_emails, expires_at,
                    last_modified, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', '', 'pending_assignment', ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    source,
                    recording_id,
                    recording_title,
                    recording_duration,
                    meeting_name,
                    meeting_datetime,
                    json.dumps([], ensure_ascii=False),
                    reviewer_token,
                    json.dumps([], ensure_ascii=False),
                    expires_at,
                    now,
                    now,
                ),
            )
            conn.commit()

        logger.info(
            "[ProtocolsDB] ✓ Zuordnung angelegt: %s (%s)", draft_id, meeting_name
        )
        return {
            "id": draft_id,
            "reviewer_token": reviewer_token,
            "expires_at": expires_at,
        }

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def get_by_id(self, draft_id: str) -> Optional[Dict[str, Any]]:
        """Holt ein Protokoll per ID. None falls unbekannt."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM protocols WHERE id = ?", (draft_id,)
            )
            row = cursor.fetchone()
            return self._row_to_dict(row) if row else None

    def get_by_token(self, reviewer_token: str) -> Optional[Dict[str, Any]]:
        """
        Holt ein Protokoll per Reviewer-Token. None falls unbekannt.

        Hinweis: Ablauf-Prüfung macht der Aufrufer via is_expired() —
        so kann der Endpoint zwischen 404 (unbekannt) und 410 (abgelaufen)
        unterscheiden.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM protocols WHERE reviewer_token = ?",
                (reviewer_token,),
            )
            row = cursor.fetchone()
            return self._row_to_dict(row) if row else None

    def list_finalized_since(
        self, since: Optional[str] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Holt alle finalisierten Protokolle seit `since` (ISO-Timestamp).

        Args:
            since: ISO-Timestamp; None = alle finalisierten
            limit: Max. Anzahl (default 50)
        """
        with self._get_connection() as conn:
            if since:
                cursor = conn.execute(
                    """
                    SELECT * FROM protocols
                    WHERE status = 'finalized' AND finalized_at > ?
                    ORDER BY finalized_at ASC
                    LIMIT ?
                    """,
                    (since, limit),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM protocols
                    WHERE status = 'finalized'
                    ORDER BY finalized_at ASC
                    LIMIT ?
                    """,
                    (limit,),
                )
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def get_by_recording_id(self, recording_id: str) -> Optional[Dict[str, Any]]:
        """
        Holt das jüngste Protokoll zu einer Plaud-Aufnahme.

        Der Poller nutzt das, um nicht zweimal dieselbe Zuordnung anzulegen,
        und Mara, um den Draft-Text in die richtige Zeile zu schreiben.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM protocols WHERE recording_id = ?"
                " ORDER BY created_at DESC LIMIT 1",
                (recording_id,),
            )
            row = cursor.fetchone()
            return self._row_to_dict(row) if row else None

    def list_without_transcript(self) -> List[Dict[str, Any]]:
        """
        Protokolle mit Plaud-Aufnahme, aber ohne gespeichertes Transkript.

        Grundlage für das Nachtragen der Bestandsprotokolle.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM protocols"
                " WHERE recording_id IS NOT NULL AND recording_id != ''"
                "   AND (transcript IS NULL OR transcript = '')"
                " ORDER BY created_at"
            )
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def list_with_recording(self) -> List[Dict[str, Any]]:
        """
        Alle Protokolle mit Plaud-Aufnahme, unabhängig davon, ob schon ein
        Transkript gespeichert ist.

        Für das erneute Ziehen nach einer Sprecherkorrektur in Plaud: die
        archivierten Transkripte tragen dann die alte Zuordnung und sind
        wertlos — schlimmer noch, sie sehen brauchbar aus.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM protocols"
                " WHERE recording_id IS NOT NULL AND recording_id != ''"
                "   AND status NOT IN ('discarded', 'rejected')"
                " ORDER BY meeting_datetime"
            )
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def list_pending_assignments(
        self, older_than_hours: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        Alle Aufnahmen, die noch auf Svens Zuordnung warten.

        Mit older_than_hours nur die, deren letzte Erinnerung (ersatzweise:
        deren Anlage) länger zurückliegt — die Grundlage des Nachfass-Laufs.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM protocols WHERE status = 'pending_assignment'"
                " ORDER BY created_at ASC"
            )
            rows = [self._row_to_dict(row) for row in cursor.fetchall()]

        if older_than_hours is None:
            return rows

        cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
        due: List[Dict[str, Any]] = []
        for row in rows:
            reference = row.get("reminder_sent_at") or row.get("created_at")
            try:
                ts = datetime.fromisoformat(reference)
            except (TypeError, ValueError):
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts <= cutoff:
                due.append(row)
        return due

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------
    def confirm_assignment(
        self,
        draft_id: str,
        event_id: Optional[str],
        eingeladene: Optional[List[str]] = None,
        asana_board_gid: Optional[str] = None,
        asana_section_gid: Optional[str] = None,
        create_asana_task: bool = True,
        ablageort: Optional[str] = None,
        meeting_name: Optional[str] = None,
        meeting_datetime: Optional[str] = None,
    ) -> bool:
        """
        Übernimmt Svens Zuordnung und gibt die Aufnahme für Mara frei.

        Teilnehmer werden hier bewusst NICHT gesetzt. Zum Zeitpunkt der
        Freigabe sind sie noch nicht bekannt: sie ergeben sich aus der
        Sprecherzuordnung, die Sven unmittelbar vor der Freigabe in Plaud
        korrigiert hat und die Mara erst mit dem Transkript sieht.

        Gespeichert werden stattdessen die Eingeladenen aus dem Outlook-Termin
        — als Gegenprobe für Mara, nicht als Teilnehmerliste.

        meeting_name/meeting_datetime sind optional — sie kommen aus dem
        gewählten Outlook-Termin und ersetzen dann den Plaud-Titel und die
        Aufnahmezeit, die beide ungenau sein können.
        """
        now = _utcnow_iso()
        fields = [
            "event_id = ?",
            "eingeladene = ?",
            "asana_board_gid = ?",
            "asana_section_gid = ?",
            "create_asana_task = ?",
            "status = 'assigned'",
            "assigned_at = ?",
            "last_modified = ?",
        ]
        params: List[Any] = [
            event_id,
            json.dumps(eingeladene or [], ensure_ascii=False),
            asana_board_gid,
            asana_section_gid,
            1 if create_asana_task else 0,
            now,
            now,
        ]
        if ablageort is not None:
            fields.append("ablageort = ?")
            params.append(ablageort)
        if meeting_name:
            fields.append("meeting_name = ?")
            params.append(meeting_name)
        if meeting_datetime:
            fields.append("meeting_datetime = ?")
            params.append(meeting_datetime)
        params.append(draft_id)

        with self._get_connection() as conn:
            cursor = conn.execute(
                f"UPDATE protocols SET {', '.join(fields)} WHERE id = ?",
                params,
            )
            conn.commit()
            return cursor.rowcount > 0

    def save_transcript(self, draft_id: str, transcript: str) -> bool:
        """
        Legt das Rohtranskript zum Protokoll ab.

        Getrennt von attach_draft_markdown, damit sich Transkripte auch für
        bereits bestehende Protokolle nachtragen lassen, ohne deren Text
        anzufassen.
        """
        if not transcript:
            return False
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE protocols SET transcript = ?, transcript_saved_at = ? WHERE id = ?",
                (transcript, _utcnow_iso(), draft_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def attach_draft_markdown(
        self,
        draft_id: str,
        markdown: str,
        teilnehmer: Optional[List[str]] = None,
        transcript: Optional[str] = None,
        modified_by: str = "mara",
    ) -> bool:
        """
        Setzt den von Mara erzeugten Protokolltext in eine zugeordnete Zeile
        und hebt sie damit auf 'draft' — den Zustand, in dem der bestehende
        Review-Editor übernimmt.

        Hier kommen auch die Teilnehmer an: Mara hat sie aus der korrigierten
        Sprecherzuordnung ermittelt. Erst ab diesem Moment sind sie bekannt.
        teilnehmer=None lässt einen vorhandenen Stand unangetastet.

        Die Review-Frist beginnt neu. Ohne das wäre ein neu gefasstes
        Protokoll nicht aufrufbar, sobald der ursprüngliche Token älter als
        TOKEN_TTL_DAYS ist — bei den Altprotokollen von Juni betraf das 35
        von 41. Der Link führte dann auf „abgelaufen", obwohl der Inhalt
        frisch war.
        """
        now = _utcnow_iso()
        neu_bis = (
            datetime.now(timezone.utc) + timedelta(days=TOKEN_TTL_DAYS)
        ).isoformat()
        fields = [
            "draft_markdown = ?",
            "current_markdown = ?",
            "status = 'draft'",
            "expires_at = ?",
            "last_modified = ?",
            "last_modified_by = ?",
        ]
        params: List[Any] = [markdown, markdown, neu_bis, now, modified_by]
        if teilnehmer is not None:
            fields.insert(2, "teilnehmer = ?")
            params.insert(2, json.dumps(teilnehmer, ensure_ascii=False))
        if transcript:
            fields.insert(2, "transcript = ?")
            params.insert(2, transcript)
            fields.insert(3, "transcript_saved_at = ?")
            params.insert(3, now)
        params.append(draft_id)

        with self._get_connection() as conn:
            cursor = conn.execute(
                f"UPDATE protocols SET {', '.join(fields)} WHERE id = ?",
                params,
            )
            conn.commit()
            return cursor.rowcount > 0

    def discard(self, draft_id: str, reason: str = "Fehlaufnahme") -> bool:
        """
        Verwirft eine Aufnahme, zu der kein Protokoll entstehen soll.

        Gedacht für versehentliche Mitschnitte — die Aufnahme bleibt in Plaud,
        hier verschwindet sie aus dem Workflow. Ein eigener Status statt
        'rejected': abgelehnt heißt „Protokoll überarbeiten", verworfen heißt
        „hier kommt nie eines".

        Die Zeile bleibt erhalten, damit nachvollziehbar ist, warum zu dieser
        Aufnahme nichts existiert. Der Poller meldet sie nicht erneut, weil
        ihr Eintrag in der state.db bestehen bleibt.
        """
        now = _utcnow_iso()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE protocols
                SET status = 'discarded', rejection_reason = ?, last_modified = ?
                WHERE id = ?
                """,
                (reason, now, draft_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def mark_reminder_sent(self, draft_id: str) -> bool:
        """Vermerkt eine verschickte Erinnerung für den Nachfass-Rhythmus."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE protocols"
                " SET reminder_sent_at = ?, reminder_count = COALESCE(reminder_count, 0) + 1"
                " WHERE id = ?",
                (_utcnow_iso(), draft_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def update_markdown(
        self, draft_id: str, markdown: str, modified_by: str = "reviewer"
    ) -> bool:
        """Speichert den aktuellen Editor-Stand."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE protocols
                SET current_markdown = ?, last_modified = ?, last_modified_by = ?
                WHERE id = ?
                """,
                (markdown, _utcnow_iso(), modified_by, draft_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def set_status(self, draft_id: str, status: str) -> bool:
        """Setzt den Status (z.B. draft → in_review beim Öffnen des Editors)."""
        if status not in VALID_STATUSES:
            raise ValueError(f"Ungültiger Status: {status}")
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE protocols SET status = ?, last_modified = ? WHERE id = ?",
                (status, _utcnow_iso(), draft_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def set_approved(
        self,
        draft_id: str,
        event_id: str,
        asana_board_gid: Optional[str] = None,
        asana_section_gid: Optional[str] = None,
        create_asana_task: bool = True,
        approved_by: str = "reviewer",
    ) -> bool:
        """
        Speichert die Reviewer-Auswahl (Termin, Asana-Checkbox, Board + Section)
        und setzt Status auf 'approved'. Löscht einen evtl. alten
        finalization_error. Board/Section sind nur Pflicht wenn
        create_asana_task=True — das validiert der Endpoint.
        """
        now = _utcnow_iso()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE protocols
                SET event_id = ?, asana_board_gid = ?, asana_section_gid = ?,
                    create_asana_task = ?,
                    status = 'approved', approved_at = ?,
                    finalization_error = NULL,
                    last_modified = ?, last_modified_by = ?
                WHERE id = ?
                """,
                (
                    event_id,
                    asana_board_gid,
                    asana_section_gid,
                    1 if create_asana_task else 0,
                    now,
                    now,
                    approved_by,
                    draft_id,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0

    def set_finalized(
        self,
        draft_id: str,
        asana_task_gid: Optional[str] = None,
        asana_task_url: Optional[str] = None,
    ) -> bool:
        """Markiert das Protokoll als finalisiert (Hintergrund-Job erfolgreich)."""
        now = _utcnow_iso()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE protocols
                SET status = 'finalized', finalized_at = ?,
                    asana_task_gid = ?, asana_task_url = ?,
                    finalization_error = NULL, last_modified = ?
                WHERE id = ?
                """,
                (now, asana_task_gid, asana_task_url, now, draft_id),
            )
            conn.commit()
            if cursor.rowcount > 0:
                logger.info(f"[ProtocolsDB] ✓ Protokoll finalisiert: {draft_id}")
                return True
            return False

    def set_finalization_error(self, draft_id: str, error: str) -> bool:
        """
        Hintergrund-Job gescheitert: Status zurück auf 'in_review',
        Fehlertext speichern — Reviewer kann erneut freigeben.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE protocols
                SET status = 'in_review', finalization_error = ?, last_modified = ?
                WHERE id = ?
                """,
                (error, _utcnow_iso(), draft_id),
            )
            conn.commit()
            if cursor.rowcount > 0:
                logger.warning(
                    f"[ProtocolsDB] ⚠️ Finalisierung fehlgeschlagen für {draft_id}: {error}"
                )
                return True
            return False

    def set_rejected(
        self, draft_id: str, reason: str, rejected_by: str = "reviewer"
    ) -> bool:
        """Lehnt das Protokoll ab und speichert den Grund."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE protocols
                SET status = 'rejected', rejection_reason = ?,
                    last_modified = ?, last_modified_by = ?
                WHERE id = ?
                """,
                (reason, _utcnow_iso(), rejected_by, draft_id),
            )
            conn.commit()
            return cursor.rowcount > 0
