#!/usr/bin/env python3
"""
lena_mail_triage_poller.py

Pollt Svens Outlook-Posteingang alle N Sekunden und kategorisiert un-kategorisierte
Mails mit zwei Outlook-Kategorien: 1× Aktion (Lena: Antworten | Tun | Warten |
Recherchieren | Weiterleiten | Ablegen) + 1× Priorität (Priorität: Hoch | Mittel
| Niedrig).

Hybrid-Triage:
1) Schnelle Regeln zuerst (sparen LLM-Cost):
   - Newsletter/Automated-Sender -> Ablegen + Niedrig
   - Kalender-Notifications -> Ablegen + Niedrig
2) Alles andere -> Claude-Haiku LLM-Call mit Sven-Persona-Context
   und Direktbericht-Liste fuer informierte Priorisierung.

v1 (regelbasiert) war zu konservativ — Default-Bucket "Antworten + Mittel"
hat 80%+ der Mails getroffen. LLM-Triage liest jetzt Subject + Sender +
Body-Preview und entscheidet kontextuell, mit Audit-Trail-Reasoning.

Re-Triage-Mode: LENA_MAIL_TRIAGE_RETRIAGE_ALL=1 ignoriert
processed_message_ids + nutzt include_categorized=true, damit Bestandsinbox
nach Logik-Upgrade neu durchgenudelt wird. Nach einem Lauf ENV wieder
entfernen (oder Service auf normalen Mode restarten).

Env-Vars:
  MEIN_ASSISTENT_API_URL          API-Basis (Standard: http://127.0.0.1:8502)
  API_SECRET_KEY                  X-API-Key Header-Wert fuer /api/lena/*
  ANTHROPIC_API_KEY               Anthropic API-Key fuer LLM-Triage (Pflicht)
  LENA_MAIL_TRIAGE_LLM_MODEL      Claude-Modell (Standard: claude-haiku-4-5)
  LENA_MAIL_TRIAGE_POLL_INTERVAL_SEC  Polling-Intervall (Standard: 600 = 10 Min)
  LENA_MAIL_TRIAGE_LOOKBACK_DAYS     Erst-Lauf Lookback (Standard: 7)
  LENA_MAIL_TRIAGE_BATCH_LIMIT       Max Mails pro Cycle (Standard: 50)
  LENA_MAIL_TRIAGE_RETRIAGE_ALL      "1" = ALL Mails neu triagieren (Bestandsinbox-Lauf)
  LENA_MAIL_TRIAGE_STATE_FILE        State-File
  LENA_MAIL_TRIAGE_LOG_FILE          Log-Datei
  TELEGRAM_BOT_TOKEN                 Fuer Hoch-Prio-Alerts (optional)
  TELEGRAM_ADMIN_CHAT_ID             Chat-ID (Sven)
  LENA_MAIL_TRIAGE_CONFIG_FILE       Pfad zur Persona-Config (Standard: config/lena-mail-triage.yaml)
  LENA_MAIL_TRIAGE_LEARN_THRESHOLD   Anzahl Override-Ereignisse bis Pattern gelernt (Standard: 3)
  LENA_MAIL_TRIAGE_LEARNING_DB       SQLite-Datei fuer Hindsight-Lernloop (Standard: /var/lib/...)
  LENA_MAIL_TRIAGE_OVERRIDE_LOOKBACK_DAYS  Lookback fuer Override-Detection (Standard: 30)
"""
from __future__ import annotations

import json
import logging
import os
import re
import signal
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

try:
    import yaml as _yaml
except ImportError:
    _yaml = None  # graceful degradation — Config-File-Loader fällt auf Hardcoded-Default zurück

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None  # graceful degradation, wir checken in main()

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
POLL_INTERVAL_SEC = int(os.getenv("LENA_MAIL_TRIAGE_POLL_INTERVAL_SEC", "600"))
LOOKBACK_DAYS     = int(os.getenv("LENA_MAIL_TRIAGE_LOOKBACK_DAYS", "7"))
BATCH_LIMIT       = int(os.getenv("LENA_MAIL_TRIAGE_BATCH_LIMIT", "50"))
RETRIAGE_ALL      = os.getenv("LENA_MAIL_TRIAGE_RETRIAGE_ALL", "0").strip() == "1"
STATE_FILE        = os.getenv("LENA_MAIL_TRIAGE_STATE_FILE", "/opt/mein-assistent/data/lena-mail-triage-poller.state")
LOG_FILE          = os.getenv("LENA_MAIL_TRIAGE_LOG_FILE", "/var/log/lena-mail-triage-poller/lena-mail-triage-poller.log")

