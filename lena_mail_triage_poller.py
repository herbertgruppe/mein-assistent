#!/usr/bin/env python3
"""
lena_mail_triage_poller.py

Pollt Svens Outlook-Posteingang alle N Sekunden und kategorisiert un-kategorisierte
Mails mit genau einer Outlook-Kategorie (Aktion: Lena: Antworten | Tun | Warten |
Recherchieren | Weiterleiten | Ablegen) und setzt die Outlook-Wichtigkeit (importance:
high | normal | low) statt einer zweiten Kategorie.

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
import unicodedata
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
MAX_TRIAGE_RESULTS = 500     # cap for triage_results cache in state
MAX_BACKOFF_SEC    = 300
TELEGRAM_HOCH_PRIO_DAILY_CAP = 5  # max alerts/day (anti-spam)

# ── Hindsight-Lernloop-Config ──────────────────────────────────────────────────
LEARN_THRESHOLD     = int(os.getenv("LENA_MAIL_TRIAGE_LEARN_THRESHOLD", "3"))
LEARNING_DB         = os.getenv(
    "LENA_MAIL_TRIAGE_LEARNING_DB",
    "/var/lib/mail-triage-poller/triage_learning.db",
)
OVERRIDE_LOOKBACK_DAYS = int(os.getenv("LENA_MAIL_TRIAGE_OVERRIDE_LOOKBACK_DAYS", "30"))

# ── Mail-Konzept 2026-09 ──────────────────────────────────────────────────────
# Trockenlauf: alles rechnen und protokollieren, aber KEINE Kategorie setzen und
# KEINE Mail verschieben. Zum Beobachten der neuen Regeln vor Scharfschaltung.
DRY_RUN = os.getenv("LENA_MAIL_TRIAGE_DRY_RUN", "0").strip() == "1"

# Absender-Profil aus der Archiv-Analyse (Antwortquote je Absender).
# Erzeugt von scripts/build_sender_profile.py. Fehlt die Datei, laeuft die
# Triage wie bisher weiter — das Profil ist eine Verbesserung, keine Bedingung.
SENDER_PROFILE_DB = os.getenv(
    "LENA_MAIL_TRIAGE_SENDER_PROFILE_DB",
    "/var/lib/mail-triage-poller/sender_profile.db",
)
# Nur Absender mit dieser Einstufung duerfen automatisch archiviert werden.
# Alle anderen "ablegen"-Urteile werden zum Vorschlag (Mail bleibt im Posteingang).
AUTO_ARCHIVE_VERDICTS = {"safe_archive"}


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

    # Fallback, falls die YAML fehlt. Adressen verifiziert 2026-09-14 aus dem Archiv.
    direktberichte = cfg.get("direktberichte", [
        {"name": "Frank Herbert", "funktion": "Kfm. Leiter & Stellv.", "email": "f.herbert@herbert.de"},
        {"name": "Laura Ann Hernandez-Allmann", "funktion": "Persönliche Assistentin", "email": "l.hernandez-allmann@herbert.de"},
        {"name": "Walter Melcher", "funktion": "Marketing", "email": "w.melcher@herbert.de"},
        {"name": "Sven Walter", "funktion": "IT", "email": "s.walter@herbert.de"},
        {"name": "Tim Kneusels", "funktion": "Personal", "email": "t.kneusels@herbert.de"},
        {"name": "Jan Herbert", "funktion": "Einkauf & Logistik", "email": "jan.herbert@herbert.de"},
        {"name": "Philipp Scheidlock", "funktion": "QM", "email": "p.scheidlock@herbert.de"},
        {"name": "Dragan Mihaljevic", "funktion": "NL-Leiter HBO/Bornemann Frankfurt", "email": "dmihaljevic@bornemann-haustechnik.de"},
        {"name": "Thomas Winzer", "funktion": "NL-Leiter HRN/Rhein-Neckar", "email": "t.winzer@herbert.de"},
        {"name": "Thorsten Vogel", "funktion": "NL-Leiter HS/Service", "email": "t.vogel@herbert.de"},
        {"name": "René Turtschan", "funktion": "NL-Leiter HRE/Reibstein Nauheim", "email": "r.turtschan@reibstein.de"},
        {"name": "Franjo Senk", "funktion": "Teamleiter TGM", "email": "f.senk@herbert.de"},
        {"name": "Lev Keimes", "funktion": "NL-Leiter Dimexcon Innovation & Digitalisierung", "email": "l.keimes@dimexcon.de"},
    ])
    externe = cfg.get("externe_wichtige_kontakte", [
        {"name": "Caroline Flick", "context": "Volksbank Aufsichtsrat, künftige AR-Vorsitzende 2027", "default_prioritaet": "hoch"},
        {"name": "SHK Aktiv", "context": "Verband", "default_prioritaet": "mittel"},
    ])
    routing = cfg.get("weiterleitung_routing", {
        "kfm": "Frank", "marketing": "Walter", "personal": "Tim",
        "it": "Sven Walter", "regional": "jeweiliger NL-Leiter",
    })

    # HBE-3044: Adressen mit ausgeben. Vorher stand im Prompt pauschal
    # "alle @herbert.de" — das stimmt nicht (Dragan schreibt von
    # @bornemann-haustechnik.de, Rene von @reibstein.de, Lev von @dimexcon.de).
    dr_lines = "\n".join(
        f"- {d['name']} ({d.get('funktion', '')})"
        + (f" — {d['email']}" if d.get("email") else "")
        for d in direktberichte
    )
    ext_lines = "\n".join(
        f"- {e['name']} ({e.get('context', '')}) — {e.get('default_prioritaet', 'mittel').capitalize()}"
        for e in externe
    )
    routing_str = (
        f"{routing.get('kfm','Frank Herbert')} für kaufmännische Themen, "
        f"{routing.get('marketing','Walter Melcher')} für Marketing, "
        f"{routing.get('personal','Tim Kneusels')} für Personal, "
        f"{routing.get('it','Sven Walter')} für IT, "
        f"{routing.get('regional','jeweiliger NL-Leiter')} für regionale Themen"
    )

    return f"""Du bist Lena, persönliche Assistentin von Sven Herbert.

