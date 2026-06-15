#!/usr/bin/env python3
"""
lena_mail_triage_poller.py

Pollt Svens Outlook-Posteingang alle N Sekunden und kategorisiert un-kategorisierte
Mails mit zwei Outlook-Kategorien: 1× Aktion (Lena: Antworten | Tun | Warten |
Recherchieren | Weiterleiten | Ablegen) + 1× Priorität (Priorität: Hoch | Mittel
| Niedrig).

v1 (heutige Implementierung): regelbasiert mit drei Pattern-Klassen
(Newsletter/Automated, Kalender-Notification, Dringlichkeits-Signal). Default
"Antworten + Mittel". Designziel: harmlose Vor-Sortierung mit hoher Recall —
Sven korrigiert manuell, in v2 fließt das via Hindsight-Recall in die Logik
zurück.

v2 (Folge-Issue): LLM-basierte Triage (Claude/OpenAI) mit Hindsight-Recall auf
Sven's bisherige Korrekturen pro Absender/Subject-Pattern.

Env-Vars:
  MEIN_ASSISTENT_API_URL          API-Basis (Standard: http://127.0.0.1:8502)
  API_SECRET_KEY                  X-API-Key Header-Wert fuer /api/lena/*
  LENA_MAIL_TRIAGE_POLL_INTERVAL_SEC  Polling-Intervall (Standard: 600 = 10 Min)
  LENA_MAIL_TRIAGE_LOOKBACK_DAYS     Erst-Lauf Lookback (Standard: 7)
  LENA_MAIL_TRIAGE_BATCH_LIMIT       Max Mails pro Cycle (Standard: 50)
  LENA_MAIL_TRIAGE_STATE_FILE        State-File
  LENA_MAIL_TRIAGE_LOG_FILE          Log-Datei
  TELEGRAM_BOT_TOKEN                 Fuer Hoch-Prio-Alerts (optional)
  TELEGRAM_ADMIN_CHAT_ID             Chat-ID (Sven)
"""
from __future__ import annotations

import json
import logging
import os
import re
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
POLL_INTERVAL_SEC = int(os.getenv("LENA_MAIL_TRIAGE_POLL_INTERVAL_SEC", "600"))
LOOKBACK_DAYS     = int(os.getenv("LENA_MAIL_TRIAGE_LOOKBACK_DAYS", "7"))
BATCH_LIMIT       = int(os.getenv("LENA_MAIL_TRIAGE_BATCH_LIMIT", "50"))
STATE_FILE        = os.getenv("LENA_MAIL_TRIAGE_STATE_FILE", "/opt/mein-assistent/data/lena-mail-triage-poller.state")
LOG_FILE          = os.getenv("LENA_MAIL_TRIAGE_LOG_FILE", "/var/log/lena-mail-triage-poller/lena-mail-triage-poller.log")

API_URL  = os.getenv("MEIN_ASSISTENT_API_URL", "http://127.0.0.1:8502")
API_KEY  = os.getenv("API_SECRET_KEY", "")

TG_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_ADMIN_CHAT = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")

MAX_PROCESSED_IDS  = 5_000   # cap to prevent unbounded state
MAX_BACKOFF_SEC    = 300
TELEGRAM_HOCH_PRIO_DAILY_CAP = 5  # max alerts/day (anti-spam)


# ── Triage-Regeln (v1, regelbasiert) ──────────────────────────────────────────
# Newsletter/Automated-Sender — werden auf Ablegen + Niedrig gesetzt.
NEWSLETTER_SENDER_PATTERNS = [
    r'^noreply@',
    r'^no-reply@',
    r'^newsletter@',
    r'^marketing@',
    r'^mailings?@',
    r'^donotreply@',
    r'^do-not-reply@',
    r'^updates?@',
    r'^notifications?@',
    r'^notify@',
    r'@mailchimp\.',
    r'@sendgrid\.',
    r'@email\.linkedin\.com$',
    r'@email\.xing\.com$',
]
# Kalender-Notifications (Einladungen/Absagen) — Ablegen + Niedrig.
CALENDAR_SUBJECT_PATTERNS = [
    r'^Einladung:',
    r'^Annahme:',
    r'^Absage:',
    r'^Aktualisiert:',
    r'^Vorlaeufige Annahme:',
    r'^Vorläufige Annahme:',
    r'^Accepted:',
    r'^Declined:',
    r'^Updated:',
    r'^Canceled:',
    r'^Tentatively Accepted:',
]
# Dringlichkeit/Frist — Antworten + Hoch.
URGENCY_PATTERNS = [
    r'\bmahnung\b',
    r'\bdringend\b',
    r'\bfrist\b',
    r'\beilig\b',
    r'\burgent\b',
    r'\basap\b',
    r'\b(letzte|finale)\s+erinnerung\b',
]