# Persona-Config-File (externalizes Direktbericht-Liste — Änderungen ohne PR möglich)
_DEFAULT_CONFIG_PATHS = [
    Path(__file__).resolve().parent / "config" / "lena-mail-triage.yaml",
    Path("/opt/mein-assistent/config/lena-mail-triage.yaml"),
]
PERSONA_CONFIG_FILE = os.getenv("LENA_MAIL_TRIAGE_CONFIG_FILE", "")


def _load_persona_config() -> Dict[str, Any]:
    """Loads persona config from YAML. Falls back to empty dict if unavailable."""
    paths = ([Path(PERSONA_CONFIG_FILE)] if PERSONA_CONFIG_FILE else []) + _DEFAULT_CONFIG_PATHS
    for p in paths:
        if p.exists():
            if _yaml is None:
                break  # PyYAML not installed — use hardcoded fallback
            try:
                return _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception as exc:
                print(f"[warn] Cannot parse persona config {p}: {exc}", file=sys.stderr)
    return {}

API_URL  = os.getenv("MEIN_ASSISTENT_API_URL", "http://127.0.0.1:8502")
API_KEY  = os.getenv("API_SECRET_KEY", "")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LLM_MODEL         = os.getenv("LENA_MAIL_TRIAGE_LLM_MODEL", "claude-haiku-4-5")
LLM_MAX_TOKENS    = 200
LLM_TIMEOUT_SEC   = 30

TG_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_ADMIN_CHAT = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")

MAX_PROCESSED_IDS  = 5_000   # cap to prevent unbounded state
MAX_BACKOFF_SEC    = 300
TELEGRAM_HOCH_PRIO_DAILY_CAP = 5  # max alerts/day (anti-spam)

# ── Hindsight-Lernloop-Config ──────────────────────────────────────────────────
LEARN_THRESHOLD     = int(os.getenv("LENA_MAIL_TRIAGE_LEARN_THRESHOLD", "3"))
LEARNING_DB         = os.getenv(
    "LENA_MAIL_TRIAGE_LEARNING_DB",
    "/var/lib/mail-triage-poller/triage_learning.db",
)
OVERRIDE_LOOKBACK_DAYS = int(os.getenv("LENA_MAIL_TRIAGE_OVERRIDE_LOOKBACK_DAYS", "30"))


# ── LLM-Persona für Sven (aus config/lena-mail-triage.yaml) ──────────────────
# Persona-Text wird beim Start aus YAML gebaut. Direktberichte und externe
# Kontakte können ohne Code-PR geändert werden — nur YAML updaten + Service neu.

_PERSONA_CONFIG: Dict[str, Any] = {}  # populated in main() after logging is ready