Sven ist {titel} ({mitarbeiter} Mitarbeiter, {branche},
{region}). Er hat {n_dr} direkte Berichte und führt die Gruppe operativ.

SVENS DIREKTBERICHTE — nur diese Personen kommen für eine Weiterleitung infrage:
{dr_lines}

Zuständigkeiten: {routing_str}

EXTERNE WICHTIGE KONTAKTE:
{ext_lines}
- Kunden/Lieferanten — Mittel (kontextabhängig)

DIE 5-SCHRITTE-REGEL
Prüfe die Schritte STRENG DER REIHE NACH und nimm den ERSTEN, der zutrifft.

1. LÖSCHEN — Newsletter, Werbung, Kaltakquise, Veranstaltungseinladung ohne
   konkreten Bezug zur Herbert Gruppe, automatische Systemmeldung, Massenmail.
2. ABLEGEN — Information zur Kenntnis. Kein Handlungsbedarf für Sven, aber die
   Mail soll aufbewahrt werden. Auch: Sven wartet auf die Aktion eines anderen,
   oder die Sache ist bereits erledigt.
3. WEITERLEITEN — Das Thema gehört fachlich einem der oben genannten
   Direktberichte, und Sven selbst muss nichts beitragen. NUR wenn du die Person
   konkret benennen kannst. Automatische Systemmails werden NIEMALS
   weitergeleitet. Eine Mail, die an Sven persönlich gerichtet ist, ebenfalls nicht.
4. TERMINIEREN — Sven muss selbst etwas tun, das länger als zwei Minuten dauert
   oder einen Termin braucht. Wird eine Aufgabe.
5. ERLEDIGEN — Sven muss kurz antworten oder etwas in unter zwei Minuten tun.

Im Zweifel zwischen zwei Schritten: nimm den NIEDRIGEREN.

PRIORITÄT:
- hoch: Frist heute oder diese Woche, Eskalation, Mahnung
- mittel: sollte diese Woche erledigt werden
- niedrig: kann liegenbleiben

Antworte AUSSCHLIESSLICH mit JSON:
{{"schritt": 1-5, "empfaenger": "Name oder null", "prioritaet": "hoch|mittel|niedrig",
  "begruendung": "max 12 Wörter"}}
"""


def _get_sven_persona() -> str:
    return _build_sven_persona(_PERSONA_CONFIG)

TRIAGE_USER_PROMPT_TEMPLATE = """Triagiere folgende E-Mail.

Absender: {sender_name} <{sender_email}>
Betreff: {subject}
Vorschau (erste 500 Zeichen):
{body_preview}
{hint_block}
Antworte AUSSCHLIESSLICH mit einem JSON-Objekt der Form:
{{"schritt": 1, "empfaenger": null, "prioritaet": "mittel", "begruendung": "kurzer deutscher Satz, max 80 Zeichen"}}

