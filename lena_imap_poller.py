#!/usr/bin/env python3
"""
lena_imap_poller.py

Pollt IMAP-Postfach(er) periodisch und erstellt Paperclip-Issues fuer neue Mails.

Env-Vars:
  SMTP_USER                 IMAP-Benutzername (Standard: herbertgruppe-com-0003)
  SMTP_PASSWORD             IMAP-Passwort
  LENA_IMAP_POLL_INTERVAL_SEC   Polling-Intervall in Sekunden (Standard: 300)
  LENA_IMAP_HOST            IMAP-Server (Standard: imaps.udag.de)
  LENA_IMAP_PORT            IMAP-Port (Standard: 993)
  LENA_IMAP_LOG_FILE        Log-Datei (Standard: /var/log/lena-imap-poller.log)
  LENA_IMAP_DB_PATH         SQLite-Pfad fuer Idempotenz (Standard: data/lena_processed_mails.db)
  LENA_POLLER_MAILBOXES     Multi-Mailbox: "imap-user:agent-id,imap-user2:agent-id2"
                            Passwort je Mailbox aus IMAP_PASSWORD_{USER} (- zu _, uppercase),
                            Fallback: SMTP_PASSWORD
  PAPERCLIP_API_URL         Paperclip-API-Basis-URL
  PAPERCLIP_API_KEY_MA      Bearer-Token fuer Paperclip (mein-assistent App-Key)
  PAPERCLIP_COMPANY_ID_MA   HBE Company-ID
  TELEGRAM_BOT_TOKEN        Telegram-Bot-Token fuer Fehler-Alerts
  TELEGRAM_ADMIN_CHAT_ID    Chat-ID (Sven) fuer IMAP-Fehler-Alerts
"""
from __future__ import annotations

import email
import email.header
import imaplib
import json
import logging
import os
import re
import signal
import sqlite3
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
POLL_INTERVAL_SEC = int(os.getenv("LENA_IMAP_POLL_INTERVAL_SEC", "300"))
IMAP_HOST         = os.getenv("LENA_IMAP_HOST", "imaps.udag.de")
IMAP_PORT         = int(os.getenv("LENA_IMAP_PORT", "993"))
LOG_FILE          = os.getenv("LENA_IMAP_LOG_FILE", "/var/log/lena-imap-poller.log")
DB_PATH           = os.getenv("LENA_IMAP_DB_PATH", "data/lena_processed_mails.db")

PC_API_URL      = os.getenv("PAPERCLIP_API_URL", "https://paperclip.herbertgruppe.com")
PC_API_KEY      = os.getenv("PAPERCLIP_API_KEY_MA", "")
PC_COMPANY_ID   = os.getenv("PAPERCLIP_COMPANY_ID_MA", "9df4976b-9ac8-4e8f-a156-c06c7fa40cdc")

TG_BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_ADMIN_CHAT   = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")

DEFAULT_SMTP_USER = os.getenv("SMTP_USER", "")
DEFAULT_SMTP_PASS = os.getenv("SMTP_PASSWORD", "")
MAILBOXES_ENV     = os.getenv("LENA_POLLER_MAILBOXES", "")
LENA_AGENT_ID     = "7517114f-e731-4df5-96cf-a044719e9318"

MAX_BACKOFF_SEC  = 300
MAX_BODY_CHARS   = 4000
ALERT_THRESHOLD  = 3  # consecutive errors before Telegram alert

SVEN_SENDERS         = frozenset({"sven.herbert@herbert.de", "s.herbert@herbertgruppe.com"})
TRANSCRIPT_KEYWORDS  = frozenset({"transkript", "plaud"})
TRANSCRIPT_EXTS      = frozenset({".txt", ".docx"})
RE_NOREPLY           = re.compile(r'\bno[-_]?reply\b', re.IGNORECASE)
RE_EMAIL_ADDR        = re.compile(r'<([^>]+)>')


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

    logger = logging.getLogger("lena_imap_poller")
    logger.setLevel(logging.INFO)
    for h in handlers:
        logger.addHandler(h)
    logger.propagate = False
    return logger


logger = _setup_logging()


