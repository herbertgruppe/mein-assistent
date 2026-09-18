#!/usr/bin/env python3
"""
plaud_poller.py

Pollt Plaud-Account(s) periodisch und erstellt Paperclip-Issues fuer neue Aufnahmen.

Env-Vars:
  PLAUD_POLL_INTERVAL_SEC       Polling-Intervall in Sek (Standard: 600)
  PLAUD_MIN_DURATION_SEC        Min-Dauer Filter (Standard: 180 = 3 Min)
  PLAUD_DB_PATH                 SQLite-Pfad (Standard: /var/lib/plaud/state.db)
  PLAUD_LOG_FILE                Log-Datei (Standard: /var/log/plaud/plaud-poller.log)
  PLAUD_ACCOUNTS                Multi-Account: "home_dir:agent_id,home_dir2:agent_id2"
                                Standard (Sven): "/var/lib/plaud:PAPERCLIP_PROTOKOLL_AGENT_ID"
  PLAUD_RECENT_DAYS             Tage fuer `plaud recent --days N` (Standard: 1)
  PAPERCLIP_API_URL             Paperclip-API-Basis-URL
  PAPERCLIP_API_KEY_MA          Bearer-Token fuer Paperclip (mein-assistent App-Key)
  PAPERCLIP_COMPANY_ID_MA       HBE Company-ID
  PAPERCLIP_PROTOKOLL_AGENT_ID  Protokoll-Agent-ID (Empfaenger der Issues, Fallback: CEO)
  TELEGRAM_BOT_TOKEN            Telegram-Bot-Token fuer Fehler-Alerts
  TELEGRAM_ADMIN_CHAT_ID        Chat-ID (Sven) fuer Alerts
"""
from __future__ import annotations

import json
import logging
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
POLL_INTERVAL_SEC    = int(os.getenv("PLAUD_POLL_INTERVAL_SEC", "600"))
MIN_DURATION_SEC     = int(os.getenv("PLAUD_MIN_DURATION_SEC",  "180"))
DB_PATH              = os.getenv("PLAUD_DB_PATH",  "/var/lib/plaud/state.db")
LOG_FILE             = os.getenv("PLAUD_LOG_FILE", "/var/log/plaud/plaud-poller.log")
RECENT_DAYS          = int(os.getenv("PLAUD_RECENT_DAYS", "1"))

PC_API_URL      = os.getenv("PAPERCLIP_API_URL",    "https://paperclip.herbertgruppe.com")
PC_API_KEY      = os.getenv("PAPERCLIP_API_KEY_MA", "")
PC_COMPANY_ID   = os.getenv("PAPERCLIP_COMPANY_ID_MA", "")
PC_PROTOKOLL_AGENT_ID = os.getenv("PAPERCLIP_PROTOKOLL_AGENT_ID", "")

TG_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN",    "")
TG_ADMIN_CHAT = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")

# ── Zweistufiger Workflow ─────────────────────────────────────────────────────
# Stufe 1 meldet die Aufnahme und wartet auf Svens Zuordnung; erst danach zieht
# Mara das Transkript. Grund: eine Sprecherkorrektur in Plaud wirkt nur, solange
# das Transkript noch nicht geholt ist. Mit TWO_STAGE=false laeuft der alte
# einstufige Weg (Issue direkt an Mara) unveraendert weiter.
TWO_STAGE        = os.getenv("PLAUD_TWO_STAGE", "false").strip().lower() in ("1", "true", "yes")
MA_API_URL       = os.getenv("MEIN_ASSISTENT_API_URL", "http://127.0.0.1:8502").rstrip("/")
MA_API_KEY       = os.getenv("API_SECRET_KEY", "")

# Multi-Account-Format: "home_dir:agent_id,home_dir2:agent_id2"
# home_dir ist das HOME-Verzeichnis fuer den jeweiligen plaud-Token (~/.plaud/tokens.json)
PLAUD_ACCOUNTS_ENV = os.getenv("PLAUD_ACCOUNTS", f"/var/lib/plaud:{PC_PROTOKOLL_AGENT_ID}")

MAX_BACKOFF_SEC = 300
ALERT_THRESHOLD = 3  # consecutive errors before Telegram alert