"schritt" ist eine ZAHL von 1 bis 5 nach der 5-Schritte-Regel.
"empfaenger" nur bei Schritt 3 setzen, sonst null.

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
# HBE-3044: Praefix statt exakter Uebereinstimmung. Vorher verlangte
# `^noreply@` genau "noreply@" — `noreply-dmarc-support@google.com` rutschte
# durch und wurde vom Sprachmodell zur Weiterleitung vorgeschlagen.
# Zusaetzlich: die Maschinen-Kennung darf auch hinten stehen
# (`newsletters-noreply@linkedin.com`, `messaging-digest-noreply@...`).
NEWSLETTER_SENDER_PATTERNS = [
    r'^no-?_?reply',
    r'^do-?not-?reply',
    r'^newsletters?',
    r'^marketing[@._-]',
    r'^mailings?[@._-]',
    r'^mailer[@._-]',
    r'^updates?[@._-]',
    r'^notifications?[@._-]',
    r'^notify[@._-]',
    r'^invitations?[@._-]',
    r'^messaging[@._-]',
    r'^bounce[@._-]',
    r'-no-?reply@',
    r'-noreply@',
    r'_noreply@',
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


# ── Systemabsender-Regeln (Mail-Konzept 2026-09) ─────────────────────────────
# Absender, deren Mails deterministisch behandelt werden — ohne LLM-Aufruf.
# Hintergrund: jobrouter@intern.herbert.de stellt rund ein Drittel des
# Postaufkommens und erzeugte 65 % aller Aktions-Markierungen, obwohl Sven die
# Einzelmails nicht liest (er arbeitet direkt im JobRouter). Die Erinnerung an
# offene Vorgaenge kommt stattdessen als Briefing-Zeile aus der Statistik-Mail.
# Gepflegt in config/lena-mail-triage.yaml unter `system_absender`.

def _load_system_senders(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Liest die Systemabsender-Regeln aus der Config und kompiliert die Ausnahmen."""
    out: List[Dict[str, Any]] = []
    for entry in (cfg.get("system_absender") or []):
        email = (entry.get("email") or "").strip().lower()
        if not email:
            continue
        keywords = [k.strip().lower() for k in (entry.get("ausnahmen_betreff") or []) if k.strip()]
        out.append({
            "email":              email,
            "aktion":             (entry.get("aktion") or "ablegen").strip().lower(),
            "prioritaet":         (entry.get("prioritaet") or "niedrig").strip().lower(),
            "ausnahmen":          keywords,
            "ausnahme_aktion":    (entry.get("ausnahme_aktion") or "tun").strip().lower(),
            "ausnahme_prio":      (entry.get("ausnahme_prioritaet") or "mittel").strip().lower(),
        })
    return out


_SYSTEM_SENDERS: Optional[List[Dict[str, Any]]] = None


def _get_system_senders() -> List[Dict[str, Any]]:
    global _SYSTEM_SENDERS
    if _SYSTEM_SENDERS is None:
        _SYSTEM_SENDERS = _load_system_senders(_PERSONA_CONFIG or _load_persona_config())
    return _SYSTEM_SENDERS


def match_system_sender(sender_email: str, subject: str) -> Optional[Tuple[str, str, str]]:
    """
    Prueft die Systemabsender-Regeln.

    Returns (action, priority, rule_id) oder None wenn keine Regel greift.
    Die Ausnahme-Stichworte gewinnen gegen die Grundregel — so bleiben z. B.
    Mitarbeitereintritte sichtbar, waehrend der Rest abgelegt wird.
    """
    sender = (sender_email or "").strip().lower()
    if not sender:
        return None
    subj = (subject or "").lower()
    for rule in _get_system_senders():
        if sender != rule["email"]:
            continue
        for kw in rule["ausnahmen"]:
            if kw in subj:
                return rule["ausnahme_aktion"], rule["ausnahme_prio"], f"system_sender_exception:{kw}"
        return rule["aktion"], rule["prioritaet"], f"system_sender:{sender}"
    return None


# ── Absender-Profil (Mail-Konzept 2026-09) ────────────────────────────────────
# Aus 59.580 archivierten Posteingangsmails berechnet: wie oft hat Sven einem
# Absender je geantwortet? Nur Absender ohne jede Reaktion bei ausreichender
# Historie duerfen automatisch archiviert werden.

_PROFILE_CACHE: Dict[str, Optional[str]] = {}


def sender_verdict(sender_email: str) -> Optional[str]:
    """
    Liefert die Einstufung eines Absenders: 'safe_archive', 'conversational',
    'neutral' — oder None wenn kein Profil vorliegt (unbekannter Absender).
    """
    sender = (sender_email or "").strip().lower()
    if not sender:
        return None
    if sender in _PROFILE_CACHE:
        return _PROFILE_CACHE[sender]

    verdict: Optional[str] = None
    try:
        if os.path.exists(SENDER_PROFILE_DB):
            conn = sqlite3.connect(f"file:{SENDER_PROFILE_DB}?mode=ro", uri=True, timeout=5)
            try:
                row = conn.execute(
                    "SELECT verdict FROM sender_profile WHERE sender_email = ?", (sender,)
                ).fetchone()
                verdict = row[0] if row else None
            finally:
                conn.close()
    except Exception as exc:
        logger.warning("Absender-Profil nicht lesbar (%s): %s", SENDER_PROFILE_DB, exc)

    _PROFILE_CACHE[sender] = verdict
    return verdict


def may_auto_archive(sender_email: str, rule_id: str, schritt: Optional[int] = None) -> bool:
    """
    Entscheidet, ob ein 'ablegen'-Urteil die Mail auch wirklich verschieben darf.

    Erlaubt bei:
      - deterministischer Regel (Systemabsender, Newsletter, Kalender)
      - Absendern mit Einstufung 'safe_archive' aus dem Archiv-Profil

    Gesperrt bei Schritt 2 der 5-Schritte-Regel (HBE-3044): "zur Kenntnis,
    aufbewahren" ist etwas anderes als "kann ungelesen weg". Nur Schritt 1
    (Loeschen) darf still archivieren.

    Sonst bleibt die Mail als Vorschlag im Posteingang. Damit kann kein Urteil,
    das allein auf einer LLM-Einschaetzung beruht, still Post verschwinden lassen.
    """
    if schritt is not None and schritt in SCHRITTE_OHNE_AUTOARCHIV:
        return False
    if rule_id.startswith(("system_sender", "newsletter_sender", "calendar_subject")):
        return True
    return sender_verdict(sender_email) in AUTO_ARCHIVE_VERDICTS


def _normalize_subject_prefix(subject: str) -> str:
    # NFC + lowercase + strip whitespace, then first 30 chars.
    # Write-path (override recording) and read-path (recall) MUST use this exact function.
    return unicodedata.normalize("NFC", (subject or "").strip().lower())[:30]


# ── Hindsight-Lern-Loop (HBE-945) ────────────────────────────────────────────

class TriageLearningDB:
    """SQLite-Backend für Pattern-Learning. Thread-safe via per-call connections."""

    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS triage_patterns (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender_domain    TEXT NOT NULL,
                    subject_prefix   TEXT NOT NULL,
                    learned_action   TEXT NOT NULL,
                    learned_priority TEXT NOT NULL,
                    count            INTEGER NOT NULL DEFAULT 1,
                    first_seen_at    TEXT NOT NULL,
                    last_seen_at     TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_pattern_key
                    ON triage_patterns(sender_domain, subject_prefix, learned_action, learned_priority)
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS triage_overrides (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id        TEXT NOT NULL UNIQUE,
                    sender_domain     TEXT NOT NULL,
                    subject_prefix    TEXT NOT NULL,
                    original_action   TEXT,
                    original_priority TEXT,
                    override_action   TEXT NOT NULL,
                    override_priority TEXT NOT NULL,
                    detected_at       TEXT NOT NULL
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def record_override(
        self,
        message_id: str,
        sender_domain: str,
        subject_prefix: str,
        original_action: Optional[str],
        original_priority: Optional[str],
        override_action: str,
        override_priority: str,
    ) -> bool:
        """Insert override into audit log. Returns True if new (not a duplicate)."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO triage_overrides "
                "(message_id, sender_domain, subject_prefix, original_action, original_priority, "
                "override_action, override_priority, detected_at) VALUES (?,?,?,?,?,?,?,?)",
                (message_id, sender_domain, subject_prefix, original_action, original_priority,
                 override_action, override_priority, now),
            )
            changed = conn.execute("SELECT changes()").fetchone()[0]
            conn.commit()
            return changed > 0
        except Exception as exc:
            logger.warning("TriageLearningDB.record_override error: %s", exc)
            return False
        finally:
            conn.close()

    def upsert_pattern(
        self,
        sender_domain: str,
        subject_prefix: str,
        learned_action: str,
        learned_priority: str,
    ) -> int:
        """Upsert aggregated pattern. Returns updated count."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO triage_patterns "
                "(sender_domain, subject_prefix, learned_action, learned_priority, count, first_seen_at, last_seen_at) "
                "VALUES (?,?,?,?,1,?,?) "
                "ON CONFLICT(sender_domain, subject_prefix, learned_action, learned_priority) "
                "DO UPDATE SET count = count + 1, last_seen_at = excluded.last_seen_at",
                (sender_domain, subject_prefix, learned_action, learned_priority, now, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT count FROM triage_patterns WHERE sender_domain=? AND subject_prefix=? "
                "AND learned_action=? AND learned_priority=?",
                (sender_domain, subject_prefix, learned_action, learned_priority),
            ).fetchone()
            return int(row[0]) if row else 0
        except Exception as exc:
            logger.warning("TriageLearningDB.upsert_pattern error: %s", exc)
            return 0
        finally:
            conn.close()

    def recall_pattern(
        self,
        sender_domain: str,
        subject_prefix: str,
        threshold: int,
    ) -> Optional[Dict[str, Any]]:
        """Return dominant learned pattern if count >= threshold, else None."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT learned_action, learned_priority, count FROM triage_patterns "
                "WHERE sender_domain=? AND subject_prefix=? AND count >= ? "
                "ORDER BY count DESC LIMIT 1",
                (sender_domain, subject_prefix, threshold),
            ).fetchone()
            if row:
                return {"action": str(row[0]), "priority": str(row[1]), "count": int(row[2])}
        except Exception as exc:
            logger.warning("TriageLearningDB.recall_pattern error: %s", exc)
        finally:
            conn.close()
        return None