# ── SQLite ─────────────────────────────────────────────────────────────────────
def _init_db(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lena_processed_mails (
            message_id   TEXT PRIMARY KEY,
            imap_user    TEXT NOT NULL,
            processed_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def _is_processed(conn: sqlite3.Connection, message_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM lena_processed_mails WHERE message_id = ?", (message_id,)
    ).fetchone() is not None


def _mark_processed(conn: sqlite3.Connection, message_id: str, imap_user: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO lena_processed_mails (message_id, imap_user, processed_at)"
        " VALUES (?, ?, ?)",
        (message_id, imap_user, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


# ── Mail helpers ───────────────────────────────────────────────────────────────
def _decode_header(value: Optional[str]) -> str:
    if not value:
        return ""
    parts = email.header.decode_header(value)
    out = []
    for fragment, charset in parts:
        if isinstance(fragment, bytes):
            out.append(fragment.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(str(fragment))
    return "".join(out).strip()


def _extract_body(msg: email.message.Message) -> str:
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if (part.get_content_type() == "text/plain"
                    and "attachment" not in str(part.get("Content-Disposition", ""))):
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace")
                    break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            body = payload.decode(charset, errors="replace")
    return body[:MAX_BODY_CHARS]


def _bare_addr(from_header: str) -> str:
    m = RE_EMAIL_ADDR.search(from_header)
    return m.group(1).lower() if m else from_header.lower().strip()


# ── Issue classification ───────────────────────────────────────────────────────
def _is_transcript(from_addr: str, subject: str, msg: email.message.Message) -> bool:
    if from_addr not in SVEN_SENDERS:
        return False
    if any(kw in subject.lower() for kw in TRANSCRIPT_KEYWORDS):
        return True
    for part in msg.walk():
        fname = (part.get_filename() or "").lower()
        if any(fname.endswith(ext) for ext in TRANSCRIPT_EXTS):
            return True
    return False


def _build_payload(
    from_header: str,
    from_addr: str,
    subject: str,
    date_str: str,
    message_id: str,
    body: str,
    msg: email.message.Message,
    agent_id: str,
) -> dict:
    if _is_transcript(from_addr, subject, msg):
        title    = f"📝 E-Mail an lena@: {subject}"
        priority = "high"
    elif RE_NOREPLY.search(from_header):
        title    = f"📬 E-Mail an lena@: {subject}"
        priority = "low"
    else:
        title    = f"📬 E-Mail an lena@: {subject}"
        priority = "medium"

    description = (
        f"**Von:** {from_header}\n"
        f"**Datum:** {date_str}\n"
        f"**Message-ID:** {message_id}\n\n"
        "---\n\n"
        f"{body}"
    )
    return {
        "title":           title,
        "description":     description,
        "assigneeAgentId": agent_id,
        "priority":        priority,
    }


# ── Paperclip ──────────────────────────────────────────────────────────────────
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
            json={"chat_id": TG_ADMIN_CHAT, "text": text},
            timeout=10,
        )
    except Exception as exc:
        logger.error("Telegram alert failed: %s", exc)


# ── IMAP poll ──────────────────────────────────────────────────────────────────
def _poll_mailbox(
    imap_user: str,
    imap_password: str,
    agent_id: str,
    db: sqlite3.Connection,
) -> Tuple[int, int]:
    """Poll one mailbox. Returns (new_found, issues_created)."""
    new_found = 0
    issues_created = 0

    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        conn.login(imap_user, imap_password)
        conn.select("INBOX", readonly=True)

        status, data = conn.search(None, "UNSEEN")
        if status != "OK":
            logger.warning(json.dumps({"event": "search_failed", "status": status, "user": imap_user}))
            return 0, 0

        uids = data[0].split()
        for uid in uids:
            try:
                status, msg_data = conn.fetch(uid, "(BODY.PEEK[])")
            except Exception as exc:
                logger.error(json.dumps({"event": "fetch_error", "uid": uid.decode(), "error": str(exc)}))
                continue

            if status != "OK" or not msg_data or msg_data[0] is None:
                continue

            raw = msg_data[0][1]
            if not isinstance(raw, bytes):
                continue

            msg        = email.message_from_bytes(raw)
            message_id = (msg.get("Message-ID") or "").strip()
            if not message_id:
                message_id = f"<synthetic-{imap_user}-{uid.decode()}@poller>"

            if _is_processed(db, message_id):
                continue

            new_found += 1
            subject     = _decode_header(msg.get("Subject", "(kein Betreff)"))
            from_header = _decode_header(msg.get("From", ""))
            date_str    = msg.get("Date", "")
            body        = _extract_body(msg)
            from_addr   = _bare_addr(from_header)

            payload  = _build_payload(
                from_header, from_addr, subject, date_str,
                message_id, body, msg, agent_id,
            )

            try:
                issue_id = _create_pc_issue(payload)
            except Exception as exc:
                logger.error(
                    json.dumps({
                        "event":      "issue_create_failed",
                        "message_id": message_id,
                        "error":      str(exc),
                    })
                )
                continue

            if issue_id:
                _mark_processed(db, message_id, imap_user)
                issues_created += 1
                logger.info(
                    json.dumps({
                        "event":            "issue_created",
                        "imap_user":        imap_user,
                        "message_id":       message_id,
                        "subject":          subject[:80],
                        "paperclip_issue":  issue_id,
                    })
                )
    finally:
        for action in (conn.close, conn.logout):
            try:
                action()
            except Exception:
                pass

    return new_found, issues_created


# ── Mailbox config ─────────────────────────────────────────────────────────────
def _parse_mailboxes() -> List[Tuple[str, str, str]]:
    """Parse LENA_POLLER_MAILBOXES or fall back to SMTP_USER + Lena."""
    mailboxes: List[Tuple[str, str, str]] = []

    if MAILBOXES_ENV:
        for entry in MAILBOXES_ENV.split(","):
            entry = entry.strip()
            if ":" not in entry:
                continue
            imap_user, agent_id = entry.split(":", 1)
            imap_user = imap_user.strip()
            agent_id  = agent_id.strip()
            pw_env    = f"IMAP_PASSWORD_{imap_user.upper().replace('-', '_')}"
            password  = os.getenv(pw_env) or DEFAULT_SMTP_PASS
            if imap_user and agent_id and password:
                mailboxes.append((imap_user, password, agent_id))
            else:
                logger.warning("Skipping %s — missing password (%s) or agent_id", imap_user, pw_env)

    if not mailboxes and DEFAULT_SMTP_USER and DEFAULT_SMTP_PASS:
        mailboxes.append((DEFAULT_SMTP_USER, DEFAULT_SMTP_PASS, LENA_AGENT_ID))

    return mailboxes


# ── Main ───────────────────────────────────────────────────────────────────────
_running = True


def _sig_handler(sig: int, _frame: object) -> None:
    global _running
    logger.info(json.dumps({"event": "signal", "sig": sig}))
    _running = False


def main() -> None:
    signal.signal(signal.SIGTERM, _sig_handler)
    signal.signal(signal.SIGINT, _sig_handler)

    logger.info(json.dumps({"event": "startup", "poll_interval_sec": POLL_INTERVAL_SEC}))

    mailboxes = _parse_mailboxes()
    if not mailboxes:
        logger.error(
            "No mailboxes configured. Set SMTP_USER/SMTP_PASSWORD or LENA_POLLER_MAILBOXES."
        )
        sys.exit(1)

    logger.info(json.dumps({
        "event":   "mailboxes_loaded",
        "count":   len(mailboxes),
        "users":   [m[0] for m in mailboxes],
    }))

    db = _init_db(DB_PATH)
    consecutive_errors = 0
    current_backoff    = POLL_INTERVAL_SEC

    while _running:
        cycle_start = time.monotonic()
        total_new = 0
        total_created = 0
        cycle_ok = True

        for imap_user, imap_password, agent_id in mailboxes:
            try:
                new, created = _poll_mailbox(imap_user, imap_password, agent_id, db)
                total_new     += new
                total_created += created
            except Exception as exc:
                cycle_ok = False
                consecutive_errors += 1
                logger.error(json.dumps({
                    "event":             "poll_error",
                    "imap_user":         imap_user,
                    "error":             str(exc),
                    "consecutive_errors": consecutive_errors,
                }))
                if consecutive_errors >= ALERT_THRESHOLD:
                    _tg_alert(
                        f"🚨 lena-imap-poller: {consecutive_errors} aufeinanderfolgende Fehler\n"
                        f"Host: {IMAP_HOST}\nUser: {imap_user}\nFehler: {exc}"
                    )
                    consecutive_errors = 0  # reset after alert

        if cycle_ok:
            consecutive_errors = 0
            current_backoff    = POLL_INTERVAL_SEC
        else:
            current_backoff = min(current_backoff * 2, MAX_BACKOFF_SEC)

        logger.info(json.dumps({
            "event":          "poll_cycle",
            "new_mails":      total_new,
            "issues_created": total_created,
            "duration_sec":   round(time.monotonic() - cycle_start, 2),
            "next_sleep_sec": current_backoff,
        }))

        deadline = time.monotonic() + current_backoff
        while _running and time.monotonic() < deadline:
            time.sleep(1)

    db.close()
    logger.info(json.dumps({"event": "shutdown"}))


if __name__ == "__main__":
    main()
