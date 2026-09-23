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
4. TERMINIEREN — Sven muss selbst HANDELN, und die Handlung findet NICHT per
   E-Mail statt: etwas im JobRouter freigeben, ein Formular oder eine Umfrage
   ausfüllen, ein Dokument unterschreiben, etwas vorbereiten, jemanden anrufen.
   Auch wenn es länger als zwei Minuten dauert oder einen Termin braucht.
5. ERLEDIGEN — Eine ANTWORTMAIL ist die richtige Reaktion, und sie ist kurz.
   Zusage, Absage, Terminbestätigung, knappe Rückfrage beantworten.

Unterscheidung 4 gegen 5: Frage dich, ob Sven ZURÜCKSCHREIBEN muss. Wenn die
eigentliche Handlung woanders passiert — in einem System, auf Papier, am
Telefon — dann ist es Schritt 4, auch wenn die Mail höflich um etwas bittet.

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


# ── Svens eigene Regeln (HBE-3056) ───────────────────────────────────────────
# Regeln, die Sven selbst gesetzt hat. Sie gewinnen gegen alles andere — wenn er
# sagt "immer ablegen", diskutiert kein Modell mehr darueber.
#
# Zwei Schluessel, weil die Praxis beide braucht:
#   absender          Anthropic-Belege kommen immer von derselben Adresse,
#                     der Betreff wechselt (Rechnungsnummer).
#   betreff_enthaelt  DMARC-Berichte kommen von acht verschiedenen Absendern,
#                     der Betreff ist durch einen Standard festgelegt.
# Sind beide gesetzt, muessen beide zutreffen.
#
# Feste Regeln stehen in config/lena-mail-triage.yaml (versioniert, ueber PR
# geaendert). Regeln, die Sven zur Laufzeit ergaenzt, landen in einer eigenen
# Datei ausserhalb des Repos — sonst wuerde der naechste Deploy sie ueberschreiben.
LAUFZEIT_REGELN = os.getenv(
    "LENA_MAIL_TRIAGE_REGELN",
    "/var/lib/mail-triage-poller/regeln.json",
)