_learning_db: Optional["TriageLearningDB"] = None


def _get_learning_db() -> Optional["TriageLearningDB"]:
    global _learning_db
    if _learning_db is None:
        try:
            _learning_db = TriageLearningDB(LEARNING_DB)
        except Exception as exc:
            logger.warning("TriageLearningDB init failed — learning disabled: %s", exc)
    return _learning_db


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
    # Cap triage_results (LRU by insertion order — oldest keys evicted first)
    tr = state.get("triage_results")
    if isinstance(tr, dict) and len(tr) > MAX_TRIAGE_RESULTS:
        excess = len(tr) - MAX_TRIAGE_RESULTS
        for old_key in list(tr.keys())[:excess]:
            del tr[old_key]
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

# ── 5-Schritte-Regel (HBE-3044) ───────────────────────────────────────────────
# Sven arbeitet seine Mails nach der 5-Schritte-Regel ab. Sie ist eine geordnete
# Entscheidungsfolge statt einer Sammlung gleichrangiger Etiketten — das nimmt
# dem Modell die Freiheit, die vorher die Fehlklassifikationen erzeugt hat.
# Gemessen am realen Posteingang sank der Anteil der Mails mit Handlungsbedarf
# von 86 % auf 40 %.
#
# Die Outlook-Kategorien bleiben unveraendert; die Schritte werden auf die
# bestehenden Aktionen abgebildet:
SCHRITT_NAMEN = {
    1: "Löschen",
    2: "Ablegen",
    3: "Weiterleiten",
    4: "Terminieren",
    5: "Erledigen",
}
SCHRITT_ZU_AKTION = {
    1: "ablegen",       # darf archiviert werden, sofern gedeckt
    2: "ablegen",       # bleibt immer Vorschlag — nie still archivieren
    3: "weiterleiten",
    4: "tun",           # wird spaeter eine Asana-Aufgabe
    5: "antworten",
}
# Schritt 2 ist bewusst nie automatisch archivierbar: "zur Kenntnis, aufbewahren"
# ist etwas anderes als "kann ungelesen weg".
SCHRITTE_OHNE_AUTOARCHIV = {2}

# Rueckwaerts-Abbildung fuer das alte Antwortformat. "warten" wird zu Ablegen,
# "recherchieren" zu Terminieren — beide Kategorien entfallen mit der neuen Regel.
AKTION_ZU_SCHRITT = {
    "ablegen": 2,          # konservativ: nicht automatisch archivieren
    "warten": 2,
    "weiterleiten": 3,
    "tun": 4,
    "recherchieren": 4,
    "antworten": 5,
}

# Das Modell liefert gelegentlich den Schrittnamen statt der Zahl — auf Deutsch
# oder Englisch. Statt den Aufruf scheitern zu lassen (und in den Fallback
# "antworten" zu fallen) wird der Name uebersetzt. Im ersten Praxistest betraf
# das 26 von 145 Mails.
NAME_ZU_SCHRITT = {
    "löschen": 1, "loeschen": 1, "delete": 1, "1": 1,
    "ablegen": 2, "file": 2, "archive": 2, "2": 2,
    "weiterleiten": 3, "forward": 3, "3": 3,
    "terminieren": 4, "terminate": 4, "schedule": 4, "task": 4, "4": 4,
    "erledigen": 5, "do": 5, "reply": 5, "5": 5,
}


def _parse_schritt(wert: Any) -> Optional[int]:
    """Nimmt 1-5, '3', 'terminieren' oder 'Schritt 4' und liefert die Zahl."""
    if wert is None:
        return None
    if isinstance(wert, bool):
        return None
    if isinstance(wert, int):
        return wert if wert in SCHRITT_ZU_AKTION else None
    text = str(wert).strip().lower()
    if text in NAME_ZU_SCHRITT:
        return NAME_ZU_SCHRITT[text]
    m = re.search(r'[1-5]', text)
    if m:
        return int(m.group())
    for name, nr in NAME_ZU_SCHRITT.items():
        if name in text:
            return nr
    return None