NEWSLETTER_SENDER_RE = re.compile('|'.join(NEWSLETTER_SENDER_PATTERNS), re.IGNORECASE)
CALENDAR_SUBJECT_RE  = re.compile('|'.join(CALENDAR_SUBJECT_PATTERNS), re.IGNORECASE)
URGENCY_RE           = re.compile('|'.join(URGENCY_PATTERNS), re.IGNORECASE)


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
    stream_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    handlers: List[logging.Handler] = [stream_handler]
    try:
        log_path = Path(LOG_FILE)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(str(log_path), maxBytes=10_485_760, backupCount=5)
        fh.setFormatter(_JsonFormatter())
        handlers.append(fh)
    except OSError as exc:
        print(f"[warn] Cannot open log file {LOG_FILE}: {exc}", file=sys.stderr)
    log = logging.getLogger("lena_mail_triage_poller")
    log.setLevel(logging.INFO)
    for h in handlers:
        log.addHandler(h)
    log.propagate = False
    return log


logger = _setup_logging()


# ── State ─────────────────────────────────────────────────────────────────────
def _load_state() -> Dict[str, Any]:
    path = Path(STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception as exc:
            logger.warning("State file parse error, resetting: %s", exc)
    return {
        "processed_message_ids": [],
        "last_triage_at": "",
        "telegram_alerts_today": [],  # list of ISO timestamps within last 24h
    }


def _save_state(state: Dict[str, Any]) -> None:
    path = Path(STATE_FILE)
    # Cap processed_message_ids
    ids = state.get("processed_message_ids", [])
    if len(ids) > MAX_PROCESSED_IDS:
        state["processed_message_ids"] = ids[-MAX_PROCESSED_IDS:]
    # Cap telegram_alerts_today to last 24h
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    state["telegram_alerts_today"] = [
        ts for ts in state.get("telegram_alerts_today", [])
        if _try_parse_iso(ts) and _try_parse_iso(ts) > cutoff
    ]
    # Atomic write via temp + rename
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    tmp.replace(path)


def _try_parse_iso(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


# ── Triage-Logik (v1: regelbasiert) ───────────────────────────────────────────
def triage_mail(subject: str, sender_email: str, body_preview: str) -> Tuple[str, str, str]:
    """
    Returns (action, priority, rule_id).

    rule_id ist eine kurze Begründung für Audit-Trail (z.B. "newsletter_sender",
    "calendar_subject", "urgency_keyword", "default"). Hilft beim spaeteren
    LLM-Migrationsschritt (v2): Sven sieht warum eine Mail wie kategorisiert
    wurde.
    """
    subj = subject or ""
    sender = (sender_email or "").lower()
    body = (body_preview or "")

    # Regel 1: Kalender-Notifications -> Ablegen + Niedrig
    if CALENDAR_SUBJECT_RE.search(subj):
        return "ablegen", "niedrig", "calendar_subject"

    # Regel 2: Newsletter/Automated-Sender -> Ablegen + Niedrig
    if NEWSLETTER_SENDER_RE.search(sender):
        return "ablegen", "niedrig", "newsletter_sender"

    # Regel 3: Dringlichkeit in Subject ODER Body -> Antworten + Hoch
    if URGENCY_RE.search(subj) or URGENCY_RE.search(body):
        return "antworten", "hoch", "urgency_keyword"

    # Default: Antworten + Mittel
    return "antworten", "mittel", "default"


# ── API-Helpers ───────────────────────────────────────────────────────────────
def _api_headers() -> Dict[str, str]:
    return {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json",
    }


def _fetch_inbox_for_triage() -> List[Dict[str, Any]]:
    url = f"{API_URL.rstrip('/')}/api/lena/mail/inbox-for-triage"
    params = {"days": LOOKBACK_DAYS, "limit": BATCH_LIMIT}
    resp = requests.get(url, headers=_api_headers(), params=params, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"inbox-for-triage HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json().get("mails", [])


def _categorize_mail(message_id: str, action: str, priority: str) -> bool:
    url = f"{API_URL.rstrip('/')}/api/lena/mail/categorize"
    payload = {"message_id": message_id, "action": action, "priority": priority}
    resp = requests.post(url, headers=_api_headers(), json=payload, timeout=30)
    if resp.status_code != 200:
        logger.warning("categorize HTTP %d: %s", resp.status_code, resp.text[:200])
        return False
    return True


# ── Telegram-Alert (Hoch-Prio mit Daily-Cap) ──────────────────────────────────
def _tg_alert(text: str, state: Dict[str, Any]) -> None:
    if not (TG_BOT_TOKEN and TG_ADMIN_CHAT):
        return
    # Anti-Spam: max N Alerts pro 24h
    alerts = state.get("telegram_alerts_today", [])
    if len(alerts) >= TELEGRAM_HOCH_PRIO_DAILY_CAP:
        logger.info("Telegram alert suppressed — daily cap %d reached.", TELEGRAM_HOCH_PRIO_DAILY_CAP)
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_ADMIN_CHAT, "text": text, "parse_mode": "Markdown"},
            timeout=15,
        )
        if r.status_code == 200:
            state.setdefault("telegram_alerts_today", []).append(
                datetime.now(timezone.utc).isoformat()
            )
    except Exception as exc:
        logger.warning("Telegram alert failed: %s", exc)


# ── Polling-Loop ──────────────────────────────────────────────────────────────
def _poll_once(state: Dict[str, Any]) -> Dict[str, int]:
    """Run one triage pass. Returns counter dict for logging."""
    counters = {
        "fetched": 0,
        "skipped_processed": 0,
        "categorized": 0,
        "failed": 0,
        "high_priority": 0,
    }
    mails = _fetch_inbox_for_triage()
    counters["fetched"] = len(mails)

    processed = set(state.get("processed_message_ids", []))
    new_processed: List[str] = list(state.get("processed_message_ids", []))

    for m in mails:
        mid = m.get("message_id", "")
        if not mid:
            continue
        if mid in processed:
            counters["skipped_processed"] += 1
            continue

        action, priority, rule_id = triage_mail(
            m.get("subject", ""),
            m.get("sender_email", ""),
            m.get("body_preview", ""),
        )

        ok = _categorize_mail(mid, action, priority)
        if not ok:
            counters["failed"] += 1
            continue
        counters["categorized"] += 1
        new_processed.append(mid)

        logger.info(json.dumps({
            "event":       "mail_categorized",
            "message_id":  mid,
            "subject":     (m.get("subject", "") or "")[:120],
            "sender":      (m.get("sender_email", "") or ""),
            "action":      action,
            "priority":    priority,
            "rule":        rule_id,
        }, ensure_ascii=False))

        if priority == "hoch":
            counters["high_priority"] += 1
            _tg_alert(
                f"⚠️ *Lena-Triage Hoch-Prio*\n"
                f"_Von:_ {m.get('sender_name') or m.get('sender_email','')}\n"
                f"_Betreff:_ {(m.get('subject') or '')[:100]}\n"
                f"_Regel:_ `{rule_id}`",
                state,
            )

    state["processed_message_ids"] = new_processed
    state["last_triage_at"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)
    return counters


_RUNNING = True


def _sig_handler(sig: int, _frame: object) -> None:
    global _RUNNING
    logger.info("Received signal %d — shutting down", sig)
    _RUNNING = False


def main() -> None:
    if not API_KEY:
        logger.error("API_SECRET_KEY nicht gesetzt — Abbruch.")
        sys.exit(1)
    signal.signal(signal.SIGTERM, _sig_handler)
    signal.signal(signal.SIGINT, _sig_handler)

    state = _load_state()
    logger.info(
        "lena_mail_triage_poller starting — interval=%ds lookback_days=%d batch_limit=%d api=%s state_file=%s",
        POLL_INTERVAL_SEC, LOOKBACK_DAYS, BATCH_LIMIT, API_URL, STATE_FILE,
    )

    backoff = 0
    cycle = 0
    while _RUNNING:
        cycle += 1
        try:
            counters = _poll_once(state)
            logger.info(
                "Cycle %d done: %s",
                cycle,
                json.dumps({
                    "timestamp":     datetime.now(timezone.utc).isoformat(),
                    "cycle":         cycle,
                    **counters,
                }, ensure_ascii=False),
            )
            backoff = 0
        except Exception as exc:
            logger.exception("Cycle %d failed: %s", cycle, exc)
            backoff = min(backoff * 2 + 30, MAX_BACKOFF_SEC)

        sleep_for = backoff if backoff else POLL_INTERVAL_SEC
        for _ in range(sleep_for):
            if not _RUNNING:
                break
            time.sleep(1)

    logger.info("lena_mail_triage_poller stopped")


if __name__ == "__main__":
    main()