def _regel_normieren(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    absender = (r.get("absender") or "").strip().lower()
    betreff = (r.get("betreff_enthaelt") or "").strip().lower()
    aktion = (r.get("aktion") or "").strip().lower()
    if not (absender or betreff) or aktion not in _VALID_ACTIONS:
        return None
    return {
        "name": (r.get("name") or "").strip() or (absender or betreff)[:40],
        "absender": absender,
        "betreff_enthaelt": betreff,
        "aktion": aktion,
        "empfaenger": (r.get("empfaenger") or "").strip() or None,
        "quelle": r.get("quelle") or "config",
    }


def _laufzeit_regeln_lesen() -> List[Dict[str, Any]]:
    try:
        p = Path(LAUFZEIT_REGELN)
        if not p.exists():
            return []
        daten = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(daten, dict):
            daten = daten.get("regeln", [])
        return [dict(r, quelle="laufzeit") for r in daten if isinstance(r, dict)]
    except Exception as exc:
        logger.warning("Laufzeit-Regeln nicht lesbar (%s): %s", LAUFZEIT_REGELN, exc)
        return []


_REGELN: Optional[List[Dict[str, Any]]] = None


def _get_regeln(neu_laden: bool = False) -> List[Dict[str, Any]]:
    global _REGELN
    if _REGELN is None or neu_laden:
        roh = list((_PERSONA_CONFIG or _load_persona_config()).get("regeln") or [])
        roh += _laufzeit_regeln_lesen()
        _REGELN = [x for x in (_regel_normieren(r) for r in roh) if x]
    return _REGELN


def match_regel(sender_email: str, subject: str) -> Optional[Dict[str, Any]]:
    """Prueft Svens eigene Regeln. Erste passende gewinnt."""
    s = (sender_email or "").strip().lower()
    b = (subject or "").strip().lower()
    for r in _get_regeln():
        if r["absender"] and r["absender"] not in s:
            continue
        if r["betreff_enthaelt"] and r["betreff_enthaelt"] not in b:
            continue
        return r
    return None


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
    # HBE-3056: Svens eigene Regel ist die staerkste Deckung ueberhaupt.
    if rule_id.startswith(("sven_regel", "system_sender", "newsletter_sender", "calendar_subject")):
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

# ── Konsequenzen je Schritt (HBE-3048) ────────────────────────────────────────
# Eine Kategorie ohne Konsequenz ist farbig markierter Posteingang. Ab hier
# loest jeder Handlungs-Schritt etwas aus:
#   Schritt 3 Weiterleiten -> Weiterleitungs-Entwurf an den Zustaendigen
#   Schritt 4 Terminieren  -> Asana-Aufgabe
#   Schritt 5 Erledigen    -> Antwort-Entwurf
# Nichts davon wird gesendet oder zugewiesen — alles bleibt Entwurf.
#
# Standardmaessig AUS. Erst nach einem beobachteten Lauf scharfschalten.
AKTIONEN_AKTIV = os.getenv("LENA_MAIL_TRIAGE_AKTIONEN", "0").strip() == "1"

# HBE-3052: Svens Kategorie-Aenderungen erkennen, darauf reagieren und daraus
# lernen. Standardmaessig an — der Pass ist billig (ein Inbox-Abruf je Zyklus)
# und ohne ihn bleibt eine Korrektur in Outlook folgenlos.
KORREKTUREN_AKTIV = os.getenv("LENA_MAIL_TRIAGE_KORREKTUREN", "1").strip() == "1"

# HBE-3113: Eine Lena-Kategorie auf einer Mail, zu der KEINE eigene Entscheidung
# vorliegt, ist eine Anweisung — keine Korrektur. Bisher wurde sie stillschweigend
# uebergangen ("nichts zu vergleichen"), und Sven sah eine gesetzte Kategorie
# ohne jede Wirkung. Drei Wege fuehren dorthin:
#   * Sven kategorisiert schneller als der 10-Minuten-Takt
#   * die Entscheidung ist aus dem 500er-Cache gefallen
#   * die Mail wurde verschoben und hat dabei eine neue message_id bekommen
#     (Graph POST /messages/{id}/move vergibt eine neue ID)
ANWEISUNGEN_AKTIV = os.getenv("LENA_MAIL_TRIAGE_ANWEISUNGEN", "1").strip() == "1"
# Altbestand nicht ruekwirkend abarbeiten: aeltere Mails werden einmal gemeldet,
# aber nicht ausgefuehrt. Sonst loest der erste Lauf einen Schwall Aufgaben aus.
ANWEISUNG_MAX_ALTER_TAGE = int(os.getenv("LENA_MAIL_TRIAGE_ANWEISUNG_MAX_TAGE", "7"))

# Wiederholt sich eine Korrektur, schlaegt Lena eine feste Regel vor.
REGELVORSCHLAG_AKTIV = os.getenv("LENA_MAIL_TRIAGE_REGELVORSCHLAG", "1").strip() == "1"

# Kategorie auf den erzeugten Entwuerfen, damit sie im Entwuerfe-Ordner von
# Svens eigenen unterscheidbar sind.
ENTWURF_KATEGORIE = os.getenv("LENA_MAIL_TRIAGE_ENTWURF_KATEGORIE", "Lena: Entwurf")

# Asana-Board fuer Aufgaben aus Schritt 4 ("Meine Aufgaben SH")
ASANA_TOKEN = os.getenv("ASANA_ACCESS_TOKEN", "")
ASANA_BOARD_GID = os.getenv("LENA_MAIL_TRIAGE_ASANA_BOARD", "1216277431582688")
ASANA_API = "https://app.asana.com/api/1.0"

# HBE-3061: Eigene Section fuer Aufgaben aus Mails. Ohne Section sortiert Asana
# neue Aufgaben in die ERSTE Section ein — bei Sven ist das "🔴 Heute". Die drei
# bisher entstandenen Mail-Aufgaben landeten dadurch ausgerechnet im
# dringendsten Bereich.
ASANA_SECTION_NAME = os.getenv("LENA_MAIL_TRIAGE_ASANA_SECTION", "📬 Aus Mails")
# Empfaenger der erzeugten Aufgaben. Ohne Zuweisung erscheinen sie
# nicht in Asanas "Meine Aufgaben" — nur im Board selbst. Default ist Sven
# (s.herbert@herbert.de), weil das Board sein persoenliches ist. Leer setzen
# laesst die Aufgaben unzugewiesen.
ASANA_ASSIGNEE = os.getenv("LENA_MAIL_TRIAGE_ASANA_ASSIGNEE", "1202563118654849").strip()

# Rueckfragen: wenn eine Konsequenz nicht vollstaendig ausgefuehrt werden kann,
# fragt Lena nach — statt stillschweigend nichts zu tun. Eigenes Tageslimit,
# damit die Rueckfragen nicht mit den Hoch-Prio-Alarmen um dasselbe Kontingent
# konkurrieren.
RUECKFRAGEN_AKTIV = os.getenv("LENA_MAIL_TRIAGE_RUECKFRAGEN", "1").strip() == "1"
RUECKFRAGEN_TAGESLIMIT = int(os.getenv("LENA_MAIL_TRIAGE_RUECKFRAGEN_LIMIT", "12"))

# Nach dem Anlegen einer Aufgabe ist die Mail erledigt — die Aufgabe traegt
# Betreff, Absender und Inhalt. Sie wird deshalb archiviert, damit der
# Posteingang nicht mit erledigten Vorgaengen volllaeuft.
TUN_MAIL_ARCHIVIEREN = os.getenv("LENA_MAIL_TRIAGE_TUN_ARCHIVIEREN", "1").strip() == "1"

# Svens Schreibstil — Grundlage jedes Entwurfs.
SCHREIBSTIL_DATEI = os.getenv(
    "LENA_MAIL_TRIAGE_SCHREIBSTIL",
    "/opt/vault-mirror/00 Kontext/Schreibstil.md",
)
_SCHREIBSTIL_FALLBACK = """Klar, direkt und verstaendlich. Keine unnoetigen Fremdwoerter,
kein Managerjargon. Mitarbeiter intern: Du. Kunden und externe Kontakte: Sie.
Ehrlich und offen, keine Schoenfaerberei. Kurze, klare Saetze."""


def _schreibstil() -> str:
    try:
        p = Path(SCHREIBSTIL_DATEI)
        if p.exists():
            text = p.read_text(encoding="utf-8")
            # YAML-Frontmatter entfernen
            if text.startswith("---"):
                teile = text.split("---", 2)
                if len(teile) >= 3:
                    text = teile[2]
            return text.strip()[:2000]
    except Exception as exc:
        logger.warning("Schreibstil nicht lesbar (%s): %s", SCHREIBSTIL_DATEI, exc)
    return _SCHREIBSTIL_FALLBACK

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


def _erstes_json_objekt(raw: str) -> Dict[str, Any]:
    """
    Nimmt das erste vollstaendige JSON-Objekt aus der Antwort.

    Bei laengeren Eingaben haengt das Modell gelegentlich noch Fliesstext hinter
    das JSON — json.loads scheitert dann mit "Extra data". Statt den ganzen
    Aufruf zu verlieren, wird bis zur passenden schliessenden Klammer gelesen.
    """
    text = _strip_json_fences(raw)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    if start < 0:
        raise ValueError("Keine JSON-Struktur in der Antwort gefunden.")
    tiefe = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        z = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif z == "\\":
                escaped = True
            elif z == '"':
                in_string = False
            continue
        if z == '"':
            in_string = True
        elif z == "{":
            tiefe += 1
        elif z == "}":
            tiefe -= 1
            if tiefe == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("JSON-Objekt in der Antwort ist unvollstaendig.")


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
    data = _erstes_json_objekt(response.content[0].text)  # raises if malformed → caught by caller

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
) -> Tuple[str, str, str, Optional[int], int, Optional[str]]:
    """
    Hybrid-Triage: schnelle Regeln → Hindsight-Recall → LLM → mechanische Pruefungen.

    Returns (action, priority, rule_id, learned_from, schritt, empfaenger).
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

    # Svens eigene Regeln gewinnen gegen alles (HBE-3056)
    eigene = match_regel(sender, subj)
    if eigene:
        aktion = eigene["aktion"]
        # Eine ausdrueckliche Anweisung von Sven ist mindestens so verbindlich
        # wie eine Systemregel — "ablegen" heisst hier wirklich ablegen.
        schritt = 1 if aktion == "ablegen" else AKTION_ZU_SCHRITT.get(aktion, 2)
        return (aktion, "niedrig" if schritt == 1 else "mittel",
                f"sven_regel:{eigene['name']}", None, schritt, eigene.get("empfaenger"))

    # Regel 0: Systemabsender -> deterministisch, kein LLM-Aufruf (Mail-Konzept 2026-09)
    sys_hit = match_system_sender(sender, subj)
    if sys_hit:
        action, priority, rule_id = sys_hit
        # Regelbasiertes "ablegen" ist Schritt 1 (Loeschen) — es darf archiviert
        # werden. Die Personal-Ausnahme liefert "tun" und damit Schritt 4.
        schritt = 1 if action == "ablegen" else AKTION_ZU_SCHRITT.get(action, 2)
        return action, priority, rule_id, None, schritt, None

    # Regel 1: Kalender-Notifications -> Loeschen (kein LLM-Aufruf)
    if CALENDAR_SUBJECT_RE.search(subj):
        return "ablegen", "niedrig", "calendar_subject", None, 1, None

    # Regel 2: Newsletter/Automated-Sender -> Loeschen (kein LLM-Aufruf)
    if NEWSLETTER_SENDER_RE.search(sender):
        return "ablegen", "niedrig", "newsletter_sender", None, 1, None

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
            return action, priority, rule_id, hindsight_pattern["count"], schritt, empfaenger
        return action, priority, reasoning, None, schritt, empfaenger
    except Exception as exc:
        logger.warning(
            "LLM triage failed for sender=%s subject=%s: %s",
            sender_email, subj[:60], exc,
        )
        # Fallback: regelbasiert mit Urgency-Check. Bewusst Schritt 5 — ein
        # fehlgeschlagener LLM-Aufruf darf niemals zu stillem Archivieren fuehren.
        if URGENCY_RE.search(subj) or URGENCY_RE.search(body_preview or ""):
            return "antworten", "hoch", "llm_failed_urgency_fallback", None, 5, None
        return "antworten", "mittel", "llm_failed_default", None, 5, None


# ── Konsequenzen: Entwuerfe und Aufgaben (HBE-3048) ──────────────────────────

# Svens Termine der naechsten Tage. Ohne sie kann kein Entwurf auf die
# haeufigste Frage ueberhaupt antworten — "passt Ihnen Mittwoch 14 Uhr?".
# Im ersten Test scheiterten daran 2 von 8 Entwuerfen.
KALENDER_TAGE = int(os.getenv("LENA_MAIL_TRIAGE_KALENDER_TAGE", "14"))
_kalender_cache: Dict[str, Any] = {"stand": None, "text": ""}


def _kalender_kontext(max_alter_sek: int = 900) -> str:
    """Svens Termine der naechsten Tage als kompakte Liste (Ortszeit)."""
    jetzt = datetime.now(timezone.utc)
    stand = _kalender_cache.get("stand")
    if stand and (jetzt - stand).total_seconds() < max_alter_sek:
        return _kalender_cache["text"]

    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Berlin")
    except Exception:
        tz = timezone.utc

    try:
        start = jetzt.strftime("%Y-%m-%dT00:00:00")
        ende = (jetzt + timedelta(days=KALENDER_TAGE)).strftime("%Y-%m-%dT23:59:59")
        resp = requests.get(
            f"{API_URL.rstrip('/')}/api/calendar/events",
            headers={"X-API-Key": API_KEY},
            params={"start": start, "end": ende}, timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        events = resp.json().get("events", [])
    except Exception as exc:
        logger.warning("Kalender fuer Entwuerfe nicht abrufbar: %s", exc)
        _kalender_cache.update(stand=jetzt, text="")
        return ""

    WT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    zeilen: List[str] = []
    for e in events[:80]:
        dt = _try_parse_iso((e.get("start") or "")[:19] + "+00:00")
        if dt is None:
            continue
        lokal = dt.astimezone(tz)
        bis = _try_parse_iso((e.get("end") or "")[:19] + "+00:00")
        bis_txt = bis.astimezone(tz).strftime("%H:%M") if bis else "?"
        titel = (e.get("title") or "")[:52]
        zeilen.append(f"{WT[lokal.weekday()]} {lokal:%d.%m.} {lokal:%H:%M}-{bis_txt} {titel}")

    text = "\n".join(zeilen)
    _kalender_cache.update(stand=jetzt, text=text)
    return text


ENTWURF_SYSTEM = """Du bist Lena, die persoenliche Assistentin von Sven Herbert,
Geschaeftsfuehrer der Herbert Gruppe (Gebaeudetechnik/TGA).

Du bereitest eine Antwort auf eine E-Mail vor. Sven prueft und sendet selbst.

SVENS SCHREIBSTIL — halte dich strikt daran:
{schreibstil}

SVENS TERMINE (Ortszeit, naechste Tage) — nutze sie fuer Terminfragen:
{kalender}

Wenn jemand nach einem Termin fragt, pruefe diese Liste und antworte konkret:
frei -> zusagen, belegt -> absagen und den Konflikt benennen. Steht der genannte
Termin bereits in der Liste, ist er angenommen — dann bestaetigen.
Liegt der gefragte Zeitpunkt ausserhalb der Liste, sage das ehrlich ("nein").

