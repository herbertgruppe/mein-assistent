-- Migration 003: protocols-Tabelle für den Web-Review-Editor
-- Ausführen mit: sqlite3 data/protocols.db < migrations/003_protocols_table.sql
-- Hinweis: ProtocolsDB._init_db() führt dieses Schema auch automatisch beim
-- API-Start aus (CREATE TABLE IF NOT EXISTS) — manuelle Ausführung optional.

CREATE TABLE IF NOT EXISTS protocols (
  id                TEXT PRIMARY KEY,        -- UUID v4
  source            TEXT NOT NULL,           -- 'plaud-poller','email','manual','mara-generated','audio-transcribed'
  recording_id      TEXT,                    -- Plaud-ID
  audio_ref         TEXT,                    -- Phase-3-Feld, jetzt immer NULL
  meeting_name      TEXT NOT NULL,
  meeting_datetime  TEXT NOT NULL,           -- ISO-8601, Hint für Calendar-Picker
  event_id          TEXT,                    -- Outlook-Event-ID; NULL bis Reviewer auswählt
  asana_board_gid   TEXT,                    -- NULL bis Reviewer auswählt
  asana_section_gid TEXT,                    -- NULL bis Reviewer auswählt
  create_asana_task INTEGER NOT NULL DEFAULT 1,  -- Checkbox: Asana-Task + Subtasks bei Finalisierung anlegen
  ablageort         TEXT,                    -- Vault-Pfad Hint für späteren Claudian-Sync
  teilnehmer        TEXT,                    -- JSON-Array als String
  draft_markdown    TEXT NOT NULL,           -- Original von Mara
  current_markdown  TEXT NOT NULL,           -- Letzter Editor-Stand
  status            TEXT NOT NULL DEFAULT 'draft',  -- 'draft','in_review','approved','rejected','finalized'
  reviewer_token    TEXT NOT NULL UNIQUE,    -- secrets.token_urlsafe(32)
  reviewer_emails   TEXT,                    -- JSON-Array als String
  expires_at        TEXT NOT NULL,           -- Token-Ablauf ISO-8601
  rejection_reason  TEXT,
  asana_task_gid    TEXT,                    -- gesetzt bei Finalisierung
  asana_task_url    TEXT,
  last_modified     TEXT NOT NULL,
  last_modified_by  TEXT,
  created_at        TEXT NOT NULL,
  approved_at       TEXT,
  finalized_at      TEXT,
  finalization_error TEXT                    -- Fehlertext falls Hintergrund-Job scheitert
);

CREATE INDEX IF NOT EXISTS idx_protocols_status ON protocols(status);
CREATE INDEX IF NOT EXISTS idx_protocols_token ON protocols(reviewer_token);
CREATE INDEX IF NOT EXISTS idx_protocols_finalized_at ON protocols(finalized_at);
