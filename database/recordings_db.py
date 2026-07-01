"""
Recordings Database — DB-Layer für das Protokoll-Recording-Tracking (HBE-1526).

Jede Plaud-Aufnahme bekommt einen Eintrag, der den gesamten Lifecycle von
'new' bis 'done' oder 'abandoned' abbildet.

Pattern wie database/protocols_db.py: Raw SQLite, kein ORM.
Schema: migrations/004_protocol_recordings.sql (CREATE TABLE IF NOT EXISTS,
wird beim Init automatisch ausgeführt).
"""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

VALID_STATUSES = {"new", "speakers_pending", "speakers_ok", "review_ready", "done", "abandoned"}

_MIGRATION_FILE = (
    Path(__file__).resolve().parent.parent / "migrations" / "004_protocol_recordings.sql"
)

_SCHEMA_FALLBACK = """
CREATE TABLE IF NOT EXISTS protocol_recordings (
    id                  TEXT PRIMARY KEY,
    plaud_title         TEXT,
    recorded_at         TEXT,
    duration_seconds    INTEGER,
    status              TEXT NOT NULL DEFAULT 'new',
    speakers_confirmed  INTEGER NOT NULL DEFAULT 0,
    protocol_draft_id   TEXT,
    protocol_pdf_url    TEXT,
    paperclip_issue_id  TEXT,
    notes               TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_recordings_status ON protocol_recordings(status);
CREATE INDEX IF NOT EXISTS idx_recordings_recorded_at ON protocol_recordings(recorded_at);
"""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RecordingsDB:
    """
    SQLite-Datenbank für Protokoll-Recording-Tracking.

    Status-Flow:
        new → speakers_pending → speakers_ok → review_ready → done
        any → abandoned
    """

    def __init__(self, db_path: str = "data/recordings.db"):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        if _MIGRATION_FILE.exists():
            schema = _MIGRATION_FILE.read_text(encoding="utf-8")
        else:
            schema = _SCHEMA_FALLBACK
        with self._get_connection() as conn:
            conn.executescript(schema)
            conn.commit()
        logger.info("[RecordingsDB] ✓ Datenbank initialisiert: %s", self.db_path)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        d["speakers_confirmed"] = bool(d.get("speakers_confirmed", 0))
        return d

    # ------------------------------------------------------------------
    # Create / Upsert
    # ------------------------------------------------------------------
    def upsert(
        self,
        recording_id: str,
        plaud_title: Optional[str] = None,
        recorded_at: Optional[str] = None,
        duration_seconds: Optional[int] = None,
        paperclip_issue_id: Optional[str] = None,
        status: str = "new",
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Legt einen neuen Recording-Eintrag an oder aktualisiert einen bestehenden
        (idempotent bei erneutem Poller-Lauf). Status wird nur beim initialen
        INSERT gesetzt — ein späterer Upsert überschreibt den aktuellen Status nicht.
        """
        now = _utcnow_iso()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO protocol_recordings
                    (id, plaud_title, recorded_at, duration_seconds,
                     paperclip_issue_id, status, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    plaud_title        = COALESCE(excluded.plaud_title, plaud_title),
                    recorded_at        = COALESCE(excluded.recorded_at, recorded_at),
                    duration_seconds   = COALESCE(excluded.duration_seconds, duration_seconds),
                    paperclip_issue_id = COALESCE(excluded.paperclip_issue_id, paperclip_issue_id),
                    notes              = COALESCE(excluded.notes, notes),
                    updated_at         = excluded.updated_at
                """,
                (
                    recording_id,
                    plaud_title,
                    recorded_at,
                    duration_seconds,
                    paperclip_issue_id,
                    status,
                    notes,
                    now,
                    now,
                ),
            )
            conn.commit()
        logger.info("[RecordingsDB] upsert recording %s (status=%s)", recording_id, status)
        return self.get_by_id(recording_id)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def get_by_id(self, recording_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM protocol_recordings WHERE id = ?", (recording_id,)
            )
            row = cursor.fetchone()
            return self._row_to_dict(row) if row else None

    def list_recordings(
        self,
        status: Optional[str] = None,
        limit: int = 100,
        stale_days: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Listet Recordings — optional gefiltert nach status.
        Mit stale_days werden nur Recordings zurückgegeben, deren updated_at
        älter als N Tage ist (für proaktive Nachfass-Abfragen).
        """
        conditions = []
        params: list = []

        if status:
            conditions.append("status = ?")
            params.append(status)

        if stale_days is not None:
            from datetime import timedelta
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=stale_days)
            ).isoformat()
            conditions.append("updated_at < ?")
            params.append(cutoff)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)

        with self._get_connection() as conn:
            cursor = conn.execute(
                f"SELECT * FROM protocol_recordings {where} ORDER BY recorded_at DESC LIMIT ?",
                params,
            )
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def list_open(self) -> List[Dict[str, Any]]:
        """Alle nicht-abgeschlossenen Recordings (nicht done/abandoned)."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM protocol_recordings
                WHERE status NOT IN ('done', 'abandoned')
                ORDER BY recorded_at DESC
                """,
            )
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------
    def update(
        self,
        recording_id: str,
        status: Optional[str] = None,
        speakers_confirmed: Optional[bool] = None,
        protocol_draft_id: Optional[str] = None,
        protocol_pdf_url: Optional[str] = None,
        paperclip_issue_id: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Aktualisiert beliebige Felder eines Recording-Eintrags."""
        if status and status not in VALID_STATUSES:
            raise ValueError(f"Ungültiger Status: {status!r} — erlaubt: {VALID_STATUSES}")

        fields = []
        params = []

        if status is not None:
            fields.append("status = ?")
            params.append(status)
        if speakers_confirmed is not None:
            fields.append("speakers_confirmed = ?")
            params.append(1 if speakers_confirmed else 0)
        if protocol_draft_id is not None:
            fields.append("protocol_draft_id = ?")
            params.append(protocol_draft_id)
        if protocol_pdf_url is not None:
            fields.append("protocol_pdf_url = ?")
            params.append(protocol_pdf_url)
        if paperclip_issue_id is not None:
            fields.append("paperclip_issue_id = ?")
            params.append(paperclip_issue_id)
        if notes is not None:
            fields.append("notes = ?")
            params.append(notes)

        if not fields:
            return self.get_by_id(recording_id)

        fields.append("updated_at = ?")
        params.append(_utcnow_iso())
        params.append(recording_id)

        with self._get_connection() as conn:
            cursor = conn.execute(
                f"UPDATE protocol_recordings SET {', '.join(fields)} WHERE id = ?",
                params,
            )
            conn.commit()
            if cursor.rowcount == 0:
                return None

        logger.info("[RecordingsDB] updated recording %s", recording_id)
        return self.get_by_id(recording_id)