def _build_sven_persona(cfg: Dict[str, Any]) -> str:
    """Builds the LLM system-prompt from config. Falls back to hardcoded defaults."""
    persona = cfg.get("sven_persona", {})
    titel = persona.get("titel", "Geschäftsführer der Herbert Gruppe")
    mitarbeiter = persona.get("mitarbeiter", 550)
    branche = persona.get("branche", "Gebäudetechnik/TGA")
    region = persona.get("region", "Rhein-Main-Neckar-Raum")
    n_dr = persona.get("direktberichte_count", 13)

    direktberichte = cfg.get("direktberichte", [
        {"name": "Frank Herbert", "funktion": "Kfm. Leiter & Stellv."},
        {"name": "Laura Ann Hernandez-Allmann", "funktion": "Persönliche Assistentin"},
        {"name": "Walter Melcher", "funktion": "Marketing"},
        {"name": "Sven Walter", "funktion": "IT"},
        {"name": "Tim Kneusels", "funktion": "Personal"},
        {"name": "Jan Herbert", "funktion": "Einkauf & Logistik"},
        {"name": "Philipp Scheidlock", "funktion": "QM"},
        {"name": "Dragan Mihaljevic", "funktion": "NL-Leiter HBO/Bornemann Frankfurt"},
        {"name": "Thomas Winzer", "funktion": "NL-Leiter HRN/Rhein-Neckar"},
        {"name": "Thorsten Vogel", "funktion": "NL-Leiter HS/Service"},
        {"name": "René Turtschan", "funktion": "NL-Leiter HRE/Reibstein Nauheim"},
        {"name": "Franjo Senk", "funktion": "Teamleiter TGM"},
        {"name": "Lev Keimes", "funktion": "NL-Leiter Dimexcon Innovation & Digitalisierung"},
    ])
    externe = cfg.get("externe_wichtige_kontakte", [
        {"name": "Caroline Flick", "context": "Volksbank Aufsichtsrat, künftige AR-Vorsitzende 2027", "default_prioritaet": "hoch"},
        {"name": "SHK Aktiv", "context": "Verband", "default_prioritaet": "mittel"},
    ])
    routing = cfg.get("weiterleitung_routing", {
        "kfm": "Frank", "marketing": "Walter", "personal": "Tim",
        "it": "Sven Walter", "regional": "jeweiliger NL-Leiter",
    })

    dr_lines = "\n".join(f"- {d['name']} ({d.get('funktion', '')})" for d in direktberichte)
    ext_lines = "\n".join(
        f"- {e['name']} ({e.get('context', '')}) — {e.get('default_prioritaet', 'mittel').capitalize()}"
        for e in externe
    )
    routing_str = (
        f"{routing.get('kfm','Frank')} für kfm. Themen, "
        f"{routing.get('marketing','Walter')} für Marketing, "
        f"{routing.get('personal','Tim')} für Personal, "
        f"{routing.get('it','Sven Walter')} für IT, "
        f"{routing.get('regional','jeweiliger NL-Leiter')} für regionale Themen"
    )

    return f"""Du bist Lena, persönliche Assistentin von Sven Herbert.

Sven ist {titel} ({mitarbeiter} Mitarbeiter, {branche},
{region}). Er hat {n_dr} direkte Berichte und führt die Gruppe operativ.

WICHTIGE PERSONEN für E-Mail-Priorität (alle @herbert.de):

DIREKTBERICHTE (Antwort/Aktion meist Hoch- oder Mittel-Prio):
{dr_lines}

EXTERNE WICHTIGE KONTAKTE:
{ext_lines}
- Kunden/Lieferanten — Mittel (kontextabhängig)

AKTION-OPTIONEN:
- antworten: Sven muss zurückschreiben (echte Frage, Bitte um Stellungnahme,
  persönliche Anfrage, AW/Re-Faden mit Frage)
- tun: Sven muss aktiv handeln, aber keine Mail-Antwort (z.B. Dokument
  unterschreiben, Link prüfen, Vereinbarung umsetzen)
- warten: Reine Info, Sven wartet auf Folge von anderen (FYI, Status-Update,
  CC für Awareness)
- recherchieren: Sven muss erst Vorbereitung machen (großer Anhang lesen,
  Hintergrund klären, mit dritter Person abstimmen)
- weiterleiten: Geht eigentlich an jemand anderen ({routing_str})
- ablegen: Keine Aktion, nur archivieren (Marketing-Mails, externe Newsletter,
  Werbung, automated Notifications, FYI ohne Erwartung)

PRIORITÄT:
- hoch: Frist heute/diese Woche, oder von Direktbericht mit konkretem
  Action-Bezug, oder Eskalation/Mahnung
- mittel: Sollte diese Woche erledigt werden (Standard für Direktberichte,
  laufende Themen)
- niedrig: Kann auch mal liegenbleiben (FYI, optional, externe Info)

REGEL: Wenn unsicher → "antworten" + "mittel". Übersetze sparsam zu "weiterleiten"
(nur wenn klar erkennbar dass jemand anderes zuständig).
"""


def _get_sven_persona() -> str:
    return _build_sven_persona(_PERSONA_CONFIG)