# ── Dead-man switch ───────────────────────────────────────────────────────────
# The 2026-09 outage stayed invisible for four days because a broken parser and
# a quiet week look identical from the outside: both report "0 recordings".
# These three watchdogs make silence distinguishable from failure.
SILENCE_ALERT_HOURS  = int(os.getenv("PLAUD_SILENCE_ALERT_HOURS", "72"))
TOKEN_WARN_HOURS     = int(os.getenv("PLAUD_TOKEN_WARN_HOURS", "48"))
ALERT_COOLDOWN_HOURS = int(os.getenv("PLAUD_ALERT_COOLDOWN_HOURS", "24"))

# Demo/Tutorial-Aufnahmen überspringen (HBE-1212).
# Pipe-separierte, case-insensitive Substring-Liste. Standard: Plaud-Tutorial-Video.
_SKIP_TITLE_PATTERNS: List[str] = [
    p.strip().lower()
    for p in os.getenv("PLAUD_SKIP_TITLE_PATTERNS", "How to use Plaud|Plaud Tutorial|Let's walk through how to use Plaud").split("|")
    if p.strip()
]


# ── Logging ───────────────────────────────────────────────────────────────────
class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "time":  datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "msg":   record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def _setup_logging() -> logging.Logger:
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    handlers: List[logging.Handler] = [stream_handler]

    try:
        log_path = Path(LOG_FILE)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(str(log_path), maxBytes=10_485_760, backupCount=5)
        fh.setFormatter(_JsonFormatter())
        handlers.append(fh)
    except OSError as exc:
        print(f"[warn] Cannot open log file {LOG_FILE}: {exc}", file=sys.stderr)

    logger = logging.getLogger("plaud_poller")
    logger.setLevel(logging.INFO)
    for h in handlers:
        logger.addHandler(h)
    logger.propagate = False
    return logger


logger = _setup_logging()


# ── Recording-ID shapes ───────────────────────────────────────────────────────
# Bare form (pre-2026-09-15):  cecd2ad55fff73d23ac083c1e2f7c646
# Prefixed form (current):     of_cecd2ad55fff73d23ac083c1e2f7c646
_RECORDING_ID_PREFIX_RE = re.compile(r'^[a-z]{1,8}_')
_RECORDING_ID_RE = re.compile(r'^(?:[a-z]{1,8}_)?[0-9a-f]{32}$')


