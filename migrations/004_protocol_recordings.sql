-- Migration 004: protocol_recordings — Tracking-Tabelle für Plaud-Aufnahmen (HBE-1526)
-- Ausführen mit: sqlite3 data/recordings.db < migrations/004_protocol_recordings.sql
-- Hinweis: RecordingsDB._init_db() führt dieses Schema auch automatisch beim
-- API-Start aus (CREATE TABLE IF NOT EXISTS) — manuelle Ausführung optional.

CREATE TABLE IF NOT EXISTS protocol_recordings (
    id                  TEXT PRIMARY KEY,           -- Plaud recording_id (32-Hex)
    plaud_title         TEXT,                       -- Titel aus Plaud
    recorded_at         TEXT,                       -- ISO-8601 Zeitpunkt der Aufnahme
    duration_seconds    INTEGER,
    status              TEXT NOT NULL DEFAULT 'new', -- new|speakers_pending|speakers_ok|review_ready|done|abandoned
    speakers_confirmed  INTEGER NOT NULL DEFAULT 0,  -- 0=false, 1=true
    protocol_draft_id   TEXT,                       -- Draft-ID aus /api/protocols/draft
    protocol_pdf_url    TEXT,                       -- URL zum fertigen PDF (nach Freigabe)
    paperclip_issue_id  TEXT,                       -- zugehöriges Paperclip-Issue UUID
    notes               TEXT,                       -- freies Notizfeld (z.B. Grund für abandoned)
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_recordings_status ON protocol_recordings(status);
CREATE INDEX IF NOT EXISTS idx_recordings_recorded_at ON protocol_recordings(recorded_at);