# Mails ab diesem Alter sind praktisch immer von der Zeit ueberholt. Befund aus
# der Posteingangs-Durchsicht vom 14.09.2026: drei Rueckrufbitten aus Juli
# wurden noch zur Weiterleitung vorgeschlagen, eine Frist war laengst verstrichen.
MAX_ALTER_TAGE = int(os.getenv("LENA_MAIL_TRIAGE_MAX_ALTER_TAGE", "28"))

# Mails eines Vorgangs gemeinsam entscheiden. Abschaltbar, falls sich zeigt,
# dass einzelne Mails eines Threads doch unterschiedlich behandelt werden muessen.
THREAD_GROUPING = os.getenv("LENA_MAIL_TRIAGE_THREAD_GROUPING", "1").strip() == "1"

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


def _llm_triage(
    subject: str,
    sender_email: str,
    sender_name: str,
    body_preview: str,
    hindsight_hint: Optional[str] = None,
) -> Tuple[str, str, str]:
    """LLM-basierte Triage. Returns (action, priority, reasoning_with_prefix).

    Raises Exception bei nicht-recoverable LLM-Fehlern — triage_mail faengt
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
        hint_block=f"\n{hindsight_hint}" if hindsight_hint else "",
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

    # HBE-3044: Das Modell antwortet jetzt mit einem Schritt der 5-Schritte-Regel.
    # Altes Format (action/priority/reasoning) wird weiter akzeptiert, damit ein
    # Rollback des Prompts ohne Codeaenderung moeglich bleibt.
    schritt = _parse_schritt(data.get("schritt"))
    if schritt is None and "action" in data:
        # Modell hat den Schrittnamen unter dem alten Schluessel geliefert
        schritt = _parse_schritt(data.get("action"))
    if schritt is not None:
        priority = str(data.get("prioritaet") or data.get("priority") or "mittel").strip().lower()
        reasoning = str(data.get("begruendung") or data.get("reasoning") or "").strip()[:120]
        empfaenger = data.get("empfaenger")
        empfaenger = str(empfaenger).strip() if empfaenger else None
        if priority not in _VALID_PRIORITIES:
            priority = "mittel"
        return schritt, priority, f"llm:{reasoning}", empfaenger

    action = str(data.get("action", "")).strip().lower()
    priority = str(data.get("priority", "")).strip().lower()
    reasoning = str(data.get("reasoning", "")).strip()[:120]

    if action not in _VALID_ACTIONS:
        raise ValueError(f"LLM returned invalid action: {action!r}")
    if priority not in _VALID_PRIORITIES:
        raise ValueError(f"LLM returned invalid priority: {priority!r}")

    schritt = AKTION_ZU_SCHRITT.get(action, 2)
    return schritt, priority, f"llm:{reasoning}", None


def _namens_tokens(text: str) -> set:
    return {w for w in re.split(r'[\s,.;]+', (text or "").strip().lower()) if len(w) > 2}


def empfaenger_zu_adresse(empfaenger: Optional[str]) -> Optional[str]:
    """
    Loest einen vorgeschlagenen Empfaengernamen zur E-Mail-Adresse auf.

    Ausschliesslich ueber die Direktberichte-Liste aus der Konfiguration — es
    wird NICHT unscharf im Adresstext gesucht. Grund: "Herbert" ist der
    Firmenname und steckt in jeder @herbert.de-Adresse. Eine Teilstring-Suche
    nach dem Nachnamen liefert deshalb massenhaft Falschtreffer (im ersten
    Praxistest erschien "Frank Herbert" faelschlich als Empfaenger von Mails,
    die nur irgendeinen @herbert.de-Adressaten hatten).
    """
    if not empfaenger:
        return None
    e = empfaenger.strip().lower()
    if not e:
        return None
    if "@" in e:
        return e

    cfg = _PERSONA_CONFIG or _load_persona_config()
    kandidaten = cfg.get("direktberichte") or []
    gesucht = _namens_tokens(e)
    if not gesucht:
        return None

    for d in kandidaten:
        adr = (d.get("email") or "").strip().lower()
        if not adr:
            continue
        vorhanden = _namens_tokens(d.get("name", ""))
        if not vorhanden:
            continue
        # Voller Name oder eindeutige Teilmenge (z. B. nur der Nachname)
        if gesucht == vorhanden or gesucht <= vorhanden or vorhanden <= gesucht:
            return adr
    return None


def _self_forward(empfaenger: Optional[str], sender_email: str, sender_name: str) -> bool:
    """Wuerde die Mail an ihren eigenen Absender weitergeleitet?"""
    if not empfaenger:
        return False
    absender = (sender_email or "").strip().lower()
    adr = empfaenger_zu_adresse(empfaenger)
    if adr and absender and adr == absender:
        return True
    # Namensgleichheit als zweiter Weg — verlangt volle Uebereinstimmung der
    # Namenstokens, damit "Frank Herbert" nicht auf "Sven Herbert" passt.
    a, b = _namens_tokens(empfaenger), _namens_tokens(sender_name)
    return bool(a) and bool(b) and a == b


def _already_recipient(empfaenger: Optional[str], to_emails: List[str],
                       cc_emails: List[str]) -> bool:
    """
    Steht der vorgeschlagene Empfaenger schon im Verteiler der Mail?

    Vergleich ausschliesslich ueber die aufgeloeste Adresse, exakt. Laesst sich
    der Name nicht aufloesen, wird NICHT blockiert — lieber eine ueberfluessige
    Weiterleitung vorschlagen als eine noetige unterdruecken.
    """
    adr = empfaenger_zu_adresse(empfaenger)
    if not adr:
        return False
    verteiler = {(a or "").strip().lower() for a in list(to_emails or []) + list(cc_emails or [])}
    return adr in verteiler


def _ist_veraltet(received_at: str) -> bool:
    """Ist die Mail aelter als MAX_ALTER_TAGE?"""
    if not received_at or MAX_ALTER_TAGE <= 0:
        return False
    dt = _try_parse_iso(received_at)
    if dt is None:
        return False
    return (datetime.now(timezone.utc) - dt).days > MAX_ALTER_TAGE


def apply_guards(
    schritt: int,
    reasoning: str,
    empfaenger: Optional[str],
    sender_email: str = "",
    sender_name: str = "",
    to_emails: Optional[List[str]] = None,
    cc_emails: Optional[List[str]] = None,
    received_at: str = "",
) -> Tuple[int, str]:
    """
    Mechanische Pruefungen nach der LLM-Entscheidung (HBE-3044).

    Alle drei Pruefungen stammen aus der Posteingangs-Durchsicht vom 14.09.2026
    und brauchen kein Sprachmodell — sie sind rein logisch:

      1. Der Empfaenger darf nie der Absender sein. Gefunden bei zwei Mails:
         eine Mail von Walter Melcher sollte an Walter Melcher gehen.
      2. Wer bereits im Verteiler steht, braucht keine Weiterleitung. Gefunden
         bei einer Mail, die an Sven Walter adressiert war und Sven nur in Kopie
         hatte — der Vorschlag lautete trotzdem "weiterleiten an Sven Walter".
      3. Alte Mails sind von der Zeit ueberholt. Drei Rueckrufbitten aus Juli
         wurden im September noch zur Weiterleitung vorgeschlagen.

    Jede Pruefung stuft auf Schritt 2 (Ablegen) zurueck — nie hoeher.
    """
    if schritt == 3:
        if _self_forward(empfaenger, sender_email, sender_name):
            return 2, f"guard:empfaenger_ist_absender ({empfaenger}) | {reasoning}"
        if _already_recipient(empfaenger, to_emails or [], cc_emails or []):
            return 2, f"guard:empfaenger_bereits_im_verteiler ({empfaenger}) | {reasoning}"

    if schritt >= 3 and _ist_veraltet(received_at):
        return 2, f"guard:aelter_als_{MAX_ALTER_TAGE}_tage | {reasoning}"

    return schritt, reasoning


def triage_mail(
    subject: str,
    sender_email: str,
    body_preview: str,
    sender_name: str = "",
    to_emails: Optional[List[str]] = None,
    cc_emails: Optional[List[str]] = None,
    received_at: str = "",
) -> Tuple[str, str, str, Optional[int], int]:
    """
    Hybrid-Triage: schnelle Regeln → Hindsight-Recall → LLM → mechanische Pruefungen.

    Returns (action, priority, rule_id, learned_from, schritt).
    learned_from: count der Overrides die das angewendete Pattern erzeugt haben, sonst None.
    schritt: 1-5 nach der 5-Schritte-Regel. Schritt 2 wird nie still archiviert.

    Reasoning-Prefixes als Audit-Trail:
      "calendar_subject"           → Regel: Kalender-Notification
      "newsletter_sender"          → Regel: Automated-Sender
      "llm+memory:<domain>/<pfx>"  → Gelerntes Pattern angewendet (Hindsight)
      "llm:<text>"                 → LLM-Entscheidung mit Begruendung
      "llm_failed_<reason>"        → LLM-Aufruf fehlgeschlagen, Default-Fallback
    """
    subj = subject or ""
    sender = (sender_email or "").lower()

    # Regel 0: Systemabsender -> deterministisch, kein LLM-Aufruf (Mail-Konzept 2026-09)
    sys_hit = match_system_sender(sender, subj)
    if sys_hit:
        action, priority, rule_id = sys_hit
        # Regelbasiertes "ablegen" ist Schritt 1 (Loeschen) — es darf archiviert
        # werden. Die Personal-Ausnahme liefert "tun" und damit Schritt 4.
        schritt = 1 if action == "ablegen" else AKTION_ZU_SCHRITT.get(action, 2)
        return action, priority, rule_id, None, schritt

    # Regel 1: Kalender-Notifications -> Loeschen (kein LLM-Aufruf)
    if CALENDAR_SUBJECT_RE.search(subj):
        return "ablegen", "niedrig", "calendar_subject", None, 1

    # Regel 2: Newsletter/Automated-Sender -> Loeschen (kein LLM-Aufruf)
    if NEWSLETTER_SENDER_RE.search(sender):
        return "ablegen", "niedrig", "newsletter_sender", None, 1

    # Hindsight-Recall: gelerntes Pattern als Hint an LLM übergeben
    sender_domain = sender_email.split("@")[-1].lower() if "@" in sender_email else ""
    subject_prefix = _normalize_subject_prefix(subj)
    hindsight_hint: Optional[str] = None
    hindsight_pattern: Optional[Dict[str, Any]] = None

    db = _get_learning_db()
    if db:
        hindsight_pattern = db.recall_pattern(sender_domain, subject_prefix, LEARN_THRESHOLD)
        if hindsight_pattern:
            hindsight_hint = (
                f"Sven hat in der Vergangenheit ähnliche Mails von {sender_domain} auf "
                f"\"{hindsight_pattern['action']} + {hindsight_pattern['priority']}\" gesetzt "
                f"({hindsight_pattern['count']}× — Lena-Hinweis: bitte dieses Pattern berücksichtigen)."
            )

    # LLM-Triage mit optionalem Hindsight-Hint
    try:
        schritt, priority, reasoning, empfaenger = _llm_triage(
            subj, sender_email, sender_name, body_preview or "", hindsight_hint
        )
        schritt, reasoning = apply_guards(
            schritt, reasoning, empfaenger,
            sender_email=sender_email, sender_name=sender_name,
            to_emails=to_emails, cc_emails=cc_emails, received_at=received_at,
        )
        action = SCHRITT_ZU_AKTION[schritt]
        if hindsight_pattern and action == hindsight_pattern["action"] and priority == hindsight_pattern["priority"]:
            rule_id = f"llm+memory:{sender_domain}/{subject_prefix}"
            return action, priority, rule_id, hindsight_pattern["count"], schritt
        return action, priority, reasoning, None, schritt
    except Exception as exc:
        logger.warning(
            "LLM triage failed for sender=%s subject=%s: %s",
            sender_email, subj[:60], exc,
        )
        # Fallback: regelbasiert mit Urgency-Check. Bewusst Schritt 5 — ein
        # fehlgeschlagener LLM-Aufruf darf niemals zu stillem Archivieren fuehren.
        if URGENCY_RE.search(subj) or URGENCY_RE.search(body_preview or ""):
            return "antworten", "hoch", "llm_failed_urgency_fallback", None, 5
        return "antworten", "mittel", "llm_failed_default", None, 5


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


_PRIORITY_TO_IMPORTANCE = {"hoch": "high", "mittel": "normal", "niedrig": "low"}


def _categorize_mail(message_id: str, action: str, skip_archive: bool = False) -> bool:
    """
    Setzt die Kategorie. Bei action='ablegen' archiviert die API die Mail
    automatisch — ausser skip_archive=True ("Ablegen (Vorschlag)").
    """
    url = f"{API_URL.rstrip('/')}/api/lena/mail/categorize"
    payload = {"message_id": message_id, "action": action, "skip_archive": skip_archive}
    resp = requests.post(url, headers=_api_headers(), json=payload, timeout=30)
    if resp.status_code != 200:
        logger.warning("categorize HTTP %d: %s", resp.status_code, resp.text[:200])
        return False
    return True


def _set_importance(message_id: str, priority: str) -> bool:
    importance = _PRIORITY_TO_IMPORTANCE.get(priority, "normal")
    url = f"{API_URL.rstrip('/')}/api/lena/mail/set-importance"
    payload = {"message_id": message_id, "importance": importance}
    resp = requests.post(url, headers=_api_headers(), json=payload, timeout=30)
    if resp.status_code != 200:
        logger.warning("set-importance HTTP %d: %s", resp.status_code, resp.text[:200])
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


# ── Hindsight-Lern-Pass ───────────────────────────────────────────────────────

def _learn_from_overrides(since: str, state: Dict[str, Any], db: "TriageLearningDB") -> int:
    """
    Erkennt Svens manuelle Overrides (Mails mit Lena:*-Kategorien, die nach `since`
    modifiziert wurden) und trägt sie ins Lern-Backend ein.
    Gibt die Anzahl neuer (bisher unbekannter) Overrides zurück.
    """
    if not since:
        since = (datetime.now(timezone.utc) - timedelta(days=OVERRIDE_LOOKBACK_DAYS)).isoformat()

    url = f"{API_URL.rstrip('/')}/api/lena/mail/categorized-overrides"
    try:
        resp = requests.get(
            url,
            headers=_api_headers(),
            params={"since": since, "limit": 100},
            timeout=60,
        )
        if resp.status_code != 200:
            logger.warning("categorized-overrides HTTP %d: %s", resp.status_code, resp.text[:200])
            return 0
    except Exception as exc:
        logger.warning("categorized-overrides request failed: %s", exc)
        return 0

    processed = set(state.get("processed_message_ids", []))
    new_count = 0

    for mail in resp.json().get("mails", []):
        mid = mail.get("message_id", "")
        if not mid or mid not in processed:
            # Nur Mails lernen, die der Poller selbst triagiert hat
            continue

        sender_email = mail.get("sender_email", "") or ""
        sender_domain = mail.get("sender_domain", "") or (
            sender_email.split("@")[-1].lower() if "@" in sender_email else ""
        )
        subject_prefix = _normalize_subject_prefix(mail.get("subject", "") or "")
        override_action = mail.get("current_action", "") or ""
        override_priority = mail.get("current_priority", "") or ""

        if not override_action or not override_priority:
            continue

        cached = state.get("triage_results", {}).get(mid)
        is_new = db.record_override(
            message_id=mid,
            sender_domain=sender_domain,
            subject_prefix=subject_prefix,
            original_action=cached["action"] if cached else None,
            original_priority=cached["priority"] if cached else None,
            override_action=override_action,
            override_priority=override_priority,
        )

        if is_new:
            new_count += 1
            count = db.upsert_pattern(sender_domain, subject_prefix, override_action, override_priority)
            logger.info(json.dumps({
                "event":             "override_learned",
                "message_id":        mid,
                "sender_domain":     sender_domain,
                "subject_prefix":    subject_prefix,
                "override_action":   override_action,
                "override_priority": override_priority,
                "pattern_count":     count,
            }, ensure_ascii=False))

    return new_count


# ── Polling-Loop ──────────────────────────────────────────────────────────────
def _poll_once(state: Dict[str, Any]) -> Dict[str, int]:
    """Run one triage pass. Returns counter dict for logging."""
    counters = {
        "fetched": 0,
        "skipped_processed": 0,
        "categorized": 0,
        "failed": 0,
        "high_priority": 0,
        "overrides_learned": 0,
        # Mail-Konzept 2026-09
        "ablegen_vorschlag": 0,   # als Vorschlag markiert statt archiviert
        "dry_run": 0,             # im Trockenlauf nur protokolliert
        # HBE-3044
        "thread_uebernommen": 0,  # Entscheidung vom Vorgang uebernommen
        "guard_korrigiert": 0,    # mechanische Pruefung hat zurueckgestuft
    }

    # Save before the pass — used as `since` for override detection below
    prev_last_triage_at = state.get("last_triage_at", "")

    mails = _fetch_inbox_for_triage()
    counters["fetched"] = len(mails)

    if RETRIAGE_ALL:
        # Im Re-Triage-Mode processed_message_ids ignorieren — alles neu durchnudeln.
        processed: set = set()
        new_processed: List[str] = list(state.get("processed_message_ids", []))
    else:
        processed = set(state.get("processed_message_ids", []))
        new_processed = list(state.get("processed_message_ids", []))

    # HBE-3044: Vorgaenge zusammen entscheiden. Mails mit derselben
    # conversationId gehoeren zu einem Thema — die neueste bestimmt den Stand.
    # Befund aus der Durchsicht: ein Vorgang "Wareg Bensheim" lag als fuenf
    # Einzelmails im Posteingang und bekam fuenf getrennte, vage Urteile.
    thread_entscheidung: Dict[str, Tuple[str, str, str, int]] = {}
    if THREAD_GROUPING:
        mails = sorted(mails, key=lambda x: (x.get("received_at") or ""), reverse=True)

    for m in mails:
        mid = m.get("message_id", "")
        if not mid:
            continue
        if mid in processed:
            counters["skipped_processed"] += 1
            continue

        conv = (m.get("conversation_id") or "") if THREAD_GROUPING else ""
        if conv and conv in thread_entscheidung:
            action, priority, base_rule, schritt = thread_entscheidung[conv]
            rule_id = f"thread:{base_rule}"
            learned_from = None
            counters["thread_uebernommen"] += 1
        else:
            action, priority, rule_id, learned_from, schritt = triage_mail(
                m.get("subject", ""),
                m.get("sender_email", ""),
                m.get("body_preview", ""),
                m.get("sender_name", ""),
                to_emails=m.get("to_emails") or [],
                cc_emails=m.get("cc_emails") or [],
                received_at=m.get("received_at", "") or "",
            )
            if conv:
                thread_entscheidung[conv] = (action, priority, rule_id, schritt)

        if rule_id.startswith("guard:") or ":guard:" in rule_id:
            counters["guard_korrigiert"] += 1

        # Mail-Konzept 2026-09: 'ablegen' verschiebt die Mail nur, wenn die
        # Entscheidung durch eine deterministische Regel oder das Absender-Profil
        # gedeckt ist. Sonst bleibt sie als Vorschlag im Posteingang.
        sender_for_rule = m.get("sender_email", "") or ""
        will_archive = action == "ablegen" and may_auto_archive(sender_for_rule, rule_id, schritt)
        skip_archive = action == "ablegen" and not will_archive
        if skip_archive:
            counters["ablegen_vorschlag"] += 1

        if DRY_RUN:
            counters["dry_run"] += 1
            logger.info(json.dumps({
                "event":        "dry_run_decision",
                "message_id":   mid,
                "subject":      (m.get("subject", "") or "")[:120],
                "sender":       sender_for_rule,
                "action":       action,
                "priority":     priority,
                "rule":         rule_id,
                "would_archive": will_archive,
                "sender_verdict": sender_verdict(sender_for_rule),
            }, ensure_ascii=False))
            # Im Trockenlauf nichts schreiben — auch nicht in processed_message_ids,
            # damit der Scharflauf dieselben Mails erneut sieht.
            continue

        ok = _categorize_mail(mid, action, skip_archive=skip_archive)
        if not ok:
            counters["failed"] += 1
            continue
        # HBE-Mail-Konzept 2026-09: Nach dem Archivieren hat die Mail eine NEUE
        # message_id (Graph POST /move). Ein set-importance auf die alte ID
        # laeuft zwangslaeufig in HTTP 404 und flutete bisher das Log.
        if not will_archive:
            _set_importance(mid, priority)  # best-effort; Kategorie ist primaer
        counters["categorized"] += 1
        new_processed.append(mid)

        # Cache Lena's triage result so _learn_from_overrides can fill original_action/priority
        triage_results = state.setdefault("triage_results", {})
        triage_results[mid] = {"action": action, "priority": priority}
        if len(triage_results) > MAX_TRIAGE_RESULTS:
            excess = len(triage_results) - MAX_TRIAGE_RESULTS
            for old_key in list(triage_results.keys())[:excess]:
                del triage_results[old_key]

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

        log_entry: Dict[str, Any] = {
            "event":       "mail_categorized",
            "message_id":  mid,
            "subject":     (m.get("subject", "") or "")[:120],
            "sender":      (m.get("sender_email", "") or ""),
            "action":      action,
            "priority":    priority,
            "rule":        rule_id,
        }
        if learned_from is not None:
            log_entry["learned_from"] = learned_from
        logger.info(json.dumps(log_entry, ensure_ascii=False))

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

    # Lern-Pass: Overrides seit letztem Lauf erkennen und ins Pattern-Backend schreiben
    db = _get_learning_db()
    if db:
        n_new = _learn_from_overrides(prev_last_triage_at, state, db)
        counters["overrides_learned"] = n_new
        if n_new:
            logger.info("Override-Learning: %d neue Override(s) aufgezeichnet", n_new)

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

    # Mail-Konzept 2026-09: Systemabsender + Absender-Profil + Trockenlauf
    sys_senders = _get_system_senders()
    logger.info(
        "Systemabsender-Regeln: %d (%s)",
        len(sys_senders),
        ", ".join(s["email"] for s in sys_senders) or "keine",
    )
    if os.path.exists(SENDER_PROFILE_DB):
        try:
            _c = sqlite3.connect(f"file:{SENDER_PROFILE_DB}?mode=ro", uri=True, timeout=5)
            n_safe = _c.execute(
                "SELECT COUNT(*) FROM sender_profile WHERE verdict='safe_archive'"
            ).fetchone()[0]
            n_all = _c.execute("SELECT COUNT(*) FROM sender_profile").fetchone()[0]
            _c.close()
            logger.info(
                "Absender-Profil geladen: %d Absender, davon %d fuer Auto-Archivierung freigegeben (%s)",
                n_all, n_safe, SENDER_PROFILE_DB,
            )
        except Exception as exc:
            logger.warning("Absender-Profil vorhanden, aber nicht lesbar: %s", exc)
    else:
        logger.warning(
            "Absender-Profil fehlt (%s) — 'ablegen' wird nur noch bei deterministischen "
            "Regeln archiviert, alles andere bleibt Vorschlag im Posteingang.",
            SENDER_PROFILE_DB,
        )
    if DRY_RUN:
        logger.warning(
            "TROCKENLAUF AKTIV (LENA_MAIL_TRIAGE_DRY_RUN=1) — es werden KEINE Kategorien "
            "gesetzt und KEINE Mails verschoben. Entscheidungen nur im Log."
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