ENTSCHEIDE ZUERST, OB DU UEBERHAUPT ANTWORTEN KANNST:

- "voll"    Die Antwort folgt vollstaendig aus der Mail. Terminbestaetigung,
            Empfangsbestaetigung, Dank, Zusage zu etwas bereits Abgestimmtem,
            einfache Rueckfrage beantworten. Schreibe die fertige Antwort.
- "geruest" Anrede, Struktur und Schluss stehen fest, aber ein inhaltlicher Kern
            fehlt, den nur Sven kennt (ein Termin, eine Zahl, eine Einschaetzung).
            Schreibe den Rahmen und markiere die Luecke mit [[...]].
- "nein"    Die Antwort ist eine Entscheidung oder braucht Wissen, das nicht in
            der Mail steht. Schreibe KEINEN Text. Nenne stattdessen unter
            "offene_frage" praezise, was Sven klaeren muss.

Erfinde niemals Inhalte, Zusagen, Termine oder Zahlen. Im Zweifel "nein".

Antworte AUSSCHLIESSLICH mit JSON:
{{"stufe": "voll|geruest|nein", "text": "Antworttext oder leer",
  "offene_frage": "nur bei nein, sonst leer"}}"""


def _volltext(message_id: str, rueckfall: str = "") -> str:
    """
    Holt den vollstaendigen Mailtext. Faellt auf die Vorschau zurueck.

    bodyPreview ist auf 500 Zeichen begrenzt und schneidet mitten im Satz ab.
    Das Modell verweigerte im ersten Test mehrfach den Entwurf mit der
    Begruendung, die Mail breche ab — nicht weil die Antwort schwierig war.
    """
    if not message_id:
        return rueckfall
    try:
        resp = requests.get(
            f"{API_URL.rstrip('/')}/api/lena/mail/{message_id}/body",
            headers={"X-API-Key": API_KEY}, params={"max_chars": 6000}, timeout=40,
        )
        if resp.status_code == 200:
            text = (resp.json().get("body_text") or "").strip()
            if text:
                return text
        else:
            logger.warning("mail/body HTTP %d", resp.status_code)
    except Exception as exc:
        logger.warning("Volltext nicht abrufbar: %s", exc)
    return rueckfall


def _llm_entwurf(subject: str, sender_name: str, sender_email: str,
                 body_preview: str, message_id: str = "") -> Tuple[str, str, str]:
    """Erzeugt einen Antwortentwurf. Returns (stufe, text, offene_frage)."""
    client = _get_llm_client()
    if client is None:
        raise RuntimeError("Anthropic-Client nicht verfuegbar.")
    inhalt = _volltext(message_id, body_preview or "")
    prompt = (f"Von: {sender_name} <{sender_email}>\n"
              f"Betreff: {subject}\n\n{inhalt[:6000]}")
    kal = _kalender_kontext()
    resp = client.messages.create(
        model=LLM_MODEL,
        max_tokens=700,
        system=ENTWURF_SYSTEM.format(
            schreibstil=_schreibstil(),
            kalender=kal or "(keine Termindaten verfuegbar — bei Terminfragen 'nein')",
        ),
        messages=[{"role": "user", "content": prompt}],
        timeout=LLM_TIMEOUT_SEC,
    )
    data = _erstes_json_objekt(resp.content[0].text)
    stufe = str(data.get("stufe", "nein")).strip().lower()
    if stufe not in ("voll", "geruest", "nein"):
        stufe = "nein"
    return stufe, str(data.get("text") or "").strip(), str(data.get("offene_frage") or "").strip()


def _entwurf_anlegen(message_id: str, betreff: str, text: str,
                     modus: str = "reply", to: Optional[List[Dict[str, str]]] = None) -> Optional[str]:
    """Legt einen Entwurf in Svens Entwuerfe-Ordner an. Gibt die Draft-ID zurueck."""
    payload: Dict[str, Any] = {
        "to": to or [],
        "cc": [],
        "subject": betreff,
        "body_text": text,
        "reply_to_message_id": message_id,
        "mode": modus,
        "category": ENTWURF_KATEGORIE,
    }
    resp = requests.post(f"{API_URL.rstrip('/')}/api/lena/mail/draft",
                         headers=_api_headers(), json=payload, timeout=40)
    if resp.status_code != 200:
        logger.warning("draft HTTP %d: %s", resp.status_code, resp.text[:200])
        return None
    return resp.json().get("draft_id")


def _titel_normalisieren(text: str) -> str:
    """Vereinheitlicht Betreffzeilen fuer den Dublettenvergleich."""
    t = (text or "").lower()
    t = re.sub(r'^\s*(aw|re|wg|fwd?|antw)\s*:\s*', '', t)
    t = re.sub(r'[^a-zäöüß0-9]+', ' ', t)
    return " ".join(t.split())[:80]


_asana_cache: Dict[str, Any] = {"stand": None, "titel": set()}


_BETREFF_ZEILE = re.compile(r"^Betreff:\s*(.+)$", re.MULTILINE)


def _asana_offene_titel(max_alter_sek: int = 600) -> set:
    """
    Erkennungsmerkmale der offenen Aufgaben im Board — fuer die Dublettenpruefung.

    HBE-3120: Frueher wurden nur die Aufgaben-TITEL gesammelt und gegen den
    Mail-Betreff verglichen. Das ging auf, solange der Titel der Betreff war.
    Seit HBE-3061 formuliert das Modell den Titel ("Lageplan" wird zu "Lageplan
    mit Faechern, Anzahl und Traglast erstellen") — seitdem konnten die beiden
    Seiten gar nicht mehr uebereinstimmen und die Pruefung lief ins Leere. Am
    18.09.2026 entstand dadurch eine zweite Aufgabe zur selben Mail.

    Deshalb zusaetzlich der Betreff aus den Notizen: Jede erzeugte Aufgabe
    traegt dort die Zeile "Betreff: <X>". Der Schluessel wirkt damit auch
    rueckwirkend fuer Aufgaben, die vor diesem Fix entstanden sind.
    """
    jetzt = datetime.now(timezone.utc)
    stand = _asana_cache.get("stand")
    if stand and (jetzt - stand).total_seconds() < max_alter_sek:
        return _asana_cache["titel"]
    schluessel: set = set()
    if ASANA_TOKEN:
        # HBE-3122: Reichweite. Die Abfrage ging ueber das GANZE Board und war
        # bei 100 Aufgaben abgeschnitten — Svens Board hat mehr, und die
        # Mail-Aufgaben lagen jenseits der Grenze. Die Pruefung sah also
        # ausgerechnet die Aufgaben nicht, gegen die sie schuetzen soll.
        # Die Section "📬 Aus Mails" enthaelt genau diese und ist klein.
        section = _asana_section_gid()
        if section:
            url = f"{ASANA_API}/sections/{section}/tasks"
            params: Dict[str, Any] = {"opt_fields": "name,completed,notes", "limit": 100}
        else:
            url = f"{ASANA_API}/tasks"
            params = {"project": ASANA_BOARD_GID,
                      "opt_fields": "name,completed,notes", "limit": 100}
        try:
            while url:
                resp = requests.get(
                    url, headers={"Authorization": f"Bearer {ASANA_TOKEN}"},
                    params=params, timeout=30,
                )
                if resp.status_code != 200:
                    logger.warning("Asana-Liste HTTP %d", resp.status_code)
                    break
                daten = resp.json()
                for t in daten.get("data", []):
                    if t.get("completed"):
                        continue
                    schluessel.add(_titel_normalisieren(t.get("name", "")))
                    treffer = _BETREFF_ZEILE.search(t.get("notes") or "")
                    if treffer:
                        schluessel.add(_titel_normalisieren(treffer.group(1)))
                # Ohne Section muss geblaettert werden, sonst bleibt die Luecke.
                weiter = (daten.get("next_page") or {}).get("uri") if not section else None
                url, params = weiter, {}
        except Exception as exc:
            logger.warning("Asana-Liste nicht abrufbar: %s", exc)
    schluessel.discard("")
    _asana_cache.update(stand=jetzt, titel=schluessel)
    return schluessel


_asana_section_cache: Dict[str, Any] = {"gid": None, "geprueft": False}


def _asana_section_gid() -> Optional[str]:
    """Liefert die Section fuer Mail-Aufgaben, legt sie bei Bedarf an."""
    if _asana_section_cache["geprueft"]:
        return _asana_section_cache["gid"]
    _asana_section_cache["geprueft"] = True
    if not ASANA_TOKEN or not ASANA_SECTION_NAME:
        return None
    kopf = {"Authorization": f"Bearer {ASANA_TOKEN}", "Content-Type": "application/json"}
    try:
        resp = requests.get(f"{ASANA_API}/projects/{ASANA_BOARD_GID}/sections",
                            headers=kopf, params={"opt_fields": "name"}, timeout=30)
        if resp.status_code == 200:
            for s in resp.json().get("data", []):
                if (s.get("name") or "").strip() == ASANA_SECTION_NAME:
                    _asana_section_cache["gid"] = s.get("gid")
                    return s.get("gid")
        # Nicht vorhanden -> anlegen
        resp = requests.post(f"{ASANA_API}/projects/{ASANA_BOARD_GID}/sections",
                             headers=kopf, json={"data": {"name": ASANA_SECTION_NAME}},
                             timeout=30)
        if resp.status_code in (200, 201):
            gid = resp.json().get("data", {}).get("gid")
            _asana_section_cache["gid"] = gid
            logger.info("Asana-Section '%s' angelegt (%s)", ASANA_SECTION_NAME, gid)
            return gid
        logger.warning("Asana-Section nicht anlegbar: HTTP %d", resp.status_code)
    except Exception as exc:
        logger.warning("Asana-Section nicht ermittelbar: %s", exc)
    return None


def _kuerzen(text: str, maximal: int) -> str:
    """
    Kuerzt an der Wortgrenze statt mitten im Wort.

    HBE-3061: Ein Aufgabentitel endete mit "Anforderungen (Snapshot-Modell, Histo"
    — eine harte Kuerzung mitten im Wort sieht nach Fehler aus.
    """
    t = (text or "").strip()
    if len(t) <= maximal:
        return t
    schnitt = t[:maximal].rsplit(" ", 1)[0].rstrip(" ,;:-(")
    return (schnitt or t[:maximal]) + "…"


AUFGABE_SYSTEM = """Du bist Lena, die Assistentin von Sven Herbert.

Aus einer E-Mail soll eine Aufgabe fuer Sven werden.

TITEL: Formuliere, WAS Sven tun muss — als Handlung, nicht als Betreffzeile.
Schlecht: "Lageplan". Gut: "Lageplan Musterstrasse pruefen und zurueckmelden".
HOECHSTENS 70 Zeichen, keine Anrede, kein "Bitte", keine Klammern mit
Aufzaehlungen. Details gehoeren in den Kontext, nicht in den Titel.

FRIST: NUR wenn die Mail ausdruecklich ein Datum oder eine Frist nennt
("bis 04.09.", "bis Ende der Woche", "innerhalb von 14 Tagen"). Rechne relative
Angaben auf ein Datum um, ausgehend vom Empfangsdatum. Nennt die Mail KEINE
Frist, gib null zurueck. Erfinde niemals ein Datum.

KONTEXT: Ein bis zwei Saetze, worum es geht.

Antworte AUSSCHLIESSLICH mit JSON:
{"titel": "...", "frist": "YYYY-MM-DD oder null", "kontext": "..."}"""


def _llm_aufgabe(betreff: str, sender_name: str, sender_email: str,
                 inhalt: str, empfangen: str) -> Tuple[str, Optional[str], str]:
    """Macht aus einer Mail eine Aufgabe. Returns (titel, frist, kontext)."""
    client = _get_llm_client()
    if client is None:
        return betreff[:90], None, ""
    try:
        prompt = (f"Empfangen am: {empfangen[:10]}\n"
                  f"Von: {sender_name} <{sender_email}>\n"
                  f"Betreff: {betreff}\n\n{(inhalt or '')[:3000]}")
        resp = client.messages.create(
            model=LLM_MODEL, max_tokens=400, system=AUFGABE_SYSTEM,
            messages=[{"role": "user", "content": prompt}], timeout=LLM_TIMEOUT_SEC)
        d = _erstes_json_objekt(resp.content[0].text)
        titel = _kuerzen(str(d.get("titel") or "").strip() or betreff, 90)
        frist = d.get("frist")
        frist = str(frist).strip() if frist and str(frist).lower() not in ("null", "none", "") else None
        if frist and not re.match(r'^\d{4}-\d{2}-\d{2}$', frist):
            frist = None
        return titel, frist, str(d.get("kontext") or "").strip()[:400]
    except Exception as exc:
        logger.warning("Aufgaben-Formulierung fehlgeschlagen: %s", exc)
        return betreff[:90], None, ""


def _asana_aufgabe(betreff: str, sender_name: str, sender_email: str,
                   body_preview: str, empfangen: str,
                   titel: Optional[str] = None, frist: Optional[str] = None,
                   kontext: str = "") -> Optional[str]:
    """Legt eine Aufgabe im Board 'Meine Aufgaben SH' an — falls es sie nicht gibt."""
    if not ASANA_TOKEN:
        logger.warning("ASANA_ACCESS_TOKEN fehlt — keine Aufgabe angelegt.")
        return None

    # HBE-3052: Dublettenpruefung. Am 15.09. entstanden drei Aufgaben fuer zwei
    # Vorgaenge, davon zwei Dubletten einer bereits vorhandenen Aufgabe.
    # HBE-3120: Betreff UND formulierten Titel pruefen. Der Betreff ist der
    # verlaessliche Schluessel — er steht in den Notizen jeder Aufgabe. Der
    # Titel kommt vom Modell und faellt bei jedem Lauf anders aus.
    vorhanden = _asana_offene_titel()
    for kandidat in (betreff, titel):
        norm = _titel_normalisieren(kandidat or "")
        if norm and norm in vorhanden:
            logger.info("Asana: Aufgabe zu '%s' existiert bereits — uebersprungen.",
                        (betreff or "")[:60])
            return None
    name = (titel or betreff)[:120]
    notes = ((f"{kontext}\n\n" if kontext else "")
             + f"Aus einer E-Mail vom {empfangen[:10]}.\n\n"
             f"Von: {sender_name} <{sender_email}>\n"
             f"Betreff: {betreff}\n\n"
             f"{(body_preview or '')[:1200]}\n\n"
             f"— angelegt von Lena aus der Mail-Triage")
    kopf = {"Authorization": f"Bearer {ASANA_TOKEN}", "Content-Type": "application/json"}
    daten: Dict[str, Any] = {"name": name, "notes": notes, "projects": [ASANA_BOARD_GID]}
    # Ohne assignee taucht die Aufgabe in Asanas "Meine Aufgaben"
    # nicht auf — sie existiert nur im Projekt-Board. 23 Aufgaben lagen so
    # unsichtbar herum, waehrend Sven sie in seiner Aufgabenliste suchte.
    if ASANA_ASSIGNEE:
        daten["assignee"] = ASANA_ASSIGNEE
    # HBE-3061: Faelligkeit NUR wenn die Mail eine nennt. Ein erfundenes Datum
    # sieht aus wie Information und wird zu Rauschen — das Board zeigt zehn
    # ueberfaellige Aufgaben mit Daten aus Januar bis August.
    if frist:
        daten["due_on"] = frist
    try:
        resp = requests.post(f"{ASANA_API}/tasks", headers=kopf,
                             json={"data": daten}, timeout=30)
        if resp.status_code not in (200, 201):
            logger.warning("Asana HTTP %d: %s", resp.status_code, resp.text[:200])
            return None
        gid = resp.json().get("data", {}).get("gid")
        if gid:
            # Beide Schluessel sofort merken — gegen Dubletten im selben Lauf,
            # bevor der Cache das naechste Mal vom Board geholt wird.
            for frisch in (betreff, name):
                n = _titel_normalisieren(frisch or "")
                if n:
                    _asana_cache.setdefault("titel", set()).add(n)

        # In die eigene Section verschieben, sonst landet die Aufgabe in "Heute".
        sec = _asana_section_gid()
        if gid and sec:
            try:
                r2 = requests.post(f"{ASANA_API}/sections/{sec}/addTask", headers=kopf,
                                   json={"data": {"task": gid}}, timeout=30)
                if r2.status_code not in (200, 201):
                    logger.warning("Asana-Section-Zuordnung HTTP %d", r2.status_code)
            except Exception as exc:
                logger.warning("Asana-Section-Zuordnung fehlgeschlagen: %s", exc)
        return gid
    except Exception as exc:
        logger.warning("Asana-Aufgabe fehlgeschlagen: %s", exc)
        return None


def _mail_archivieren(message_id: str) -> bool:
    """Verschiebt eine Mail ins Archiv."""
    try:
        resp = requests.post(f"{API_URL.rstrip('/')}/api/lena/mail/move",
                             headers=_api_headers(), timeout=30,
                             json={"message_id": message_id, "target_folder": "Archive"})
        if resp.status_code != 200:
            logger.warning("mail/move HTTP %d: %s", resp.status_code, resp.text[:150])
            return False
        return True
    except Exception as exc:
        logger.warning("Mail archivieren fehlgeschlagen: %s", exc)
        return False


def _rueckfrage(text: str, state: Dict[str, Any]) -> bool:
    """
    Stellt Sven eine Rueckfrage per Telegram.

    Eigenes Tageslimit, damit Rueckfragen nicht mit den Hoch-Prio-Alarmen um
    dasselbe Kontingent konkurrieren.
    """
    if not RUECKFRAGEN_AKTIV:
        return False
    heute = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    zaehler = state.setdefault("rueckfragen", {})
    if zaehler.get("tag") != heute:
        zaehler.clear()
        zaehler["tag"] = heute
        zaehler["anzahl"] = 0
    if zaehler.get("anzahl", 0) >= RUECKFRAGEN_TAGESLIMIT:
        logger.info("Rueckfrage unterdrueckt — Tageslimit %d erreicht.", RUECKFRAGEN_TAGESLIMIT)
        return False
    if not TG_ADMIN_CHAT:
        logger.warning("TELEGRAM_ADMIN_CHAT_ID fehlt — Rueckfrage nicht moeglich.")
        return False
    try:
        # WICHTIG: ueber mein-assistent senden, nicht direkt an die Telegram-API.
        # Nur so landet die Nachricht in outbound_messages — und nur dann sieht
        # Lena Svens Antwort mitsamt dem zitierten Original. Ohne das Tracking
        # laeuft jede Rueckfrage ins Leere.
        resp = requests.post(f"{API_URL.rstrip('/')}/api/lena/telegram/send",
                             headers=_api_headers(), timeout=30,
                             # HBE-3107: ohne parse_mode. Rueckfragen enthalten
                             # Betreffzeilen und Namen — dort stehen Klammern,
                             # Bindestriche und Punkte, die Telegram im Markdown-
                             # Modus als kaputte Auszeichnung zurueckweist.
                             json={"chat_id": TG_ADMIN_CHAT, "text": text})
        if resp.status_code != 200:
            logger.warning("Rueckfrage HTTP %d: %s", resp.status_code, resp.text[:150])
            return False
        zaehler["anzahl"] = zaehler.get("anzahl", 0) + 1
        return True
    except Exception as exc:
        logger.warning("Rueckfrage fehlgeschlagen: %s", exc)
        return False


WEITERLEITUNG_SYSTEM = """Du formulierst fuer Sven Herbert einen einzigen Satz,
der erklaert, warum er eine E-Mail an einen seiner Direktberichte weitergibt.

Regeln:
- EIN Satz, hoechstens 20 Woerter.
- Sven duzt seine Direktberichte.
- Sachlich, ohne Floskeln, ohne "bitte um Erledigung".
- Nenne den Kern der Sache, nicht die Betreffzeile.
- Schreibe AN den Empfaenger, nicht ueber Sven. Also nicht "Ich soll die
  Rechnungen hochladen", sondern "hier geht es um die Rechnungen im JobRouter".

Beispiele:
  "das ist eine Zahlungserinnerung von Coglas, schau bitte mal drauf."
  "hier fragt jemand nach einer Entwicklungspartnerschaft fuer einen Bauroboter."

Antworte NUR mit dem Satz, ohne Anrede, ohne Gruss, ohne Anfuehrungszeichen."""


def _weiterleitungstext(vorname: str, betreff: str, absender: str,
                        vorschau: str) -> str:
    """
    Baut den Text des Weiterleitungs-Entwurfs.

    HBE-3061: Vorher stand hier ein fester Platzhalter ohne Anrede, ohne Kontext
    und ohne Umlaute ("Hallo, kannst du das bitte uebernehmen? Viele Gruesse").
    In einer Mail an einen Direktbericht sah das nach Maschine aus.
    """
    anrede = f"Hallo {vorname}," if vorname else "Hallo,"
    satz = ""
    client = _get_llm_client()
    if client is not None:
        try:
            resp = client.messages.create(
                model=LLM_MODEL, max_tokens=120, system=WEITERLEITUNG_SYSTEM,
                messages=[{"role": "user", "content":
                           f"Von: {absender}\nBetreff: {betreff}\n\n{(vorschau or '')[:800]}"}],
                timeout=LLM_TIMEOUT_SEC)
            satz = resp.content[0].text.strip().strip('"').split("\n")[0][:200]
        except Exception as exc:
            logger.warning("Weiterleitungstext nicht formulierbar: %s", exc)
    if not satz:
        satz = f"kannst du das bitte übernehmen? Es geht um „{betreff[:70]}“."
    return f"{anrede}\n\n{satz}\n\nViele Grüße\nSven"


MAX_VORGANGS_MERKER = 400


def vorgang_schon_erledigt(state: Dict[str, Any], conv_id: str) -> Optional[Dict[str, Any]]:
    """Hat dieser Vorgang bereits eine Konsequenz bekommen?"""
    if not conv_id:
        return None
    return (state.get("konsequenz_vorgaenge") or {}).get(conv_id)


def vorgang_merken(state: Dict[str, Any], conv_id: str, ergebnis: Dict[str, Any]) -> None:
    if not conv_id or not ergebnis.get("art"):
        return
    merker = state.setdefault("konsequenz_vorgaenge", {})
    merker[conv_id] = {"art": ergebnis["art"], "id": ergebnis.get("id"),
                       "ts": datetime.now(timezone.utc).isoformat()}
    if len(merker) > MAX_VORGANGS_MERKER:
        for alt in list(merker.keys())[:len(merker) - MAX_VORGANGS_MERKER]:
            del merker[alt]


def konsequenz_ausfuehren(mail: Dict[str, Any], schritt: int,
                          empfaenger: Optional[str] = None,
                          state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Fuehrt die Konsequenz des Schritts aus. Gibt ein Protokoll-Dict zurueck.

    Nichts hiervon wird gesendet oder zugewiesen — es entstehen ausschliesslich
    Entwuerfe und Aufgaben, die Sven prueft.

    HBE-3052: Die Konsequenz gilt pro VORGANG, nicht pro Mail. Am 15.09.
    erzeugten zwei Mails desselben Themas zwei Asana-Aufgaben; ein Vorgang mit
    fuenf Mails haette fuenf erzeugt.
    """
    ergebnis: Dict[str, Any] = {"art": None, "id": None, "stufe": None, "hinweis": None}
    if not AKTIONEN_AKTIV or schritt not in (3, 4, 5):
        return ergebnis

    conv = (mail.get("conversation_id") or "") if state is not None else ""
    if conv:
        schon = vorgang_schon_erledigt(state, conv)
        if schon:
            ergebnis["hinweis"] = (f"Vorgang hat bereits eine Konsequenz "
                                   f"({schon.get('art')} vom {str(schon.get('ts'))[:16]})")
            return ergebnis

    betreff = mail.get("subject", "") or ""
    s_name = mail.get("sender_name", "") or ""
    s_mail = mail.get("sender_email", "") or ""
    vorschau = mail.get("body_preview", "") or ""
    mid = mail.get("message_id", "") or ""

    try:
        if schritt == 4:
            inhalt = _volltext(mid, vorschau)
            titel, frist, kontext = _llm_aufgabe(betreff, s_name, s_mail, inhalt,
                                                 mail.get("received_at", "") or "")
            gid = _asana_aufgabe(betreff, s_name, s_mail, vorschau,
                                 mail.get("received_at", "") or "",
                                 titel=titel, frist=frist, kontext=kontext)
            ergebnis.update(art="asana" if gid else None, id=gid)
            if not gid:
                ergebnis["hinweis"] = "Aufgabe existiert bereits oder Asana nicht erreichbar"
                return ergebnis
            if state is not None:
                vorgang_merken(state, conv, ergebnis)
            # HBE-3061: Die Aufgabe traegt Betreff, Absender und Inhalt — die Mail
            # hat ihren Zweck erfuellt und muss den Posteingang nicht blockieren.
            archiviert = _mail_archivieren(mid) if TUN_MAIL_ARCHIVIEREN else False
            ergebnis["hinweis"] = "Mail archiviert" if archiviert else "Mail bleibt liegen"
            if state is not None:
                _rueckfrage(
                    f"📋 Aufgabe angelegt\n\n{titel}\n"  # HBE-3120: Klartext, wir senden ohne parse_mode
                    + (f"Fällig: {frist}\n" if frist else "")
                    + ("Mail abgelegt." if archiviert else "Mail bleibt im Posteingang.")
                    + f"\n\nhttps://app.asana.com/0/{ASANA_BOARD_GID}/{gid}",
                    state)
            return ergebnis

        if schritt == 3:
            adr = empfaenger_zu_adresse(empfaenger)
            if not adr:
                ergebnis["hinweis"] = f"Empfaenger '{empfaenger}' nicht aufloesbar"
                if state is not None:
                    _rueckfrage(
                        f"↪️ An wen weiterleiten?\n\n"
                        f"{s_name or s_mail}\n{betreff[:80]}\n\n"
                        + (f"Vorschlag war „{empfaenger}“, konnte ich aber nicht zuordnen.\n\n"
                           if empfaenger else "Ich konnte niemanden zuordnen.\n\n")
                        + "Antwort: Name — oder andere Kategorie nennen "
                          "(ablegen / erledigen / terminieren)",
                        state)
                return ergebnis
            vorname = (empfaenger or "").split()[0] if empfaenger else ""
            text = _weiterleitungstext(vorname, betreff, s_name, vorschau)
            did = _entwurf_anlegen(mid, f"WG: {betreff}", text, modus="forward",
                                   to=[{"name": empfaenger or "", "email": adr}])
            ergebnis.update(art="weiterleitung" if did else None, id=did,
                            hinweis=f"an {adr}" if did else "Entwurf fehlgeschlagen")
            if did and state is not None:
                vorgang_merken(state, conv, ergebnis)
            return ergebnis

        # Schritt 5 — Antwortentwurf
        stufe, text, frage = _llm_entwurf(betreff, s_name, s_mail, vorschau, mid)
        ergebnis["stufe"] = stufe
        text = (text or "").strip()
        # Leerer oder nur aus Leerzeichen bestehender Text ist kein Entwurf.
        if stufe == "nein" or not text:
            ergebnis["hinweis"] = frage or "kein Entwurf moeglich"
            # HBE-3061: Nicht schweigen. Die Begruendung des Modells ist bereits
            # eine brauchbare Frage mit Optionen — die gehoert zu Sven, nicht ins Log.
            if state is not None and frage:
                _rueckfrage(
                    f"✏️ Wie soll ich antworten?\n\n"
                    f"{s_name or s_mail}\n{betreff[:80]}\n\n{frage[:600]}",
                    state)
            return ergebnis
        if stufe == "geruest":
            text += ("\n\n---\nHinweis von Lena: Die mit [[...]] markierten Stellen "
                     "brauchen deine Angabe.")
        did = _entwurf_anlegen(mid, f"AW: {betreff}", text, modus="reply")
        ergebnis.update(art="antwort" if did else None, id=did)
        if did and state is not None:
            vorgang_merken(state, conv, ergebnis)
        elif not did:
            ergebnis["hinweis"] = "Entwurf konnte nicht angelegt werden"
        return ergebnis

    except Exception as exc:
        logger.warning("Konsequenz fuer Schritt %s fehlgeschlagen: %s", schritt, exc)
        ergebnis["hinweis"] = f"Fehler: {exc}"[:120]
        return ergebnis


# ── Svens Korrekturen erkennen (HBE-3052) ────────────────────────────────────
# Der bisherige Lern-Loop war seit drei Monaten tot: er versuchte, Svens
# Korrekturen aus Outlook-Metadaten zu erraten, und hielt dabei Lenas eigene
# Kategorisierung fuer eine Korrektur. Beide Lerntabellen hatten null Zeilen.
#
# Der neue Weg ist direkt: Der Poller merkt sich seine eigene Entscheidung.
# Findet er die Mail spaeter mit einer ANDEREN Lena-Kategorie vor, war das Sven.
# Dann passiert zweierlei — die Konsequenz der neuen Kategorie wird ausgefuehrt,
# und die Korrektur wird gelernt. Damit ist das Setzen einer Kategorie in
# Outlook gleichzeitig Anweisung und Lernsignal, ohne Zusatzaufwand fuer Sven.

AKTION_ZU_KATEGORIE = {
    "ablegen": "Lena: Ablegen",
    "antworten": "Lena: Antworten",
    "tun": "Lena: Tun",
    "warten": "Lena: Warten",
    "recherchieren": "Lena: Recherchieren",
    "weiterleiten": "Lena: Weiterleiten",
}
KATEGORIE_ZU_AKTION = {v: k for k, v in AKTION_ZU_KATEGORIE.items()}
MAX_KORREKTUR_MERKER = 300


def _lena_kategorie(categories: List[str],
                    eigene_aktion: Optional[str] = None) -> Optional[str]:
    """
    Findet die massgebliche Lena-Kategorie auf einer Mail.

    HBE-3056: Liegen ZWEI Lena-Kategorien auf der Mail, gilt die, die NICHT von
    Lena selbst stammt. Damit genuegt es, eine Kategorie hinzuzufuegen — das
    Abwaehlen der alten entfaellt. Vorher entschied die Reihenfolge, in der
    Outlook die Kategorien zurueckgibt, also der Zufall.
    """
    gefunden = [c for c in (categories or []) if c in KATEGORIE_ZU_AKTION]
    if not gefunden:
        return None
    if len(gefunden) > 1 and eigene_aktion:
        meine = AKTION_ZU_KATEGORIE.get(eigene_aktion)
        andere = [c for c in gefunden if c != meine]
        if andere:
            return andere[0]
    return gefunden[0]


def regel_schluessel_waehlen(absender: str, betreff: str) -> Tuple[str, str]:
    """
    Waehlt den passenden Regel-Schluessel: Absender oder Betreff.

    Die Praxis verlangt beides. Anthropic-Belege kommen immer von
    invoice+statements@mail.anthropic.com, der Betreff traegt eine wechselnde
    Rechnungsnummer — dort greift der Absender. DMARC-Berichte kommen von acht
    verschiedenen Absendern, der Betreff ist durch einen Standard festgelegt —
    dort greift der Betreff.

    Heuristik: Enthaelt der lokale Teil der Adresse eine lange Zufallskennung,
    ist der Absender unbrauchbar. Dann wird der Betreff genommen.
    """
    s = (absender or "").strip().lower()
    local = s.split("@")[0]
    zufall = bool(re.search(r'[0-9a-z]{16,}', local)) or local.count("-") >= 3
    if s and not zufall:
        return "absender", s
    kern = re.sub(r'[0-9]+', '', (betreff or "")).strip()
    kern = re.sub(r'\s+', ' ', kern)[:45].strip().lower()
    return ("betreff_enthaelt", kern) if kern else ("absender", s)


def regel_vorschlagen(state: Dict[str, Any], absender: str, betreff: str,
                      aktion: str, db: Any) -> bool:
    """
    Schlaegt Sven eine feste Regel vor, wenn sich ein Muster wiederholt hat.

    Der Vorschlag nennt den gewaehlten Schluessel und wie viele Mails er
    getroffen haette — damit sofort sichtbar ist, ob er zu weit greift.
    """
    if not REGELVORSCHLAG_AKTIV or not db:
        return False
    domain = absender.lower().split("@")[-1] if "@" in absender else absender.lower()
    prefix = _normalize_subject_prefix(betreff)
    try:
        muster = db.recall_pattern(domain, prefix, LEARN_THRESHOLD)
    except Exception:
        return False
    if not muster or muster.get("action") != aktion:
        return False

    art, wert = regel_schluessel_waehlen(absender, betreff)
    vorgeschlagen = state.setdefault("regelvorschlaege", {})
    schluessel = f"{art}:{wert}:{aktion}"
    if schluessel in vorgeschlagen:
        return False          # nicht zweimal fragen

    treffer = _regel_treffer_schaetzen(art, wert)
    beschreibung = (f"Absender `{wert}`" if art == "absender"
                    else f"Betreff enthält „{wert}“")
    # HBE-3061: ueber _rueckfrage und damit ueber mein-assistent senden. Vorher
    # ging der Vorschlag direkt an die Telegram-API — dadurch fehlte das
    # outbound_messages-Tracking, und Svens "ja" erreichte Lena ohne den
    # zitierten Vorschlag. Sie haette gar nicht gewusst, worauf er antwortet.
    _rueckfrage(
        f"📌 Regel vorschlagen?\n\n"
        f"Du hast das jetzt {muster.get('count', LEARN_THRESHOLD)}× auf "
        f"{aktion} gesetzt.\n\n"
        f"Regel: {beschreibung} → {aktion}\n"
        f"Hätte {treffer} Mails der letzten Monate betroffen.\n\n"
        f"Antwort: ja — oder korrigiert tippen",
        state,
    )
    vorgeschlagen[schluessel] = datetime.now(timezone.utc).isoformat()
    logger.info("Regelvorschlag gesendet: %s -> %s (%d Treffer)", beschreibung, aktion, treffer)
    return True


def _regel_treffer_schaetzen(art: str, wert: str) -> int:
    """Wie viele Mails der Historie haette diese Regel betroffen?"""
    pfad = os.getenv("LENA_MAIL_TRIAGE_ARCHIV_DB", "/root/mail-archive/archive.db")
    if not os.path.exists(pfad):
        return 0
    try:
        conn = sqlite3.connect(f"file:{pfad}?mode=ro", uri=True, timeout=5)
        try:
            spalte = "sender_email" if art == "absender" else "subject"
            n = conn.execute(
                f"SELECT COUNT(*) FROM mails WHERE lower({spalte}) LIKE ?",
                (f"%{wert}%",)).fetchone()[0]
            return int(n)
        finally:
            conn.close()
    except Exception:
        return 0


def regeln_rueckwirkend(state: Dict[str, Any]) -> Dict[str, int]:
    """
    Wendet neu hinzugekommene Regeln auf Mails an, die schon im Posteingang liegen.

    HBE-3061: Eine Regel wirkte bisher nur auf neue Mails. Am 16.09. lagen ein
    Anthropic-Beleg und vier DMARC-Berichte im Posteingang, auf die die frisch
    angelegten Regeln gepasst haetten — sie waren aber schon kategorisiert und
    wurden nie wieder angefasst. Wer eine Regel anlegt, erwartet zu Recht, dass
    sie auch aufraeumt, was bereits dasteht.

    Laeuft nur, wenn sich die Regeldatei geaendert hat.
    """
    z = {"geprueft": 0, "angewendet": 0}
    try:
        p = Path(LAUFZEIT_REGELN)
        stand = p.stat().st_mtime if p.exists() else 0.0
    except Exception:
        stand = 0.0
    # Config-Regeln aendern sich nur beim Deploy — der Poller-Neustart deckt das ab.
    letzter = state.get("regeln_stand")
    if letzter is not None and abs(stand - float(letzter)) < 0.001:
        return z
    state["regeln_stand"] = stand
    if letzter is None:
        return z          # erster Lauf: nur merken, nicht rueckwirkend anwenden

    _get_regeln(neu_laden=True)
    try:
        resp = requests.get(f"{API_URL.rstrip('/')}/api/lena/mail/inbox",
                            headers={"X-API-Key": API_KEY},
                            params={"limit": 100, "unread_only": "false"}, timeout=60)
        if resp.status_code != 200:
            return z
        msgs = resp.json().get("messages", [])
    except Exception as exc:
        logger.warning("Rueckwirkende Regelpruefung fehlgeschlagen: %s", exc)
        return z

    for m in msgs:
        z["geprueft"] += 1
        regel = match_regel(m.get("from_email", "") or "", m.get("subject", "") or "")
        if not regel:
            continue
        mid = m.get("message_id") or ""
        aktion = regel["aktion"]
        vorher = _lena_kategorie(m.get("categories") or [])
        if vorher == AKTION_ZU_KATEGORIE.get(aktion):
            continue          # steht schon richtig
        schritt = 1 if aktion == "ablegen" else AKTION_ZU_SCHRITT.get(aktion, 2)
        if not _categorize_mail(mid, aktion, skip_archive=(aktion != "ablegen")):
            continue
        z["angewendet"] += 1
        logger.info(json.dumps({
            "event": "regel_rueckwirkend", "message_id": mid,
            "subject": (m.get("subject") or "")[:100], "regel": regel["name"],
            "vorher": vorher, "nachher": aktion,
        }, ensure_ascii=False))
        state.setdefault("triage_results", {})[mid] = {"action": aktion, "priority": "niedrig"}
        if schritt in (3, 4, 5):
            mail = {"message_id": mid, "subject": m.get("subject", "") or "",
                    "sender_name": m.get("from_name", "") or "",
                    "sender_email": m.get("from_email", "") or "",
                    "body_preview": m.get("body_preview", "") or "",
                    "received_at": m.get("received_at", "") or "",
                    "conversation_id": ""}
            konsequenz_ausfuehren(mail, schritt, regel.get("empfaenger"), state=state)
    if z["angewendet"]:
        logger.info("Rueckwirkend angewendet: %d Mails", z["angewendet"])
    return z


def _ist_zu_alt_fuer_anweisung(received_at: str) -> bool:
    """Ohne verwertbares Datum lieber als alt behandeln — nicht ausfuehren."""
    if not received_at:
        return True
    try:
        empfangen = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    alter = (datetime.now(timezone.utc) - empfangen).days
    return alter > ANWEISUNG_MAX_ALTER_TAGE


def _anweisung_ausfuehren(m: Dict[str, Any], mid: str, kategorie: str,
                          state: Dict[str, Any], erledigt: Dict[str, str],
                          eigene: Dict[str, Any], z: Dict[str, int]) -> None:
    """
    Fuehrt die Konsequenz einer von Sven gesetzten Kategorie aus (HBE-3118).

    Anders als bei einer Korrektur gibt es hier nichts zu lernen: Es liegt keine
    eigene Entscheidung vor, der die Kategorie widersprechen koennte. Ein
    Override waere eine erfundene Gegenueberstellung.
    """
    if not ANWEISUNGEN_AKTIV:
        return
    aktion = KATEGORIE_ZU_AKTION[kategorie]
    if erledigt.get(mid) == aktion:
        return                       # schon ausgefuehrt

    betreff = m.get("subject", "") or ""
    absender = m.get("from_email", "") or ""
    empfangen = m.get("received_at", "") or ""

    if _ist_zu_alt_fuer_anweisung(empfangen):
        # Einmal melden, dann Ruhe. Altbestand wird nicht rueckwirkend
        # abgearbeitet — sonst legt der erste Lauf einen Schwall Aufgaben an.
        gemeldet = state.setdefault("anweisungen_uebergangen", [])
        if mid not in gemeldet:
            gemeldet.append(mid)
            if len(gemeldet) > MAX_KORREKTUR_MERKER:
                del gemeldet[:len(gemeldet) - MAX_KORREKTUR_MERKER]
            logger.info(json.dumps({
                "event": "anweisung_uebergangen",
                "grund": f"aelter_als_{ANWEISUNG_MAX_ALTER_TAGE}_tage",
                "subject": betreff[:100], "sender": absender,
                "kategorie": kategorie, "empfangen": empfangen[:10],
            }, ensure_ascii=False))
            z["anweisungen_uebergangen"] = z.get("anweisungen_uebergangen", 0) + 1
        return

    z["anweisungen"] = z.get("anweisungen", 0) + 1
    logger.info(json.dumps({
        "event": "anweisung_erkannt",
        "message_id": mid, "subject": betreff[:100],
        "sender": absender, "kategorie": kategorie, "aktion": aktion,
    }, ensure_ascii=False))

    schritt = AKTION_ZU_SCHRITT.get(aktion, 2)
    mail = {
        "message_id": mid,
        "subject": betreff,
        "sender_name": m.get("from_name", "") or "",
        "sender_email": absender,
        "body_preview": m.get("body_preview", "") or "",
        "received_at": empfangen,
        "conversation_id": "",       # die Anweisung gilt dieser einen Mail
    }
    empf = None
    if schritt == 3:
        try:
            *_r, empf = triage_mail(betreff, absender, mail["body_preview"],
                                    mail["sender_name"], received_at=empfangen)
        except Exception as exc:
            logger.warning("Empfaenger fuer Anweisung nicht bestimmbar: %s", exc)

    erg = konsequenz_ausfuehren(mail, schritt, empf, state=state)
    if erg.get("art"):
        z["konsequenzen"] = z.get("konsequenzen", 0) + 1
        logger.info("Anweisungs-Konsequenz: %s fuer '%s'", erg["art"], betreff[:60])

    # Auch ohne Konsequenz vermerken — sonst laeuft der Fall in jedem Zyklus neu.
    erledigt[mid] = aktion
    eigene[mid] = {"action": aktion, "priority": "mittel"}


def korrekturen_verarbeiten(state: Dict[str, Any]) -> Dict[str, int]:
    """
    Sucht Mails, deren Lena-Kategorie Sven geaendert hat, und reagiert darauf.

    Returns Zaehler-Dict.
    """
    z = {"geprueft": 0, "korrekturen": 0, "konsequenzen": 0, "gelernt": 0,
         "anweisungen": 0, "anweisungen_uebergangen": 0}
    if not KORREKTUREN_AKTIV:
        return z

    try:
        resp = requests.get(f"{API_URL.rstrip('/')}/api/lena/mail/inbox",
                            headers={"X-API-Key": API_KEY},
                            params={"limit": 80, "unread_only": "false"}, timeout=60)
        if resp.status_code != 200:
            logger.warning("Korrektur-Pass: inbox HTTP %d", resp.status_code)
            return z
        msgs = resp.json().get("messages", [])
    except Exception as exc:
        logger.warning("Korrektur-Pass fehlgeschlagen: %s", exc)
        return z

    eigene = state.get("triage_results", {}) or {}
    erledigt = state.setdefault("korrekturen_erledigt", {})
    db = _get_learning_db()

    for m in msgs:
        mid = m.get("message_id") or ""
        if not mid:
            continue
        meine = (eigene.get(mid) or {}).get("action")
        aktuell_kat = _lena_kategorie(m.get("categories") or [], meine)
        if not aktuell_kat:
            continue
        if not meine:
            # HBE-3118: Keine eigene Entscheidung — also keine Korrektur, sondern
            # eine Anweisung. Bisher endete der Fall hier mit "continue", und die
            # gesetzte Kategorie blieb folgenlos.
            _anweisung_ausfuehren(m, mid, aktuell_kat, state, erledigt, eigene, z)
            continue
        z["geprueft"] += 1

        aktuell_aktion = KATEGORIE_ZU_AKTION[aktuell_kat]
        if aktuell_aktion == meine:
            continue          # unveraendert
        if erledigt.get(mid) == aktuell_aktion:
            continue          # diese Korrektur schon verarbeitet

        z["korrekturen"] += 1
        betreff = m.get("subject", "") or ""
        absender = m.get("from_email", "") or ""
        logger.info(json.dumps({
            "event": "korrektur_erkannt",
            "message_id": mid,
            "subject": betreff[:100],
            "sender": absender,
            "lena": meine,
            "sven": aktuell_aktion,
        }, ensure_ascii=False))

        # 1) Lernen — jetzt mit einem echten Signal statt einer Vermutung
        if db:
            try:
                domain = absender.lower().split("@")[-1] if "@" in absender else absender.lower()
                db.record_override(
                    message_id=mid,
                    sender_domain=domain,
                    subject_prefix=_normalize_subject_prefix(betreff),
                    original_action=meine,
                    original_priority=(eigene.get(mid) or {}).get("priority", "mittel"),
                    override_action=aktuell_aktion,
                    override_priority=(eigene.get(mid) or {}).get("priority", "mittel"),
                )
                z["gelernt"] += 1
            except Exception as exc:
                logger.warning("Override konnte nicht gespeichert werden: %s", exc)

        # 2) Konsequenz der NEUEN Kategorie ausfuehren
        schritt = AKTION_ZU_SCHRITT.get(aktuell_aktion, 2)
        empf = None
        mail = {
            "message_id": mid,
            "subject": betreff,
            "sender_name": m.get("from_name", "") or "",
            "sender_email": absender,
            "body_preview": m.get("body_preview", "") or "",
            "received_at": m.get("received_at", "") or "",
            "conversation_id": "",   # Korrekturen gelten der einzelnen Mail
        }
        if schritt == 3:
            try:
                *_r, empf = triage_mail(betreff, absender, mail["body_preview"],
                                        mail["sender_name"],
                                        received_at=mail["received_at"])
            except Exception as exc:
                logger.warning("Empfaenger fuer Korrektur nicht bestimmbar: %s", exc)
        erg = konsequenz_ausfuehren(mail, schritt, empf, state=state)
        if erg.get("art"):
            z["konsequenzen"] += 1
            logger.info("Korrektur-Konsequenz: %s fuer '%s'", erg["art"], betreff[:60])

        erledigt[mid] = aktuell_aktion
        eigene[mid] = {"action": aktuell_aktion,
                       "priority": (eigene.get(mid) or {}).get("priority", "mittel")}

        # 3) Reicht es fuer einen Regelvorschlag?
        regel_vorschlagen(state, absender, betreff, aktuell_aktion, db)

    if len(erledigt) > MAX_KORREKTUR_MERKER:
        for alt in list(erledigt.keys())[:len(erledigt) - MAX_KORREKTUR_MERKER]:
            del erledigt[alt]
    return z


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
            json={"chat_id": TG_ADMIN_CHAT, "text": text},  # HBE-3107: siehe _rueckfrage
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
        # HBE-3048 — Konsequenzen
        "entwurf_antwort": 0,        # Antwortentwurf angelegt
        "entwurf_weiterleitung": 0,  # Weiterleitungs-Entwurf angelegt
        "entwurf_verzichtet": 0,     # bewusst kein Entwurf (Antwort braucht Sven)
        "asana_aufgabe": 0,          # Aufgabe im Board angelegt
        # HBE-3052 — Svens Korrekturen
        "korrekturen": 0,             # Kategorie von Sven geaendert
        "korrektur_konsequenzen": 0,  # daraufhin ausgeloeste Konsequenz
        "korrektur_gelernt": 0,       # als Lernsignal gespeichert
        # HBE-3118 — von Sven gesetzte Kategorien ohne eigene Vorentscheidung
        "anweisungen": 0,             # ausgefuehrt
        "anweisungen_uebergangen": 0, # zu alt, nur gemeldet
        # HBE-3061
        "regel_rueckwirkend": 0,      # neue Regel auf vorhandene Mails angewendet
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
            action, priority, base_rule, schritt, empfaenger = thread_entscheidung[conv]
            rule_id = f"thread:{base_rule}"
            learned_from = None
            counters["thread_uebernommen"] += 1
        else:
            action, priority, rule_id, learned_from, schritt, empfaenger = triage_mail(
                m.get("subject", ""),
                m.get("sender_email", ""),
                m.get("body_preview", ""),
                m.get("sender_name", ""),
                to_emails=m.get("to_emails") or [],
                cc_emails=m.get("cc_emails") or [],
                received_at=m.get("received_at", "") or "",
            )
            if conv:
                thread_entscheidung[conv] = (action, priority, rule_id, schritt, empfaenger)

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

        # HBE-3048: Konsequenz des Schritts ausfuehren — Entwurf oder Aufgabe.
        # Laeuft NACH dem Kategorisieren, damit ein Fehler hier die Kategorie
        # nicht verliert. Nichts wird gesendet oder zugewiesen.
        konsequenz = konsequenz_ausfuehren(m, schritt, empfaenger, state=state)
        if konsequenz.get("art") == "antwort":
            counters["entwurf_antwort"] += 1
        elif konsequenz.get("art") == "weiterleitung":
            counters["entwurf_weiterleitung"] += 1
        elif konsequenz.get("art") == "asana":
            counters["asana_aufgabe"] += 1
        elif AKTIONEN_AKTIV and schritt == 5 and konsequenz.get("stufe") == "nein":
            counters["entwurf_verzichtet"] += 1

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
        if konsequenz.get("art") or konsequenz.get("hinweis"):
            log_entry["konsequenz"] = {k: v for k, v in konsequenz.items() if v}
        logger.info(json.dumps(log_entry, ensure_ascii=False))

        if priority == "hoch":
            counters["high_priority"] += 1
            _tg_alert(
                f"⚠️ Lena-Triage Hoch-Prio\n"
                f"Von: {m.get('sender_name') or m.get('sender_email','')}\n"
                f"Betreff: {(m.get('subject') or '')[:100]}\n"
                f"Regel: {rule_id}",
                state,
            )

    # HBE-3052: Hat Sven eine Kategorie geaendert? Dann Konsequenz ausfuehren
    # und daraus lernen. Laeuft nach der Kategorisierung, damit die eigenen
    # Entscheidungen dieses Laufs bereits im State stehen.
    if not DRY_RUN:
        # HBE-3061: Neue Regeln zuerst rueckwirkend anwenden, dann Korrekturen.
        r = regeln_rueckwirkend(state)
        counters["regel_rueckwirkend"] = r["angewendet"]
        k = korrekturen_verarbeiten(state)
        counters["korrekturen"] = k["korrekturen"]
        counters["korrektur_konsequenzen"] = k["konsequenzen"]
        counters["korrektur_gelernt"] = k["gelernt"]
        counters["anweisungen"] = k.get("anweisungen", 0)
        counters["anweisungen_uebergangen"] = k.get("anweisungen_uebergangen", 0)

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

    # HBE-3048: Konsequenzen je Schritt
    if AKTIONEN_AKTIV:
        stil = _schreibstil()
        logger.info(
            "Konsequenzen AKTIV: Schritt 3 -> Weiterleitungs-Entwurf, "
            "Schritt 4 -> Asana (%s), Schritt 5 -> Antwort-Entwurf. "
            "Kategorie auf Entwuerfen: '%s'. Schreibstil: %d Zeichen aus %s",
            ASANA_BOARD_GID, ENTWURF_KATEGORIE, len(stil),
            SCHREIBSTIL_DATEI if Path(SCHREIBSTIL_DATEI).exists() else "Fallback",
        )
        if not ASANA_TOKEN:
            logger.warning("ASANA_ACCESS_TOKEN fehlt — Schritt 4 legt keine Aufgaben an.")
    else:
        logger.info(
            "Konsequenzen inaktiv (LENA_MAIL_TRIAGE_AKTIONEN=0) — es entstehen "
            "keine Entwuerfe und keine Aufgaben."
        )

    if KORREKTUREN_AKTIV:
        logger.info(
            "Korrektur-Erkennung aktiv: eine geaenderte Outlook-Kategorie loest "
            "die Konsequenz aus und wird als Lernsignal gespeichert."
        )
    else:
        logger.warning("Korrektur-Erkennung abgeschaltet (LENA_MAIL_TRIAGE_KORREKTUREN=0).")

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