# ── SQLite ─────────────────────────────────────────────────────────────────────
def _init_db(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plaud_processed_recordings (
            recording_id     TEXT PRIMARY KEY,
            start_at         TEXT,
            processed_at     TEXT NOT NULL,
            issue_identifier TEXT,
            account_home     TEXT,
            status           TEXT
        )
    """)
    # Safe migration: add status column to existing DBs (no-op if already present)
    try:
        conn.execute(
            "ALTER TABLE plaud_processed_recordings ADD COLUMN status TEXT"
        )
    except sqlite3.OperationalError:
        pass
    # Safe migration: add tracking columns for HBE-1527 (no-op if already present)
    try:
        conn.execute("ALTER TABLE plaud_processed_recordings ADD COLUMN tracking_status TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE plaud_processed_recordings ADD COLUMN tracking_notes TEXT")
    except sqlite3.OperationalError:
        pass
    # Safe migration: recording_title is written by _mark_processed but was never
    # part of CREATE TABLE — a freshly built state DB would crash on first write.
    try:
        conn.execute("ALTER TABLE plaud_processed_recordings ADD COLUMN recording_title TEXT")
    except sqlite3.OperationalError:
        pass
    # Key/value scratch space for the dead-man switch (last alert timestamps).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS poller_meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def _meta_get(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM poller_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO poller_meta (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def _id_variants(recording_id: str) -> List[str]:
    """
    All spellings under which a recording may appear in the state DB.

    Plaud changed its CLI output format around 2026-09-15: IDs are now emitted
    with a type prefix (``of_<32-hex>``) where they used to be bare 32-hex
    strings. Rows written before that change carry the bare form, rows written
    after it carry the prefixed form. Dedup must match across both so that a
    format flip — in either direction — never re-processes an old recording.

    The *raw* ID as emitted by the CLI stays authoritative for CLI calls and for
    new DB writes; only the lookup is widened.
    """
    variants = [recording_id]
    bare = _RECORDING_ID_PREFIX_RE.sub("", recording_id, count=1)
    if bare != recording_id:
        variants.append(bare)
    return variants


def _is_processed(conn: sqlite3.Connection, recording_id: str) -> bool:
    variants = _id_variants(recording_id)
    placeholders = ",".join("?" * len(variants))
    return conn.execute(
        f"SELECT 1 FROM plaud_processed_recordings WHERE recording_id IN ({placeholders})",
        variants,
    ).fetchone() is not None


def _get_status(conn: sqlite3.Connection, recording_id: str) -> Optional[str]:
    variants = _id_variants(recording_id)
    placeholders = ",".join("?" * len(variants))
    row = conn.execute(
        f"SELECT status FROM plaud_processed_recordings WHERE recording_id IN ({placeholders})",
        variants,
    ).fetchone()
    return row[0] if row else None


def _mark_processed(
    conn: sqlite3.Connection,
    recording_id: str,
    start_at: str,
    issue_identifier: str,
    account_home: str,
    recording_title: Optional[str] = None,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO plaud_processed_recordings"
        " (recording_id, start_at, processed_at, issue_identifier, account_home, recording_title)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            recording_id,
            start_at,
            datetime.now(timezone.utc).isoformat(),
            issue_identifier,
            account_home,
            recording_title,
        ),
    )
    conn.commit()


# ── Plaud CLI helpers ──────────────────────────────────────────────────────────
def _run_plaud(args: List[str], home_dir: str, timeout: int = 60) -> str:
    """Run `plaud <args>` with HOME overridden for multi-account support."""
    env = {**os.environ, "HOME": home_dir}
    result = subprocess.run(
        ["plaud"] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"plaud {' '.join(args)} exited {result.returncode}: {result.stderr.strip()}"
        )
    return result.stdout


def _parse_recent_ids(output: str) -> List[str]:
    """
    Extract recording IDs from `plaud recent --days N` output.
    Tries JSON first, then line-by-line heuristic.
    """
    ids: List[str] = []
    stripped = output.strip()
    if not stripped:
        return ids

    # Try JSON array
    if stripped.startswith("["):
        try:
            data = json.loads(stripped)
            for item in data:
                if isinstance(item, dict):
                    rid = item.get("id") or item.get("recording_id") or item.get("uuid")
                    if rid:
                        ids.append(str(rid))
                elif isinstance(item, str) and len(item) >= 8:
                    ids.append(item)
            return ids
        except json.JSONDecodeError:
            pass

    # Line-by-line: first whitespace-separated token that looks like a Plaud file ID.
    # Plaud file IDs are 32 lowercase hex characters (UUID without dashes), since
    # ~2026-09-15 preceded by a short type prefix (e.g. "of_"). Both spellings are
    # accepted; the ID is returned exactly as emitted, because the CLI only
    # resolves the form it printed (`plaud file <bare-hex>` → NOT_FOUND).
    for line in stripped.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        first = line.split()[0] if line.split() else ""
        if _RECORDING_ID_RE.match(first):
            ids.append(first)
        elif first:
            logger.debug("Ignoring non-ID token from plaud recent output: %r", first)

    return ids


def _parse_reported_count(output: str) -> Optional[int]:
    """
    Read the recording count the CLI states in its own header.

    `plaud recent` prints "Recordings in the last 7 days: 4" before the list.
    Comparing that number against the number of IDs actually parsed turns a
    silent parser failure into a loud one — had this existed in September 2026,
    the prefixed-ID regression would have alerted within ten minutes instead of
    going unnoticed for four days.

    Returns None if the header is absent (format changed, nothing to compare).
    """
    match = re.search(r'Recordings\s+in\s+the\s+last\s+\d+\s+days?:\s*(\d+)', output, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _parse_file_metadata(output: str) -> Dict[str, Any]:
    """Parse `plaud file <id>` output into a metadata dict."""
    stripped = output.strip()
    meta: Dict[str, Any] = {}

    # Try JSON
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

    # Try key: value lines
    for line in stripped.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip().lower().replace(" ", "_").replace("-", "_")] = value.strip()

    return meta


def _extract_duration_sec(meta: Dict[str, Any]) -> int:
    """
    Extract duration in seconds from metadata dict.
    Handles: int seconds, "HH:MM:SS", "MM:SS", "Xs" strings.
    """
    raw = meta.get("duration") or meta.get("duration_sec") or meta.get("length") or ""
    if not raw:
        return 0

    if isinstance(raw, (int, float)):
        return int(raw)

    raw_str = str(raw).strip()

    # "123s" or "123" pure number
    if re.match(r'^\d+\.?\d*s?$', raw_str):
        return int(float(raw_str.rstrip("s")))

    # "HH:MM:SS" or "MM:SS"
    parts = raw_str.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(float(parts[2]))
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(float(parts[1]))
    except ValueError:
        pass

    return 0


def _format_duration(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


# ── Paperclip issue builder ────────────────────────────────────────────────────
def _build_issue_payload(
    recording_id: str,
    meta: Dict[str, Any],
    summary: str,
    agent_id: str,
) -> dict:
    name     = meta.get("name") or meta.get("title") or recording_id
    start_at = meta.get("start_at") or meta.get("date") or meta.get("created_at") or ""
    duration_sec = _extract_duration_sec(meta)
    duration_str = _format_duration(duration_sec) if duration_sec else str(meta.get("duration", ""))

    has_audio      = bool(meta.get("audio_url") or meta.get("audio") or meta.get("has_audio"))
    has_transcript = bool(meta.get("transcript_url") or meta.get("transcript") or meta.get("has_transcript"))
    has_summary    = bool(summary.strip())

    audio_str = "verfuegbar" if has_audio else "nicht verfuegbar"
    transcript_str = "verfuegbar" if has_transcript else "nicht verfuegbar"
    summary_str = "verfuegbar" if has_summary else "nicht verfuegbar"

    description = (
        f"**Recording-ID:** {recording_id}\n"
        f"**start_at:** {start_at}\n"
        f"**Dauer:** {duration_str}\n"
        f"**Audio:** {audio_str}\n"
        f"**Transkript:** {transcript_str}\n"
        f"**Zusammenfassung:** {summary_str}\n"
        "\n---\n\n"
        + (summary.strip() if summary.strip() else "_Keine Zusammenfassung verfuegbar._")
    )

    return {
        "title":           f"\U0001f4dd Neue Plaud-Aufnahme: {name}",
        "description":     description,
        "assigneeAgentId": agent_id,
        "priority":        "medium",
    }


# ── Paperclip API ──────────────────────────────────────────────────────────────
def _create_pc_issue(payload: dict) -> Optional[str]:
    if not PC_API_KEY:
        logger.warning("PAPERCLIP_API_KEY_MA not set — skipping issue creation")
        return None
    url = f"{PC_API_URL}/api/companies/{PC_COMPANY_ID}/issues"
    resp = requests.post(
        url,
        json=payload,
        headers={"Authorization": f"Bearer {PC_API_KEY}"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return str(data.get("identifier") or data.get("id", "?"))


# ── Zweistufiger Workflow: Stufe 1 ─────────────────────────────────────────────
def _create_assignment(
    recording_id: str,
    title: str,
    start_at: str,
    duration_sec: int,
) -> Optional[str]:
    """
    Meldet eine neue Aufnahme zur Zuordnung an und schickt Sven den Link.

    Bewusst ohne Transkript und ohne Zusammenfassung: beides wuerde die
    Sprecherkorrektur in Plaud wirkungslos machen, weil der Text dann schon
    gezogen waere. Mara kommt erst zum Zug, wenn Sven bestaetigt hat.

    Returns eine kurze Referenz fuer die state.db, oder None bei Fehlschlag.
    """
    if not MA_API_KEY:
        logger.error("[assignment] API_SECRET_KEY fehlt — Zuordnung nicht moeglich")
        return None

    resp = requests.post(
        f"{MA_API_URL}/api/protocols/assignment",
        headers={"X-API-Key": MA_API_KEY, "Content-Type": "application/json"},
        json={
            "meeting_name": title,
            "meeting_datetime": start_at,
            "source": "plaud-poller",
            "recording_id": recording_id,
            "recording_title": title,
            "recording_duration": _format_duration(duration_sec) if duration_sec else None,
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()

    if not data.get("created", True):
        logger.info("[assignment] %s war bereits gemeldet — keine zweite Meldung", recording_id)
        return f"assignment:{data.get('draft_id', '?')}"

    _tg_alert(
        f"🎙 <b>Neue Aufnahme wartet auf Zuordnung</b>\n"
        f"<b>{title}</b>\n"
        f"Dauer: {_format_duration(duration_sec) if duration_sec else 'unbekannt'}\n\n"
        f"<b>Bitte zuerst in Plaud die Sprecher pruefen</b> — Aufnahme "
        f"„{title}\". Danach hier zuordnen:\n"
        f"{data.get('assignment_url', '')}\n\n"
        f"Das Transkript wird erst nach deiner Bestaetigung gezogen."
    )
    return f"assignment:{data.get('draft_id', '?')}"


# ── Telegram ───────────────────────────────────────────────────────────────────
def _tg_alert(text: str) -> None:
    if not TG_BOT_TOKEN or not TG_ADMIN_CHAT:
        logger.warning(
            "Telegram alert suppressed — set TELEGRAM_BOT_TOKEN + TELEGRAM_ADMIN_CHAT_ID"
        )
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_ADMIN_CHAT, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as exc:
        logger.error("Telegram alert failed: %s", exc)


def _alert_throttled(conn: sqlite3.Connection, key: str, text: str) -> bool:
    """
    Send a Telegram alert at most once per ALERT_COOLDOWN_HOURS per key.

    A watchdog that fires every 10 minutes trains the reader to ignore it, which
    is the same failure mode as no alert at all. Returns True if sent.
    """
    now = time.time()
    last_raw = _meta_get(conn, f"alert:{key}")
    if last_raw:
        try:
            if now - float(last_raw) < ALERT_COOLDOWN_HOURS * 3600:
                logger.debug("Alert %r suppressed (cooldown active)", key)
                return False
        except ValueError:
            pass
    _tg_alert(text)
    _meta_set(conn, f"alert:{key}", str(now))
    logger.warning("Dead-man alert sent: %s", key)
    return True


def _clear_alert(conn: sqlite3.Connection, key: str) -> None:
    """Reset an alert's cooldown so a recurrence is reported immediately."""
    if _meta_get(conn, f"alert:{key}") is not None:
        conn.execute("DELETE FROM poller_meta WHERE key = ?", (f"alert:{key}",))
        conn.commit()


# ── Plaud Auth: Auto-Refresh ───────────────────────────────────────────────────
def _jwt_expiry(token: str) -> Optional[float]:
    """Unix timestamp from a JWT's exp claim, or None if unreadable."""
    import base64 as _b64
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(_b64.urlsafe_b64decode(payload))
        exp = claims.get("exp")
        return float(exp) if exp else None
    except Exception:
        return None


def _check_refresh_token_expiry(conn: sqlite3.Connection, home_dir: str, tokens: dict) -> None:
    """
    Watchdog 3: warn before the refresh token dies.

    The access token renews itself; the refresh token lasts six days and only
    survives while the poller keeps refreshing it. If the service is down over a
    long holiday — or refreshes keep failing — it expires and only an
    interactive `plaud login` can recover. Warning early keeps that from
    becoming a discovery after the fact.
    """
    exp = _jwt_expiry(tokens.get("refresh_token", ""))
    if exp is None:
        return
    hours_left = (exp - time.time()) / 3600
    if hours_left > TOKEN_WARN_HOURS:
        _clear_alert(conn, f"token_expiry:{home_dir}")
        return
    if hours_left <= 0:
        headline = "🔴 <b>Plaud-Token abgelaufen</b>\nDer Poller kann keine Aufnahmen mehr abholen."
    else:
        headline = (
            f"🟠 <b>Plaud-Token laeuft ab</b>\n"
            f"Noch <b>{hours_left:.0f} Stunden</b> gueltig "
            f"(bis {datetime.fromtimestamp(exp, timezone.utc):%d.%m. %H:%M} UTC)."
        )
    _alert_throttled(
        conn,
        f"token_expiry:{home_dir}",
        f"{headline}\n\n"
        f"<b>So erneuerst du ihn:</b>\n"
        f"1. Lokal <code>plaud login</code> ausfuehren und im Browser bestaetigen\n"
        f"2. Inhalt von <code>~/.plaud/tokens.json</code> an "
        f"<code>POST /plaud/auth/upload-tokens</code> schicken\n\n"
        f"Konto: <code>{home_dir}</code>",
    )


def _auto_refresh_token(home_dir: str, conn: Optional[sqlite3.Connection] = None) -> None:
    """Refresh Plaud access_token if it expires within the next hour."""
    import base64 as _b64
    token_file = Path(home_dir) / ".plaud" / "tokens.json"
    if not token_file.exists():
        # Also try home_dir directly (when home_dir is the .plaud dir itself)
        token_file_alt = Path(home_dir) / "tokens.json"
        if token_file_alt.exists():
            token_file = token_file_alt
        else:
            return
    try:
        tokens = json.loads(token_file.read_text())
        if conn is not None:
            _check_refresh_token_expiry(conn, home_dir, tokens)
        expires_at_ms = tokens.get("expires_at", 0)
        now_ms = time.time() * 1000
        # Refresh if token expires within 60 minutes
        if expires_at_ms - now_ms > 3_600_000:
            return
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            logger.warning("[plaud_auth] Kein Refresh-Token — manueller Login nötig")
            return
        logger.info("[plaud_auth] Access-Token läuft ab, refreshe...")
        basic = _b64.b64encode(b"client_f9e0b214-c11f-434b-8b95-c4497d1feb81:").decode()
        resp = requests.post(
            "https://platform.plaud.ai/developer/api/oauth/third-party/access-token/refresh",
            headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            timeout=15,
        )
        if resp.status_code == 200:
            new_tokens = resp.json()
            if not new_tokens.get("refresh_token"):
                new_tokens["refresh_token"] = refresh_token
            if "expires_in" in new_tokens and "expires_at" not in new_tokens:
                new_tokens["expires_at"] = int((time.time() + new_tokens["expires_in"]) * 1000)
            token_file.write_text(json.dumps(new_tokens, indent=2))
            logger.info("[plaud_auth] Token erfolgreich aktualisiert")
            if conn is not None:
                _clear_alert(conn, f"token_refresh_failed:{home_dir}")
        else:
            logger.error("[plaud_auth] Refresh fehlgeschlagen: %s %s", resp.status_code, resp.text[:200])
            # A failing refresh is how a working poller quietly becomes a dead
            # one: the access token simply runs out a few hours later.
            if conn is not None:
                _alert_throttled(
                    conn,
                    f"token_refresh_failed:{home_dir}",
                    f"⚠️ <b>Plaud-Token-Refresh fehlgeschlagen</b>\n"
                    f"HTTP {resp.status_code} — der Zugang laeuft in Kuerze aus.\n"
                    f"Konto: <code>{home_dir}</code>\n\n"
                    f"Wenn es sich nicht von selbst faengt: lokal <code>plaud login</code>, "
                    f"dann <code>tokens.json</code> an "
                    f"<code>POST /plaud/auth/upload-tokens</code>.",
                )
    except Exception as exc:
        logger.error("[plaud_auth] Refresh-Fehler: %s", exc)


# ── Poll one account ───────────────────────────────────────────────────────────
def _poll_account(
    home_dir: str,
    agent_id: str,
    db: sqlite3.Connection,
) -> Tuple[List[str], List[str], List[str], List[str]]:
    """
    Poll a single Plaud account.
    Returns (new_ids, created_issues, skipped_ids, error_ids).
    """
    new_ids: List[str] = []
    created_issues: List[str] = []
    skipped: List[str] = []
    errors: List[str] = []

    logger.info("Polling account home=%s", home_dir)
    _auto_refresh_token(home_dir, db)
    try:
        recent_out = _run_plaud(["recent", "--days", str(RECENT_DAYS)], home_dir)
    except Exception as exc:
        logger.error("plaud recent failed (home=%s): %s", home_dir, exc)
        raise

    ids = _parse_recent_ids(recent_out)
    logger.info("Found %d recording IDs in recent output", len(ids))

    # Watchdog 1: the CLI says N, we parsed M. Any shortfall means the output
    # format moved and we are dropping recordings on the floor.
    reported = _parse_reported_count(recent_out)
    if reported is not None and reported > len(ids):
        sample = next(
            (ln.strip() for ln in recent_out.splitlines()
             if ln.strip() and not ln.strip().startswith(("-", "#", "Recordings"))),
            "",
        )
        _alert_throttled(
            db,
            f"parse_mismatch:{home_dir}",
            f"⚠️ <b>plaud-poller erkennt Aufnahmen nicht</b>\n"
            f"Plaud meldet <b>{reported}</b> Aufnahmen, erkannt wurden <b>{len(ids)}</b>.\n"
            f"Das Ausgabeformat hat sich vermutlich geaendert.\n"
            f"Nicht erkannte Zeile: <code>{sample[:120]}</code>\n"
            f"Pruefen: <code>journalctl -u plaud-poller -n 50</code>",
        )
    elif reported is not None:
        _clear_alert(db, f"parse_mismatch:{home_dir}")
    else:
        logger.debug("No count header in `plaud recent` output — skipping parse check")

    for recording_id in ids:
        if _is_processed(db, recording_id):
            status = _get_status(db, recording_id)
            if status == "cancelled":
                logger.info("Skip cancelled recording %s (marked cancelled_by_user)", recording_id)
            else:
                logger.debug("Skip already-processed %s", recording_id)
            continue

        new_ids.append(recording_id)

        # Get metadata
        try:
            file_out = _run_plaud(["file", recording_id], home_dir)
            meta = _parse_file_metadata(file_out)
        except Exception as exc:
            logger.error("plaud file %s failed: %s", recording_id, exc)
            errors.append(recording_id)
            continue

        duration_sec = _extract_duration_sec(meta)
        if 0 < duration_sec < MIN_DURATION_SEC:
            logger.info(
                "Skip short recording %s (%ds < %ds)",
                recording_id, duration_sec, MIN_DURATION_SEC,
            )
            skipped.append(recording_id)
            _title = meta.get("name") or meta.get("title") or None
            _mark_processed(db, recording_id, meta.get("start_at", ""), "skipped:too_short", home_dir, recording_title=_title)
            continue

        # Skip Plaud demo/tutorial recordings (HBE-1212)
        recording_name = (meta.get("name") or meta.get("title") or "").lower()
        if _SKIP_TITLE_PATTERNS and any(pattern in recording_name for pattern in _SKIP_TITLE_PATTERNS):
            logger.info(
                "Skip demo/tutorial recording %s (title=%r matches PLAUD_SKIP_TITLE_PATTERNS)",
                recording_id, meta.get("name") or meta.get("title"),
            )
            skipped.append(recording_id)
            _title = meta.get("name") or meta.get("title") or None
            _mark_processed(db, recording_id, meta.get("start_at", ""), "skipped:demo_recording", home_dir, recording_title=_title)
            continue

        _title = meta.get("name") or meta.get("title") or recording_id
        _start = meta.get("start_at") or meta.get("date") or meta.get("created_at") or ""

        if TWO_STAGE:
            # Stufe 1: nur melden. Kein Transkript, keine Zusammenfassung — beides
            # wuerde die Sprecherkorrektur in Plaud wirkungslos machen, weil der
            # Text dann schon gezogen waere.
            try:
                ref = _create_assignment(recording_id, _title, _start, duration_sec)
            except Exception as exc:
                logger.error("Assignment creation failed for %s: %s", recording_id, exc)
                errors.append(recording_id)
                continue
            if ref:
                created_issues.append(ref)
                _mark_processed(db, recording_id, _start, ref, home_dir, recording_title=_title)
                logger.info("Zuordnung angelegt fuer %s (%s)", recording_id, ref)
            continue

        # Get summary (best-effort — don't fail if unavailable)
        summary = ""
        try:
            summary = _run_plaud(["summary", recording_id], home_dir, timeout=120)
        except Exception as exc:
            logger.warning("plaud summary %s failed (continuing): %s", recording_id, exc)

        # Create Paperclip issue
        payload = _build_issue_payload(recording_id, meta, summary, agent_id)
        backoff = 1
        for attempt in range(1, 4):
            try:
                identifier = _create_pc_issue(payload)
                if identifier:
                    created_issues.append(identifier)
                    start_at = meta.get("start_at", "")
                    _recording_title = meta.get("name") or meta.get("title") or None
                    _mark_processed(db, recording_id, start_at, identifier, home_dir, recording_title=_recording_title)
                    logger.info("Created issue %s for recording %s", identifier, recording_id)
                break
            except Exception as exc:
                if attempt == 3:
                    logger.error(
                        "Issue creation failed after 3 attempts for %s: %s",
                        recording_id, exc,
                    )
                    errors.append(recording_id)
                else:
                    logger.warning(
                        "Issue creation attempt %d failed for %s: %s — retry in %ds",
                        attempt, recording_id, exc, backoff,
                    )
                    time.sleep(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF_SEC)

    return new_ids, created_issues, skipped, errors


def _check_silence(conn: sqlite3.Connection) -> None:
    """
    Watchdog 2: nothing processed for SILENCE_ALERT_HOURS.

    The catch-all for failure modes the specific watchdogs miss. It cannot tell
    a quiet week from a broken pipeline, so it is worded as a question rather
    than an alarm, and fires at most once per cooldown.
    """
    last_raw = _meta_get(conn, "last_recording_seen")
    if last_raw is None:
        # First run after the upgrade: start the clock rather than alert on a
        # history we never recorded.
        _meta_set(conn, "last_recording_seen", str(time.time()))
        return
    try:
        hours_quiet = (time.time() - float(last_raw)) / 3600
    except ValueError:
        _meta_set(conn, "last_recording_seen", str(time.time()))
        return
    if hours_quiet < SILENCE_ALERT_HOURS:
        return
    _alert_throttled(
        conn,
        "silence",
        f"🔕 <b>plaud-poller seit {hours_quiet:.0f} Stunden ohne neue Aufnahme</b>\n"
        f"Das kann eine ruhige Phase sein — oder die Kette steht.\n"
        f"Kurz gegenpruefen, ob in Plaud Aufnahmen liegen, die hier nicht ankommen.",
    )


# ── Account config parser ──────────────────────────────────────────────────────
def _parse_accounts() -> List[Tuple[str, str]]:
    """
    Parse PLAUD_ACCOUNTS="home_dir:agent_id,home_dir2:agent_id2"
    Returns list of (home_dir, agent_id) tuples.
    """
    accounts: List[Tuple[str, str]] = []
    for entry in PLAUD_ACCOUNTS_ENV.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" in entry:
            parts = entry.rsplit(":", 1)
            home = parts[0].strip()
            agent = parts[1].strip()
        else:
            home = entry
            agent = PC_PROTOKOLL_AGENT_ID
        if home and agent:
            accounts.append((home, agent))
    if not accounts:
        accounts = [("/root", PC_PROTOKOLL_AGENT_ID)]
    return accounts


# ── Main loop ──────────────────────────────────────────────────────────────────
_running = True


def _handle_signal(signum: int, _frame: Any) -> None:
    global _running
    logger.info("Received signal %d — shutting down", signum)
    _running = False


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    _required = {
        "PAPERCLIP_COMPANY_ID_MA": PC_COMPANY_ID,
        "PAPERCLIP_PROTOKOLL_AGENT_ID": PC_PROTOKOLL_AGENT_ID,
    }
    missing = [k for k, v in _required.items() if not v]
    if missing:
        logger.error("Required env vars not set — aborting: %s", ", ".join(missing))
        sys.exit(1)

    logger.info(
        "plaud_poller starting — interval=%ds min_duration=%ds db=%s",
        POLL_INTERVAL_SEC, MIN_DURATION_SEC, DB_PATH,
    )

    db = _init_db(DB_PATH)
    accounts = _parse_accounts()
    logger.info("Accounts configured: %d", len(accounts))

    consecutive_errors = 0
    cycle_num = 0

    while _running:
        cycle_num += 1
        cycle_start = datetime.now(timezone.utc).isoformat()
        all_new: List[str] = []
        all_created: List[str] = []
        all_skipped: List[str] = []
        all_errors: List[str] = []

        for home_dir, agent_id in accounts:
            try:
                new_ids, created, skipped, errors = _poll_account(home_dir, agent_id, db)
                all_new.extend(new_ids)
                all_created.extend(created)
                all_skipped.extend(skipped)
                all_errors.extend(errors)
                consecutive_errors = 0
            except Exception as exc:
                consecutive_errors += 1
                logger.error(
                    "Account poll failed (home=%s consecutive_errors=%d): %s",
                    home_dir, consecutive_errors, exc,
                )
                all_errors.append(f"account:{home_dir}")
                if consecutive_errors >= ALERT_THRESHOLD:
                    _tg_alert(
                        f"⚠️ <b>plaud-poller Fehler</b>\n"
                        f"Konto: <code>{home_dir}</code>\n"
                        f"Fehler: {consecutive_errors}x in Folge\n"
                        f"Letzter Fehler: {exc}\n"
                        f"Pruefen: <code>journalctl -u plaud-poller -n 50</code>"
                    )

        if all_new:
            _meta_set(db, "last_recording_seen", str(time.time()))
            _clear_alert(db, "silence")
        else:
            _check_silence(db)

        audit = {
            "timestamp":       cycle_start,
            "cycle":           cycle_num,
            "new_recordings":  all_new,
            "created_issues":  all_created,
            "skipped":         all_skipped,
            "errors":          all_errors,
        }
        logger.info("Cycle %d done: %s", cycle_num, json.dumps(audit, ensure_ascii=False))

        # Wait for next cycle (honour shutdown signal promptly)
        elapsed = 0
        while _running and elapsed < POLL_INTERVAL_SEC:
            time.sleep(min(5, POLL_INTERVAL_SEC - elapsed))
            elapsed += 5

    db.close()
    logger.info("plaud_poller stopped")


if __name__ == "__main__":
    main()