TRIAGE_USER_PROMPT_TEMPLATE = """Triagiere folgende E-Mail.

Absender: {sender_name} <{sender_email}>
Betreff: {subject}
Vorschau (erste 500 Zeichen):
{body_preview}

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt der Form:
{{"action": "...", "priority": "...", "reasoning": "kurzer deutscher Satz, max 80 Zeichen"}}

KEIN Markdown, KEIN ```json``` Block, KEIN Fließtext drumherum.
"""


# ── Hindsight-Lernloop (SQLite-Backend) ───────────────────────────────────────

def _init_learning_db() -> None:
    """Erstellt SQLite-Schema für den Hindsight-Lernloop (idempotent)."""
    db_path = Path(LEARNING_DB)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS triage_history (
                message_id      TEXT PRIMARY KEY,
                sender_domain   TEXT NOT NULL,
                subject_prefix  TEXT NOT NULL,
                action          TEXT NOT NULL,
                priority        TEXT NOT NULL,
                categorized_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS triage_overrides (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id        TEXT NOT NULL UNIQUE,
                sender_domain     TEXT NOT NULL,
                subject_prefix    TEXT NOT NULL,
                original_action   TEXT NOT NULL,
                original_priority TEXT NOT NULL,
                override_action   TEXT NOT NULL,
                override_priority TEXT NOT NULL,
                detected_at       TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS triage_patterns (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_domain    TEXT NOT NULL,
                subject_prefix   TEXT NOT NULL,
                learned_action   TEXT NOT NULL,
                learned_priority TEXT NOT NULL,
                count            INTEGER NOT NULL DEFAULT 1,
                first_seen_at    TEXT NOT NULL,
                last_seen_at     TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_pattern_key
                ON triage_patterns(sender_domain, subject_prefix, learned_action, learned_priority);
        """)
        conn.commit()
    finally:
        conn.close()


def _normalize_subject_prefix(subject: str) -> str:
    """Normalisiert Subject für Pattern-Matching (lowercase, erste 30 Zeichen)."""
    # Strip common reply/forward prefixes
    s = re.sub(r'^(re|aw|fwd|wg):\s*', '', (subject or "").lower().strip(), flags=re.IGNORECASE)
    return s[:30].strip()


def _record_categorization(message_id: str, sender_domain: str, subject_prefix: str,
                            action: str, priority: str) -> None:
    """Speichert Lenas Kategorie-Entscheidung für spätere Override-Detection."""
    try:
        conn = sqlite3.connect(LEARNING_DB)
        try:
            conn.execute(
                """INSERT OR REPLACE INTO triage_history
                   (message_id, sender_domain, subject_prefix, action, priority, categorized_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (message_id, sender_domain, subject_prefix, action, priority,
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Failed to record categorization for %s: %s", message_id, exc)


def _detect_and_store_overrides(overrides_from_api: List[Dict[str, Any]]) -> int:
    """
    Vergleicht API-Overrides mit gespeicherten Originalen und speichert echte Overrides.
    Returns: Anzahl neu erkannter Overrides.
    """
    if not overrides_from_api:
        return 0
    new_count = 0
    try:
        conn = sqlite3.connect(LEARNING_DB)
        try:
            for item in overrides_from_api:
                mid = item.get("message_id", "")
                if not mid:
                    continue
                row = conn.execute(
                    "SELECT action, priority FROM triage_history WHERE message_id = ?", (mid,)
                ).fetchone()
                if not row:
                    continue  # kein Original gespeichert → kein Override erkennbar
                orig_action, orig_priority = row
                cur_action = item.get("current_action", "")
                cur_priority = item.get("current_priority", "")
                if cur_action == orig_action and cur_priority == orig_priority:
                    continue  # keine Änderung
                # Echter Override — speichern (IGNORE falls schon bekannt)
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO triage_overrides
                           (message_id, sender_domain, subject_prefix,
                            original_action, original_priority,
                            override_action, override_priority, detected_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            mid,
                            item.get("sender_domain", ""),
                            _normalize_subject_prefix(item.get("subject", "")),
                            orig_action, orig_priority,
                            cur_action, cur_priority,
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                    if conn.execute("SELECT changes()").fetchone()[0] > 0:
                        new_count += 1
                except Exception as e:
                    logger.warning("Failed to store override for %s: %s", mid, e)
            conn.commit()
            # Aggregiere Patterns nach neuem Override
            if new_count > 0:
                _aggregate_patterns(conn)
                conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Override detection DB error: %s", exc)
    return new_count


def _aggregate_patterns(conn: sqlite3.Connection) -> None:
    """Aktualisiert triage_patterns aus triage_overrides (INSERT OR REPLACE by count)."""
    conn.executescript("""
        INSERT INTO triage_patterns
            (sender_domain, subject_prefix, learned_action, learned_priority, count, first_seen_at, last_seen_at)
        SELECT
            sender_domain, subject_prefix, override_action, override_priority,
            COUNT(*) as cnt,
            MIN(detected_at), MAX(detected_at)
        FROM triage_overrides
        GROUP BY sender_domain, subject_prefix, override_action, override_priority
        ON CONFLICT(sender_domain, subject_prefix, learned_action, learned_priority) DO UPDATE SET
            count = excluded.count,
            last_seen_at = excluded.last_seen_at;
    """)


def _hindsight_recall(sender_domain: str, subject_prefix: str) -> Optional[Tuple[str, str, int]]:
    """
    Schaut nach ob ein gelerntes Pattern (threshold erreicht) für diesen Absender/Betreff existiert.
    Returns (action, priority, count) wenn Pattern >= LEARN_THRESHOLD, sonst None.
    """
    if not Path(LEARNING_DB).exists():
        return None
    try:
        conn = sqlite3.connect(LEARNING_DB)
        try:
            row = conn.execute(
                """SELECT learned_action, learned_priority, count
                   FROM triage_patterns
                   WHERE sender_domain = ? AND subject_prefix = ? AND count >= ?
                   ORDER BY count DESC LIMIT 1""",
                (sender_domain, subject_prefix, LEARN_THRESHOLD),
            ).fetchone()
            return (row[0], row[1], row[2]) if row else None
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Hindsight recall DB error: %s", exc)
        return None


def _fetch_categorized_overrides(since_iso: str) -> List[Dict[str, Any]]:
    """Ruft kategorisierte Mails die seit `since_iso` verändert wurden vom Backend ab."""
    url = f"{API_URL.rstrip('/')}/api/lena/mail/categorized-overrides"
    try:
        resp = requests.get(
            url,
            headers=_api_headers(),
            params={"since": since_iso, "limit": "100"},
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning("categorized-overrides HTTP %d: %s", resp.status_code, resp.text[:200])
            return []
        return resp.json().get("overrides", [])
    except Exception as exc:
        logger.warning("categorized-overrides fetch error: %s", exc)
        return []


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


# ── Triage-Logik (Hybrid: Regeln + LLM) ───────────────────────────────────────
_VALID_ACTIONS = {"antworten", "tun", "warten", "recherchieren", "weiterleiten", "ablegen"}
_VALID_PRIORITIES = {"hoch", "mittel", "niedrig"}

_llm_client: Optional[Any] = None


def _get_llm_client():
    """Lazy-init Anthropic-Client (Singleton)."""
    global _llm_client
    if _llm_client is None and Anthropic is not None and ANTHROPIC_API_KEY:
        _llm_client = Anthropic(api_key=ANTHROPIC_API_KEY)
    return _llm_client


def _strip_json_fences(raw: str) -> str:
    """Falls LLM trotz Anweisung Markdown-Fences sendet, entfernen."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```\s*$', '', raw)
    return raw.strip()


def _llm_triage(subject: str, sender_email: str, sender_name: str, body_preview: str) -> Tuple[str, str, str]:
    """LLM-basierte Triage. Returns (action, priority, reasoning_with_prefix).

    Raises Exception bei nicht-recoverable LLM-Fehlern — _triage_mail faengt
    das ab und nutzt Fallback.
    """
    client = _get_llm_client()
    if client is None:
        raise RuntimeError("Anthropic-Client nicht verfuegbar (kein API-Key oder kein Package).")

    prompt = TRIAGE_USER_PROMPT_TEMPLATE.format(
        sender_name=sender_name or "(unbekannt)",
        sender_email=sender_email or "(keine)",
        subject=subject or "(kein Betreff)",
        body_preview=(body_preview or "(leer)")[:500],
    )

    response = client.messages.create(
        model=LLM_MODEL,
        max_tokens=LLM_MAX_TOKENS,
        system=_get_sven_persona(),
        messages=[{"role": "user", "content": prompt}],
        timeout=LLM_TIMEOUT_SEC,
    )
    raw = _strip_json_fences(response.content[0].text)
    data = json.loads(raw)  # raises if malformed → caught by caller

    action = str(data.get("action", "")).strip().lower()
    priority = str(data.get("priority", "")).strip().lower()
    reasoning = str(data.get("reasoning", "")).strip()[:120]

    if action not in _VALID_ACTIONS:
        raise ValueError(f"LLM returned invalid action: {action!r}")
    if priority not in _VALID_PRIORITIES:
        raise ValueError(f"LLM returned invalid priority: {priority!r}")

    return action, priority, f"llm:{reasoning}"


def triage_mail(subject: str, sender_email: str, body_preview: str, sender_name: str = "") -> Tuple[str, str, str]:
    """
    Hybrid-Triage: schnelle Regeln → Hindsight-Recall → LLM.

    Returns (action, priority, reasoning).

    Reasoning-Prefixes als Audit-Trail:
      "calendar_subject"           → Regel: Kalender-Notification
      "newsletter_sender"          → Regel: Automated-Sender
      "llm+memory:<domain>/<pfx>"  → Gelerntes Pattern angewendet (Hindsight)
      "llm:<text>"                 → LLM-Entscheidung mit Begruendung
      "llm_failed_<reason>"        → LLM-Aufruf fehlgeschlagen, Default-Fallback
    """
    subj = subject or ""
    sender = (sender_email or "").lower()

    # Regel 1: Kalender-Notifications -> Ablegen + Niedrig (kein LLM-Aufruf)
    if CALENDAR_SUBJECT_RE.search(subj):
        return "ablegen", "niedrig", "calendar_subject"

    # Regel 2: Newsletter/Automated-Sender -> Ablegen + Niedrig (kein LLM-Aufruf)
    if NEWSLETTER_SENDER_RE.search(sender):
        return "ablegen", "niedrig", "newsletter_sender"

    # Regel 3: Hindsight-Recall — gelerntes Pattern (Sven-Override × LEARN_THRESHOLD)
    domain = sender.split("@")[-1] if "@" in sender else sender
    prefix = _normalize_subject_prefix(subj)
    recall = _hindsight_recall(domain, prefix)
    if recall:
        action, priority, count = recall
        return action, priority, f"llm+memory:{domain}/{prefix}(n={count})"

    # Alles andere: LLM-Triage
    try:
        return _llm_triage(subj, sender_email, sender_name, body_preview or "")
    except Exception as exc:
        logger.warning(
            "LLM triage failed for sender=%s subject=%s: %s",
            sender_email, subj[:60], exc,
        )
        # Fallback: regelbasiert mit Urgency-Check
        if URGENCY_RE.search(subj) or URGENCY_RE.search(body_preview or ""):
            return "antworten", "hoch", "llm_failed_urgency_fallback"
        return "antworten", "mittel", "llm_failed_default"


# ── API-Helpers ───────────────────────────────────────────────────────────────
def _api_headers() -> Dict[str, str]:
    return {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json",
    }


def _fetch_inbox_for_triage() -> List[Dict[str, Any]]:
    url = f"{API_URL.rstrip('/')}/api/lena/mail/inbox-for-triage"
    params: Dict[str, Any] = {"days": LOOKBACK_DAYS, "limit": BATCH_LIMIT}
    if RETRIAGE_ALL:
        params["include_categorized"] = "true"
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

    if RETRIAGE_ALL:
        # Im Re-Triage-Mode processed_message_ids ignorieren — alles neu durchnudeln.
        processed: set = set()
        new_processed: List[str] = list(state.get("processed_message_ids", []))
    else:
        processed = set(state.get("processed_message_ids", []))
        new_processed = list(state.get("processed_message_ids", []))

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
            m.get("sender_name", ""),
        )

        ok = _categorize_mail(mid, action, priority)
        if not ok:
            counters["failed"] += 1
            continue
        counters["categorized"] += 1
        new_processed.append(mid)

        # Hindsight: Kategorie-Entscheidung für spätere Override-Detection speichern
        sender_email = m.get("sender_email", "") or ""
        domain = sender_email.lower().split("@")[-1] if "@" in sender_email else sender_email.lower()
        _record_categorization(
            mid, domain,
            _normalize_subject_prefix(m.get("subject", "") or ""),
            action, priority,
        )

        # Rate-Limit-Safety bei großem Re-Triage-Burst:
        # Anthropic SDK macht 429-Retry automatisch, aber wir entlasten den Burst
        # zusätzlich mit einer kleinen Pause zwischen LLM-getriebenen Cycles.
        # Regel-Pfade (calendar_subject, newsletter_sender) brauchen das nicht.
        if rule_id.startswith("llm"):
            time.sleep(0.4)

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
    last_triage_at = state.get("last_triage_at", "")
    state["last_triage_at"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)

    # Hindsight-Lern-Pass: Overrides seit dem letzten Triage-Cycle erkennen
    if not RETRIAGE_ALL and last_triage_at:
        _run_learning_pass(last_triage_at, counters)

    return counters


def _run_learning_pass(since_iso: str, counters: Dict[str, Any]) -> None:
    """Lern-Pass: holt Override-Liste vom Backend und aktualisiert SQLite-Patterns."""
    try:
        overrides = _fetch_categorized_overrides(since_iso)
        new_overrides = _detect_and_store_overrides(overrides)
        counters["learned_overrides"] = new_overrides
        if new_overrides:
            logger.info(json.dumps({
                "event": "hindsight_overrides_detected",
                "count": new_overrides,
                "threshold": LEARN_THRESHOLD,
            }, ensure_ascii=False))
    except Exception as exc:
        logger.warning("Learning pass error: %s", exc)


_RUNNING = True


def _sig_handler(sig: int, _frame: object) -> None:
    global _RUNNING
    logger.info("Received signal %d — shutting down", sig)
    _RUNNING = False


def main() -> None:
    global _PERSONA_CONFIG
    if not API_KEY:
        logger.error("API_SECRET_KEY nicht gesetzt — Abbruch.")
        sys.exit(1)
    if Anthropic is None:
        logger.error("anthropic Python-Package nicht installiert — pip install anthropic")
        sys.exit(1)
    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY nicht gesetzt — Abbruch (LLM-Triage erforderlich).")
        sys.exit(1)
    signal.signal(signal.SIGTERM, _sig_handler)
    signal.signal(signal.SIGINT, _sig_handler)

    _PERSONA_CONFIG = _load_persona_config()
    if _PERSONA_CONFIG:
        dr_count = len(_PERSONA_CONFIG.get("direktberichte", []))
        logger.info("Persona-Config geladen: %d Direktberichte", dr_count)
    else:
        logger.warning(
            "Persona-Config nicht gefunden oder PyYAML fehlt — nutze Hardcoded-Fallback. "
            "Empfehlung: pip install pyyaml && config/lena-mail-triage.yaml prüfen."
        )

    # Hindsight-Lernloop: SQLite-Schema initialisieren
    try:
        _init_learning_db()
        logger.info(
            "Hindsight-Lernloop aktiv: db=%s threshold=%d lookback_days=%d",
            LEARNING_DB, LEARN_THRESHOLD, OVERRIDE_LOOKBACK_DAYS,
        )
    except Exception as exc:
        logger.warning("Hindsight-DB init fehlgeschlagen (Lernloop deaktiviert): %s", exc)

    state = _load_state()
    logger.info(
        "lena_mail_triage_poller starting — interval=%ds lookback_days=%d batch_limit=%d "
        "retriage_all=%s llm_model=%s api=%s state_file=%s",
        POLL_INTERVAL_SEC, LOOKBACK_DAYS, BATCH_LIMIT,
        RETRIAGE_ALL, LLM_MODEL, API_URL, STATE_FILE,
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
