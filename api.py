"""
FastAPI REST-Endpunkt für den Meeting-Protokoll-Workflow der Herbert Gruppe.

Wird vom Cowork-Skill `meeting-protokoll` aufgerufen, sobald Sven ein Protokoll
review't und nach `01 Inbox/Überarbeitete Protokolle/` verschoben hat.

Aufgaben des Endpunkts:
  1. PDF aus Markdown generieren (Herbert-Blau #1F4E79) — oder pdf_base64 nutzen
  2. PDF an Outlook-Termin anhängen
  3. Kategorie „Protokoll" am Outlook-Termin setzen
  4. Betreff-Prefix „📄 " am Outlook-Termin setzen

Telegram-Bridge (HBE-402):
  POST /api/telegram/lena/webhook  — Empfängt Telegram-Updates, erstellt Paperclip-Issues für Lena
  POST /api/telegram/lena/send    — Sendet Telegram-Nachricht an einen Chat (intern, X-API-Key)
  POST /api/lena/telegram/send    — Sendet + trackt in outbound_messages für Reply-Threading (HBE-1091)
  Background-Job                  — Pollt Paperclip-Comments auf TELEGRAM_REPLY: Prefix, sendet via Telegram

Lena Mail-Management (HBE-607):
  POST /api/lena/mail/move       — Verschiebt Mail in Outlook-Ordner (Archive, Deleted Items, Custom)
  POST /api/lena/mail/mark-read  — Markiert Mail als gelesen

Auth:    X-API-Key Header (API_SECRET_KEY aus .env)
Port:    8502 (loopback, hinter nginx mit /api/-Proxy)
Token:   /app/auth/outlook_token.json (Docker-Volume `auth`)

Lokaler Start:
    uvicorn api:app --host 127.0.0.1 --port 8502 --reload
"""
import base64
import hmac
import json
import logging
import os
import re
import sqlite3
import subprocess
import tempfile
import threading
import time as _time
from collections import Counter, defaultdict, deque
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Literal, Optional, Tuple

import requests as _http
from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Security,
    UploadFile,
)
from fastapi.responses import HTMLResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

from database.protocols_db import ProtocolsDB

load_dotenv()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Mein Assistent – API",
    description=(
        "Interne REST-API für den Meeting-Protokoll-Workflow der Herbert Gruppe. "
        "Wird vom Cowork-Skill `meeting-protokoll` aufgerufen."
    ),
    version="1.0.0",
    docs_url=None,   # Swagger nicht öffentlich
    redoc_url=None,
    openapi_url=None,
)


# ---------------------------------------------------------------------------
# Auth (X-API-Key)
# ---------------------------------------------------------------------------
_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=True)
_API_SECRET_KEY = os.getenv("API_SECRET_KEY", "").strip()


def verify_api_key(key: str = Security(_API_KEY_HEADER)) -> str:
    if not _API_SECRET_KEY:
        raise HTTPException(
            status_code=500,
            detail="API_SECRET_KEY nicht konfiguriert (in .env setzen)",
        )
    if not hmac.compare_digest(key.encode(), _API_SECRET_KEY.encode()):
        raise HTTPException(status_code=403, detail="Ungültiger API Key")
    return key


# ---------------------------------------------------------------------------
# Protokoll-Review: DB, Templates, Static, Dual-Auth
# ---------------------------------------------------------------------------
_BASE_DIR = Path(__file__).resolve().parent

# Öffentliche Basis-URL für Reviewer-Links (hinter nginx/Authentik)
_PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL", "https://mein-assistent.herbertgruppe.com"
).rstrip("/")

_TEMPLATES_DIR = _BASE_DIR / "templates"
_STATIC_DIR = _BASE_DIR / "static"
_TEMPLATES_DIR.mkdir(exist_ok=True)
_STATIC_DIR.mkdir(exist_ok=True)

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
app.mount("/review-static", StaticFiles(directory=str(_STATIC_DIR)), name="review-static")

# protocols.db beim API-Start initialisieren (führt Migration automatisch aus)
_protocols_db = ProtocolsDB(db_path=str(_BASE_DIR / "data" / "protocols.db"))

# Trusted Proxies: nur von diesen IPs werden Authentik-Header (X-Authentik-Username,
# X-Forwarded-Email) akzeptiert. nginx läuft auf 127.0.0.1 — daher Default.
# Mehrere IPs kommasepariert: TRUSTED_PROXIES=127.0.0.1,10.0.0.1
_TRUSTED_PROXIES: set = set(
    ip.strip()
    for ip in os.getenv("TRUSTED_PROXIES", "127.0.0.1").split(",")
    if ip.strip()
)
if not _TRUSTED_PROXIES:
    logger.warning(
        "[security] TRUSTED_PROXIES ist leer — Authentik-Header werden nie akzeptiert. "
        "Setze TRUSTED_PROXIES=127.0.0.1 in .env."
    )


def get_authenticated_user(
    request: Request,
    x_authentik_username: Optional[str] = Header(None),
    x_forwarded_email: Optional[str] = Header(None),
    api_key: Optional[str] = Header(None, alias="X-API-Key"),
    token: Optional[str] = Query(None),
) -> str:
    """
    Dual-Auth für Browser-Endpoints (/review/, /api/asana/, /api/calendar/events).

    Akzeptiert (in dieser Reihenfolge):
      1. Authentik-Session — nginx injiziert X-Authentik-Username / X-Forwarded-Email.
         Wird nur akzeptiert wenn die Anfrage von einer vertrauenswürdigen Proxy-IP
         kommt (TRUSTED_PROXIES env, Default 127.0.0.1). Verhindert Header-Spoofing
         bei direktem FastAPI-Zugriff.
      2. X-API-Key (für Skill-/Skript-Zugriffe, timing-safe via hmac.compare_digest)
      3. Gültiger, nicht abgelaufener Reviewer-Token als ?token= Query-Param
         (review.js sendet den Token bei allen Dropdown-Calls mit)
    """
    client_ip = request.client.host if request.client else ""
    from_trusted_proxy = client_ip in _TRUSTED_PROXIES

    if from_trusted_proxy and x_authentik_username:
        return x_authentik_username
    if from_trusted_proxy and x_forwarded_email:
        return x_forwarded_email
    if api_key and _API_SECRET_KEY and hmac.compare_digest(api_key, _API_SECRET_KEY):
        return "api-client"
    if token:
        protocol = _protocols_db.get_by_token(token)
        if protocol and not ProtocolsDB.is_expired(protocol):
            return "reviewer"
    raise HTTPException(status_code=401, detail="Unauthorized")


# ---------------------------------------------------------------------------
# Telegram-Bridge (HBE-402, HBE-1421)
# ---------------------------------------------------------------------------
# Registry-Pattern: ein _TgAgentCfg pro PA, konfiguriert via Env-Vars.
# Neue PA = 3 neue Env-Vars, kein Code-Change.
#
# Neues Format (ab HBE-1421):
#   TELEGRAM_AGENT_FLORIAN_TOKEN=...
#   TELEGRAM_AGENT_FLORIAN_WEBHOOK_SECRET=...
#   TELEGRAM_AGENT_FLORIAN_PAPERCLIP_AGENT_ID=...
#
# Altes Format (weiterhin unterstützt für Lena/Mara):
#   TELEGRAM_BOT_TOKEN / TELEGRAM_WEBHOOK_SECRET / PAPERCLIP_LENA_AGENT_ID
#   TELEGRAM_MARA_BOT_TOKEN / TELEGRAM_MARA_WEBHOOK_SECRET / PAPERCLIP_MARA_AGENT_ID

from dataclasses import dataclass as _dataclass


@_dataclass
class _TgAgentCfg:
    slug: str
    token: str
    webhook_secret: str
    admin_chat_id: str
    pc_agent_id: str
    db_path: Path


# Slug → legacy DB filename (preserves existing on-disk DBs on upgrade)
_TG_LEGACY_DB_NAMES: dict = {
    "lena": "telegram.db",
    "mara": "telegram_mara.db",
}


def _build_tg_registry() -> dict:
    """Build the Telegram agent registry from env vars (new-style + legacy fallbacks)."""
    _data_dir = Path(__file__).resolve().parent / "data"
    reg: dict = {}

    # Legacy: Lena — TELEGRAM_BOT_TOKEN / TELEGRAM_WEBHOOK_SECRET
    _lena_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if _lena_token:
        _lena_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
        if not _lena_secret:
            raise RuntimeError(
                "TELEGRAM_WEBHOOK_SECRET muss gesetzt sein wenn TELEGRAM_BOT_TOKEN konfiguriert ist. "
                "Ohne das Secret ist der Webhook für beliebige Caller offen. "
                "Generierung: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
        reg["lena"] = _TgAgentCfg(
            slug="lena",
            token=_lena_token,
            webhook_secret=_lena_secret,
            admin_chat_id=os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip(),
            pc_agent_id=os.getenv("PAPERCLIP_LENA_AGENT_ID", "").strip(),
            db_path=_data_dir / "telegram.db",
        )

    # Legacy: Mara — TELEGRAM_MARA_BOT_TOKEN / TELEGRAM_MARA_WEBHOOK_SECRET
    _mara_token = os.getenv("TELEGRAM_MARA_BOT_TOKEN", "").strip()
    if _mara_token:
        _mara_secret = os.getenv("TELEGRAM_MARA_WEBHOOK_SECRET", "").strip()
        if not _mara_secret:
            raise RuntimeError(
                "TELEGRAM_MARA_WEBHOOK_SECRET muss gesetzt sein wenn TELEGRAM_MARA_BOT_TOKEN konfiguriert ist."
            )
        _mara_pc_id = os.getenv("PAPERCLIP_MARA_AGENT_ID", "").strip()
        if not _mara_pc_id:
            raise RuntimeError(
                "PAPERCLIP_MARA_AGENT_ID muss gesetzt sein wenn TELEGRAM_MARA_BOT_TOKEN konfiguriert ist. "
                "Ohne Agent-ID können eingehende Telegram-Replies nicht dem richtigen Paperclip-Agent zugeordnet werden."
            )
        reg["mara"] = _TgAgentCfg(
            slug="mara",
            token=_mara_token,
            webhook_secret=_mara_secret,
            admin_chat_id=os.getenv("TELEGRAM_MARA_ADMIN_CHAT_ID", os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")).strip(),
            pc_agent_id=_mara_pc_id,
            db_path=_data_dir / "telegram_mara.db",
        )

    # New-style: TELEGRAM_AGENT_{SLUG}_TOKEN overrides / extends legacy entries
    for _env_key, _env_val in os.environ.items():
        if not (_env_key.startswith("TELEGRAM_AGENT_") and _env_key.endswith("_TOKEN")):
            continue
        _env_val = _env_val.strip()
        if not _env_val:
            continue
        _raw_slug = _env_key[len("TELEGRAM_AGENT_"):-len("_TOKEN")]
        _slug = _raw_slug.lower()
        _secret = os.getenv(f"TELEGRAM_AGENT_{_raw_slug}_WEBHOOK_SECRET", "").strip()
        if not _secret:
            raise RuntimeError(
                f"TELEGRAM_AGENT_{_raw_slug}_WEBHOOK_SECRET muss gesetzt sein wenn "
                f"TELEGRAM_AGENT_{_raw_slug}_TOKEN konfiguriert ist."
            )
        _pc_id = os.getenv(f"TELEGRAM_AGENT_{_raw_slug}_PAPERCLIP_AGENT_ID", "").strip()
        _admin = os.getenv(
            f"TELEGRAM_AGENT_{_raw_slug}_ADMIN_CHAT_ID",
            os.getenv("TELEGRAM_ADMIN_CHAT_ID", ""),
        ).strip()
        _db_name = _TG_LEGACY_DB_NAMES.get(_slug, f"telegram_{_slug}.db")
        reg[_slug] = _TgAgentCfg(
            slug=_slug,
            token=_env_val,
            webhook_secret=_secret,
            admin_chat_id=_admin,
            pc_agent_id=_pc_id,
            db_path=_data_dir / _db_name,
        )

    return reg


_TELEGRAM_AGENTS: dict = _build_tg_registry()

# Shared Paperclip credentials for all Telegram agents
_PC_API_URL    = os.getenv("PAPERCLIP_API_URL_MA", "https://paperclip.herbertgruppe.com").strip()
_PC_API_KEY    = os.getenv("PAPERCLIP_API_KEY_MA", "").strip()
_PC_COMPANY_ID = os.getenv("PAPERCLIP_COMPANY_ID_MA", "").strip()

# MARA_SPEAKER_FALLBACK_DEFAULT: ask | continue | pause
_MARA_SPEAKER_FALLBACK_DEFAULT = os.getenv("MARA_SPEAKER_FALLBACK_DEFAULT", "ask").strip()

# ---------------------------------------------------------------------------
# Backward-compat module-level globals (derived from registry; used by speaker
# endpoints and tests that patch these names directly).
# ---------------------------------------------------------------------------
_lena_cfg: _TgAgentCfg | None = _TELEGRAM_AGENTS.get("lena")
_mara_cfg: _TgAgentCfg | None = _TELEGRAM_AGENTS.get("mara")

_TG_BOT_TOKEN        = _lena_cfg.token          if _lena_cfg else ""
_TG_WEBHOOK_SECRET   = _lena_cfg.webhook_secret if _lena_cfg else ""
_TG_ADMIN_CHAT_ID    = _lena_cfg.admin_chat_id  if _lena_cfg else ""
_PC_LENA_AGENT_ID    = _lena_cfg.pc_agent_id    if _lena_cfg else ""

_TG_MARA_BOT_TOKEN      = _mara_cfg.token          if _mara_cfg else ""
_TG_MARA_WEBHOOK_SECRET = _mara_cfg.webhook_secret if _mara_cfg else ""
_TG_MARA_ADMIN_CHAT_ID  = _mara_cfg.admin_chat_id  if _mara_cfg else ""
_PC_MARA_AGENT_ID       = _mara_cfg.pc_agent_id    if _mara_cfg else ""

_TELEGRAM_DB_PATH      = _lena_cfg.db_path if _lena_cfg else Path(__file__).resolve().parent / "data" / "telegram.db"
_TELEGRAM_MARA_DB_PATH = _mara_cfg.db_path if _mara_cfg else Path(__file__).resolve().parent / "data" / "telegram_mara.db"

# ── Per-endpoint Telegram rate limiter (HBE-1212) ─────────────────────────────
# Limits both /api/lena/telegram/send and /api/mara/telegram/send independently.
# On breach: returns HTTP 429 + alerts Sven once per flood event.
_TG_RATE_LIMIT = int(os.getenv("TELEGRAM_SEND_RATE_LIMIT", "10"))  # max calls per window
_TG_RATE_WINDOW = 60  # seconds
_TG_RATE_LOCK: threading.Lock = threading.Lock()
_TG_RATE_BUCKETS: dict = defaultdict(deque)
_TG_RATE_LAST_ALERT: dict = {}  # endpoint -> monotonic timestamp of last alert


def _tg_rate_check(endpoint: str) -> bool:
    """
    Returns True if this call is within rate limit, False if limit exceeded.
    Automatically fires a Sven alert on the first breach per flood event (cooldown 60 s).
    """
    now = _time.monotonic()
    should_alert = False
    with _TG_RATE_LOCK:
        bucket = _TG_RATE_BUCKETS[endpoint]
        while bucket and now - bucket[0] > _TG_RATE_WINDOW:
            bucket.popleft()
        if len(bucket) >= _TG_RATE_LIMIT:
            last_alert = _TG_RATE_LAST_ALERT.get(endpoint, 0.0)
            if now - last_alert > _TG_RATE_WINDOW:
                _TG_RATE_LAST_ALERT[endpoint] = now
                should_alert = bool(_TG_ADMIN_CHAT_ID)
            # return value decided inside lock, but I/O fires outside
            result = False
        else:
            bucket.append(now)
            result = True
    if should_alert:
        try:
            _tg_send_message(
                _TG_ADMIN_CHAT_ID,
                f"⚠️ Telegram-Flood erkannt auf /{endpoint}/telegram/send"
                f" (>{_TG_RATE_LIMIT} Calls/Min). Agent pausiert oder Loop?"
                " Sven-Aktion erforderlich.",
            )
        except Exception:
            pass
    return result


@contextmanager
def _tg_agent_db(cfg: _TgAgentCfg):
    """Generic SQLite context manager for any Telegram agent's state (HBE-1421)."""
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(cfg.db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS pending_issues (
        issue_id   TEXT PRIMARY KEY,
        chat_id    TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS processed_comments (
        comment_id TEXT PRIMARY KEY,
        processed_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS speaker_questions (
        message_id INTEGER PRIMARY KEY,
        issue_id   TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS outbound_messages (
        telegram_msg_id INTEGER NOT NULL,
        chat_id         TEXT NOT NULL,
        issue_id        TEXT NOT NULL,
        comment_id      TEXT NOT NULL,
        comment_excerpt TEXT,
        button_options  TEXT,
        sent_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (telegram_msg_id, chat_id)
    )""")
    # HBE-1452: add button_options to existing DBs that predate the column
    try:
        conn.execute("ALTER TABLE outbound_messages ADD COLUMN button_options TEXT")
        conn.commit()
    except Exception:
        pass  # column already exists
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_outbound_issue ON outbound_messages(issue_id)"
    )
    conn.commit()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Backward-compat wrappers so existing code and tests can still use the old names.
def _telegram_db():
    """Backward-compat wrapper — use _tg_agent_db(_lena_cfg) directly for new code."""
    if _lena_cfg is None:
        raise RuntimeError(
            "Telegram agent 'lena' not configured — set TELEGRAM_BOT_TOKEN + TELEGRAM_WEBHOOK_SECRET env vars"
        )
    return _tg_agent_db(_lena_cfg)


def _telegram_mara_db():
    """Backward-compat wrapper — use _tg_agent_db(_mara_cfg) directly for new code."""
    if _mara_cfg is None:
        raise RuntimeError(
            "Telegram agent 'mara' not configured — set TELEGRAM_MARA_BOT_TOKEN + TELEGRAM_MARA_WEBHOOK_SECRET env vars"
        )
    return _tg_agent_db(_mara_cfg)


def _tg_agent_send(
    token: str,
    chat_id: str,
    text: str,
    reply_markup: Optional[dict] = None,
    parse_mode: Optional[str] = None,
) -> Optional[int]:
    """Generic Telegram sendMessage for any agent. Returns message_id on success, None on failure."""
    if not token:
        return None
    payload: dict = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        resp = _http.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=10,
        )
        if resp.ok:
            return resp.json().get("result", {}).get("message_id")
        return None
    except _http.exceptions.RequestException as exc:
        # Use type name only — str(exc) would include the full URL with the BOT_TOKEN
        logger.warning("[telegram] sendMessage failed: %s", type(exc).__name__)
        return None
    except Exception:
        logger.warning("[telegram] sendMessage failed: unexpected error", exc_info=False)
        return None


def _tg_agent_ack_callback(token: str, callback_query_id: str, text: str = "") -> None:
    """Generic Telegram answerCallbackQuery for any agent."""
    if not token:
        return
    try:
        _http.post(
            f"https://api.telegram.org/bot{token}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text},
            timeout=10,
        )
    except Exception:
        pass


# Backward-compat wrappers (speaker-question endpoint and existing tests use these names)
def _tg_send_message(
    chat_id: str,
    text: str,
    reply_markup: Optional[dict] = None,
    parse_mode: Optional[str] = None,
) -> Optional[int]:
    return _tg_agent_send(_TG_BOT_TOKEN, chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)


def _tg_mara_send_message(
    chat_id: str,
    text: str,
    reply_markup: Optional[dict] = None,
    parse_mode: Optional[str] = None,
) -> Optional[int]:
    return _tg_agent_send(_TG_MARA_BOT_TOKEN, chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)


def _tg_answer_callback_query(callback_query_id: str, text: str = "") -> None:
    _tg_agent_ack_callback(_TG_BOT_TOKEN, callback_query_id, text)


def _tg_mara_answer_callback_query(callback_query_id: str, text: str = "") -> None:
    _tg_agent_ack_callback(_TG_MARA_BOT_TOKEN, callback_query_id, text)


def _pc_get_issue_status(issue_id: str) -> Optional[str]:
    """Return the Paperclip issue status string, or None on error / not-found."""
    status, _ = _pc_get_issue_info(issue_id)
    return status


def _pc_get_issue_info(issue_id: str) -> tuple:
    """Return (status, assigneeAgentId) for a Paperclip issue, or (None, None) on error."""
    if not (_PC_API_URL and _PC_API_KEY):
        return None, None
    try:
        resp = _http.get(
            f"{_PC_API_URL}/api/issues/{issue_id}",
            headers={"Authorization": f"Bearer {_PC_API_KEY}"},
            timeout=15,
        )
    except Exception:
        return None, None
    if resp.status_code == 404:
        return None, None
    if not resp.ok:
        return None, None
    data = resp.json()
    return data.get("status"), data.get("assigneeAgentId")


def _pc_patch_issue_status(
    issue_id: str,
    status: str,
    blocked_reason: Optional[str] = None,
) -> bool:
    """PATCH Paperclip issue status. Returns True on success."""
    if not (_PC_API_URL and _PC_API_KEY):
        return False
    body: dict = {"status": status}
    if blocked_reason:
        body["blockedReason"] = blocked_reason
    try:
        resp = _http.patch(
            f"{_PC_API_URL}/api/issues/{issue_id}",
            json=body,
            headers={"Authorization": f"Bearer {_PC_API_KEY}"},
            timeout=15,
        )
    except Exception as exc:
        logger.warning("[telegram] pc_patch_issue error %s: %s", issue_id, type(exc).__name__)
        return False
    if resp.ok:
        return True
    logger.warning("[telegram] pc_patch_issue failed %s: %s %s", issue_id, resp.status_code, resp.text[:200])
    return False


def _pc_post_system_comment(issue_id: str, body: str) -> bool:
    """Post a system comment (no Telegram prefix) on a Paperclip issue. Returns True on success."""
    if not (_PC_API_URL and _PC_API_KEY):
        return False
    try:
        resp = _http.post(
            f"{_PC_API_URL}/api/issues/{issue_id}/comments",
            json={"body": body},
            headers={"Authorization": f"Bearer {_PC_API_KEY}"},
            timeout=15,
        )
    except Exception as exc:
        logger.warning("[telegram] pc_post_system_comment error %s: %s", issue_id, type(exc).__name__)
        return False
    if resp.ok:
        return True
    logger.warning("[telegram] pc_post_system_comment failed %s: %s", issue_id, resp.status_code)
    return False


def _pc_add_comment_to_issue(issue_id: str, username: str, text: str) -> bool:
    """Add a Telegram message as a comment on an existing Paperclip issue. Returns True on success."""
    if not (_PC_API_URL and _PC_API_KEY):
        return False
    body = f"Telegram-Nachricht von @{username}\n\n{text}"
    try:
        resp = _http.post(
            f"{_PC_API_URL}/api/issues/{issue_id}/comments",
            json={"body": body},
            headers={"Authorization": f"Bearer {_PC_API_KEY}"},
            timeout=15,
        )
    except Exception as exc:
        logger.warning("[telegram] comment post error for %s: %s", issue_id, type(exc).__name__)
        return False
    if resp.ok:
        return True
    logger.warning("[telegram] comment post failed for %s: %s %s", issue_id, resp.status_code, resp.text[:200])
    return False


# ── Triage v2 – Telegram Trigger Detection (HBE-1321) ─────────────────────

_ACTION_RUN_PATTERN = re.compile(
    r"(?i)^\s*lena[,\s]+(ablegen|weiterleiten|tun|antworten|warten|recherchieren)\b"
)

_ACTION_RUN_CAT_MAP = {
    "ablegen":       "Lena: Ablegen",
    "weiterleiten":  "Lena: Weiterleiten",
    "tun":           "Lena: Tun",
    "antworten":     "Lena: Antworten",
    "warten":        "Lena: Warten",
    "recherchieren": "Lena: Recherchieren",
}


def _detect_action_run_category(text: str) -> Optional[str]:
    """
    Erkennt Triage-v2-Trigger ('Lena, Ablegen', 'weiterleiten starten', etc.).
    Gibt den Kategorie-Key (z.B. 'ablegen') zurück oder None.
    """
    m = _ACTION_RUN_PATTERN.search(text)
    return m.group(1).lower() if m else None


def _pc_create_action_run_issue(
    chat_id: str, message_id: int, username: str, category: str
) -> Optional[str]:
    """
    Erstellt ein Paperclip-Action-Run-Issue fuer 'Lena, [Kategorie]'-Trigger (HBE-1321).
    Laed die Mail-Liste per Graph API und fuegt sie als Tabelle in die Beschreibung ein.
    Gibt die Issue-ID oder None zurueck.
    """
    if not (_PC_API_URL and _PC_API_KEY):
        return None

    lena_cat = _ACTION_RUN_CAT_MAP.get(category.lower(), f"Lena: {category.capitalize()}")
    cat_display = category.capitalize()

    mail_table = ""
    mail_count = 0
    try:
        tool = _get_outlook_tool()
        if tool.is_authenticated():
            import requests as _rq
            escaped = lena_cat.replace("'", "''")
            resp = _rq.get(
                "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages"
                f"?$filter=categories/any(c:c eq '{escaped}')"
                "&$select=id,subject,from,receivedDateTime"
                "&$top=50&$orderby=receivedDateTime asc",
                headers={"Authorization": f"Bearer {tool.access_token}"},
                timeout=15,
            )
            if resp.status_code == 200:
                mails = resp.json().get("value", [])
                mail_count = len(mails)
                if mails:
                    rows = []
                    for i, m in enumerate(mails[:20], 1):
                        sender = (m.get("from") or {}).get("emailAddress", {}) or {}
                        rows.append(
                            f"| {i} | {(m.get('subject') or '')[:60]} "
                            f"| {sender.get('address', '')} "
                            f"| {(m.get('receivedDateTime') or '')[:10]} |"
                        )
                    mail_table = (
                        f"\n\n## Mails ({mail_count})\n\n"
                        "| # | Betreff | Absender | Datum |\n"
                        "|---|---|---|---|\n"
                        + "\n".join(rows)
                    )
                    if mail_count > 20:
                        mail_table += f"\n_(+{mail_count - 20} weitere)_"
    except Exception:
        pass

    count_label = f" ({mail_count} Mails)" if mail_count else ""
    title = f"Action-Run: {cat_display}{count_label}"
    description = (
        f"Sven-Trigger per Telegram (@{username}): **{cat_display}**\n\n"
        f"Kategorie: `{lena_cat}`\n"
        "Lena arbeitet die Mails dieser Kategorie sequenziell ab."
        f"{mail_table}\n\n---\n"
        f"TELEGRAM_CHAT_ID: {chat_id}\n"
        f"TELEGRAM_MESSAGE_ID: {message_id}\n"
    )
    try:
        resp = _http.post(
            f"{_PC_API_URL}/api/companies/{_PC_COMPANY_ID}/issues",
            json={
                "title": title,
                "description": description,
                "assigneeAgentId": _PC_LENA_AGENT_ID,
                "priority": "high",
            },
            headers={"Authorization": f"Bearer {_PC_API_KEY}"},
            timeout=15,
        )
    except Exception as exc:
        logger.warning("[telegram/action-run] Paperclip request error: %s", type(exc).__name__)
        return None
    if resp.status_code in (200, 201):
        issue_id = resp.json().get("id")
        logger.info(
            "[telegram/action-run] created issue %s for category=%s mail_count=%d",
            issue_id, category, mail_count,
        )
        return issue_id
    logger.warning(
        "[telegram/action-run] issue creation failed: %s %s",
        resp.status_code, resp.text[:300],
    )
    return None


def _pc_create_tg_issue(
    cfg: _TgAgentCfg, chat_id: str, message_id: int, username: str, text: str
) -> Optional[str]:
    """Create a high-priority Paperclip issue assigned to the given agent. Returns issue ID or None."""
    if not (_PC_API_URL and _PC_API_KEY):
        logger.warning("[telegram/%s] PAPERCLIP_API_KEY_MA not set — skipping issue creation", cfg.slug)
        return None
    short = text[:50] + ("…" if len(text) > 50 else "")
    agent_display = cfg.slug.capitalize()
    description = (
        f"Telegram-Nachricht von @{username}\n\n"
        f"**Nachricht:**\n{text}\n\n"
        "---\n"
        f"TELEGRAM_CHAT_ID: {chat_id}\n"
        f"TELEGRAM_MESSAGE_ID: {message_id}\n"
    )
    try:
        resp = _http.post(
            f"{_PC_API_URL}/api/companies/{_PC_COMPANY_ID}/issues",
            json={
                "title": f"Telegram an {agent_display} von {username}: {short}",
                "description": description,
                "assigneeAgentId": cfg.pc_agent_id or None,
                "priority": "high",
            },
            headers={"Authorization": f"Bearer {_PC_API_KEY}"},
            timeout=15,
        )
    except _http.exceptions.RequestException as exc:
        logger.warning("[telegram/%s] Paperclip request error: %s", cfg.slug, type(exc).__name__)
        return None
    except Exception:
        logger.warning("[telegram/%s] Paperclip request error: unexpected error", cfg.slug, exc_info=False)
        return None
    if resp.status_code in (200, 201):
        return resp.json().get("id")
    logger.warning("[telegram/%s] issue creation failed: %s %s", cfg.slug, resp.status_code, resp.text[:300])
    return None


# Backward-compat wrappers (existing code/tests call these names directly)
def _pc_create_issue(chat_id: str, message_id: int, username: str, text: str) -> Optional[str]:
    if _lena_cfg is None:
        raise RuntimeError(
            "Telegram agent 'lena' not configured — set TELEGRAM_BOT_TOKEN + TELEGRAM_WEBHOOK_SECRET env vars"
        )
    return _pc_create_tg_issue(_lena_cfg, chat_id, message_id, username, text)


def _pc_create_mara_issue(chat_id: str, message_id: int, username: str, text: str) -> Optional[str]:
    if _mara_cfg is None:
        raise RuntimeError(
            "Telegram agent 'mara' not configured — set TELEGRAM_MARA_BOT_TOKEN + TELEGRAM_MARA_WEBHOOK_SECRET env vars"
        )
    return _pc_create_tg_issue(_mara_cfg, chat_id, message_id, username, text)


def _poll_tg_agent_replies(cfg: _TgAgentCfg) -> None:
    """
    Generic APScheduler background job (every 60 s).
    Scans Paperclip comments on tracked issues for TELEGRAM_REPLY: prefix
    and forwards the reply text via the agent's bot.
    """
    if not (cfg.token and _PC_API_URL and _PC_API_KEY):
        return
    with _tg_agent_db(cfg) as db:
        rows = db.execute("SELECT issue_id, chat_id FROM pending_issues").fetchall()
        rows = [(r["issue_id"], r["chat_id"]) for r in rows]

    for issue_id, chat_id in rows:
        try:
            resp = _http.get(
                f"{_PC_API_URL}/api/issues/{issue_id}/comments",
                headers={"Authorization": f"Bearer {_PC_API_KEY}"},
                timeout=15,
            )
        except Exception as exc:
            logger.error("[telegram/%s] comment fetch error for %s: %s", cfg.slug, issue_id, exc)
            continue

        if resp.status_code == 404:
            with _tg_agent_db(cfg) as db:
                db.execute("DELETE FROM pending_issues WHERE issue_id = ?", (issue_id,))
            continue
        if not resp.ok:
            continue

        data = resp.json()
        comments = data if isinstance(data, list) else data.get("items", data.get("comments", []))

        for comment in comments:
            cid = str(comment.get("id", ""))
            body = (comment.get("body") or comment.get("content") or "").strip()
            if not body.startswith("TELEGRAM_REPLY:"):
                continue
            with _tg_agent_db(cfg) as db:
                if db.execute(
                    "SELECT 1 FROM processed_comments WHERE comment_id = ?", (cid,)
                ).fetchone():
                    continue
            reply_text = body[len("TELEGRAM_REPLY:"):].strip()
            sent_msg_id = _tg_agent_send(cfg.token, chat_id, reply_text)
            if sent_msg_id:
                excerpt = reply_text[:200]
                with _tg_agent_db(cfg) as db:
                    db.execute(
                        "INSERT OR IGNORE INTO processed_comments (comment_id) VALUES (?)", (cid,)
                    )
                    db.execute(
                        "INSERT OR REPLACE INTO outbound_messages "
                        "(telegram_msg_id, chat_id, issue_id, comment_id, comment_excerpt) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (sent_msg_id, chat_id, issue_id, cid, excerpt),
                    )


# Backward-compat wrappers for the scheduler and any external references
def _poll_telegram_replies() -> None:
    if "lena" in _TELEGRAM_AGENTS:
        _poll_tg_agent_replies(_TELEGRAM_AGENTS["lena"])


def _poll_mara_telegram_replies() -> None:
    if "mara" in _TELEGRAM_AGENTS:
        _poll_tg_agent_replies(_TELEGRAM_AGENTS["mara"])


# ── APScheduler lifespan hooks ────────────────────────────────────────────────
_tg_scheduler = None


@app.on_event("startup")  # type: ignore[attr-defined]
def _start_tg_scheduler() -> None:
    global _tg_scheduler
    if not (_TELEGRAM_AGENTS and _PC_API_KEY):
        return
    from apscheduler.schedulers.background import BackgroundScheduler
    _tg_scheduler = BackgroundScheduler(daemon=True)
    for _agent_cfg in _TELEGRAM_AGENTS.values():
        if _agent_cfg.token:
            _job_id = f"tg_poll_{_agent_cfg.slug}"
            _cfg_ref = _agent_cfg  # capture for lambda
            _tg_scheduler.add_job(
                lambda c=_cfg_ref: _poll_tg_agent_replies(c),
                "interval",
                seconds=60,
                id=_job_id,
            )
            logger.info("[telegram/%s] reply-poll scheduler started (60 s interval)", _agent_cfg.slug)
    _tg_scheduler.start()


@app.on_event("startup")  # type: ignore[attr-defined]
def _start_vault_sync() -> None:
    """Initialisiert den Vault-Mirror und startet den 2-Min-Pull-Job."""
    import threading
    # Clone in background thread so API starts without blocking
    threading.Thread(target=_vault_init_mirror, daemon=True, name="vault-init").start()

    if _VAULT_BOT_TOKEN:
        from apscheduler.schedulers.background import BackgroundScheduler
        vault_sched = BackgroundScheduler(daemon=True)
        vault_sched.add_job(_vault_pull_from_origin, "interval", seconds=120, id="vault_pull")
        vault_sched.start()
        logger.info("[vault] pull-scheduler gestartet (120 s interval)")


@app.on_event("shutdown")  # type: ignore[attr-defined]
def _stop_tg_scheduler() -> None:
    if _tg_scheduler and _tg_scheduler.running:
        _tg_scheduler.shutdown(wait=False)


@app.on_event("shutdown")  # type: ignore[attr-defined]
def _vault_cleanup_askpass() -> None:
    import shutil
    shutil.rmtree(_VAULT_ASKPASS_DIR, ignore_errors=True)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class ProcessProtocolRequest(BaseModel):
    markdown: str = Field(..., description="Vollständiger Protokoll-Text als Markdown")
    meeting_name: str = Field(..., description="Terminname (für Dateiname)")
    event_id: Optional[str] = Field(
        None,
        description="Outlook-Event-ID. Wenn leer, werden Outlook-Schritte übersprungen.",
    )
    asana_gid: Optional[str] = Field(
        None,
        description="Asana-Board-GID — reserviert für spätere Erweiterung.",
    )
    pdf_base64: Optional[str] = Field(
        None,
        description=(
            "Optional: fertiges PDF als Base64. Wenn vorhanden, wird die "
            "Markdown→PDF-Konvertierung übersprungen."
        ),
    )


class ProcessProtocolResponse(BaseModel):
    success: bool
    pdf_generated: bool = False
    outlook_attachment: Optional[bool] = None
    outlook_category: Optional[bool] = None
    outlook_subject_prefix: Optional[bool] = None
    errors: List[str] = []
    message: str = ""


# ---------------------------------------------------------------------------
# PDF-Generierung (standalone, ohne Streamlit-Abhängigkeit)
# ---------------------------------------------------------------------------
_PDF_CSS = """
@page {
    margin: 1.8cm 2cm;
    @bottom-right {
        content: "Seite " counter(page) " / " counter(pages);
        font-family: Arial, Helvetica, sans-serif;
        font-size: 8pt;
        color: #888;
    }
}
body {
    font-family: Arial, Helvetica, sans-serif;
    font-size: 10pt;
    line-height: 1.3;
    color: #222;
}
h1 {
    color: #1F4E79;
    font-size: 16pt;
    border-bottom: 2px solid #1F4E79;
    padding-bottom: 4px;
    margin-top: 0;
    margin-bottom: 10px;
}
h2 {
    color: #1F4E79;
    font-size: 13pt;
    margin-top: 14px;
    margin-bottom: 6px;
    border-bottom: 1px solid #cdd9e8;
    padding-bottom: 3px;
}
h3 {
    color: #2e5d8c;
    font-size: 11pt;
    margin-top: 10px;
    margin-bottom: 5px;
}
p { margin: 4px 0; }
table {
    border-collapse: collapse;
    width: 100%;
    margin-bottom: 8px;
    font-size: 10pt;
}
th, td {
    border: 1px solid #cdd9e8;
    padding: 3px 6px;
    text-align: left;
    vertical-align: top;
}
th {
    background: #1F4E79;
    color: #ffffff;
    font-weight: bold;
}
tr:nth-child(even) td { background: #f3f7fc; }
ul, ol { margin: 3px 0 6px 20px; }
li { margin-bottom: 1px; }
strong { color: #1a3a5c; }
em { color: #444; }
hr {
    border: none;
    border-top: 1px solid #cdd9e8;
    margin: 10px 0;
}
code {
    background: #f0f5fb;
    padding: 1px 3px;
    border-radius: 3px;
    font-family: 'Courier New', monospace;
    font-size: 9pt;
}
pre {
    background: #f0f5fb;
    padding: 6px 8px;
    border-radius: 3px;
    font-family: 'Courier New', monospace;
    font-size: 9pt;
    line-height: 1.3;
    margin: 6px 0;
}
pre code { background: transparent; padding: 0; }
blockquote {
    border-left: 3px solid #1F4E79;
    margin: 6px 0;
    padding: 3px 10px;
    color: #555;
    background: #f6f9fc;
    font-size: 9pt;
}
a { color: #1F4E79; text-decoration: none; }
"""


def _markdown_to_pdf_standalone(markdown_text: str, output_path: Path) -> bool:
    """
    Konvertiert Markdown zu PDF mit Herbert-Gruppe-Branding.
    Keine Streamlit-Abhängigkeit (anders als utils.protocol.convert_markdown_to_pdf).
    """
    try:
        import markdown as md_lib
        from weasyprint import CSS, HTML

        # YAML-Frontmatter entfernen, falls vorhanden
        clean_md = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", markdown_text, flags=re.DOTALL)

        html_body = md_lib.markdown(
            clean_md,
            extensions=["extra", "nl2br", "tables", "sane_lists"],
        )

        full_html = (
            "<!DOCTYPE html>"
            "<html lang='de'>"
            "<head><meta charset='utf-8'></head>"
            f"<body>{html_body}</body>"
            "</html>"
        )

        HTML(string=full_html).write_pdf(
            str(output_path),
            stylesheets=[CSS(string=_PDF_CSS)],
        )
        return output_path.exists() and output_path.stat().st_size > 0

    except Exception as exc:  # noqa: BLE001
        print(f"[api] PDF-Generierung fehlgeschlagen: {exc}")
        import traceback

        traceback.print_exc()
        return False


def _safe_filename(name: str, max_len: int = 80) -> str:
    """Bereinigt einen String für die Verwendung als Dateinamen."""
    cleaned = re.sub(r"[^\w\-]+", "_", name).strip("_")
    return cleaned[:max_len] if cleaned else "Protokoll"


def _strip_obsidian_syntax(md: str) -> str:
    """
    Konvertiert Obsidian-spezifische Syntax in Standard-Markdown.

    Hintergrund: Das Markdown im Vault enthält Wikilinks und ggf. Callouts.
    WeasyPrint (PDF-Generierung) und Asana-Notes verstehen diese Syntax nicht
    und würden sie als Literaltext anzeigen. Daher hier vor jeder Weiterverarbeitung
    in Standard-Markdown konvertieren — die Vault-Version bleibt unverändert.

    Behandelt:
      - ![[bild.png]]              -> entfernt (Embeds können nicht aufgelöst werden)
      - [[pfad/datei.md|Anzeige]]  -> Anzeige
      - [[pfad/datei.md]]          -> datei (Basename, ohne .md)
      - [[Name]]                   -> Name
      - > [!type] Titel            -> > **Titel**  (Callout zu Quote-Block)
    """
    if not md:
        return md

    # 1) Embed-Wikilinks (Bilder/Audio/etc.) entfernen — nicht renderbar im PDF
    md = re.sub(r"!\[\[[^\]\n]+\]\]", "", md)

    # 2) Wikilinks mit Alias: [[pfad|Anzeige]] -> Anzeige
    md = re.sub(r"\[\[[^\]\|\n]+\|([^\]\n]+)\]\]", r"\1", md)

    # 3) Wikilinks ohne Alias: [[Pfad/Name.md]] oder [[Name]] -> Basename ohne .md
    def _basename(match: "re.Match[str]") -> str:
        target = match.group(1).strip()
        name = target.rsplit("/", 1)[-1]
        if name.endswith(".md"):
            name = name[:-3]
        return name

    md = re.sub(r"\[\[([^\]\|\n]+)\]\]", _basename, md)

    # 4) Callouts: > [!type] Titel  ->  > **Titel** (Quote-Block bleibt)
    md = re.sub(
        r"^(\s*>\s*)\[!\w+\][+-]?\s*(.*)$",
        r"\1**\2**",
        md,
        flags=re.MULTILINE,
    )

    return md


# ---------------------------------------------------------------------------
# Plaud Auth Management (Stage 1+2)
# ---------------------------------------------------------------------------

_PLAUD_CLIENT_ID = "client_f9e0b214-c11f-434b-8b95-c4497d1feb81"
_PLAUD_TOKEN_URL = "https://platform.plaud.ai/developer/api/oauth/third-party/access-token"
_PLAUD_REFRESH_URL = "https://platform.plaud.ai/developer/api/oauth/third-party/access-token/refresh"
_PLAUD_HOME = Path(os.getenv("PLAUD_HOME", "/opt/mein-assistent/data/.plaud"))
_PLAUD_TOKEN_FILE = _PLAUD_HOME / "tokens.json"


def _read_plaud_tokens() -> dict:
    """Read tokens.json from PLAUD_HOME. Returns None if missing or invalid."""
    try:
        if _PLAUD_TOKEN_FILE.exists():
            return json.loads(_PLAUD_TOKEN_FILE.read_text())
    except Exception:
        pass
    return None


def _write_plaud_tokens(tokens: dict) -> None:
    """Write tokens to PLAUD_HOME/tokens.json."""
    _PLAUD_HOME.mkdir(parents=True, exist_ok=True)
    _PLAUD_TOKEN_FILE.write_text(json.dumps(tokens, indent=2))


@app.get("/plaud/auth/status")
def plaud_auth_status(_key: str = Security(verify_api_key)):
    """Token status: expiry, validity, whether refresh is possible."""
    tokens = _read_plaud_tokens()
    if not tokens:
        return {"authenticated": False, "has_refresh_token": False}
    import time as _time_auth
    now_ms = _time_auth.time() * 1000
    expires_at_ms = tokens.get("expires_at", 0)
    # Decode refresh token expiry from JWT payload
    refresh_exp = None
    try:
        import base64 as _b64
        rt = tokens.get("refresh_token", "")
        payload = rt.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        rt_data = json.loads(_b64.urlsafe_b64decode(payload))
        refresh_exp = rt_data.get("exp")  # seconds
    except Exception:
        pass
    return {
        "authenticated": bool(tokens.get("access_token")),
        "access_token_expires_at": datetime.fromtimestamp(expires_at_ms / 1000).isoformat() if expires_at_ms else None,
        "access_token_expired": expires_at_ms < now_ms,
        "access_token_expires_in_minutes": int((expires_at_ms - now_ms) / 60000) if expires_at_ms > now_ms else 0,
        "refresh_token_expires_at": datetime.fromtimestamp(refresh_exp).isoformat() if refresh_exp else None,
        "refresh_token_expired": (refresh_exp * 1000 < now_ms) if refresh_exp else True,
        "has_refresh_token": bool(tokens.get("refresh_token")),
    }


@app.post("/plaud/auth/refresh")
def plaud_auth_refresh(_key: str = Security(verify_api_key)):
    """Use refresh_token to get a new access_token."""
    import base64 as _b64
    import time as _time_auth
    tokens = _read_plaud_tokens()
    if not tokens or not tokens.get("refresh_token"):
        raise HTTPException(status_code=400, detail="Kein Refresh-Token vorhanden. Bitte neu anmelden.")
    basic = _b64.b64encode(f"{_PLAUD_CLIENT_ID}:".encode()).decode()
    resp = _http.post(
        _PLAUD_REFRESH_URL,
        headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]},
        timeout=15,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Plaud Refresh fehlgeschlagen: {resp.status_code} {resp.text[:200]}")
    new_tokens = resp.json()
    # Preserve refresh_token if not returned
    if not new_tokens.get("refresh_token"):
        new_tokens["refresh_token"] = tokens["refresh_token"]
    # Add expires_at in ms if not present
    if "expires_in" in new_tokens and "expires_at" not in new_tokens:
        new_tokens["expires_at"] = int((_time_auth.time() + new_tokens["expires_in"]) * 1000)
    _write_plaud_tokens(new_tokens)
    return {"ok": True, "expires_at": new_tokens.get("expires_at")}


@app.post("/plaud/auth/upload-tokens")
def plaud_upload_tokens(
    payload: dict,
    _key: str = Security(verify_api_key),
):
    """Upload Plaud tokens from local plaud login. Expects the full tokens.json content."""
    import time as _time_auth
    required = {"access_token", "refresh_token", "token_type"}
    missing = required - set(payload.keys())
    if missing:
        raise HTTPException(status_code=400, detail=f"Fehlende Felder: {missing}")
    # Normalize expires_at to milliseconds if needed
    if "expires_at" not in payload and "expires_in" in payload:
        payload["expires_at"] = int((_time_auth.time() + payload["expires_in"]) * 1000)
    elif "expires_at" in payload and payload["expires_at"] < 1e12:
        # Looks like seconds, convert to ms
        payload["expires_at"] = int(payload["expires_at"] * 1000)
    _write_plaud_tokens(payload)
    return {"ok": True, "expires_at": payload.get("expires_at")}


@app.get("/plaud/auth/start")
def plaud_auth_start(_key: str = Security(verify_api_key)):
    raise HTTPException(status_code=410, detail="OAuth-Flow nicht verfügbar. Bitte Tokens lokal generieren und via /plaud/auth/upload-tokens hochladen.")


@app.get("/plaud/callback")
def plaud_oauth_callback(code: str = "", state: str = "", error: str = ""):
    from fastapi.responses import HTMLResponse
    return HTMLResponse("<h2>OAuth-Flow deaktiviert</h2><p>Bitte Tokens lokal via <code>plaud login</code> generieren und in mein-assistent hochladen.</p>", status_code=410)


# ---------------------------------------------------------------------------
# Plaud-Poller: state.db path (shared with plaud_poller.py)
# ---------------------------------------------------------------------------
_PLAUD_DB_PATH = os.getenv("PLAUD_DB_PATH", "/var/lib/plaud-poller/state.db")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    """Health-Check — kein Auth erforderlich."""
    return {
        "status": "ok",
        "service": "mein-assistent-api",
        "version": "1.2.0",
        "api_key_configured": bool(_API_SECRET_KEY),
    }


# ---------------------------------------------------------------------------
# Plaud — Cancel-Schutz (HBE-1203)
# ---------------------------------------------------------------------------
class PlaudCancelRequest(BaseModel):
    recording_id: str = Field(..., description="Plaud recording_id (32-Hex-Zeichen)")
    issue_identifier: Optional[str] = Field(
        None, description="Paperclip-Issue-Identifier (z.B. HBE-1188) — optional"
    )


@app.post("/api/plaud/cancel")
def plaud_cancel(
    req: PlaudCancelRequest,
    _key: str = Security(verify_api_key),
):
    """
    Markiert eine Plaud-Aufnahme in state.db als 'cancelled'.
    Nach diesem Aufruf überspringt der plaud_poller die Aufnahme dauerhaft,
    auch wenn sie noch im Plaud-Konto vorhanden ist.
    """
    db_path = Path(_PLAUD_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).isoformat()
    issue_ref = req.issue_identifier or "cancelled_by_api"

    try:
        with sqlite3.connect(str(db_path), timeout=10) as conn:
            # Ensure table + column exist (idempotent — mirrors plaud_poller._init_db)
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
            try:
                conn.execute(
                    "ALTER TABLE plaud_processed_recordings ADD COLUMN status TEXT"
                )
            except sqlite3.OperationalError:
                pass

            # INSERT new record OR update existing one — sets status='cancelled' in both cases
            conn.execute(
                """
                INSERT INTO plaud_processed_recordings
                    (recording_id, start_at, processed_at, issue_identifier, account_home, status)
                VALUES (?, '', ?, ?, '', 'cancelled')
                ON CONFLICT(recording_id) DO UPDATE SET
                    status           = 'cancelled',
                    issue_identifier = excluded.issue_identifier,
                    processed_at     = excluded.processed_at
                """,
                (req.recording_id, now, issue_ref),
            )
            conn.commit()
    except sqlite3.Error as exc:
        logger.error("[plaud_cancel] DB-Fehler: %s", exc)
        raise HTTPException(status_code=500, detail=f"DB-Fehler: {exc}")

    logger.info(
        "[plaud_cancel] recording_id=%s markiert als cancelled (issue=%s)",
        req.recording_id,
        issue_ref,
    )
    return {"status": "ok", "recording_id": req.recording_id, "cancelled_as": issue_ref}


# ── Plaud Recording Tracking (HBE-1527) ──────────────────────────────────────

# Reuse _PLAUD_DB_PATH defined above — same env var, same default path
PLAUD_STATE_DB = _PLAUD_DB_PATH
# protocols.db path — used to resolve review_link for plaud recordings (HBE-1603)
_PROTOCOLS_DB_PATH = str(_BASE_DIR / "data" / "protocols.db")
# protocols.status → tracking_status mapping (module-level constant, not per-request)
_PROTO_STATUS_MAP: dict = {
    "draft":     "review_ready",
    "in_review": "review_ready",
    "approved":  "review_ready",   # BackgroundTask kann scheitern; nur finalized = done
    "finalized": "done",
    "rejected":  "review_ready",
}


class PlaudRecordingStatus(BaseModel):
    recording_id: str
    start_at: Optional[str]
    processed_at: Optional[str]
    issue_identifier: Optional[str]
    poller_status: Optional[str]
    tracking_status: Optional[str]
    tracking_notes: Optional[str]
    recording_title: Optional[str] = None
    review_link: Optional[str] = None


class PlaudRecordingPatch(BaseModel):
    tracking_status: Optional[str] = None
    tracking_notes: Optional[str] = None


class PlaudRecordingsResponse(BaseModel):
    recordings: List[PlaudRecordingStatus]
    total: int


@app.get("/api/plaud/recordings", response_model=PlaudRecordingsResponse)
def list_plaud_recordings(
    tracking_status: Optional[str] = None,
    limit: int = 100,
    _key: str = Security(verify_api_key),
):
    """Liste aller Plaud-Aufnahmen mit aktuellem Tracking-Status (HBE-1527).

    Ergaenzt review_link live aus protocols.db wenn in state.db noch nicht gesetzt (HBE-1603).
    """
    import sqlite3 as _sqlite3
    if not Path(PLAUD_STATE_DB).exists():
        return PlaudRecordingsResponse(recordings=[], total=0)
    conn = _sqlite3.connect(PLAUD_STATE_DB)
    conn.row_factory = _sqlite3.Row
    try:
        query = """
            SELECT recording_id, start_at, processed_at, issue_identifier,
                   status as poller_status, tracking_status, tracking_notes, recording_title,
                   review_link
            FROM plaud_processed_recordings
        """
        params = []
        if tracking_status:
            query += " WHERE tracking_status = ?"
            params.append(tracking_status)
        query += " ORDER BY start_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        recordings_raw = [dict(r) for r in rows]
        # Count reflects active filter so callers get consistent total vs. recordings length
        count_query = "SELECT COUNT(*) FROM plaud_processed_recordings"
        count_params: list = []
        if tracking_status:
            count_query += " WHERE tracking_status = ?"
            count_params.append(tracking_status)
        total = conn.execute(count_query, count_params).fetchone()[0]
    finally:
        conn.close()

    # HBE-1603: Ergaenze review_link + tracking_status aus protocols.db (state.db ist :ro, kein Schreiben moeglich)
    # Enrichment immer durchfuehren (auch ohne tracking_status-Filter), damit Caller konsistente Werte
    # erhaelt. tracking_status-Filter betrifft SQL (inkl. COUNT), nicht das post-fetch Enrichment.
    recordings_needing_proto = [
        r["recording_id"] for r in recordings_raw
        if not r.get("review_link") or r.get("tracking_status") in (None, "new", "")
    ]
    if recordings_needing_proto and Path(_PROTOCOLS_DB_PATH).exists():
        try:
            pconn = _sqlite3.connect(f"file:{_PROTOCOLS_DB_PATH}?mode=ro", uri=True)
            try:
                pconn.row_factory = _sqlite3.Row  # inside try so pconn.close() always runs
                placeholders = ",".join("?" * len(recordings_needing_proto))
                proto_rows = pconn.execute(
                    f"SELECT recording_id, reviewer_token, status FROM protocols WHERE recording_id IN ({placeholders})",
                    recordings_needing_proto,
                ).fetchall()
                proto_map = {
                    row["recording_id"]: {"token": row["reviewer_token"], "status": row["status"]}
                    for row in proto_rows
                }
            finally:
                pconn.close()
            for r in recordings_raw:
                proto = proto_map.get(r["recording_id"])
                if proto:
                    # Fix: NULL-Guard fuer reviewer_token (kann NULL sein)
                    if not r.get("review_link") and proto.get("token"):
                        r["review_link"] = f"https://mein-assistent.herbertgruppe.com/review/{proto['token']}"
                    # Fix: approved → review_ready (BackgroundTask kann scheitern, nur finalized = done)
                    if r.get("tracking_status") in (None, "new", ""):
                        r["tracking_status"] = _PROTO_STATUS_MAP.get(proto.get("status", ""), "review_ready")
        except Exception as _exc:
            logger.warning("[plaud/recordings] protocols.db join fehlgeschlagen: %s", _exc)

    recordings = [PlaudRecordingStatus(**r) for r in recordings_raw]
    return PlaudRecordingsResponse(recordings=recordings, total=total)


@app.patch("/api/plaud/recordings/{recording_id}")
def patch_plaud_recording(
    recording_id: str,
    req: PlaudRecordingPatch,
    _key: str = Security(verify_api_key),
):
    """Tracking-Status oder Notiz einer Plaud-Aufnahme aktualisieren (HBE-1527)."""
    import sqlite3 as _sqlite3
    valid_statuses = {None, "new", "speakers_ok", "review_ready", "done", "abandoned"}
    if req.tracking_status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Ungültiger Status: {req.tracking_status}")
    if not Path(PLAUD_STATE_DB).exists():
        raise HTTPException(status_code=503, detail="Plaud state DB nicht verfügbar.")
    conn = _sqlite3.connect(PLAUD_STATE_DB)
    try:
        row = conn.execute(
            "SELECT recording_id FROM plaud_processed_recordings WHERE recording_id = ?",
            (recording_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Recording {recording_id} nicht gefunden.")
        updates = []
        params = []
        if req.tracking_status is not None:
            updates.append("tracking_status = ?")
            params.append(req.tracking_status)
        if req.tracking_notes is not None:
            updates.append("tracking_notes = ?")
            params.append(req.tracking_notes)
        if updates:
            params.append(recording_id)
            conn.execute(
                f"UPDATE plaud_processed_recordings SET {', '.join(updates)} WHERE recording_id = ?",
                params
            )
            conn.commit()
    finally:
        conn.close()
    return {"recording_id": recording_id, "updated": True}


@app.post("/api/plaud/recordings/{recording_id}/process")
def trigger_plaud_recording_process(
    recording_id: str,
    _key: str = Security(verify_api_key),
):
    """
    Verarbeitet eine spezifische Plaud-Aufnahme on-demand, unabhängig vom Alter (HBE-1527).
    Wird genutzt wenn Sprecher-Bestätigung für ältere Aufnahmen vorliegt.

    Side effects:
    - Erstellt ein Paperclip-Issue für den Mara-Agenten (PAPERCLIP_PROTOKOLL_AGENT_ID)
    - Setzt status = 'manual_trigger' und issue_identifier in plaud_processed_recordings

    Idempotent: Wenn issue_identifier bereits gesetzt ist, wird kein neues Issue erstellt.
    Rückgabe: {"status": "already_processed"} wenn bereits verarbeitet,
              {"status": "triggered"} wenn ein neues Issue erstellt wurde.
    """
    import sqlite3 as _sqlite3

    if not Path(PLAUD_STATE_DB).exists():
        raise HTTPException(status_code=503, detail="Plaud state DB nicht verfügbar.")

    # Single connection for all reads + write to avoid concurrency issues
    conn = _sqlite3.connect(PLAUD_STATE_DB)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        row = conn.execute(
            "SELECT recording_id, issue_identifier, status, recording_title, start_at FROM plaud_processed_recordings WHERE recording_id = ?",
            (recording_id,)
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail=f"Recording {recording_id} nicht in DB. Erst via GET /api/plaud/recordings prüfen.")

        existing_issue = row[1]
        if existing_issue:
            return {"recording_id": recording_id, "status": "already_processed", "issue_identifier": existing_issue}

        recording_title = row[3] or recording_id[:16]
        start_at = row[4] or "?"
    finally:
        conn.close()

    # Delegate to Mara via Paperclip issue with the recording_id
    try:
        mara_agent_id = os.getenv("PAPERCLIP_PROTOKOLL_AGENT_ID", "ed26f194-f0a9-4f70-a52d-6e39be9013e3")

        import requests as _rq_proc
        pc_url = os.getenv("PAPERCLIP_API_URL", "https://paperclip.herbertgruppe.com")
        pc_key = os.getenv("PAPERCLIP_API_KEY_MA", "")
        pc_company = os.getenv("PAPERCLIP_COMPANY_ID_MA", "9df4976b-9ac8-4e8f-a156-c06c7fa40cdc")

        issue_payload = {
            "title": f"Neue Plaud-Aufnahme (manuell): {recording_title}",
            "description": (
                f"Manuelle Verarbeitung angefordert via API.\n\n"
                f"Plaud Recording-ID: `{recording_id}`\n"
                f"Aufnahme-Zeitpunkt: {start_at}\n\n"
                f"Bitte Transkript abrufen (`plaud summary {recording_id}`), "
                f"Protokoll erstellen und Sven zur Überprüfung schicken."
            ),
            "assigneeAgentId": mara_agent_id,
            "priority": "medium",
        }

        resp = _rq_proc.post(
            f"{pc_url}/api/companies/{pc_company}/issues",
            headers={"Authorization": f"Bearer {pc_key}", "Content-Type": "application/json"},
            json=issue_payload,
            timeout=15,
        )

        if resp.status_code not in (200, 201):
            raise HTTPException(
                status_code=502,
                detail=f"Paperclip Issue konnte nicht erstellt werden: {resp.status_code} — {resp.text[:200]}"
            )

        new_issue = resp.json()
        issue_id = new_issue.get("identifier", "?")
        issue_uuid = new_issue.get("id", "")

        # Update DB: mark as triggered (single connection, WAL already set above)
        from datetime import datetime as _dt_proc, timezone as _tz_proc
        now = _dt_proc.now(_tz_proc.utc).isoformat()
        conn_upd = _sqlite3.connect(PLAUD_STATE_DB)
        conn_upd.execute("PRAGMA journal_mode=WAL")
        try:
            conn_upd.execute(
                "UPDATE plaud_processed_recordings SET issue_identifier = ?, processed_at = ?, status = 'manual_trigger' WHERE recording_id = ?",
                (issue_id, now, recording_id)
            )
            conn_upd.commit()
        finally:
            conn_upd.close()

        return {
            "recording_id": recording_id,
            "status": "triggered",
            "issue_identifier": issue_id,
            "issue_uuid": issue_uuid,
            "recording_title": recording_title,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Fehler beim Triggern: {exc}")


# ---------------------------------------------------------------------------
# Transkripte (Outlook-Subfolder)
# ---------------------------------------------------------------------------
# Konfigurierbar via .env, Default: "Transkripte" unter Posteingang
_TRANSCRIPTS_FOLDER_NAME = os.getenv("TRANSCRIPTS_FOLDER_NAME", "Transkripte")
_TRANSCRIPTS_PARENT = os.getenv("TRANSCRIPTS_PARENT_FOLDER", "inbox")
_TRANSCRIPTS_ARCHIVE_FOLDER = os.getenv("TRANSCRIPTS_ARCHIVE_FOLDER", "Transkripte erledigt")


def _get_outlook_tool():
    """Liefert eine konfigurierte OutlookGraphTool-Instanz mit Sven's Token."""
    from tools.outlook_graph_tool import OutlookGraphTool

    token_file = str(Path(__file__).resolve().parent / "auth" / "outlook_token.json")
    return OutlookGraphTool(token_file=token_file)


def _resolve_transcripts_folder_id(tool) -> Optional[str]:
    """Liefert die Folder-ID des Transkripte-Ordners (cached pro Aufruf)."""
    return tool.find_subfolder_id(
        name=_TRANSCRIPTS_FOLDER_NAME,
        parent=_TRANSCRIPTS_PARENT,
    )


class TranscriptAttachment(BaseModel):
    id: str
    name: str
    size: int
    content_type: str


class PendingTranscript(BaseModel):
    message_id: str
    subject: str
    received_at: str
    sender_name: Optional[str] = None
    sender_email: Optional[str] = None
    body_preview: str = ""
    body_text: str = ""
    has_attachments: bool = False
    attachments: List[TranscriptAttachment] = []
    meeting_time_hint: Optional[str] = Field(
        None,
        description=(
            "Aus Betreff/Body extrahierter Meeting-Startzeitstempel als naives "
            "ISO-Local (YYYY-MM-DDTHH:MM:SS). `null` wenn nicht eindeutig parsbar. "
            "Unterscheidet sich von `received_at` (Mail-Eingang, UTC)."
        ),
    )
    meeting_time_end_hint: Optional[str] = Field(
        None,
        description="Endzeitstempel des Meetings, falls eine Zeit-Range erkannt wurde.",
    )


class PendingTranscriptsResponse(BaseModel):
    folder: str
    folder_id: Optional[str] = None
    count: int
    transcripts: List[PendingTranscript] = []


class AttachmentResponse(BaseModel):
    success: bool
    name: Optional[str] = None
    content_type: Optional[str] = None
    size: Optional[int] = None
    content_base64: Optional[str] = None
    error: Optional[str] = None


class SimpleResult(BaseModel):
    success: bool
    message: str = ""


class SpeakerInfo(BaseModel):
    speaker_label: str
    probable_names: List[str] = []
    utterance_count: int
    total_words: int
    error: Optional[str] = None


# Telegram-Bridge models
class _TgChat(BaseModel):
    id: int
    type: str = ""


class _TgUser(BaseModel):
    id: int
    username: Optional[str] = None
    first_name: str = ""


class _TgMsg(BaseModel):
    message_id: int
    chat: _TgChat
    from_: Optional[_TgUser] = Field(None, alias="from")
    text: Optional[str] = None
    date: Optional[int] = None
    reply_to_message: Optional["_TgMsg"] = None

    model_config = {"populate_by_name": True}


_TgMsg.model_rebuild()


class _TgCallbackQuery(BaseModel):
    id: str
    from_: Optional[_TgUser] = Field(None, alias="from")
    message: Optional[_TgMsg] = None
    data: Optional[str] = None

    model_config = {"populate_by_name": True}


class TelegramUpdate(BaseModel):
    update_id: int
    message: Optional[_TgMsg] = None
    callback_query: Optional[_TgCallbackQuery] = None


class TelegramSendRequest(BaseModel):
    chat_id: str = Field(..., description="Telegram Chat-ID (Empfänger)")
    text: str = Field(..., description="Nachrichtentext")


class LenaTelegramSendRequest(BaseModel):
    chat_id: str = Field(..., description="Telegram Chat-ID (Empfänger)")
    text: str = Field(..., description="Nachrichtentext")
    parse_mode: str = Field("MarkdownV2", description="Telegram parse_mode (MarkdownV2 empfohlen)")
    issue_id: Optional[str] = Field(None, description="Paperclip Issue-ID für outbound_messages Tracking")
    comment_id: Optional[str] = Field(None, description="Paperclip Comment-ID für outbound_messages Tracking")
    reply_markup: Optional[dict] = Field(None, description="Telegram reply_markup (Inline-Keyboard, ReplyKeyboard, etc.)")


class LenaTelegramSendResponse(BaseModel):
    success: bool
    telegram_msg_id: Optional[int] = None


class SpeakerQuestionRequest(BaseModel):
    issue_id: str = Field(..., description="Paperclip Issue-ID (z.B. HBE-753)")
    meeting_name: str = Field(..., description="Termin-Titel für die Nachricht")
    unknown_speakers: List[str] = Field(..., description="Liste der unbekannten Speaker-Labels")


# Datums-Heuristiken für Plaud-Mail-Betreff (z. B. „04-17 Besprechung 09:30-11:00")
_RE_ISO_DATE = re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b")
_RE_DE_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})?")
_RE_MD_DATE = re.compile(r"(?<!\d)(\d{1,2})-(\d{1,2})(?!\d)")
_RE_TIME_RANGE = re.compile(r"\b(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})\b")
_RE_TIME_SINGLE = re.compile(r"\b(\d{1,2}):(\d{2})\b")


def _fallback_year_from_received(received_at: str) -> int:
    """Liefert das Jahr aus `received_at` (ISO/UTC), Fallback: aktuelles Jahr."""
    if received_at:
        try:
            return datetime.fromisoformat(received_at.replace("Z", "+00:00")).year
        except ValueError:
            pass
    return datetime.now().year


def _extract_meeting_time_hint(
    subject: str,
    body: str,
    received_at: str,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Heuristische Extraktion einer Meeting-Zeit aus dem Mail-Betreff (Fallback: Body).

    Erkennt Datums-Muster (`YYYY-MM-DD`, `DD.MM.[YYYY]`, `MM-DD`) und Zeit-Muster
    (`HH:MM`, `HH:MM-HH:MM`). Fehlt das Jahr, wird es aus `received_at` abgeleitet.

    Rückgabe: `(start_iso, end_iso)` als naive Local-ISO-Strings, jeweils `None`
    wenn nicht eindeutig parsbar. `end_iso` nur belegt, wenn eine Zeit-Range
    erkannt wurde.
    """
    fallback_year = _fallback_year_from_received(received_at)

    # Subject bevorzugen; nur in Body schauen, wenn Subject leer wäre.
    sources = [subject or "", body or ""]

    for text in sources:
        if not text:
            continue

        # 1) Datum bestimmen — ISO zuerst (verhindert MM-DD-Treffer in YYYY-MM-DD)
        year: Optional[int] = None
        month: Optional[int] = None
        day: Optional[int] = None

        m = _RE_ISO_DATE.search(text)
        if m:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            m = _RE_DE_DATE.search(text)
            if m:
                day, month = int(m.group(1)), int(m.group(2))
                year_part = m.group(3)
                if year_part:
                    year = int(year_part)
                    if year < 100:
                        year += 2000
                else:
                    year = fallback_year
            else:
                m = _RE_MD_DATE.search(text)
                if m:
                    month, day = int(m.group(1)), int(m.group(2))
                    year = fallback_year

        if year is None or month is None or day is None:
            continue
        try:
            date(year, month, day)
        except ValueError:
            continue

        # 2) Zeit bestimmen — Range bevorzugt
        sh = sm = eh = em = None
        m = _RE_TIME_RANGE.search(text)
        if m:
            sh, sm, eh, em = (int(m.group(i)) for i in (1, 2, 3, 4))
        else:
            m = _RE_TIME_SINGLE.search(text)
            if m:
                sh, sm = int(m.group(1)), int(m.group(2))

        if sh is None or sm is None:
            continue
        if not (0 <= sh < 24 and 0 <= sm < 60):
            continue

        start_iso = f"{year:04d}-{month:02d}-{day:02d}T{sh:02d}:{sm:02d}:00"
        end_iso: Optional[str] = None
        if eh is not None and em is not None and 0 <= eh < 24 and 0 <= em < 60:
            end_iso = f"{year:04d}-{month:02d}-{day:02d}T{eh:02d}:{em:02d}:00"
        return start_iso, end_iso

    return None, None


@app.get("/api/transcripts/pending", response_model=PendingTranscriptsResponse)
def list_pending_transcripts(
    max_results: int = 25,
    _key: str = Security(verify_api_key),
):
    """
    Liefert alle ungelesenen E-Mails im Outlook-Unterordner „Transkripte"
    (Default-Pfad: Posteingang/Transkripte) inkl. Anhang-Metadaten.

    Wird vom Cowork-Skill `meeting-protokoll` aufgerufen, um neue Plaud-Aufnahmen
    automatisch zu erkennen.
    """
    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(
            status_code=503,
            detail="Outlook nicht authentifiziert — Token fehlt oder abgelaufen.",
        )

    folder_id = _resolve_transcripts_folder_id(tool)
    if not folder_id:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Outlook-Unterordner '{_TRANSCRIPTS_FOLDER_NAME}' unter "
                f"'{_TRANSCRIPTS_PARENT}' nicht gefunden."
            ),
        )

    messages = tool.get_unread_in_folder(folder_id, max_results=max_results)

    transcripts: List[PendingTranscript] = []
    for msg in messages:
        sender = msg.get("from", {}).get("emailAddress", {})
        body_obj = msg.get("body", {}) or {}
        attachments_meta = [
            TranscriptAttachment(
                id=a.get("id", ""),
                name=a.get("name", ""),
                size=int(a.get("size") or 0),
                content_type=a.get("contentType", ""),
            )
            for a in (msg.get("attachments") or [])
        ]
        subject = msg.get("subject", "") or ""
        received_at = msg.get("receivedDateTime", "") or ""
        body_text = body_obj.get("content", "") or ""
        meeting_start, meeting_end = _extract_meeting_time_hint(
            subject, body_text, received_at
        )
        transcripts.append(
            PendingTranscript(
                message_id=msg.get("id", ""),
                subject=subject,
                received_at=received_at,
                sender_name=sender.get("name"),
                sender_email=sender.get("address"),
                body_preview=msg.get("bodyPreview", "") or "",
                body_text=body_text,
                has_attachments=bool(msg.get("hasAttachments")),
                attachments=attachments_meta,
                meeting_time_hint=meeting_start,
                meeting_time_end_hint=meeting_end,
            )
        )

    return PendingTranscriptsResponse(
        folder=f"{_TRANSCRIPTS_PARENT}/{_TRANSCRIPTS_FOLDER_NAME}",
        folder_id=folder_id,
        count=len(transcripts),
        transcripts=transcripts,
    )


@app.get(
    "/api/transcripts/{message_id}/attachment/{attachment_id}",
    response_model=AttachmentResponse,
)
def get_transcript_attachment(
    message_id: str,
    attachment_id: str,
    _key: str = Security(verify_api_key),
):
    """
    Liefert den Inhalt eines E-Mail-Anhangs als Base64.

    Sicherheitscheck: Der Anhang wird nur ausgeliefert, wenn die E-Mail
    tatsächlich im Transkripte-Ordner liegt. So kann der Endpunkt nicht
    missbraucht werden, um beliebige E-Mail-Anhänge auszulesen.
    """
    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    folder_id = _resolve_transcripts_folder_id(tool)
    if not folder_id:
        raise HTTPException(status_code=404, detail="Transkripte-Ordner nicht gefunden.")

    if not tool.is_message_in_folder(message_id, folder_id):
        raise HTTPException(
            status_code=403,
            detail="Mail liegt nicht im Transkripte-Ordner — Anhang wird nicht ausgeliefert.",
        )

    result = tool.download_attachment(message_id, attachment_id)
    if not result.get("success"):
        raise HTTPException(
            status_code=400, detail=result.get("error", "Anhang konnte nicht geladen werden.")
        )
    return AttachmentResponse(**result)


# ---------------------------------------------------------------------------
# Speaker-Extraktion (HBE-276)
# ---------------------------------------------------------------------------

_SPEAKER_LABEL_RE = re.compile(
    r"^(SPEAKER_\d+|[A-ZÜÖÄ][a-züöäß]+(?:\s+[A-ZÜÖÄ][a-züöäß]+)*)\s*:\s*(.+)",
    re.MULTILINE,
)
# Matches capitalized German/Latin name tokens (2+ chars after first capital)
_NAME_TOKEN_RE = re.compile(r"\b[A-ZÜÖÄ][a-züöäß]{2,}(?:\s+[A-ZÜÖÄ][a-züöäß]{2,})?\b")


def _parse_transcript_speakers(text: str) -> List[SpeakerInfo]:
    """Parse Plaud transcript text, aggregating utterances per speaker label.

    Handles two Plaud formats:
    - ``SPEAKER_N: utterance`` (unlabeled / auto-diarized)
    - ``Name [Name]: utterance`` (labeled / named by user)

    Returns speakers sorted by utterance_count desc. Returns empty list
    when no speaker labels are found (no 500).
    """
    utterances: dict = defaultdict(list)
    for m in _SPEAKER_LABEL_RE.finditer(text):
        utterances[m.group(1)].append(m.group(2).strip())

    result = []
    for label, texts in utterances.items():
        all_text = " ".join(texts)
        utterance_count = len(texts)
        total_words = sum(len(t.split()) for t in texts)

        probable_names: List[str] = []
        if label.startswith("SPEAKER_"):
            freq = Counter(_NAME_TOKEN_RE.findall(all_text))
            probable_names = [name for name, _ in freq.most_common(5)]

        result.append(SpeakerInfo(
            speaker_label=label,
            probable_names=probable_names,
            utterance_count=utterance_count,
            total_words=total_words,
        ))

    return sorted(result, key=lambda s: s.utterance_count, reverse=True)


@app.get("/api/transcripts/{message_id}/speakers", response_model=List[SpeakerInfo])
def get_transcript_speakers(
    message_id: str,
    _key: str = Security(verify_api_key),
):
    """
    Aggregiert Speaker-Labels und Utterance-Statistiken aus einem Plaud-Transkript.

    Lädt das .txt-Anhang der Mail (Fallback: body_text) und extrahiert pro
    Speaker-Label: utterance_count, total_words, probable_names.

    Sicherheitscheck: nur Mails im Transkripte-Ordner werden akzeptiert.
    Edge-case: kein Speaker-Label erkannt → leere Liste (kein 500).

    Unterstützte Formate:
    - ``SPEAKER_N: text`` (Plaud-Auto-Diarization ohne Namenszuweisung)
    - ``Name Name: text`` (Plaud mit gesetzten Sprecher-Namen)
    """
    import requests as _rq

    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    folder_id = _resolve_transcripts_folder_id(tool)
    if not folder_id:
        raise HTTPException(status_code=404, detail="Transkripte-Ordner nicht gefunden.")

    if not tool.is_message_in_folder(message_id, folder_id):
        raise HTTPException(
            status_code=403,
            detail="Mail liegt nicht im Transkripte-Ordner.",
        )

    headers = {
        "Authorization": f"Bearer {tool.access_token}",
        "Prefer": 'outlook.body-content-type="text"',
    }

    # Try .txt attachment first
    transcript_text = ""
    att_url = (
        f"https://graph.microsoft.com/v1.0/me/messages/{message_id}/attachments"
        "?$select=id,name,contentType,contentBytes"
    )
    att_resp = _rq.get(att_url, headers=headers, timeout=30)
    if att_resp.status_code == 401 and tool._refresh_access_token():
        headers["Authorization"] = f"Bearer {tool.access_token}"
        att_resp = _rq.get(att_url, headers=headers, timeout=30)

    if att_resp.status_code == 200:
        for att in att_resp.json().get("value", []):
            if att.get("name", "").lower().endswith(".txt") and att.get("contentBytes"):
                try:
                    transcript_text = base64.b64decode(att["contentBytes"]).decode(
                        "utf-8", errors="replace"
                    )
                    break
                except Exception:
                    pass

    # Fallback: message body text
    if not transcript_text:
        msg_url = (
            f"https://graph.microsoft.com/v1.0/me/messages/{message_id}?$select=body"
        )
        msg_resp = _rq.get(msg_url, headers=headers, timeout=30)
        if msg_resp.status_code == 401 and tool._refresh_access_token():
            headers["Authorization"] = f"Bearer {tool.access_token}"
            msg_resp = _rq.get(msg_url, headers=headers, timeout=30)
        if msg_resp.status_code == 200:
            transcript_text = (msg_resp.json().get("body") or {}).get("content", "") or ""

    return _parse_transcript_speakers(transcript_text)


# ---------------------------------------------------------------------------
# Kalender (für Termin-Zuordnung in Teil 1 des Skills)
# ---------------------------------------------------------------------------
class CalendarAttendee(BaseModel):
    """Pro-Person-Status eines Termin-Teilnehmers.

    Werte werden 1:1 von MS Graph durchgereicht. Siehe API_SETUP.md
    Abschnitt „Attendee-Semantik" für die Bedeutung der einzelnen Felder.
    """
    name: str = ""
    email: str = ""
    # MS Graph responseStatus.response: accepted | declined | tentative |
    # notResponded | none — zusätzlich "organizer" für den Termin-Organisator.
    response: str = "none"
    # MS Graph attendeeType: required | optional | resource
    type: str = "required"


class CalendarEvent(BaseModel):
    id: str
    title: str
    start: str
    end: str
    location: str = ""
    attendees: List[CalendarAttendee] = []
    # Backwards-Compat: einfache Namensliste für ältere Konsumenten.
    # Neue Konsumenten sollen `attendees[].name` verwenden.
    attendee_names: List[str] = []
    preview: str = ""
    is_all_day: bool = False


class CalendarEventsResponse(BaseModel):
    date: Optional[str] = None
    start: str
    end: str
    count: int
    events: List[CalendarEvent] = []


@app.get("/api/calendar/events", response_model=CalendarEventsResponse)
def get_calendar_events(
    date: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    include_all_day: bool = False,
    _user: str = Depends(get_authenticated_user),
):
    """
    Liefert Outlook-Kalender-Termine für ein Datum oder einen Zeitraum.

    Auth: Authentik-Session ODER X-API-Key ODER gültiger Reviewer-Token
    (Dual-Auth für den Web-Review-Editor).

    Query-Parameter (alternativ):
      - date=YYYY-MM-DD             → Termine an diesem Tag (00:00–24:00 lokal/UTC)
      - start=ISO & end=ISO         → benutzerdefinierter Zeitraum
      - include_all_day=true|false  → ganztägige Termine einbeziehen (Default: false)

    Wird vom Cowork-Skill `meeting-protokoll` aufgerufen, um aus dem
    Transkript-Datum den passenden Outlook-Termin zu finden.
    """
    from datetime import datetime, timedelta

    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    # Zeitraum bestimmen
    try:
        if date:
            day = datetime.strptime(date, "%Y-%m-%d")
            start_dt = day.replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = start_dt + timedelta(days=1)
            range_label: Optional[str] = date
        elif start and end:
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
            # Naive datetimes erzwingen (Graph erwartet ISO ohne Offset)
            if start_dt.tzinfo is not None:
                start_dt = start_dt.replace(tzinfo=None)
            if end_dt.tzinfo is not None:
                end_dt = end_dt.replace(tzinfo=None)
            range_label = None
        else:
            # Default: heute
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            start_dt = today
            end_dt = today + timedelta(days=1)
            range_label = today.strftime("%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Ungültiges Datum/Zeit-Format: {exc}",
        ) from exc

    if end_dt <= start_dt:
        raise HTTPException(
            status_code=400, detail="end muss nach start liegen."
        )

    raw_events = tool.get_events_for_date_range(start_dt, end_dt)

    events: List[CalendarEvent] = []
    for ev in raw_events:
        # Heuristik: ganztägige Termine erkennen (start.dateTime endet auf 00:00)
        # — aber Format aus get_events_for_date_range ist bereits gefiltert.
        # Wir lassen den Skill alle nicht-ganztägigen Termine sehen, ganztägige
        # werden anhand 'is_all_day' kenntlich gemacht.
        start_str = ev.get("start", "") or ""
        end_str = ev.get("end", "") or ""
        is_all_day = (
            start_str.endswith("T00:00:00.0000000")
            and end_str.endswith("T00:00:00.0000000")
        )
        if is_all_day and not include_all_day:
            continue
        raw_attendees = ev.get("attendees") or []
        attendees: List[CalendarAttendee] = []
        for a in raw_attendees:
            if isinstance(a, dict):
                if not (a.get("name") or a.get("email")):
                    continue
                attendees.append(
                    CalendarAttendee(
                        name=a.get("name") or "",
                        email=a.get("email") or "",
                        response=a.get("response") or "none",
                        type=a.get("type") or "required",
                    )
                )
            elif isinstance(a, str) and a:
                # Fallback falls das Tool noch das alte String-Format liefert.
                attendees.append(CalendarAttendee(name=a))
        attendee_names = ev.get("attendee_names") or [
            a.name for a in attendees if a.name
        ]
        events.append(
            CalendarEvent(
                id=ev.get("id", ""),
                title=ev.get("title", "") or "",
                start=start_str,
                end=end_str,
                location=ev.get("location", "") or "",
                attendees=attendees,
                attendee_names=[n for n in attendee_names if n],
                preview=ev.get("preview", "") or "",
                is_all_day=is_all_day,
            )
        )

    return CalendarEventsResponse(
        date=range_label,
        start=start_dt.isoformat(),
        end=end_dt.isoformat(),
        count=len(events),
        events=events,
    )


# ---------------------------------------------------------------------------
# Lena Kalender-CRUD (HBE-788)
# ---------------------------------------------------------------------------

class LenaCalendarAttendeeInput(BaseModel):
    email: str
    name: str = ""
    type: Literal["required", "optional", "resource"] = "required"


class LenaCreateEventRequest(BaseModel):
    subject: str
    start: str
    end: str
    timezone: str = "Europe/Berlin"
    location: str = ""
    body_html: str = ""
    attendees: List[LenaCalendarAttendeeInput] = []
    categories: List[str] = []
    is_online_meeting: bool = False


class LenaCreateEventResponse(BaseModel):
    event_id: str
    web_link: str
    ical_uid: str


class LenaUpdateEventRequest(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None
    timezone: Optional[str] = None
    subject: Optional[str] = None
    location: Optional[str] = None
    body_html: Optional[str] = None
    send_updates: bool = True


class LenaUpdateEventResponse(BaseModel):
    event_id: str
    subject: str
    start: str
    end: str


class LenaAttendeesRequest(BaseModel):
    add: List[LenaCalendarAttendeeInput] = []
    remove: List[str] = []


class LenaAttendeesResponse(BaseModel):
    event_id: str
    attendees_count: int


class LenaCalendarAttachmentResponse(BaseModel):
    attachment_id: str
    name: str
    size: int


class LenaFindSlotRequest(BaseModel):
    attendees: List[str]
    duration_minutes: int = 60
    earliest: str
    latest: str
    timezone: str = "Europe/Berlin"
    working_hours_only: bool = True


class LenaSlot(BaseModel):
    start: str
    end: str
    score: float = 0.0


class LenaFindSlotResponse(BaseModel):
    slots: List[LenaSlot]
    meeting_time_suggestions_result: str = ""


def _graph_headers(tool) -> dict:
    return {
        "Authorization": f"Bearer {tool.access_token}",
        "Content-Type": "application/json",
    }


def _graph_req(method: str, url: str, tool, **kwargs):
    """Graph API call with one 401 → token-refresh retry."""
    import requests as _rq
    headers = _graph_headers(tool)
    kwargs.setdefault("timeout", 30)
    resp = getattr(_rq, method)(url, headers=headers, **kwargs)
    if resp.status_code == 401 and tool._refresh_access_token():
        headers = _graph_headers(tool)
        resp = getattr(_rq, method)(url, headers=headers, **kwargs)
    return resp


@app.post("/api/lena/calendar/events", response_model=LenaCreateEventResponse)
def lena_create_calendar_event(
    req: LenaCreateEventRequest,
    _key: str = Security(verify_api_key),
):
    """
    Legt einen Outlook-Termin an (via Microsoft Graph).

    Approval-Gate: Lena sendet vor dem Aufruf eine Telegram-Vorschau und wartet
    auf Svens Freigabe (gemäß SKILL_MEETING_OPERATIONS.md).
    """
    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    graph_attendees = [
        {"emailAddress": {"address": a.email, "name": a.name}, "type": a.type}
        for a in req.attendees
    ]

    payload: dict = {
        "subject": req.subject,
        "start": {"dateTime": req.start, "timeZone": req.timezone},
        "end": {"dateTime": req.end, "timeZone": req.timezone},
        "attendees": graph_attendees,
        "isOnlineMeeting": req.is_online_meeting,
    }
    if req.location:
        payload["location"] = {"displayName": req.location}
    if req.body_html:
        payload["body"] = {"contentType": "HTML", "content": req.body_html}
    if req.categories:
        payload["categories"] = req.categories

    resp = _graph_req("post", "https://graph.microsoft.com/v1.0/me/events", tool, json=payload)
    if resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=502,
            detail=f"Graph API Fehler {resp.status_code}: {resp.text[:300]}",
        )

    data = resp.json()
    return LenaCreateEventResponse(
        event_id=data.get("id", ""),
        web_link=data.get("webLink", ""),
        ical_uid=data.get("iCalUId", ""),
    )


@app.patch("/api/lena/calendar/events/{event_id}", response_model=LenaUpdateEventResponse)
def lena_update_calendar_event(
    event_id: str,
    req: LenaUpdateEventRequest,
    _key: str = Security(verify_api_key),
):
    """
    Verschiebt oder aktualisiert einen Outlook-Termin. Nur übergebene Felder werden geändert.

    `send_updates=true` benachrichtigt alle Teilnehmer per Mail.
    """
    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    tz = req.timezone or "Europe/Berlin"
    payload: dict = {}
    if req.start is not None:
        payload["start"] = {"dateTime": req.start, "timeZone": tz}
    if req.end is not None:
        payload["end"] = {"dateTime": req.end, "timeZone": tz}
    if req.subject is not None:
        payload["subject"] = req.subject
    if req.location is not None:
        payload["location"] = {"displayName": req.location}
    if req.body_html is not None:
        payload["body"] = {"contentType": "HTML", "content": req.body_html}

    if not payload:
        raise HTTPException(status_code=400, detail="Keine Felder zum Aktualisieren angegeben.")

    url = f"https://graph.microsoft.com/v1.0/me/events/{event_id}"
    params = {"sendUpdates": "all" if req.send_updates else "none"}
    resp = _graph_req("patch", url, tool, json=payload, params=params)

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Graph API Fehler {resp.status_code}: {resp.text[:300]}",
        )

    data = resp.json()
    return LenaUpdateEventResponse(
        event_id=data.get("id", event_id),
        subject=data.get("subject", ""),
        start=(data.get("start") or {}).get("dateTime", ""),
        end=(data.get("end") or {}).get("dateTime", ""),
    )


@app.delete("/api/lena/calendar/events/{event_id}", response_model=SimpleResult)
def lena_delete_calendar_event(
    event_id: str,
    send_cancellations: bool = True,
    _key: str = Security(verify_api_key),
):
    """
    Löscht einen Outlook-Termin. Graph sendet bei DELETE automatisch Absagen an Teilnehmer.

    `send_cancellations=false` (query param) unterdrückt die Absagen.
    """
    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    if send_cancellations:
        # /cancel sends cancellation notices AND removes the event — do not also DELETE.
        cancel_url = f"https://graph.microsoft.com/v1.0/me/events/{event_id}/cancel"
        cancel_resp = _graph_req("post", cancel_url, tool, json={})
        if cancel_resp.status_code in (200, 202, 204):
            return SimpleResult(success=True, message=f"Termin {event_id[:16]}… abgesagt und gelöscht.")
        # Fall back to plain DELETE if cancel action fails (e.g. not organizer)

    url = f"https://graph.microsoft.com/v1.0/me/events/{event_id}"
    resp = _graph_req("delete", url, tool)

    if resp.status_code not in (200, 204):
        raise HTTPException(
            status_code=502,
            detail=f"Graph API Fehler {resp.status_code}: {resp.text[:300]}",
        )

    return SimpleResult(success=True, message=f"Termin {event_id[:16]}… gelöscht.")


@app.post(
    "/api/lena/calendar/events/{event_id}/attendees",
    response_model=LenaAttendeesResponse,
)
def lena_manage_attendees(
    event_id: str,
    req: LenaAttendeesRequest,
    _key: str = Security(verify_api_key),
):
    """
    Fügt Teilnehmer zu einem Outlook-Termin hinzu oder entfernt sie.

    Liest den aktuellen Termin, mergt die Änderungen und sendet einen PATCH zurück.
    """
    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    url = f"https://graph.microsoft.com/v1.0/me/events/{event_id}"

    get_resp = _graph_req("get", url, tool, params={"$select": "attendees,subject"})
    if get_resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Konnte Termin nicht lesen: HTTP {get_resp.status_code} — {get_resp.text[:200]}",
        )

    current: list = get_resp.json().get("attendees", [])
    remove_set = {e.lower() for e in req.remove}
    merged = [
        a for a in current
        if (a.get("emailAddress") or {}).get("address", "").lower() not in remove_set
    ]
    existing_emails = {
        (a.get("emailAddress") or {}).get("address", "").lower()
        for a in merged
    }
    for a in req.add:
        if a.email.lower() not in existing_emails:
            merged.append({
                "emailAddress": {"address": a.email, "name": a.name},
                "type": a.type,
            })
            existing_emails.add(a.email.lower())

    patch_resp = _graph_req("patch", url, tool, json={"attendees": merged})
    if patch_resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Teilnehmer-Update fehlgeschlagen: HTTP {patch_resp.status_code} — {patch_resp.text[:200]}",
        )

    return LenaAttendeesResponse(
        event_id=event_id,
        attendees_count=len(patch_resp.json().get("attendees", [])),
    )


@app.post(
    "/api/lena/calendar/events/{event_id}/attachments",
    response_model=LenaCalendarAttachmentResponse,
)
async def lena_attach_to_event(
    event_id: str,
    file: UploadFile = File(...),
    filename: Optional[str] = Form(None),
    content_type: Optional[str] = Form(None),
    _key: str = Security(verify_api_key),
):
    """
    Hängt eine Datei als Anhang an einen Outlook-Termin (direkte Graph-Upload, max 3 MB).

    Multipart-Form-Felder: `file` (binär, Pflicht), `filename` (optional), `content_type` (optional).
    """
    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    file_bytes = await file.read()
    if len(file_bytes) > 3 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail="Datei zu groß (max 3 MB für direkte Anhänge). Für größere Dateien UploadSession nutzen.",
        )

    used_filename = filename or file.filename or "attachment"
    used_ct = content_type or file.content_type or "application/octet-stream"
    file_b64 = base64.b64encode(file_bytes).decode("utf-8")

    payload = {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": used_filename,
        "contentType": used_ct,
        "contentBytes": file_b64,
    }

    url = f"https://graph.microsoft.com/v1.0/me/events/{event_id}/attachments"
    resp = _graph_req("post", url, tool, json=payload)
    if resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=502,
            detail=f"Graph API Fehler {resp.status_code}: {resp.text[:300]}",
        )

    data = resp.json()
    return LenaCalendarAttachmentResponse(
        attachment_id=data.get("id", ""),
        name=data.get("name", used_filename),
        size=data.get("size", len(file_bytes)),
    )


@app.post("/api/lena/calendar/find-free-slot", response_model=LenaFindSlotResponse)
def lena_find_free_slot(
    req: LenaFindSlotRequest,
    _key: str = Security(verify_api_key),
):
    """
    Sucht freie Zeitslots für mehrere Personen via Graph `findMeetingTimes`.

    Gibt bis zu 3 Vorschläge zurück, sortiert nach Confidence-Score (höchster zuerst).
    """
    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    attendees_payload = [
        {"type": "required", "emailAddress": {"address": email}}
        for email in req.attendees
    ]

    payload = {
        "attendees": attendees_payload,
        "timeConstraint": {
            "activityDomain": "work" if req.working_hours_only else "unrestricted",
            "timeslots": [
                {
                    "start": {"dateTime": req.earliest, "timeZone": req.timezone},
                    "end": {"dateTime": req.latest, "timeZone": req.timezone},
                }
            ],
        },
        "meetingDuration": f"PT{req.duration_minutes}M",
        "returnSuggestionReasons": True,
        "minimumAttendeePercentage": 100,
        "maxCandidates": 10,
    }

    resp = _graph_req("post", "https://graph.microsoft.com/v1.0/me/findMeetingTimes", tool, json=payload)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Graph findMeetingTimes Fehler {resp.status_code}: {resp.text[:300]}",
        )

    data = resp.json()
    suggestions = data.get("meetingTimeSuggestions", [])
    slots = []
    for s in suggestions[:3]:
        slot = s.get("meetingTimeSlot") or {}
        slots.append(LenaSlot(
            start=(slot.get("start") or {}).get("dateTime", ""),
            end=(slot.get("end") or {}).get("dateTime", ""),
            score=float(s.get("confidence", 0.0)),
        ))

    return LenaFindSlotResponse(
        slots=slots,
        meeting_time_suggestions_result=data.get("emptySuggestionsReason", ""),
    )


@app.post("/api/transcripts/{message_id}/mark-read", response_model=SimpleResult)
def mark_transcript_read(
    message_id: str,
    _key: str = Security(verify_api_key),
):
    """
    Markiert eine Transkript-Mail als gelesen.

    Sicherheitscheck wie bei get_transcript_attachment: nur Mails im
    Transkripte-Ordner werden akzeptiert.
    """
    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    folder_id = _resolve_transcripts_folder_id(tool)
    if not folder_id:
        raise HTTPException(status_code=404, detail="Transkripte-Ordner nicht gefunden.")

    if not tool.is_message_in_folder(message_id, folder_id):
        raise HTTPException(
            status_code=403,
            detail="Mail liegt nicht im Transkripte-Ordner.",
        )

    result = tool.mark_as_read(message_id)
    if not result.get("success"):
        raise HTTPException(
            status_code=400, detail=result.get("error", "Konnte nicht markiert werden.")
        )
    return SimpleResult(success=True, message="Mail als gelesen markiert.")


@app.post("/api/transcripts/{message_id}/archive", response_model=SimpleResult)
def archive_transcript(
    message_id: str,
    _key: str = Security(verify_api_key),
):
    """
    Verschiebt eine Transkript-Mail in den Archiv-Unterordner.

    Zielordner (konfigurierbar via TRANSCRIPTS_ARCHIVE_FOLDER): "Transkripte erledigt"
    Erwartet als Unterordner UNTER dem Transkripte-Ordner.

    Wird vom Meeting-Protokoll-Workflow am Ende von Teil 2 aufgerufen, statt nur
    die Mail als gelesen zu markieren — so kann sie nicht versehentlich erneut
    in den pending-Topf rutschen.

    Sicherheitscheck wie bei mark-read: nur Mails im Transkripte-Ordner werden
    akzeptiert.
    """
    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    folder_id = _resolve_transcripts_folder_id(tool)
    if not folder_id:
        raise HTTPException(status_code=404, detail="Transkripte-Ordner nicht gefunden.")

    if not tool.is_message_in_folder(message_id, folder_id):
        raise HTTPException(
            status_code=403,
            detail="Mail liegt nicht im Transkripte-Ordner.",
        )

    # Gezielte Suche: Archiv-Ordner als Subfolder DES Transkripte-Ordners (deterministisch,
    # robust gegen Schwester-Ordner mit ähnlichem Namen)
    archive_folder_id = tool.find_subfolder_id(
        name=_TRANSCRIPTS_ARCHIVE_FOLDER,
        parent=folder_id,
    )
    if not archive_folder_id:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Archiv-Unterordner '{_TRANSCRIPTS_ARCHIVE_FOLDER}' unter "
                f"'{_TRANSCRIPTS_FOLDER_NAME}' nicht gefunden. "
                f"Bitte in Outlook anlegen."
            ),
        )

    # Direkter Graph-API-Move (umgeht move_to_folder's flache Heuristik)
    import requests as _rq
    move_url = f"https://graph.microsoft.com/v1.0/me/messages/{message_id}/move"
    headers = {
        "Authorization": f"Bearer {tool.access_token}",
        "Content-Type": "application/json",
    }
    move_resp = _rq.post(
        move_url, headers=headers, json={"destinationId": archive_folder_id}, timeout=30
    )
    if move_resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=400,
            detail=f"Graph-API move fehlgeschlagen: HTTP {move_resp.status_code} — {move_resp.text[:200]}",
        )

    return SimpleResult(
        success=True,
        message=f"Mail in '{_TRANSCRIPTS_FOLDER_NAME}/{_TRANSCRIPTS_ARCHIVE_FOLDER}' verschoben.",
    )


@app.post("/api/process-reviewed-protocol", response_model=ProcessProtocolResponse)
def process_reviewed_protocol(
    req: ProcessProtocolRequest,
    _key: str = Security(verify_api_key),
):
    """
    Verarbeitet ein überarbeitetes Protokoll und schreibt es nach Outlook zurück.

    Schritte:
      1. PDF generieren (oder pdf_base64 verwenden)
      2. PDF als Anhang an Outlook-Termin (wenn event_id gesetzt)
      3. Kategorie „Protokoll" am Outlook-Termin
      4. Betreff-Prefix „📄 " am Outlook-Termin
    """
    # Lazy import — vermeidet harte Kopplung zur Tools-Suite beim Modul-Load
    from tools.outlook_graph_tool import OutlookGraphTool

    errors: List[str] = []
    pdf_generated = False
    outlook_attachment: Optional[bool] = None
    outlook_category: Optional[bool] = None
    outlook_subject: Optional[bool] = None

    safe_name = _safe_filename(req.meeting_name)

    # Obsidian-Syntax (Wikilinks, Callouts) für PDF/Outlook strippen.
    # Die Vault-Version bleibt unverändert — hier nur die Außenwelt-Version.
    clean_md = _strip_obsidian_syntax(req.markdown)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        pdf_path = tmp / f"Protokoll_{safe_name}.pdf"

        # --- 1. PDF bereitstellen --------------------------------------
        if req.pdf_base64:
            try:
                pdf_path.write_bytes(base64.b64decode(req.pdf_base64))
                pdf_generated = pdf_path.exists() and pdf_path.stat().st_size > 0
                if not pdf_generated:
                    errors.append("Übergebenes PDF ist leer oder ungültig")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"PDF-Dekodierung fehlgeschlagen: {exc}")
        else:
            pdf_generated = _markdown_to_pdf_standalone(clean_md, pdf_path)
            if not pdf_generated:
                errors.append("PDF-Generierung aus Markdown fehlgeschlagen")

        # --- 2. Outlook-Operationen ------------------------------------
        if req.event_id:
            token_file = str(Path(__file__).resolve().parent / "auth" / "outlook_token.json")
            tool = OutlookGraphTool(token_file=token_file)

            if not tool.is_authenticated():
                errors.append(
                    "Outlook nicht authentifiziert — Token fehlt oder Refresh schlug fehl. "
                    "Bitte in Mein-Assistent neu einloggen."
                )
            else:
                # 2a. PDF anhängen
                if pdf_generated and pdf_path.exists():
                    r = tool.add_attachment_to_event(
                        event_id=req.event_id,
                        file_path=str(pdf_path),
                        file_name=f"Protokoll_{safe_name}.pdf",
                    )
                    outlook_attachment = bool(r.get("success", False))
                    if not outlook_attachment:
                        errors.append(f"PDF-Anhang: {r.get('error', 'Unbekannter Fehler')}")

                # 2b. Kategorie „Protokoll"
                r = tool.add_category_to_event(
                    event_id=req.event_id, category="Protokoll"
                )
                outlook_category = bool(r.get("success", False))
                if not outlook_category:
                    errors.append(f"Kategorie: {r.get('error', 'Unbekannter Fehler')}")

                # 2c. Betreff-Prefix „📄 "
                r = tool.add_protocol_subject_prefix(event_id=req.event_id)
                outlook_subject = bool(r.get("success", False))
                if not outlook_subject:
                    errors.append(f"Betreff-Prefix: {r.get('error', 'Unbekannter Fehler')}")

    # Erfolg = PDF da UND (kein event_id ODER mind. Kategorie + Betreff geklappt)
    overall_success = pdf_generated and (
        not req.event_id or (outlook_category and outlook_subject)
    )

    return ProcessProtocolResponse(
        success=overall_success,
        pdf_generated=pdf_generated,
        outlook_attachment=outlook_attachment,
        outlook_category=outlook_category,
        outlook_subject_prefix=outlook_subject,
        errors=errors,
        message="OK" if overall_success else f"{len(errors)} Fehler aufgetreten",
    )


# ===========================================================================
# Protokoll-Review-Workflow (Web-Editor)
# ===========================================================================
# Mara (Paperclip-AI-Agent) legt Drafts via POST /api/protocols/draft ab.
# Reviewer öffnen /review/{token}, wählen Outlook-Termin + (optional) Asana-
# Board/-Abschnitt, korrigieren das Markdown und geben frei. Die Finalisierung
# (PDF → Outlook; optional Asana-Protokoll-Task inkl. Subtasks) läuft als
# FastAPI-BackgroundTask.
#
# --- Nginx-Konfiguration (im Repo portal-herbertgruppe einpflegen, NICHT hier):
#
#   # NEU: Review-Editor und API-Endpoints über Authentik zugänglich
#   location /review/ {
#       proxy_pass http://127.0.0.1:8502;
#       proxy_set_header Host $host;
#       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
#       proxy_set_header X-Forwarded-Proto $scheme;
#       # Authentik-Auth AKTIV (Standard) — Reviewer muss Herbert-SSO-Login haben
#   }
#
#   location /api/asana/ {
#       proxy_pass http://127.0.0.1:8502;
#       proxy_set_header Host $host;
#       # Auth: Authentik-Session ODER X-API-Key (dual-auth in api.py)
#   }
#
#   location /static/ {
#       proxy_pass http://127.0.0.1:8502;
#   }
# ===========================================================================


# ---------------------------------------------------------------------------
# Models — Protokoll-Review
# ---------------------------------------------------------------------------
class ProtocolDraftRequest(BaseModel):
    markdown: str = Field(..., description="Protokoll-Markdown von Mara")
    meeting_name: str
    meeting_datetime: str = Field(..., description="ISO-8601, Hint für Calendar-Picker")
    source: str = Field(
        ...,
        description="'plaud-poller','email','manual','mara-generated','audio-transcribed'",
    )
    teilnehmer: List[str] = []
    reviewer_emails: List[str] = []
    ablageort: Optional[str] = None
    recording_id: Optional[str] = None
    event_id: Optional[str] = None
    asana_board_gid: Optional[str] = None
    asana_section_gid: Optional[str] = None
    audio_ref: Optional[str] = None


class ProtocolDraftResponse(BaseModel):
    draft_id: str
    reviewer_url: str
    expires_at: str


class ProtocolPatchRequest(BaseModel):
    markdown: str


class ProtocolApproveRequest(BaseModel):
    event_id: str = Field(..., description="Outlook-Event-ID (Pflicht)")
    create_asana_task: bool = Field(
        True, description="Checkbox: Asana-Protokoll-Task + Subtasks anlegen"
    )
    asana_board_gid: Optional[str] = None
    asana_section_gid: Optional[str] = None


class ProtocolRejectRequest(BaseModel):
    reason: str


# ---------------------------------------------------------------------------
# Asana-Hilfen: Agent-Singleton + 15-Minuten-Cache für Dropdown-Daten
# ---------------------------------------------------------------------------
_ASANA_CACHE_TTL_SECONDS = 15 * 60
_asana_cache: dict = {}
_asana_agent = None


def _get_asana_agent():
    """Lazy AsanaAgent-Singleton (Init macht einen Workspace-API-Call)."""
    global _asana_agent
    if _asana_agent is None:
        from agents.asana_agent import AsanaAgent

        _asana_agent = AsanaAgent()
    return _asana_agent


def _asana_cached(cache_key: str, loader):
    """15-Minuten-TTL-Cache für Asana-Dropdown-Daten (Rate-Limit schonen)."""
    import time

    now = time.time()
    hit = _asana_cache.get(cache_key)
    if hit and now - hit[0] < _ASANA_CACHE_TTL_SECONDS:
        return hit[1]
    value = loader()
    _asana_cache[cache_key] = (now, value)
    return value


def _get_protocol_llm():
    """LLM für die Task-Extraktion (gleiches Muster wie agents/task_agent.py)."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            api_key=api_key,
            model=os.getenv("TASK_MODEL", "claude-3-5-sonnet-latest"),
            temperature=0,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[api] LLM-Init für Task-Extraktion fehlgeschlagen: {exc}")
        return None


def _parse_due_on(value) -> Optional[str]:
    """Normalisiert Fälligkeitsdaten aus der LLM-Extraktion zu YYYY-MM-DD."""
    from datetime import datetime as _dt

    if not value or value == "[?]":
        return None
    try:
        if isinstance(value, str) and "." in value:
            return _dt.strptime(value, "%d.%m.%Y").strftime("%Y-%m-%d")
        if isinstance(value, str) and "-" in value:
            return value
    except Exception:  # noqa: BLE001
        return None
    return None


# ---------------------------------------------------------------------------
# Hintergrund-Task: Finalisierung nach Freigabe
# ---------------------------------------------------------------------------
def _create_asana_protocol_task(protocol: dict) -> tuple:
    """
    Legt den Asana-Protokoll-Task an (Muster aus pages/meeting_manager.py):
      1. Task „📄 Protokoll {Datum} - {Meeting}" in Board/Section, Notes = Markdown
      2. PDF generieren und an den Task anhängen (Fehler nicht fatal)
      3. Aufgaben per LLM extrahieren und als Subtasks anlegen (Fehler nicht fatal)

    Returns:
        (task_gid, task_url)

    Raises:
        RuntimeError wenn der Protokoll-Task selbst nicht angelegt werden kann.
    """
    agent = _get_asana_agent()
    if not agent.is_connected():
        raise RuntimeError("Asana nicht konfiguriert (Token fehlt oder ungültig)")

    # Strip Obsidian syntax (Wikilinks, Embeds) — Vault-Version bleibt unverändert
    markdown_text = _strip_obsidian_syntax(protocol["current_markdown"])

    # Asana-Notes-Limit ca. 65k Zeichen; bei Überschreitung kürzen mit Hinweis
    _ASANA_NOTES_LIMIT = 65_000
    if len(markdown_text) > _ASANA_NOTES_LIMIT:
        truncation_note = "\n\n---\n[…] vollständiges Protokoll als PDF am Outlook-Termin"
        markdown_text = markdown_text[: _ASANA_NOTES_LIMIT - len(truncation_note)] + truncation_note

    date_str = (protocol.get("meeting_datetime") or "")[:10]
    task_title = f"📄 Protokoll {date_str} - {protocol['meeting_name']}"

    result = agent.create_task(
        name=task_title,
        notes=markdown_text,
        project_gid=protocol["asana_board_gid"],
        assignee_gid=None,
        section_gid=protocol["asana_section_gid"],
    )
    if not result.get("success"):
        raise RuntimeError(
            f"Asana-Protokoll-Task fehlgeschlagen: {result.get('error', 'Unbekannt')}"
        )
    task_gid = result.get("task_gid")
    task_url = result.get("permalink_url")

    # --- PDF anhängen (nicht fatal) ---
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / f"Protokoll_{_safe_filename(protocol['meeting_name'])}.pdf"
            if _markdown_to_pdf_standalone(markdown_text, pdf_path):
                agent.attach_file_to_task(
                    task_gid=task_gid,
                    file_path=str(pdf_path),
                    file_name=pdf_path.name,
                )
    except Exception as exc:  # noqa: BLE001
        print(f"[api] PDF-Anhang an Asana-Task fehlgeschlagen: {exc}")

    # --- Subtasks aus „Weitere Schritte" (nicht fatal) ---
    try:
        llm = _get_protocol_llm()
        if llm is None:
            print("[api] ⚠️ Kein LLM verfügbar — Subtask-Extraktion übersprungen")
        else:
            from utils.protocol import extract_tasks_from_protocol_text

            tasks = extract_tasks_from_protocol_text(markdown_text, llm)
            for task in tasks:
                title = task.get("title", "")
                if not title:
                    continue
                origin_lines = [f"📎 Ursprung: {task_title}"]
                if task.get("top"):
                    origin_lines.append(f"📋 Tagesordnungspunkt: {task['top']}")
                assignee_name = task.get("assignee")
                if assignee_name and assignee_name != "[?]":
                    origin_lines.append(f"👤 Geplanter Verantwortlicher: {assignee_name}")
                origin_block = "\n".join(origin_lines)
                description = task.get("description", "")
                notes = f"{description}\n\n---\n{origin_block}" if description else origin_block

                sub = agent.create_subtask(
                    parent_task_gid=task_gid,
                    name=title,
                    notes=notes,
                    due_on=_parse_due_on(task.get("due_date")),
                    assignee_gid=None,
                )
                if not sub.get("success"):
                    print(f"[api] ⚠️ Subtask fehlgeschlagen: {title}: {sub.get('error')}")
    except Exception as exc:  # noqa: BLE001
        print(f"[api] Subtask-Extraktion fehlgeschlagen: {exc}")

    return task_gid, task_url


def _finalize_protocol(draft_id: str) -> None:
    """
    Hintergrund-Job nach Freigabe:
      1. PDF + Outlook (Anhang, Kategorie, Betreff-Prefix) via bestehender
         process_reviewed_protocol-Logik (direkter Aufruf, kein HTTP)
      2. Optional Asana-Protokoll-Task + Subtasks (create_asana_task-Checkbox)
      3. Erfolg → status 'finalized'; Fehler → 'in_review' + finalization_error
    """
    protocol = _protocols_db.get_by_id(draft_id)
    if not protocol or protocol["status"] != "approved":
        return

    try:
        result = process_reviewed_protocol(
            ProcessProtocolRequest(
                markdown=protocol["current_markdown"],
                meeting_name=protocol["meeting_name"],
                event_id=protocol["event_id"],
                asana_gid=protocol["asana_board_gid"],
            ),
            _key="internal",
        )
        if not result.success:
            raise RuntimeError(
                "PDF/Outlook fehlgeschlagen: " + "; ".join(result.errors)
            )

        task_gid = None
        task_url = None
        if protocol.get("create_asana_task"):
            task_gid, task_url = _create_asana_protocol_task(protocol)

        _protocols_db.set_finalized(draft_id, task_gid, task_url)

    except Exception as exc:  # noqa: BLE001
        _protocols_db.set_finalization_error(draft_id, str(exc))


# ---------------------------------------------------------------------------
# Endpoints — Protokoll-Review
# ---------------------------------------------------------------------------
@app.post(
    "/api/protocols/draft", response_model=ProtocolDraftResponse, status_code=201
)
def create_protocol_draft(
    req: ProtocolDraftRequest,
    _key: str = Security(verify_api_key),
):
    """Mara legt einen Protokoll-Draft ab und erhält die Reviewer-URL."""
    result = _protocols_db.create_draft(
        markdown=req.markdown,
        meeting_name=req.meeting_name,
        meeting_datetime=req.meeting_datetime,
        source=req.source,
        teilnehmer=req.teilnehmer,
        reviewer_emails=req.reviewer_emails,
        ablageort=req.ablageort,
        recording_id=req.recording_id,
        event_id=req.event_id,
        asana_board_gid=req.asana_board_gid,
        asana_section_gid=req.asana_section_gid,
        audio_ref=req.audio_ref,
    )
    return ProtocolDraftResponse(
        draft_id=result["id"],
        reviewer_url=f"{_PUBLIC_BASE_URL}/review/{result['reviewer_token']}",
        expires_at=result["expires_at"],
    )


# WICHTIG: /finalized muss VOR /{draft_id} deklariert sein (Routing-Reihenfolge)
@app.get("/api/protocols/finalized")
def list_finalized_protocols(
    since: Optional[str] = None,
    limit: int = 50,
    _key: str = Security(verify_api_key),
):
    """Vault-Sync für Claudian: alle finalisierten Protokolle seit `since`."""
    protocols = _protocols_db.list_finalized_since(since=since, limit=limit)
    return {
        "protocols": [
            {
                "id": p["id"],
                "meeting_name": p["meeting_name"],
                "meeting_datetime": p["meeting_datetime"],
                "ablageort": p["ablageort"],
                "markdown": p["current_markdown"],
                "frontmatter": {
                    "asana_protokoll_task_gid": p["asana_task_gid"],
                    "asana_protokoll_task_url": p["asana_task_url"],
                    "teilnehmer": p["teilnehmer"],
                },
                "finalized_at": p["finalized_at"],
            }
            for p in protocols
        ]
    }


def _get_protocol_for_token(
    draft_id: str, token: Optional[str], allow_api_key: Optional[str] = None
) -> dict:
    """
    Lädt ein Protokoll und prüft den Reviewer-Token.
      - 404: draft_id unbekannt
      - 401: kein Token/Key
      - 403: Token gehört nicht zu diesem Draft / Key ungültig
      - 410: Token abgelaufen
    """
    protocol = _protocols_db.get_by_id(draft_id)
    if not protocol:
        raise HTTPException(status_code=404, detail="Protokoll nicht gefunden")

    if allow_api_key and _API_SECRET_KEY and hmac.compare_digest(allow_api_key, _API_SECRET_KEY):
        return protocol

    if not token:
        raise HTTPException(status_code=401, detail="Token fehlt (?token=...)")
    if not hmac.compare_digest(token, protocol["reviewer_token"]):
        raise HTTPException(status_code=403, detail="Ungültiger Token")
    if ProtocolsDB.is_expired(protocol):
        raise HTTPException(status_code=410, detail="Token abgelaufen")
    return protocol


@app.get("/api/protocols/{draft_id}")
def get_protocol_draft(
    draft_id: str,
    token: Optional[str] = Query(None),
    api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """Aktueller Draft-Stand. Auth: Reviewer-Token ODER X-API-Key."""
    protocol = _get_protocol_for_token(draft_id, token, allow_api_key=api_key)
    protocol.pop("reviewer_token", None)
    return protocol


@app.patch("/api/protocols/{draft_id}")
def patch_protocol_markdown(
    draft_id: str,
    req: ProtocolPatchRequest,
    token: Optional[str] = Query(None),
    x_authentik_username: Optional[str] = Header(None),
):
    """Speichert den aktuellen Editor-Stand."""
    _get_protocol_for_token(draft_id, token)
    _protocols_db.update_markdown(
        draft_id, req.markdown, modified_by=x_authentik_username or "reviewer"
    )
    return {"status": "saved"}


@app.post("/api/protocols/{draft_id}/approve", status_code=202)
def approve_protocol(
    draft_id: str,
    req: ProtocolApproveRequest,
    background_tasks: BackgroundTasks,
    token: Optional[str] = Query(None),
    x_authentik_username: Optional[str] = Header(None),
):
    """
    Freigabe: speichert Termin-/Asana-Auswahl, gibt sofort 202 zurück und
    startet die Finalisierung als Hintergrund-Task.
    """
    protocol = _get_protocol_for_token(draft_id, token)

    if protocol["status"] in ("approved", "finalized"):
        raise HTTPException(
            status_code=409,
            detail=f"Protokoll ist bereits {protocol['status']}.",
        )

    if req.create_asana_task and not (req.asana_board_gid and req.asana_section_gid):
        raise HTTPException(
            status_code=422,
            detail=(
                "asana_board_gid und asana_section_gid sind Pflicht, "
                "wenn create_asana_task=true."
            ),
        )

    _protocols_db.set_approved(
        draft_id,
        event_id=req.event_id,
        asana_board_gid=req.asana_board_gid,
        asana_section_gid=req.asana_section_gid,
        create_asana_task=req.create_asana_task,
        approved_by=x_authentik_username or "reviewer",
    )
    background_tasks.add_task(_finalize_protocol, draft_id)

    return {
        "status": "approved",
        "message": "Protokoll wird im Hintergrund fertiggestellt.",
    }


@app.post("/api/protocols/{draft_id}/reject")
def reject_protocol(
    draft_id: str,
    req: ProtocolRejectRequest,
    token: Optional[str] = Query(None),
    x_authentik_username: Optional[str] = Header(None),
):
    """Ablehnen: Status 'rejected' + Grund speichern."""
    _get_protocol_for_token(draft_id, token)
    _protocols_db.set_rejected(
        draft_id, req.reason, rejected_by=x_authentik_username or "reviewer"
    )
    return {"status": "rejected"}


# ---------------------------------------------------------------------------
# Endpoints — Asana-Dropdown-Daten (15 Minuten gecacht)
# ---------------------------------------------------------------------------
@app.get("/api/asana/boards")
def get_asana_boards(_user: str = Depends(get_authenticated_user)):
    """Alle Asana-Projekte des Workspace. Auth: Authentik/X-API-Key/Token."""

    def load():
        agent = _get_asana_agent()
        if not agent.is_connected():
            raise HTTPException(status_code=503, detail="Asana nicht konfiguriert.")
        return [
            {"gid": p["gid"], "name": p["name"]} for p in agent.list_projects()
        ]

    return _asana_cached("boards", load)


@app.get("/api/asana/boards/{board_gid}/sections")
def get_asana_board_sections(
    board_gid: str, _user: str = Depends(get_authenticated_user)
):
    """Sections eines Asana-Boards. Auth: Authentik/X-API-Key/Token."""

    def load():
        agent = _get_asana_agent()
        if not agent.is_connected():
            raise HTTPException(status_code=503, detail="Asana nicht konfiguriert.")
        return [
            {"gid": s["gid"], "name": s["name"]}
            for s in agent.get_project_sections(board_gid)
        ]

    return _asana_cached(f"sections:{board_gid}", load)


# ---------------------------------------------------------------------------
# Endpoint — Review-Editor (HTML)
# ---------------------------------------------------------------------------
@app.get("/review/{token}", response_class=HTMLResponse)
def review_page(
    request: Request,
    token: str,
    x_authentik_username: Optional[str] = Header(None),
):
    """
    Editor-Seite für Reviewer. Auth: Authentik-SSO (erzwungen durch nginx;
    der Reviewer-Token im Pfad identifiziert das Protokoll).
    """
    protocol = _protocols_db.get_by_token(token)
    if not protocol:
        return templates.TemplateResponse(
            request, "review_error.html", {"reason": "unknown"}, status_code=404
        )
    if ProtocolsDB.is_expired(protocol):
        return templates.TemplateResponse(
            request, "review_error.html", {"reason": "expired"}, status_code=410
        )

    if protocol["status"] == "draft":
        _protocols_db.set_status(protocol["id"], "in_review")
        protocol["status"] = "in_review"

    meeting_dt_fmt = protocol["meeting_datetime"]
    try:
        from datetime import datetime as _dt

        meeting_dt_fmt = _dt.fromisoformat(
            protocol["meeting_datetime"].replace("Z", "+00:00")
        ).strftime("%d.%m.%Y %H:%M")
    except (ValueError, AttributeError):
        pass

    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "draft_id": protocol["id"],
            "token": token,
            "meeting_name": protocol["meeting_name"],
            "meeting_datetime": protocol["meeting_datetime"],
            "meeting_datetime_fmt": meeting_dt_fmt,
            "teilnehmer": protocol["teilnehmer"],
            "teilnehmer_str": ", ".join(protocol["teilnehmer"]),
            "current_markdown": protocol["current_markdown"],
            "status": protocol["status"],
            "create_asana_task": protocol["create_asana_task"],
            "finalization_error": protocol["finalization_error"],
            "reviewer_name": x_authentik_username or "",
        },
    )


@app.get("/review/{token}/success", response_class=HTMLResponse)
def review_success_page(request: Request, token: str):
    """Erfolgsseite nach Freigabe (Redirect-Ziel von review.js)."""
    protocol = _protocols_db.get_by_token(token)
    if not protocol:
        return templates.TemplateResponse(
            request, "review_error.html", {"reason": "unknown"}, status_code=404
        )
    return templates.TemplateResponse(
        request,
        "review_success.html",
        {
            "meeting_name": protocol["meeting_name"],
            "create_asana_task": protocol["create_asana_task"],
        },
    )


# ---------------------------------------------------------------------------
# Lena – E-Mail-Endpoints (PA Sven, Phase 2)
# ---------------------------------------------------------------------------

class LenaEmailAddress(BaseModel):
    name: str = ""
    email: str


class LenaInboxMessage(BaseModel):
    message_id: str
    subject: str
    from_name: str = ""
    from_email: str = ""
    received_at: str
    is_read: bool = False
    importance: str = "normal"
    body_preview: str = ""
    has_attachments: bool = False
    categories: List[str] = []


class LenaInboxResponse(BaseModel):
    count: int
    messages: List[LenaInboxMessage]


class LenaDraftRequest(BaseModel):
    to: List[LenaEmailAddress]
    cc: List[LenaEmailAddress] = []
    subject: str
    body_html: str = ""
    body_text: str = ""
    reply_to_message_id: Optional[str] = None


class LenaDraftResponse(BaseModel):
    draft_id: str
    subject: str
    created_at: str


class LenaSendMailRequest(BaseModel):
    to: List[LenaEmailAddress]
    subject: str
    body_text: str
    body_html: Optional[str] = None
    reply_to: Optional[str] = None


class LenaSendMailResponse(BaseModel):
    success: bool
    message_id: str = ""


_SMTP_HOST = "smtps.udag.de"
_SMTP_PORT = 587
_SMTP_USER = os.getenv("SMTP_USER", "").strip()
_SMTP_FROM = os.getenv("SMTP_FROM", "").strip()


@app.get("/api/lena/mail/inbox", response_model=LenaInboxResponse)
def lena_mail_inbox(
    limit: int = 20,
    folder: str = "inbox",
    unread_only: bool = False,
    _key: str = Security(verify_api_key),
):
    """
    Liest die letzten N Mails aus dem Posteingang (Svens Outlook via Graph API).

    Query-Parameter:
      - limit        Maximale Anzahl Nachrichten (Default: 20)
      - folder       Outlook-Ordner-Name oder well-known-ID (Default: "inbox")
      - unread_only  Nur ungelesene Mails zurückgeben (Default: false)
    """
    import requests as _rq

    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    url = f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder}/messages"
    headers = {
        "Authorization": f"Bearer {tool.access_token}",
        "Content-Type": "application/json",
    }
    params: dict = {
        "$top": limit,
        "$orderby": "receivedDateTime desc",
        "$select": "id,subject,from,receivedDateTime,isRead,importance,bodyPreview,hasAttachments,categories",
    }
    if unread_only:
        params["$filter"] = "isRead eq false"

    resp = _rq.get(url, headers=headers, params=params, timeout=30)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Graph API Fehler: HTTP {resp.status_code} — {resp.text[:300]}",
        )

    messages: List[LenaInboxMessage] = []
    for m in resp.json().get("value", []):
        sender = m.get("from", {}).get("emailAddress", {})
        messages.append(
            LenaInboxMessage(
                message_id=m.get("id", ""),
                subject=m.get("subject", "") or "",
                from_name=sender.get("name", "") or "",
                from_email=sender.get("address", "") or "",
                received_at=m.get("receivedDateTime", "") or "",
                is_read=bool(m.get("isRead", False)),
                importance=m.get("importance", "normal") or "normal",
                body_preview=m.get("bodyPreview", "") or "",
                has_attachments=bool(m.get("hasAttachments", False)),
                categories=m.get("categories") or [],
            )
        )

    return LenaInboxResponse(count=len(messages), messages=messages)


@app.post("/api/lena/mail/draft", response_model=LenaDraftResponse)
def lena_mail_draft(
    req: LenaDraftRequest,
    _key: str = Security(verify_api_key),
):
    """
    Erstellt einen Entwurf im Drafts-Ordner von Sven.

    Lena erstellt Entwürfe, Sven genehmigt und sendet sie selbst.
    Bei reply_to_message_id wird der Entwurf als Antwort auf die angegebene Mail erstellt.
    """
    import requests as _rq

    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    headers = {
        "Authorization": f"Bearer {tool.access_token}",
        "Content-Type": "application/json",
    }

    def _recipients(addrs: List[LenaEmailAddress]) -> List[dict]:
        return [
            {"emailAddress": {"name": a.name, "address": a.email}}
            for a in addrs
        ]

    body_content = req.body_html if req.body_html else req.body_text
    body_type = "HTML" if req.body_html else "Text"

    if req.reply_to_message_id:
        # Create reply draft linked to original message for proper threading
        url = f"https://graph.microsoft.com/v1.0/me/messages/{req.reply_to_message_id}/createReply"
        payload = {
            "message": {
                "subject": req.subject,
                "body": {"contentType": body_type, "content": body_content},
                "toRecipients": _recipients(req.to),
                "ccRecipients": _recipients(req.cc),
            }
        }
    else:
        url = "https://graph.microsoft.com/v1.0/me/messages"
        payload = {
            "subject": req.subject,
            "body": {"contentType": body_type, "content": body_content},
            "toRecipients": _recipients(req.to),
            "ccRecipients": _recipients(req.cc),
        }

    resp = _rq.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=502,
            detail=f"Graph API Fehler: HTTP {resp.status_code} — {resp.text[:300]}",
        )

    draft = resp.json()
    return LenaDraftResponse(
        draft_id=draft.get("id", ""),
        subject=draft.get("subject", req.subject),
        created_at=draft.get("createdDateTime", "") or "",
    )


@app.post("/api/lena/send-mail", response_model=LenaSendMailResponse)
def lena_send_mail(
    req: LenaSendMailRequest,
    _key: str = Security(verify_api_key),
):
    """
    Sendet eine Mail via SMTP STARTTLS.

    SMTP-Config: smtps.udag.de:587. Credentials aus Env-Vars SMTP_USER, SMTP_FROM, SMTP_PASSWORD.
    """
    import smtplib
    import uuid
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    if not smtp_password:
        raise HTTPException(status_code=500, detail="SMTP_PASSWORD nicht konfiguriert.")

    if req.body_html:
        msg: MIMEMultipart | MIMEText = MIMEMultipart("alternative")
        msg.attach(MIMEText(req.body_text or "", "plain", "utf-8"))
        msg.attach(MIMEText(req.body_html, "html", "utf-8"))
    else:
        msg = MIMEText(req.body_text or "", "plain", "utf-8")

    message_id = f"<{uuid.uuid4()}@herbertgruppe.com>"
    msg["Message-ID"] = message_id
    msg["From"] = _SMTP_FROM
    msg["To"] = ", ".join(
        f"{a.name} <{a.email}>" if a.name else a.email for a in req.to
    )
    msg["Subject"] = req.subject
    if req.reply_to:
        msg["Reply-To"] = req.reply_to

    to_addrs = [a.email for a in req.to]

    try:
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(_SMTP_USER, smtp_password)
            smtp.sendmail(_SMTP_FROM, to_addrs, msg.as_string())
    except smtplib.SMTPException as exc:
        raise HTTPException(status_code=502, detail=f"SMTP-Fehler: {exc}") from exc

    return LenaSendMailResponse(success=True, message_id=message_id)


# Well-known Outlook folder aliases (Graph API canonical IDs).
# Maps lowercase display names (DE + EN) to Graph well-known folder names.
_WELL_KNOWN_FOLDER_MAP: dict[str, str] = {
    "inbox": "inbox",
    "posteingang": "inbox",
    "drafts": "drafts",
    "entwürfe": "drafts",
    "sent items": "sentitems",
    "gesendete elemente": "sentitems",
    "deleted items": "deleteditems",
    "papierkorb": "deleteditems",
    "gelöschte elemente": "deleteditems",
    "junk email": "junkemail",
    "spam": "junkemail",
    "junk-e-mail": "junkemail",
    "archive": "archive",
    "archiv": "archive",
}


# ---------------------------------------------------------------------------
# Input-validation helpers (HBE-610 — security fix)
# Extracted as module-level functions so tests can cover them without a full
# Pydantic model load.
# ---------------------------------------------------------------------------

_RE_MESSAGE_ID = re.compile(r"^[A-Za-z0-9_\-=]+$")
_RE_TARGET_FOLDER = re.compile(r"^[A-Za-z0-9 ÄÖÜäöüß_\-/]+$")


def _check_message_id(v: str) -> str:
    """Validate a Graph message ID (base64url-safe characters only)."""
    if not _RE_MESSAGE_ID.match(v):
        raise ValueError("message_id enthält unzulässige Zeichen — erwartet: base64url-sicher.")
    return v


def _check_target_folder(v: str) -> str:
    """Validate an Outlook folder name against an allowlist character set."""
    if not _RE_TARGET_FOLDER.match(v):
        raise ValueError("target_folder enthält unzulässige Zeichen.")
    return v


def _resolve_folder_id(target_folder: str, headers: dict) -> str:
    """Return the Graph folder ID for *target_folder*.

    HBE-1618: Wenn target_folder auf 'archive'/'archiv' matcht und ENV
    LENA_ARCHIVE_FOLDER_ID gesetzt ist, wird dieser Wert direkt zurueckgegeben.
    Damit kann Sven den Archiv-Zielordner konfigurieren ohne Code-Aenderung
    (Default well-known 'archive' zeigt auf 'Posteingang erledigt 2016').

    Checks well-known alias table next; falls back to GET /me/mailFolders query by displayName.
    Raises HTTPException 404 when the folder cannot be found.
    """
    import requests as _rq

    tf_lower = target_folder.strip().lower()

    # HBE-1618: ENV override fuer Archive-Ordner
    if tf_lower in ("archive", "archiv"):
        override_id = os.getenv("LENA_ARCHIVE_FOLDER_ID", "").strip()
        if override_id:
            return override_id

    alias = _WELL_KNOWN_FOLDER_MAP.get(tf_lower)
    if alias:
        # Verify it exists and fetch its real ID so the PATCH has a stable value.
        resp = _rq.get(
            f"https://graph.microsoft.com/v1.0/me/mailFolders/{alias}",
            headers=headers,
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json()["id"]

    # OData-escape single quotes (defense-in-depth even after validator).
    safe_folder = target_folder.replace("'", "''")
    # Fall back: query by displayName (supports custom folders).
    resp = _rq.get(
        "https://graph.microsoft.com/v1.0/me/mailFolders",
        headers=headers,
        params={"$filter": f"displayName eq '{safe_folder}'", "$select": "id,displayName"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Graph API Fehler beim Ordner-Lookup: HTTP {resp.status_code} — {resp.text[:300]}",
        )
    folders = resp.json().get("value", [])
    if not folders:
        raise HTTPException(
            status_code=404,
            detail=f"Outlook-Ordner nicht gefunden: '{target_folder}'",
        )
    return folders[0]["id"]


class LenaMoveMailRequest(BaseModel):
    message_id: str
    target_folder: str

    @field_validator("message_id")
    @classmethod
    def _vid_message_id(cls, v: str) -> str:
        return _check_message_id(v)

    @field_validator("target_folder")
    @classmethod
    def _vid_target_folder(cls, v: str) -> str:
        return _check_target_folder(v)


class LenaMoveMailResponse(BaseModel):
    success: bool
    message_id: str
    folder: str


class LenaMarkReadRequest(BaseModel):
    message_id: str

    @field_validator("message_id")
    @classmethod
    def _vid_message_id(cls, v: str) -> str:
        return _check_message_id(v)


class LenaMarkReadResponse(BaseModel):
    success: bool


class LenaArchiveByCategoryRequest(BaseModel):
    category: str

    @field_validator("category")
    @classmethod
    def _vid_category(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("category darf nicht leer sein")
        return v.strip()


class LenaArchiveByCategoryResponse(BaseModel):
    archived_count: int
    message_ids: List[str]


class LenaClearCategoriesRequest(BaseModel):
    category: Optional[str] = None  # None = alle Lena:-Kategorien loeschen
    dry_run: bool = False  # Wenn True: nur Mails zählen, keine PATCH-Aufrufe


class LenaClearCategoriesResponse(BaseModel):
    cleared_count: int
    message_ids: List[str]


class LenaByCategoryMail(BaseModel):
    message_id: str
    subject: str
    sender_email: str
    sender_name: str
    received_at: str
    body_preview: str


class LenaByCategoryResponse(BaseModel):
    mails: List[LenaByCategoryMail]
    category: str


class LenaActionRunRequest(BaseModel):
    category: str  # "weiterleiten", "tun", "ablegen", etc.


class LenaActionRunResponse(BaseModel):
    mails: List[LenaByCategoryMail]
    category: str
    mail_count: int


# ── Mail-Triage (HBE-Mail-Categorize) ─────────────────────────────────────
LENA_ACTION_CATEGORIES = {
    "antworten":      "Lena: Antworten",
    "tun":            "Lena: Tun",
    "warten":         "Lena: Warten",
    "recherchieren":  "Lena: Recherchieren",
    "weiterleiten":   "Lena: Weiterleiten",
    "ablegen":        "Lena: Ablegen",
}
# Importance wird jetzt über das Outlook-Priorität-Feld (importance) gesetzt,
# nicht mehr als Kategorie.
LENA_IMPORTANCE_MAP = {
    "hoch":    "high",
    "mittel":  "normal",
    "niedrig": "low",
}
OUTLOOK_IMPORTANCE_TO_LENA = {v: k for k, v in LENA_IMPORTANCE_MAP.items()}

# Outlook-Color-Presets siehe Graph-Docs (preset0=Red, preset1=Orange, preset2=Brown,
# preset3=Yellow, preset4=Green, preset5=Teal, preset6=Olive, preset7=Blue, preset8=Purple,
# preset9=Cranberry, preset10=Steel, preset11=DarkSteel, preset12=Grey, preset13=DarkGrey,
# preset14=Black, preset15=DarkRed, preset16=DarkOrange, preset17=DarkBrown, preset18=DarkYellow,
# preset19=DarkGreen, preset20=DarkTeal, preset21=DarkOlive, preset22=DarkBlue, preset23=DarkPurple,
# preset24=DarkCranberry)
LENA_MASTER_CATEGORIES = [
    {"displayName": "Lena: Antworten",     "color": "preset0"},   # Red
    {"displayName": "Lena: Tun",           "color": "preset1"},   # Orange
    {"displayName": "Lena: Warten",        "color": "preset3"},   # Yellow
    {"displayName": "Lena: Recherchieren", "color": "preset7"},   # Blue
    {"displayName": "Lena: Weiterleiten",  "color": "preset8"},   # Purple
    {"displayName": "Lena: Ablegen",       "color": "preset12"},  # Grey
]


def _check_lena_action(v: str) -> str:
    if v not in LENA_ACTION_CATEGORIES:
        raise ValueError(f"action muss eines sein: {', '.join(LENA_ACTION_CATEGORIES.keys())}")
    return v


def _check_lena_importance(v: str) -> str:
    valid = {"high", "normal", "low"}
    if v not in valid:
        raise ValueError(f"importance muss eines sein: {', '.join(sorted(valid))}")
    return v


class LenaCategorizeRequest(BaseModel):
    message_id: str
    action: str

    @field_validator("message_id")
    @classmethod
    def _vid_message_id(cls, v: str) -> str:
        return _check_message_id(v)

    @field_validator("action")
    @classmethod
    def _vid_action(cls, v: str) -> str:
        return _check_lena_action(v)


class LenaSetImportanceRequest(BaseModel):
    message_id: str
    importance: str  # "high" | "normal" | "low"

    @field_validator("message_id")
    @classmethod
    def _vid_message_id(cls, v: str) -> str:
        return _check_message_id(v)

    @field_validator("importance")
    @classmethod
    def _vid_importance(cls, v: str) -> str:
        return _check_lena_importance(v)


class LenaSetImportanceResponse(BaseModel):
    success: bool
    message_id: str
    importance: str


class LenaCategorizeResponse(BaseModel):
    success: bool
    message_id: str
    categories: List[str]
    moved_to_archive: Optional[bool] = None  # True wenn action=ablegen und Mail archiviert (HBE-1603)


class LenaSyncCategoriesResponse(BaseModel):
    success: bool
    created: List[str]
    existing: List[str]


class LenaTriageInboxMail(BaseModel):
    message_id: str
    subject: str
    sender_email: str
    sender_name: str
    received_at: str
    body_preview: str
    has_attachments: bool


class LenaTriageInboxResponse(BaseModel):
    mails: List[LenaTriageInboxMail]


class LenaTriageSummaryResponse(BaseModel):
    # Aktion-Buckets
    antworten: int
    tun: int
    warten: int
    recherchieren: int
    weiterleiten: int
    ablegen: int
    # Priorität-Buckets
    hoch: int
    mittel: int
    niedrig: int
    # Metadaten
    since: str
    total_categorized: int


class LenaTriageOverride(BaseModel):
    message_id: str
    subject: str
    sender_email: str
    sender_domain: str
    current_action: str   # aktuell gesetzte Lena-Kategorie (nach Sven-Override)
    current_priority: str
    last_modified_at: str


class LenaTriageOverridesResponse(BaseModel):
    overrides: List[LenaTriageOverride]
    since: str


class LenaContactResult(BaseModel):
    id: str = ""
    name: str
    email: str
    title: str = ""
    company: str = ""


class LenaContactsSearchResponse(BaseModel):
    contacts: List[LenaContactResult]


class LenaContactUpdateRequest(BaseModel):
    mobilePhone: Optional[str] = None
    businessPhone: Optional[str] = None
    birthday: Optional[str] = None
    categories: Optional[List[str]] = None


class LenaContactUpdateResponse(BaseModel):
    success: bool
    contact_id: str


class LenaContactCreateRequest(BaseModel):
    givenName: str = Field(..., description="Vorname (Pflicht)")
    surname: str = Field(..., description="Nachname (Pflicht)")
    emailAddresses: List[str] = Field(default_factory=list, description="E-Mail-Adressen (max. 3)")
    mobilePhone: Optional[str] = None
    businessPhones: List[str] = Field(default_factory=list)
    jobTitle: Optional[str] = None
    companyName: Optional[str] = None
    fileAs: Optional[str] = None
    birthday: Optional[str] = None
    categories: List[str] = Field(default_factory=list)

    @field_validator("givenName", "surname")
    @classmethod
    def must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("darf nicht leer sein")
        return v.strip()

    @field_validator("emailAddresses")
    @classmethod
    def max_three_emails(cls, v: List[str]) -> List[str]:
        if len(v) > 3:
            raise ValueError("maximal 3 E-Mail-Adressen erlaubt")
        return v

    @field_validator("birthday")
    @classmethod
    def birthday_format(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        import re
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            raise ValueError("birthday muss im Format YYYY-MM-DD sein")
        return v


class LenaContactCreateResponse(BaseModel):
    success: bool
    contact_id: str
    display_name: str


class LenaContactDeleteResponse(BaseModel):
    success: bool
    contact_id: str


@app.post("/api/lena/mail/move", response_model=LenaMoveMailResponse)
def lena_mail_move(
    req: LenaMoveMailRequest,
    _key: str = Security(verify_api_key),
):
    """
    Verschiebt eine Mail in einen Outlook-Ordner (HBE-607, fix HBE-1106).

    Unterstützt well-known Ordner-Aliase (Archive/Archiv, Deleted Items/Papierkorb,
    Junk Email/Spam, Inbox/Posteingang) sowie beliebige Custom-Folder per displayName.
    Implementierung: PATCH /me/messages/{id} mit parentFolderId — message_id bleibt
    unverändert (POST /move erzeugt ein neues Objekt mit neuer ID, HBE-1106).
    Nach PATCH wird per GET verifiziert, dass parentFolderId wirklich geändert wurde.
    """
    import requests as _rq

    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    headers = {
        "Authorization": f"Bearer {tool.access_token}",
        "Content-Type": "application/json",
    }

    folder_id = _resolve_folder_id(req.target_folder, headers)

    # HBE-1616: POST /me/messages/{id}/move statt PATCH parentFolderId.
    # PATCH parentFolderId gibt 200 zurueck aber verschiebt die Mail NICHT (Graph API No-Op).
    # POST /move ist der offizielle Weg — gibt 201 + neues Message-Objekt mit neuer message_id.
    resp = _rq.post(
        f"https://graph.microsoft.com/v1.0/me/messages/{req.message_id}/move",
        headers=headers,
        json={"destinationId": folder_id},
        timeout=30,
    )
    if resp.status_code == 401 and tool._refresh_access_token():
        headers["Authorization"] = f"Bearer {tool.access_token}"
        resp = _rq.post(
            f"https://graph.microsoft.com/v1.0/me/messages/{req.message_id}/move",
            headers=headers,
            json={"destinationId": folder_id},
            timeout=30,
        )
    if resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=502,
            detail=f"Graph API Fehler beim Verschieben: HTTP {resp.status_code} — {resp.text[:300]}",
        )

    # POST /move erzeugt ein neues Message-Objekt; parentFolderId in der Response bestaetigt den Move.
    moved = resp.json()
    new_message_id = moved.get("id", req.message_id)
    actual_folder = moved.get("parentFolderId", "")
    if actual_folder and actual_folder != folder_id:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Move nicht bestaetigt — parentFolderId nach POST /move ist {actual_folder!r}, "
                f"erwartet {folder_id!r}. Mail wurde nicht verschoben."
            ),
        )

    return LenaMoveMailResponse(
        success=True,
        message_id=new_message_id,
        folder=req.target_folder,
    )


@app.post("/api/lena/mail/mark-read", response_model=LenaMarkReadResponse)
def lena_mail_mark_read(
    req: LenaMarkReadRequest,
    _key: str = Security(verify_api_key),
):
    """
    Markiert eine Mail als gelesen (HBE-607).

    Implementierung: PATCH /me/messages/{id} mit { "isRead": true }.
    """
    import requests as _rq

    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    headers = {
        "Authorization": f"Bearer {tool.access_token}",
        "Content-Type": "application/json",
    }

    resp = _rq.patch(
        f"https://graph.microsoft.com/v1.0/me/messages/{req.message_id}",
        headers=headers,
        json={"isRead": True},
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=502,
            detail=f"Graph API Fehler beim Markieren: HTTP {resp.status_code} — {resp.text[:300]}",
        )

    return LenaMarkReadResponse(success=True)


@app.post("/api/lena/mail/archive-by-category", response_model=LenaArchiveByCategoryResponse)
def lena_mail_archive_by_category(
    req: LenaArchiveByCategoryRequest,
    _key: str = Security(verify_api_key),
):
    """
    Archiviert alle Inbox-Mails, die die angegebene Outlook-Kategorie tragen (HBE-987).

    Logik:
      1. Alle Inbox-Mails mit categories/any(c:c eq '<category>') abrufen
      2. Jede gefundene Mail in den Archive-Ordner verschieben
      3. Zusammenfassung zurückgeben
    """
    import requests as _rq

    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    headers = {
        "Authorization": f"Bearer {tool.access_token}",
        "Content-Type": "application/json",
    }

    # Alle Seiten laden via _fetch_inbox_mails_by_lena_category (folgt @odata.nextLink, HBE-1614)
    try:
        raw = _fetch_inbox_mails_by_lena_category(headers, req.category)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Graph API Fehler beim Abrufen: {exc}") from exc

    messages = raw
    archive_folder_id = _resolve_folder_id("archive", headers)

    # HBE-1616: POST /move statt PATCH parentFolderId (PATCH ist Graph-API-No-Op)
    archived_ids: List[str] = []
    for msg in messages:
        msg_id = msg.get("id", "")
        if not msg_id:
            continue
        move_resp = _rq.post(
            f"https://graph.microsoft.com/v1.0/me/messages/{msg_id}/move",
            headers=headers,
            json={"destinationId": archive_folder_id},
            timeout=30,
        )
        if move_resp.status_code in (200, 201):
            archived_ids.append(msg_id)

    return LenaArchiveByCategoryResponse(
        archived_count=len(archived_ids),
        message_ids=archived_ids,
    )


# ── Triage v2 – On-Demand Action Runner (HBE-1320) ────────────────────────

def _fetch_inbox_mails_by_lena_category(
    headers: dict, lena_category_full: str, top: int = 250
) -> List[dict]:
    """
    Hilfsfunktion: Holt Inbox-Mails mit exakt der angegebenen Lena-Vollkategorie
    (z.B. 'Lena: Weiterleiten') per Graph API und gibt die value-Liste zurück.
    Folgt @odata.nextLink bis alle Mails geladen sind (max. 500).
    """
    import requests as _rq

    escaped = lena_category_full.replace("'", "''")
    url = (
        "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages"
        f"?$filter=categories/any(c:c eq '{escaped}')"
        f"&$select=id,subject,from,receivedDateTime,bodyPreview,categories"
        f"&$top={min(top, 250)}"
        "&$orderby=receivedDateTime asc"
    )
    mails: List[dict] = []
    while url:
        resp = _rq.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Graph API Fehler beim Abrufen der Kategorie-Mails: HTTP {resp.status_code} — {resp.text[:300]}",
            )
        data = resp.json()
        mails.extend(data.get("value", []))
        if len(mails) >= 500:
            break
        url = data.get("@odata.nextLink")
    return mails


def _graph_mails_to_lena_by_category(raw_mails: List[dict]) -> List[LenaByCategoryMail]:
    result = []
    for m in raw_mails:
        sender = (m.get("from") or {}).get("emailAddress", {}) or {}
        result.append(LenaByCategoryMail(
            message_id=m.get("id", ""),
            subject=(m.get("subject") or ""),
            sender_email=(sender.get("address") or "").lower(),
            sender_name=(sender.get("name") or ""),
            received_at=(m.get("receivedDateTime") or ""),
            body_preview=(m.get("bodyPreview") or ""),
        ))
    return result


@app.post("/api/lena/mail/clear-categories", response_model=LenaClearCategoriesResponse)
def lena_mail_clear_categories(
    req: LenaClearCategoriesRequest,
    _key: str = Security(verify_api_key),
):
    """
    Loescht Lena-Kategorien (Lena:*) aus allen Inbox-Mails (HBE-1320).

    Wenn `category` angegeben ist, werden nur Mails mit dieser spezifischen
    Lena-Kategorie bereinigt. Ohne `category`: alle Mails mit irgendeiner
    Lena:*-Kategorie.

    Implementierung:
    - Alle Inbox-Mails abrufen ($select=id,categories)
    - Client-seitig filtern: Mails mit passenden Lena:-Kategorien
    - Fuer jede Mail: PATCH mit categories = kept (ohne die Lena:-Kategorie)
    """
    import requests as _rq

    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    headers = {
        "Authorization": f"Bearer {tool.access_token}",
        "Content-Type": "application/json",
    }

    # Bestimme welche Lena:-Kategorie(n) entfernt werden sollen
    target_cat: Optional[str] = None
    if req.category:
        cat_key = req.category.strip().lower()
        if cat_key in LENA_ACTION_CATEGORIES:
            target_cat = LENA_ACTION_CATEGORIES[cat_key]
        elif req.category.startswith("Lena: "):
            target_cat = req.category.strip()
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unbekannte Kategorie '{req.category}'. Gueltig: {', '.join(LENA_ACTION_CATEGORIES.keys())}",
            )

    # Alle Inbox-Mails mit Kategorien abrufen (paginiert)
    url = (
        "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages"
        "?$select=id,categories&$top=250"
    )
    all_mails: List[dict] = []
    while url:
        resp = _rq.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Graph API Fehler beim Abrufen: HTTP {resp.status_code} — {resp.text[:300]}",
            )
        data = resp.json()
        all_mails.extend(data.get("value", []))
        if len(all_mails) >= 500:
            break
        url = data.get("@odata.nextLink")

    cleared_ids: List[str] = []
    for msg in all_mails:
        msg_id = msg.get("id", "")
        if not msg_id:
            continue
        cats = msg.get("categories") or []
        if target_cat:
            has_target = target_cat in cats
            if not has_target:
                continue
            new_cats = [c for c in cats if c != target_cat]
        else:
            has_lena = any(c.startswith("Lena: ") for c in cats)
            if not has_lena:
                continue
            new_cats = [c for c in cats if not c.startswith("Lena: ")]

        if req.dry_run:
            cleared_ids.append(msg_id)
        else:
            patch_resp = _rq.patch(
                f"https://graph.microsoft.com/v1.0/me/messages/{msg_id}",
                headers=headers,
                json={"categories": new_cats},
                timeout=30,
            )
            if patch_resp.status_code in (200, 201):
                cleared_ids.append(msg_id)

    return LenaClearCategoriesResponse(
        cleared_count=len(cleared_ids),
        message_ids=cleared_ids,
    )


@app.get("/api/lena/mail/by-category/{category}", response_model=LenaByCategoryResponse)
def lena_mail_by_category(
    category: str,
    _key: str = Security(verify_api_key),
):
    """
    Gibt alle Inbox-Mails mit der angegebenen Lena-Kategorie zurueck (HBE-1320).

    Pfad-Parameter `category`: Kurzname der Kategorie (z.B. 'Weiterleiten', 'Tun',
    'Ablegen', 'Antworten', 'Warten', 'Recherchieren') — Gross-/Kleinschreibung egal.

    Antwort: Liste der Mails mit id, subject, from, receivedDateTime, body_preview.
    """
    cat_key = category.strip().lower()
    if cat_key not in LENA_ACTION_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Unbekannte Kategorie '{category}'. Gueltig: {', '.join(LENA_ACTION_CATEGORIES.keys())}",
        )

    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    headers = {"Authorization": f"Bearer {tool.access_token}"}
    lena_cat = LENA_ACTION_CATEGORIES[cat_key]
    raw_mails = _fetch_inbox_mails_by_lena_category(headers, lena_cat)
    mails = _graph_mails_to_lena_by_category(raw_mails)

    return LenaByCategoryResponse(mails=mails, category=lena_cat)


@app.post("/api/lena/mail/action-run", response_model=LenaActionRunResponse)
def lena_mail_action_run(
    req: LenaActionRunRequest,
    _key: str = Security(verify_api_key),
):
    """
    Einstiegspunkt fuer On-Demand-Action-Run einer Kategorie (HBE-1320).

    Lena ruft diesen Endpoint auf wenn Sven per Telegram eine Kategorie triggert
    ('Lena, Ablegen', 'Lena, Weiterleiten', etc.). Der Endpoint liefert alle
    Inbox-Mails mit der Kategorie — Lena verarbeitet sie danach sequenziell.

    Gueltige Kategorie-Keys: antworten, tun, warten, recherchieren, weiterleiten, ablegen.
    """
    cat_key = req.category.strip().lower()
    if cat_key not in LENA_ACTION_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Unbekannte Kategorie '{req.category}'. Gueltig: {', '.join(LENA_ACTION_CATEGORIES.keys())}",
        )

    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    headers = {"Authorization": f"Bearer {tool.access_token}"}
    lena_cat = LENA_ACTION_CATEGORIES[cat_key]
    raw_mails = _fetch_inbox_mails_by_lena_category(headers, lena_cat)
    mails = _graph_mails_to_lena_by_category(raw_mails)

    return LenaActionRunResponse(
        mails=mails,
        category=lena_cat,
        mail_count=len(mails),
    )


# ── Mail-Triage Endpoints (HBE-Mail-Categorize) ───────────────────────────


@app.post("/api/lena/outlook/master-categories/sync", response_model=LenaSyncCategoriesResponse)
def lena_outlook_master_categories_sync(
    _key: str = Security(verify_api_key),
):
    """
    Legt die 9 Outlook-MasterCategories für Lena-Triage idempotent an.

    Schema:
      - 6 Aktion-Kategorien (Lena: Antworten | Tun | Warten | Recherchieren | Weiterleiten | Ablegen)
      - 3 Prioritäts-Kategorien (Priorität: Hoch | Mittel | Niedrig)

    Existierende Kategorien werden NICHT überschrieben — nur fehlende neu angelegt.
    Implementierung: POST /me/outlook/masterCategories pro fehlende Kategorie.
    """
    import requests as _rq

    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    headers = {
        "Authorization": f"Bearer {tool.access_token}",
        "Content-Type": "application/json",
    }

    get_resp = _rq.get(
        "https://graph.microsoft.com/v1.0/me/outlook/masterCategories",
        headers=headers,
        timeout=30,
    )
    if get_resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Graph API Fehler beim Lesen der MasterCategories: HTTP {get_resp.status_code} — {get_resp.text[:300]}",
        )
    existing_names = {c.get("displayName", "") for c in get_resp.json().get("value", [])}

    created: List[str] = []
    existing: List[str] = []
    for cat in LENA_MASTER_CATEGORIES:
        name = cat["displayName"]
        if name in existing_names:
            existing.append(name)
            continue
        post_resp = _rq.post(
            "https://graph.microsoft.com/v1.0/me/outlook/masterCategories",
            headers=headers,
            json=cat,
            timeout=30,
        )
        if post_resp.status_code in (200, 201):
            created.append(name)
        else:
            raise HTTPException(
                status_code=502,
                detail=f"Graph API Fehler beim Anlegen von '{name}': HTTP {post_resp.status_code} — {post_resp.text[:200]}",
            )

    return LenaSyncCategoriesResponse(success=True, created=created, existing=existing)


@app.post("/api/lena/mail/categorize", response_model=LenaCategorizeResponse)
def lena_mail_categorize(
    req: LenaCategorizeRequest,
    _key: str = Security(verify_api_key),
):
    """
    Setzt genau eine Outlook-Kategorie auf eine Mail (Aktion/Typ).

    Bestehende Lena:*- und Priorität:*-Kategorien werden ERSETZT (nicht dupliziert),
    andere bestehende User-Kategorien bleiben erhalten. Implementierung:
    1) GET /me/messages/{id}?$select=categories  → bestehende lesen
    2) Filter: Nicht-Lena/Priorität-Kategorien behalten
    3) PATCH /me/messages/{id} mit { categories: kept + [action] }

    Wichtigkeit/Priorität wird separat via POST /api/lena/mail/set-importance gesetzt.
    """
    import requests as _rq

    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    headers = {
        "Authorization": f"Bearer {tool.access_token}",
        "Content-Type": "application/json",
    }

    get_resp = _rq.get(
        f"https://graph.microsoft.com/v1.0/me/messages/{req.message_id}?$select=categories",
        headers=headers,
        timeout=30,
    )
    if get_resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Graph API Fehler beim Lesen der Mail-Kategorien: HTTP {get_resp.status_code} — {get_resp.text[:300]}",
        )
    existing_cats = get_resp.json().get("categories", []) or []
    # Strip existing Lena:* and legacy Priorität:* categories (clean up old format)
    kept = [c for c in existing_cats if not (c.startswith("Lena: ") or c.startswith("Priorität: "))]

    action_cat = LENA_ACTION_CATEGORIES[req.action]
    new_categories = kept + [action_cat]

    patch_resp = _rq.patch(
        f"https://graph.microsoft.com/v1.0/me/messages/{req.message_id}",
        headers=headers,
        json={"categories": new_categories},
        timeout=30,
    )
    if patch_resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=502,
            detail=f"Graph API Fehler beim Setzen der Kategorien: HTTP {patch_resp.status_code} — {patch_resp.text[:300]}",
        )

    # HBE-1603: Wenn action="ablegen", Mail sofort archivieren — keine zweite API-Call-Runde notwendig
    moved_to_archive: Optional[bool] = None
    if req.action == "ablegen":
        try:
            archive_folder_id = _resolve_folder_id("archive", headers)
            # HBE-1616: POST /move statt PATCH parentFolderId
            archive_resp = _rq.post(
                f"https://graph.microsoft.com/v1.0/me/messages/{req.message_id}/move",
                headers=headers,
                json={"destinationId": archive_folder_id},
                timeout=30,
            )
            moved_to_archive = archive_resp.status_code in (200, 201)
            if not moved_to_archive:
                logger.warning(
                    "[categorize] action=ablegen: archive PATCH fehlgeschlagen HTTP %s — %s",
                    archive_resp.status_code,
                    archive_resp.text[:200],
                )
        except Exception as _exc:
            logger.warning("[categorize] action=ablegen: archive fehlgeschlagen: %s", _exc)
            moved_to_archive = False

    return LenaCategorizeResponse(
        success=True,
        message_id=req.message_id,
        categories=new_categories,
        moved_to_archive=moved_to_archive,
    )


@app.post("/api/lena/mail/set-importance", response_model=LenaSetImportanceResponse)
def lena_mail_set_importance(
    req: LenaSetImportanceRequest,
    _key: str = Security(verify_api_key),
):
    """
    Setzt die Outlook-Wichtigkeit (importance) einer Mail via Microsoft Graph.

    Mögliche Werte: "high" | "normal" | "low"
    Entspricht in Outlook der Prioritätsspalte (Hoch / Normal / Niedrig).
    """
    import requests as _rq

    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    headers = {
        "Authorization": f"Bearer {tool.access_token}",
        "Content-Type": "application/json",
    }

    patch_resp = _rq.patch(
        f"https://graph.microsoft.com/v1.0/me/messages/{req.message_id}",
        headers=headers,
        json={"importance": req.importance},
        timeout=30,
    )
    if patch_resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=502,
            detail=f"Graph API Fehler beim Setzen der Wichtigkeit: HTTP {patch_resp.status_code} — {patch_resp.text[:300]}",
        )

    return LenaSetImportanceResponse(
        success=True,
        message_id=req.message_id,
        importance=req.importance,
    )


@app.get("/api/lena/mail/inbox-for-triage", response_model=LenaTriageInboxResponse)
def lena_mail_inbox_for_triage(
    days: int = 7,
    limit: int = 50,
    include_categorized: bool = False,
    _key: str = Security(verify_api_key),
):
    """
    Listet Mails aus dem Posteingang der letzten N Tage für die Triage.

    Standardverhalten (`include_categorized=false`): nur UN-kategorisierte Mails
    (keine Lena:*- oder Priorität:*-Kategorie). Triage-Poller nutzt das für den
    Auto-Categorize-Pass.

    Mit `include_categorized=true`: ALLE Mails der letzten N Tage (auch schon
    kategorisierte). Wird vom Poller im Re-Triage-Mode genutzt
    (LENA_MAIL_TRIAGE_RETRIAGE_ALL=1), z.B. um die Bestandsinbox nach einem
    Logik-Upgrade (Regel → LLM) neu durchzunudeln.

    Implementierung:
    - GET /me/mailFolders/Inbox/messages mit $filter receivedDateTime ge <since>
    - Client-side Filter nur bei include_categorized=false
    - Over-Fetch (limit*2), weil Categories-Filter via Graph $filter unzuverlässig
    """
    import requests as _rq
    from datetime import datetime, timezone, timedelta

    if days < 1 or days > 90:
        raise HTTPException(status_code=400, detail="days muss zwischen 1 und 90 liegen.")
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit muss zwischen 1 und 200 liegen.")

    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    headers = {"Authorization": f"Bearer {tool.access_token}"}
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    over_fetch = min(limit, 200) if include_categorized else min(limit * 2, 200)
    url = (
        "https://graph.microsoft.com/v1.0/me/mailFolders/Inbox/messages"
        f"?$filter=receivedDateTime ge {since}"
        f"&$top={over_fetch}"
        "&$select=id,subject,from,receivedDateTime,bodyPreview,hasAttachments,categories"
        "&$orderby=receivedDateTime desc"
    )

    resp = _rq.get(url, headers=headers, timeout=30)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Graph API Fehler beim Laden der Inbox: HTTP {resp.status_code} — {resp.text[:300]}",
        )

    mails: List[LenaTriageInboxMail] = []
    for m in resp.json().get("value", []):
        if not include_categorized:
            cats = m.get("categories", []) or []
            if any(c.startswith("Lena: ") for c in cats):
                continue
        sender = (m.get("from") or {}).get("emailAddress", {}) or {}
        mails.append(LenaTriageInboxMail(
            message_id=m.get("id", "") or "",
            subject=(m.get("subject") or ""),
            sender_email=(sender.get("address") or ""),
            sender_name=(sender.get("name") or ""),
            received_at=(m.get("receivedDateTime") or ""),
            body_preview=(m.get("bodyPreview") or "")[:500],
            has_attachments=bool(m.get("hasAttachments")),
        ))
        if len(mails) >= limit:
            break

    return LenaTriageInboxResponse(mails=mails)


@app.get("/api/lena/mail/triage-summary", response_model=LenaTriageSummaryResponse)
def lena_mail_triage_summary(
    since: str,
    _key: str = Security(verify_api_key),
):
    """
    Liefert Zähler pro Triage-Bucket für Mails, die seit `since` empfangen wurden
    und bereits kategorisiert sind (d.h. eine Lena:*-Kategorie haben).

    Wird von SKILL_BRIEFING.md für das Tages-Briefing genutzt.

    Query-Parameter:
      - since  ISO 8601 UTC-Zeitstempel (z.B. "2026-06-15T00:00:00Z").
               MUSS UTC sein — kein lokaler Server-Zeitstempel.

    Response: Zähler pro Aktion (antworten/tun/warten/recherchieren/weiterleiten/ablegen)
              + Zähler pro Priorität (hoch/mittel/niedrig) + Metadaten.

    Implementierung: GET /me/mailFolders/Inbox/messages mit receivedDateTime-Filter,
    nur Mails MIT Lena:*-Kategorie zählen (client-side, analog inbox-for-triage).
    """
    import requests as _rq
    from datetime import datetime, timezone

    if not since:
        raise HTTPException(status_code=400, detail="since-Parameter ist Pflicht (ISO 8601 UTC).")
    try:
        # Parse and normalize to UTC — reject timestamps without tz info
        since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        if since_dt.tzinfo is None:
            raise ValueError("No timezone")
        since_dt = since_dt.astimezone(timezone.utc)
        since_str = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError):
        raise HTTPException(
            status_code=400,
            detail="since muss ein gültiger ISO 8601 UTC-Zeitstempel sein (z.B. '2026-06-15T00:00:00Z').",
        )

    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    headers = {"Authorization": f"Bearer {tool.access_token}"}

    # Fetch categorized mails since `since` — up to 200 (over-fetch, filter client-side)
    # importance field added to track priority (replaces Priorität:* categories)
    url = (
        "https://graph.microsoft.com/v1.0/me/mailFolders/Inbox/messages"
        f"?$filter=receivedDateTime ge {since_str}"
        "&$top=200"
        "&$select=id,categories,importance"
        "&$orderby=receivedDateTime desc"
    )
    resp = _rq.get(url, headers=headers, timeout=30)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Graph API Fehler: HTTP {resp.status_code} — {resp.text[:300]}",
        )

    # Map Outlook category display names → internal bucket keys
    action_map = {v: k for k, v in LENA_ACTION_CATEGORIES.items()}

    counts: dict = {
        "antworten": 0, "tun": 0, "warten": 0,
        "recherchieren": 0, "weiterleiten": 0, "ablegen": 0,
        "hoch": 0, "mittel": 0, "niedrig": 0,
    }
    total = 0

    for m in resp.json().get("value", []):
        cats = m.get("categories", []) or []
        lena_cats = [c for c in cats if c.startswith("Lena: ")]
        if not lena_cats:
            continue  # skip un-categorized mails
        total += 1
        for c in lena_cats:
            if c in action_map:
                counts[action_map[c]] += 1
        # Priority from Outlook importance field (replaces Priorität:* categories)
        imp = (m.get("importance") or "normal").lower()
        counts[OUTLOOK_IMPORTANCE_TO_LENA.get(imp, "mittel")] += 1

    return LenaTriageSummaryResponse(
        antworten=counts["antworten"],
        tun=counts["tun"],
        warten=counts["warten"],
        recherchieren=counts["recherchieren"],
        weiterleiten=counts["weiterleiten"],
        ablegen=counts["ablegen"],
        hoch=counts["hoch"],
        mittel=counts["mittel"],
        niedrig=counts["niedrig"],
        since=since_str,
        total_categorized=total,
    )


@app.get("/api/lena/mail/categorized-overrides", response_model=LenaTriageOverridesResponse)
def lena_mail_categorized_overrides(
    since: str,
    limit: int = 50,
    _key: str = Security(verify_api_key),
):
    """
    Liefert Mails bei denen eine Lena:*-Kategorie existiert und die seit `since`
    verändert wurden (lastModifiedDateTime >= since).

    Wird vom Mail-Triage-Poller für den Hindsight-Lern-Pass genutzt: der Poller
    vergleicht die aktuellen Kategorien mit seinen gespeicherten Originalen und
    erkennt so Sven-Overrides.

    Query-Parameter:
      - since  ISO 8601 UTC-Zeitstempel — MUSS UTC sein.
      - limit  Max Ergebnisse (1–200, Standard: 50).

    Implementierung: GET /me/mailFolders/Inbox/messages?$filter=lastModifiedDateTime ge {since}
    Client-side Filter: nur Mails MIT Lena:*-Kategorie zurückgeben.
    """
    import requests as _rq
    from datetime import datetime, timezone

    if not since:
        raise HTTPException(status_code=400, detail="since-Parameter ist Pflicht (ISO 8601 UTC).")
    try:
        since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        if since_dt.tzinfo is None:
            raise ValueError("No timezone")
        since_dt = since_dt.astimezone(timezone.utc)
        since_str = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError):
        raise HTTPException(
            status_code=400,
            detail="since muss ein gültiger ISO 8601 UTC-Zeitstempel sein.",
        )
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit muss zwischen 1 und 200 liegen.")

    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    headers = {"Authorization": f"Bearer {tool.access_token}"}
    action_map = {v: k for k, v in LENA_ACTION_CATEGORIES.items()}

    url = (
        "https://graph.microsoft.com/v1.0/me/mailFolders/Inbox/messages"
        f"?$filter=lastModifiedDateTime ge {since_str}"
        f"&$top={min(limit * 3, 200)}"
        "&$select=id,subject,from,lastModifiedDateTime,categories,importance"
        "&$orderby=lastModifiedDateTime desc"
    )
    resp = _rq.get(url, headers=headers, timeout=30)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Graph API Fehler: HTTP {resp.status_code} — {resp.text[:300]}",
        )

    overrides: List[LenaTriageOverride] = []
    for m in resp.json().get("value", []):
        cats = m.get("categories", []) or []
        lena_action = next((action_map[c] for c in cats if c in action_map), None)
        if not lena_action:
            continue  # skip un-categorized mails
        # Priority from Outlook importance field (replaces Priorität:* categories)
        imp = (m.get("importance") or "normal").lower()
        lena_priority = OUTLOOK_IMPORTANCE_TO_LENA.get(imp, "mittel")
        sender = (m.get("from") or {}).get("emailAddress", {}) or {}
        email_addr = (sender.get("address") or "").lower()
        domain = email_addr.split("@")[-1] if "@" in email_addr else email_addr
        overrides.append(LenaTriageOverride(
            message_id=m.get("id", ""),
            subject=(m.get("subject") or ""),
            sender_email=email_addr,
            sender_domain=domain,
            current_action=lena_action,
            current_priority=lena_priority,
            last_modified_at=(m.get("lastModifiedDateTime") or ""),
        ))
        if len(overrides) >= limit:
            break

    return LenaTriageOverridesResponse(overrides=overrides, since=since_str)


@app.get("/api/lena/contacts/search", response_model=LenaContactsSearchResponse)
def lena_contacts_search(
    q: str,
    _key: str = Security(verify_api_key),
):
    """
    Sucht Kontakte im Outlook-Adressbuch via Microsoft Graph People API (HBE-647).

    Query-Parameter:
      - q  Suchbegriff (Name, E-Mail, Firma; max. 100 Zeichen)

    Primäre Suche: GET /me/people?$search={q}
    Fallback (kein Ergebnis): GET /me/contacts?$filter=startswith(displayName,'{q}')
    HTTP 503 wenn Token abgelaufen — identisch zu /api/lena/mail/inbox.
    """
    import requests as _rq

    q = q.strip()
    if not q:
        return LenaContactsSearchResponse(contacts=[])
    if len(q) > 100:
        raise HTTPException(status_code=400, detail="Suchbegriff zu lang (max. 100 Zeichen).")

    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    headers = {"Authorization": f"Bearer {tool.access_token}"}

    # Primary: People API
    resp = _rq.get(
        "https://graph.microsoft.com/v1.0/me/people",
        headers=headers,
        params={
            "$search": q,
            "$top": "10",
            "$select": "id,displayName,emailAddresses,jobTitle,companyName",
        },
        timeout=30,
    )

    contacts: List[LenaContactResult] = []

    if resp.status_code == 200:
        for person in resp.json().get("value", []):
            emails = person.get("emailAddresses") or []
            email = emails[0].get("address", "") if emails else ""
            if not email:
                continue
            contacts.append(
                LenaContactResult(
                    id=person.get("id", ""),
                    name=person.get("displayName", ""),
                    email=email,
                    title=person.get("jobTitle") or "",
                    company=person.get("companyName") or "",
                )
            )

    # Fallback: Contacts API when People API returned nothing
    if not contacts:
        q_esc = q.replace("'", "''")  # OData single-quote escaping
        resp2 = _rq.get(
            "https://graph.microsoft.com/v1.0/me/contacts",
            headers=headers,
            params={
                "$filter": f"startswith(displayName,'{q_esc}')",
                "$top": "10",
                "$select": "id,displayName,emailAddresses,jobTitle,companyName",
            },
            timeout=30,
        )
        if resp2.status_code == 200:
            for contact in resp2.json().get("value", []):
                emails = contact.get("emailAddresses") or []
                email = emails[0].get("address", "") if emails else ""
                if not email:
                    continue
                contacts.append(
                    LenaContactResult(
                        id=contact.get("id", ""),
                        name=contact.get("displayName", ""),
                        email=email,
                        title=contact.get("jobTitle") or "",
                        company=contact.get("companyName") or "",
                    )
                )

    return LenaContactsSearchResponse(contacts=contacts)


@app.post("/api/lena/contacts", response_model=LenaContactCreateResponse)
def lena_contact_create(
    req: LenaContactCreateRequest,
    _key: str = Security(verify_api_key),
):
    """
    Legt einen neuen Outlook-Kontakt an (HBE-979).

    Pflichtfelder: givenName, surname.
    Optional: emailAddresses (max. 3), mobilePhone, businessPhones, jobTitle, companyName, fileAs.

    Unter der Haube: POST https://graph.microsoft.com/v1.0/me/contacts
    HTTP 400 bei leeren Pflichtfeldern oder mehr als 3 E-Mail-Adressen.
    HTTP 503 wenn Token abgelaufen.
    HTTP 502 bei Graph-API-Fehler.
    """
    import requests as _rq

    payload: dict = {
        "givenName": req.givenName,
        "surname": req.surname,
    }
    if req.emailAddresses:
        payload["emailAddresses"] = [
            {"address": email, "name": email} for email in req.emailAddresses
        ]
    if req.mobilePhone is not None:
        payload["mobilePhone"] = req.mobilePhone
    if req.businessPhones:
        payload["businessPhones"] = req.businessPhones
    if req.jobTitle is not None:
        payload["jobTitle"] = req.jobTitle
    if req.companyName is not None:
        payload["companyName"] = req.companyName
    if req.fileAs is not None:
        payload["fileAs"] = req.fileAs
    if req.birthday is not None:
        payload["birthday"] = f"{req.birthday}T00:00:00Z"
    if req.categories:
        payload["categories"] = req.categories

    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    headers = {
        "Authorization": f"Bearer {tool.access_token}",
        "Content-Type": "application/json",
    }

    resp = _rq.post(
        "https://graph.microsoft.com/v1.0/me/contacts",
        headers=headers,
        json=payload,
        timeout=30,
    )

    if resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=502,
            detail=f"Graph API Fehler beim Anlegen: HTTP {resp.status_code} — {resp.text[:300]}",
        )

    data = resp.json()
    return LenaContactCreateResponse(
        success=True,
        contact_id=data.get("id", ""),
        display_name=data.get("displayName", f"{req.givenName} {req.surname}"),
    )


@app.patch("/api/lena/contacts/{contact_id}", response_model=LenaContactUpdateResponse)
def lena_contact_update(
    contact_id: str,
    req: LenaContactUpdateRequest,
    _key: str = Security(verify_api_key),
):
    """
    Aktualisiert Felder eines Outlook-Kontakts via Microsoft Graph (HBE-940, HBE-1067).

    Path-Parameter:
      - contact_id  Outlook-Kontakt-ID (aus /api/lena/contacts/search)

    Body (mind. ein Feld erforderlich):
      - mobilePhone    Mobilnummer
      - businessPhone  Geschäftliche Telefonnummer
      - birthday       Geburtstag (YYYY-MM-DD)
      - categories     Liste von Kategorien (z.B. ["Kunden", "Führungskreis"])

    Unter der Haube: PATCH https://graph.microsoft.com/v1.0/me/contacts/{id}
    HTTP 503 wenn Token abgelaufen, HTTP 404 wenn Kontakt nicht gefunden.
    """
    import requests as _rq

    payload: dict = {}
    if req.mobilePhone is not None:
        payload["mobilePhone"] = req.mobilePhone
    if req.businessPhone is not None:
        payload["businessPhones"] = [req.businessPhone]
    if req.birthday is not None:
        import re
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", req.birthday):
            raise HTTPException(status_code=400, detail="birthday muss im Format YYYY-MM-DD sein.")
        payload["birthday"] = f"{req.birthday}T00:00:00Z"
    if req.categories is not None:
        payload["categories"] = req.categories

    if not payload:
        raise HTTPException(
            status_code=400,
            detail="Mindestens ein Feld (mobilePhone, businessPhone, birthday oder categories) muss angegeben werden.",
        )

    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    headers = {
        "Authorization": f"Bearer {tool.access_token}",
        "Content-Type": "application/json",
    }

    resp = _rq.patch(
        f"https://graph.microsoft.com/v1.0/me/contacts/{contact_id}",
        headers=headers,
        json=payload,
        timeout=30,
    )

    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail=f"Kontakt '{contact_id}' nicht gefunden.")
    if resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=502,
            detail=f"Graph API Fehler beim Aktualisieren: HTTP {resp.status_code} — {resp.text[:300]}",
        )

    return LenaContactUpdateResponse(success=True, contact_id=contact_id)


@app.delete("/api/lena/contacts/{contact_id}", response_model=LenaContactDeleteResponse)
def lena_contact_delete(
    contact_id: str,
    _key: str = Security(verify_api_key),
):
    """
    Löscht einen Outlook-Kontakt via Microsoft Graph (HBE-1067).

    Path-Parameter:
      - contact_id  Outlook-Kontakt-ID (aus /api/lena/contacts/search)

    Unter der Haube: DELETE https://graph.microsoft.com/v1.0/me/contacts/{id}
    HTTP 503 wenn Token abgelaufen, HTTP 404 wenn Kontakt nicht gefunden.
    """
    import requests as _rq

    tool = _get_outlook_tool()
    if not tool.is_authenticated():
        raise HTTPException(status_code=503, detail="Outlook nicht authentifiziert.")

    headers = {
        "Authorization": f"Bearer {tool.access_token}",
    }

    resp = _rq.delete(
        f"https://graph.microsoft.com/v1.0/me/contacts/{contact_id}",
        headers=headers,
        timeout=30,
    )

    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail=f"Kontakt '{contact_id}' nicht gefunden.")
    if resp.status_code not in (200, 201, 204):
        raise HTTPException(
            status_code=502,
            detail=f"Graph API Fehler beim Löschen: HTTP {resp.status_code} — {resp.text[:300]}",
        )

    return LenaContactDeleteResponse(success=True, contact_id=contact_id)


# ---------------------------------------------------------------------------
# Telegram-Bridge Endpoints (HBE-402)
# ---------------------------------------------------------------------------

@app.post("/api/telegram/{slug}/webhook")
async def telegram_agent_webhook(slug: str, req: Request):
    """
    Generischer Telegram-Webhook für alle registrierten PA-Agenten (HBE-1421).

    Auth: X-Telegram-Bot-Api-Secret-Token Header.
    Kein X-API-Key — Telegram ruft diesen Endpoint direkt auf.
    Erstellt ein Paperclip-Issue (Assignee: jeweiliger Agent, Priority: high) pro Nachricht.
    Antwortet immer HTTP 200 damit Telegram den Aufruf nicht wiederholt.

    Slug wird aus der URL entnommen und gegen die _TELEGRAM_AGENTS-Registry aufgelöst.
    Neue PA = 3 neue Env-Vars, kein Code-Change.
    """
    cfg = _TELEGRAM_AGENTS.get(slug)
    if not cfg:
        return {"ok": True}

    incoming_secret = req.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not cfg.webhook_secret:
        logger.warning("[telegram/%s] webhook received but secret not set — rejecting", slug)
        return {"ok": True}
    if not hmac.compare_digest(incoming_secret, cfg.webhook_secret):
        logger.warning(
            "[telegram/%s] webhook auth failure: secret mismatch (client=%s)",
            slug, req.client.host if req.client else "unknown",
        )
        return {"ok": True}

    try:
        body = await req.json()
        update = TelegramUpdate(**body)
    except Exception:
        return {"ok": True}

    # --- callback_query: agent-specific handling ---
    if update.callback_query:
        cq = update.callback_query
        if slug == "lena":
            _tg_agent_ack_callback(cfg.token, cq.id)
            data = cq.data or ""
            chat_id = str(cq.message.chat.id) if cq.message else cfg.admin_chat_id
            # Try speaker-specific callbacks first; fall back to generic inline buttons (HBE-1452)
            if not _handle_speaker_callback(data, chat_id):
                _handle_inline_button_reply(cfg, cq)
        elif slug == "mara":
            sender_id = str(cq.from_.id) if cq.from_ else ""
            if cfg.admin_chat_id and sender_id != cfg.admin_chat_id:
                _tg_mara_answer_callback_query(cq.id)
                logger.warning(
                    "[telegram/mara] callback_query from unexpected sender %s — ignored", sender_id
                )
                return {"ok": True}
            _tg_mara_answer_callback_query(cq.id)
            data = cq.data or ""
            chat_id_cb = str(cq.message.chat.id) if cq.message else cfg.admin_chat_id
            # Post TELEGRAM_CALLBACK comment if callback_data contains issue_id; fall back to generic
            if not _handle_inline_button_reply(cfg, cq):
                _handle_mara_callback(data, chat_id_cb)
        else:
            # New agents: ack + try generic inline button handler
            _tg_agent_ack_callback(cfg.token, cq.id)
            _handle_inline_button_reply(cfg, cq)
        return {"ok": True}

    msg = update.message
    if not msg or not msg.text:
        return {"ok": True}

    user = msg.from_ or _TgUser(id=0)
    username = user.username or user.first_name or "Unbekannt"
    chat_id = str(msg.chat.id)

    # Clean stale/foreign pending_issues.
    # An entry is stale if the issue is done, cancelled, or not found.
    # blocked is NOT stale — user reply must add a comment and reset to in_progress (HBE-1314).
    # An entry is foreign if its assignee differs from the expected agent (guards against
    # stale DB rows routing messages to a wrong agent, causing silent 403s).
    active_issue_id = None
    active_issue_status = None
    with _tg_agent_db(cfg) as db:
        rows = db.execute(
            "SELECT issue_id FROM pending_issues WHERE chat_id = ?", (chat_id,)
        ).fetchall()
        for row in rows:
            status, assignee = _pc_get_issue_info(row["issue_id"])
            is_stale = status is None or status in {"done", "cancelled"}
            is_foreign = bool(cfg.pc_agent_id) and assignee != cfg.pc_agent_id
            if is_stale or is_foreign:
                db.execute("DELETE FROM pending_issues WHERE issue_id = ?", (row["issue_id"],))
                if is_foreign and not is_stale:
                    logger.warning(
                        "[telegram/%s] purged foreign pending_issue %s (assignee=%s, expected=%s)",
                        slug, row["issue_id"], assignee, cfg.pc_agent_id,
                    )
            else:
                active_issue_id = row["issue_id"]
                active_issue_status = status

    # Build quote_block for reply-threading (HBE-1026)
    agent_display = slug.capitalize()
    quote_block = ""
    if msg.reply_to_message:
        replied_id = msg.reply_to_message.message_id
        with _tg_agent_db(cfg) as db:
            row = db.execute(
                "SELECT comment_id, comment_excerpt FROM outbound_messages "
                "WHERE telegram_msg_id = ? AND chat_id = ?",
                (replied_id, chat_id),
            ).fetchone()
        if row:
            excerpt = (row["comment_excerpt"] or "")[:200]
            comment_short = row["comment_id"][:8] if row["comment_id"] else "?"
            ts = msg.reply_to_message.date
            if ts:
                time_label = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M UTC")
            else:
                time_label = datetime.now().strftime("%H:%M")
            quote_block = f'> **Re {agent_display} [Comment {comment_short}, {time_label}]:** „{excerpt}"\n\n'
        elif msg.reply_to_message.text:
            raw = msg.reply_to_message.text[:200]
            quote_block = f'> **Re:** „{raw}"\n\n'

    comment_text = quote_block + (msg.text or "")

    # HBE-2011: If user replied to a known outbound message but no active_issue_id was
    # found via pending_issues (e.g. original issue already done/stale), route to the
    # originating issue via outbound_messages. Ensures Sven's Telegram reply is always
    # threaded back into the correct context instead of spawning a new context-less issue.
    if not active_issue_id and msg.reply_to_message:
        _replied_id = msg.reply_to_message.message_id
        with _tg_agent_db(cfg) as db:
            _origin_row = db.execute(
                "SELECT issue_id FROM outbound_messages WHERE telegram_msg_id = ? AND chat_id = ?",
                (_replied_id, chat_id),
            ).fetchone()
        if _origin_row and _origin_row["issue_id"]:
            _origin_status, _origin_assignee = _pc_get_issue_info(_origin_row["issue_id"])
            _is_foreign = bool(cfg.pc_agent_id) and _origin_assignee != cfg.pc_agent_id
            if not _is_foreign and _origin_status is not None:
                active_issue_id = _origin_row["issue_id"]
                active_issue_status = _origin_status
                logger.info(
                    "[telegram/%s] routed reply to originating issue %s (status=%s) via outbound_messages — HBE-2011",
                    slug, active_issue_id, active_issue_status,
                )
    if active_issue_id:
        # Reset in_review/blocked → in_progress so agent wakes up on user reply.
        # in_review blocks agent wake-up; Telegram reply must always restart (HBE-794).
        # blocked treated the same: user reply signals an unblock (HBE-1314).
        if active_issue_status in {"in_review", "blocked"}:
            _pc_patch_issue_status(active_issue_id, "in_progress")
            logger.info(
                "[telegram/%s] reset issue %s from %s to in_progress on user reply",
                slug, active_issue_id, active_issue_status,
            )
        _pc_add_comment_to_issue(active_issue_id, username, comment_text)
    else:
        # Detect Triage-v2 action-run trigger for Lena only — HBE-1321.
        action_cat = _detect_action_run_category(msg.text or "") if cfg.slug == "lena" else None
        if action_cat:
            issue_id = _pc_create_action_run_issue(
                chat_id=chat_id,
                message_id=msg.message_id,
                username=username,
                category=action_cat,
            )
        else:
            issue_id = _pc_create_tg_issue(cfg, chat_id, msg.message_id, username, comment_text)
        if issue_id:
            # Variant-1 guard: only track issues actually assigned to the expected agent.
            _, created_assignee = _pc_get_issue_info(issue_id)
            if bool(cfg.pc_agent_id) and created_assignee != cfg.pc_agent_id:
                logger.warning(
                    "[telegram/%s] new issue %s not assigned to expected agent (assignee=%s) — not tracking",
                    slug, issue_id, created_assignee,
                )
            else:
                with _tg_agent_db(cfg) as db:
                    db.execute(
                        "INSERT OR REPLACE INTO pending_issues (issue_id, chat_id) VALUES (?, ?)",
                        (issue_id, chat_id),
                    )

    return {"ok": True}


# Backward-compat function aliases (tests call telegram_lena_webhook / telegram_mara_webhook directly)
async def telegram_lena_webhook(req: Request):
    return await telegram_agent_webhook("lena", req)


@app.post("/api/telegram/mara/webhook")
async def telegram_mara_webhook(req: Request):
    """
    Telegram-Webhook für @mara_hberatung_bot (HBE-1205).

    Auth: X-Telegram-Bot-Api-Secret-Token Header.
    Erstellt ein Paperclip-Issue (Assignee: Mara) pro Text-Nachricht von Sven.
    Reply-Threading via outbound_messages in telegram_mara.db.
    """
    incoming_secret = req.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not _TG_MARA_WEBHOOK_SECRET:
        logger.warning("[telegram/mara] webhook received but TELEGRAM_MARA_WEBHOOK_SECRET not set — rejecting.")
        return {"ok": True}
    if not hmac.compare_digest(incoming_secret, _TG_MARA_WEBHOOK_SECRET):
        _client = getattr(req, "client", None)
        logger.warning(
            "[telegram/mara] webhook auth failure: secret mismatch (client=%s)",
            _client.host if _client else "unknown",
        )
        return {"ok": True}

    try:
        body = await req.json()
        update = TelegramUpdate(**body)
    except Exception:
        return {"ok": True}

    if update.callback_query:
        cq = update.callback_query
        # Security: only accept callbacks from the configured admin chat (Sven)
        sender_id = str(cq.from_.id) if cq.from_ else ""
        if _TG_MARA_ADMIN_CHAT_ID and sender_id != _TG_MARA_ADMIN_CHAT_ID:
            _tg_mara_answer_callback_query(cq.id)
            logger.warning(
                "[telegram/mara] callback_query from unexpected sender %s — ignored", sender_id
            )
            return {"ok": True}
        _tg_mara_answer_callback_query(cq.id)
        data = cq.data or ""
        chat_id = str(cq.message.chat.id) if cq.message else _TG_MARA_ADMIN_CHAT_ID
        _handle_mara_callback(data, chat_id)
        return {"ok": True}

    return await telegram_agent_webhook("mara", req)


def _handle_speaker_callback(data: str, chat_id: str) -> bool:
    """
    Process a speaker-question callback_query from Sven.
    Returns True if the callback matched a speaker action, False otherwise.
    """
    # data format: "spkr_pause:HBE-753", "spkr_cont:HBE-753", "spkr_ready:HBE-753"
    parts = data.split(":", 1)
    if len(parts) != 2 or not parts[0].startswith("spkr_"):
        return False
    action, issue_id = parts[0], parts[1]

    if action == "spkr_pause":
        _pc_patch_issue_status(issue_id, "blocked", "awaiting_plaud_update")
        _pc_post_system_comment(issue_id, "TELEGRAM_CALLBACK: speaker_plaud_update")
        fertig_keyboard = {
            "inline_keyboard": [[
                {"text": "✅ Fertig — Transkript neu einlesen", "callback_data": f"spkr_ready:{issue_id}"},
            ]]
        }
        _tg_send_message(
            chat_id,
            f"⏸ {issue_id} wartet auf Plaud-Update.\n"
            "Bitte benenne die Speaker in der Plaud-App und drücke dann Fertig.",
            reply_markup=fertig_keyboard,
        )

    elif action == "spkr_cont":
        _pc_post_system_comment(issue_id, "TELEGRAM_CALLBACK: speaker_continue")
        _tg_send_message(
            chat_id,
            f"▶️ {issue_id}: Protokoll wird mit Platzhaltern für unbekannte Speaker erstellt.",
        )

    elif action == "spkr_ready":
        _pc_patch_issue_status(issue_id, "in_progress")
        _pc_post_system_comment(issue_id, "TELEGRAM_CALLBACK: speaker_ready")
        _tg_send_message(
            chat_id,
            f"✅ {issue_id}: Mara liest das aktualisierte Transkript ein und erstellt das Protokoll neu.",
        )

    else:
        logger.warning("[telegram] unknown speaker callback action: %s", action)
        return False

    return True


def _handle_inline_button_reply(cfg: "_TgAgentCfg", cq: "_TgCallbackQuery") -> bool:
    """
    Generic inline button handler (HBE-1452).
    Looks up the originating Paperclip issue via outbound_messages,
    posts the selected callback_data as a comment, and resets issue status to in_progress.
    Returns True if an issue was found and the comment posted, False otherwise.
    """
    if not cq.message:
        return False
    chat_id = str(cq.message.chat.id)
    msg_id = cq.message.message_id
    callback_data = cq.data or ""
    username = (
        (cq.from_.username or cq.from_.first_name)
        if cq.from_
        else "Unbekannt"
    )

    with _tg_agent_db(cfg) as db:
        row = db.execute(
            "SELECT issue_id FROM outbound_messages WHERE telegram_msg_id = ? AND chat_id = ?",
            (msg_id, chat_id),
        ).fetchone()

    if not row or not row["issue_id"]:
        logger.warning(
            "[telegram/%s] inline button callback: no outbound_message for msg_id=%s chat_id=%s",
            cfg.slug, msg_id, chat_id,
        )
        return False

    issue_id = row["issue_id"]
    comment_body = f"Telegram-Button von @{username}: {callback_data}"

    status = _pc_get_issue_status(issue_id)
    if status in {"in_review", "blocked"}:
        _pc_patch_issue_status(issue_id, "in_progress")
        logger.info(
            "[telegram/%s] inline button: reset issue %s from %s to in_progress",
            cfg.slug, issue_id, status,
        )

    ok = _pc_post_system_comment(issue_id, comment_body)
    logger.info(
        "[telegram/%s] inline button posted to %s (data=%r, ok=%s)",
        cfg.slug, issue_id, callback_data, ok,
    )
    return ok


def _handle_mara_callback(data: str, chat_id: str) -> None:
    """Post a Mara callback_query result as a system comment on the referenced Paperclip issue.

    Expected data format: "{action}:{issue_id}:{value}" or "{action}:{issue_id}"
    e.g. "speaker:HBE-1258:dragan" or "approve:HBE-1300"
    The Mara agent reads the TELEGRAM_CALLBACK comment and acts on it.
    """
    parts = data.split(":", 2)
    if len(parts) < 2 or not parts[1]:
        logger.warning("[telegram/mara] malformed callback data, skipping: %r", data)
        return
    action = parts[0]
    issue_id = parts[1]
    value = parts[2] if len(parts) > 2 else ""
    comment = f"TELEGRAM_CALLBACK: {action}={value}" if value else f"TELEGRAM_CALLBACK: {action}"
    _pc_post_system_comment(issue_id, comment)
    logger.info("[telegram/mara] callback action=%s issue=%s value=%r → comment posted", action, issue_id, value)


@app.post("/api/telegram/{slug}/send", response_model=LenaTelegramSendResponse)
def telegram_agent_send(
    slug: str,
    req: LenaTelegramSendRequest,
    _key: str = Security(verify_api_key),
):
    """
    Generischer Telegram-Send-Endpoint für alle registrierten PA-Agenten (HBE-1421).
    Erfordert X-API-Key. Rate-Limit: 10 Calls/Min pro Agent (HBE-1212 Flood-Schutz).
    Trackt outbound_messages für Reply-Threading.
    """
    cfg = _TELEGRAM_AGENTS.get(slug)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Telegram-Agent '{slug}' nicht konfiguriert.")
    if not cfg.token:
        raise HTTPException(status_code=503, detail=f"TELEGRAM_AGENT_{slug.upper()}_TOKEN nicht konfiguriert.")
    if not _tg_rate_check(slug):
        raise HTTPException(
            status_code=429,
            detail=f"Rate-Limit überschritten: max. {_TG_RATE_LIMIT} Telegram-Sends pro Minute ({slug}).",
            headers={"Retry-After": "60"},
        )
    sent_msg_id = _tg_agent_send(
        cfg.token, req.chat_id, req.text,
        reply_markup=req.reply_markup, parse_mode=req.parse_mode,
    )
    # Always track in outbound_messages (HBE-1212: auch ohne issue_id für retroaktive Flood-Analyse)
    if sent_msg_id:
        import json as _json
        _btn_opts = None
        if req.reply_markup:
            try:
                _btn_opts = _json.dumps(req.reply_markup, ensure_ascii=False)
            except Exception:
                pass
        with _tg_agent_db(cfg) as db:
            db.execute(
                "INSERT OR REPLACE INTO outbound_messages "
                "(telegram_msg_id, chat_id, issue_id, comment_id, comment_excerpt, button_options) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sent_msg_id, req.chat_id, req.issue_id or "", req.comment_id or "", req.text[:200], _btn_opts),
            )
    return LenaTelegramSendResponse(success=bool(sent_msg_id), telegram_msg_id=sent_msg_id)


# ---------------------------------------------------------------------------
# Backward-compat send aliases — agents have these URLs hardcoded in their
# Instructions. Route via the generic handler so tracking/rate-limiting is shared.
# ---------------------------------------------------------------------------

@app.post("/api/lena/telegram/send", response_model=LenaTelegramSendResponse)
def lena_telegram_send(
    req: LenaTelegramSendRequest,
    _key: str = Security(verify_api_key),
):
    """Backward-compat alias: delegates to POST /api/telegram/lena/send."""
    return telegram_agent_send("lena", req, _key="")


@app.post("/api/mara/telegram/send", response_model=LenaTelegramSendResponse)
def mara_telegram_send(
    req: LenaTelegramSendRequest,
    _key: str = Security(verify_api_key),
):
    """Backward-compat alias: delegates to POST /api/telegram/mara/send."""
    return telegram_agent_send("mara", req, _key="")


@app.post("/api/telegram/speaker-question", response_model=SimpleResult)
def telegram_speaker_question(
    req: SpeakerQuestionRequest,
    _key: str = Security(verify_api_key),
):
    """
    Mara ruft diesen Endpoint auf wenn SKILL_SPEAKER unbekannte Speaker nicht
    aufloesen konnte. Schickt eine strukturierte Telegram-Nachricht mit zwei
    Quick-Reply-Buttons an TELEGRAM_ADMIN_CHAT_ID (Sven).

    Erfordert X-API-Key und konfiguriertes TELEGRAM_ADMIN_CHAT_ID.
    """
    if not _TG_BOT_TOKEN:
        raise HTTPException(status_code=503, detail="TELEGRAM_BOT_TOKEN nicht konfiguriert.")
    if not _TG_ADMIN_CHAT_ID:
        raise HTTPException(
            status_code=503,
            detail="TELEGRAM_ADMIN_CHAT_ID nicht konfiguriert — Speaker-Fragen koennen nicht gesendet werden.",
        )

    speakers_str = ", ".join(req.unknown_speakers)
    text = (
        f"🎙️ Unbekannte Speaker in {req.issue_id}\n"
        f"Termin: {req.meeting_name}\n"
        f"Unklar: {speakers_str}\n\n"
        "Was soll ich tun?"
    )
    keyboard = {
        "inline_keyboard": [[
            {
                "text": "🔄 In Plaud ergänzen",
                "callback_data": f"spkr_pause:{req.issue_id}",
            },
            {
                "text": "▶️ Weitermachen ohne",
                "callback_data": f"spkr_cont:{req.issue_id}",
            },
        ]]
    }
    message_id = _tg_send_message(_TG_ADMIN_CHAT_ID, text, reply_markup=keyboard)
    if not message_id:
        raise HTTPException(status_code=502, detail="Telegram sendMessage fehlgeschlagen.")

    with _telegram_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO speaker_questions (message_id, issue_id) VALUES (?, ?)",
            (message_id, req.issue_id),
        )

    return SimpleResult(
        success=True,
        message=f"Speaker-Frage fuer {req.issue_id} an Sven gesendet (TG message_id={message_id}).",
    )


@app.get("/api/telegram/speaker-fallback-config")
def telegram_speaker_fallback_config(
    _key: str = Security(verify_api_key),
):
    """
    Gibt den konfigurierten MARA_SPEAKER_FALLBACK_DEFAULT-Wert zurueck.
    Mara liest diesen beim Start des SKILL_SPEAKER, um das Default-Verhalten zu bestimmen.

    Werte: ask (Default) | continue | pause
    """
    return {"fallback_default": _MARA_SPEAKER_FALLBACK_DEFAULT}


# ---------------------------------------------------------------------------
# Vault-Sync-API (HBE-757) — Lena liest + schreibt Svens Obsidian-Vault
# ---------------------------------------------------------------------------

_VAULT_MIRROR_PATH = Path(os.getenv("VAULT_MIRROR_PATH", "/opt/vault-mirror")).resolve()
_VAULT_BOT_TOKEN   = os.getenv("GITHUB_BOT_TOKEN", "").strip()
_VAULT_AUDIT_LOG   = Path(os.getenv("VAULT_AUDIT_LOG", "/app/data/vault-lena.log"))
_VAULT_GITHUB_REPO = "https://github.com/herbertgruppe/vault-memory.git"

# Pfad-Whitelist: prefix → Zugriffsart ('full' | 'append_only')
# append_only: create auf neue Files OK, append OK, overwrite immer → 403
_VAULT_WRITE_WHITELIST: dict = {
    "05 Daily Notes/":         "append_only",  # Sven-Bereich schützen: kein overwrite (HBE-757)
    "09 Lena Inbox/":          "full",
    "01 Inbox/":               "full",
    "04 Ressourcen/Personen/": "append_only",
}

# GIT_ASKPASS helper — token is passed via GIT_PUSH_TOKEN env var, never embedded in URL
import stat as _stat
_VAULT_ASKPASS_DIR  = Path(tempfile.mkdtemp(prefix="vault-askpass-"))
_VAULT_ASKPASS_PATH = _VAULT_ASKPASS_DIR / "askpass.sh"
_VAULT_ASKPASS_PATH.write_text(
    "#!/bin/sh\n"
    "case \"$1\" in\n"
    "  *sername*) printf 'x-access-token' ;;\n"
    "  *assword*) printf '%s' \"$GIT_PUSH_TOKEN\" ;;\n"
    "esac\n",
    encoding="ascii",
)
_VAULT_ASKPASS_PATH.chmod(_stat.S_IRWXU)


def _vault_auth_env() -> dict:
    """Liefert env-Variablen für git-Operationen die den Token benötigen."""
    return {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": str(_VAULT_ASKPASS_PATH),
        "GIT_PUSH_TOKEN": _VAULT_BOT_TOKEN,
    }


def _vault_resolve(path: str) -> Path:
    """Normalisiert und validiert den Vault-Pfad gegen path-traversal."""
    path = path.replace("\\", "/").lstrip("/")
    resolved = (_VAULT_MIRROR_PATH / path).resolve()
    if not str(resolved).startswith(str(_VAULT_MIRROR_PATH) + "/") and resolved != _VAULT_MIRROR_PATH:
        raise HTTPException(status_code=400, detail="Ungültiger Pfad: path traversal erkannt.")
    return resolved


def _vault_check_write_access(path: str) -> str:
    """Gibt den Schreibmodus zurück oder wirft 403."""
    path = path.replace("\\", "/").lstrip("/")
    for prefix, mode in _VAULT_WRITE_WHITELIST.items():
        if path.startswith(prefix):
            return mode
    allowed = list(_VAULT_WRITE_WHITELIST.keys())
    raise HTTPException(
        status_code=403,
        detail=f"Schreibzugriff auf '{path}' nicht erlaubt. Erlaubte Pfade: {allowed}",
    )


def _vault_run_git(args: list, extra_env: Optional[dict] = None) -> subprocess.CompletedProcess:
    env = {**os.environ}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["git", "-c", f"safe.directory={_VAULT_MIRROR_PATH}"] + args,
        cwd=str(_VAULT_MIRROR_PATH),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def _vault_push_to_origin() -> str:
    """Pusht nach GitHub via GIT_ASKPASS (token nie in URL oder Prozessliste)."""
    if not _VAULT_BOT_TOKEN:
        return "GITHUB_BOT_TOKEN nicht konfiguriert — Push übersprungen."
    result = _vault_run_git(
        ["push", _VAULT_GITHUB_REPO, "master"],
        extra_env=_vault_auth_env(),
    )
    if result.returncode != 0:
        return f"Push fehlgeschlagen: {result.stderr[:300]}"
    return ""


def _vault_init_mirror() -> None:
    """Initialisiert den Vault-Mirror beim API-Start falls noch nicht geklont."""
    if not _VAULT_BOT_TOKEN:
        logger.info("[vault] GITHUB_BOT_TOKEN nicht gesetzt — Mirror-Init übersprungen.")
        return
    if (_VAULT_MIRROR_PATH / ".git").exists():
        return
    logger.info(f"[vault] Klone {_VAULT_GITHUB_REPO} nach {_VAULT_MIRROR_PATH} …")
    _VAULT_MIRROR_PATH.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **_vault_auth_env()}
    result = subprocess.run(
        ["git", "clone", _VAULT_GITHUB_REPO, str(_VAULT_MIRROR_PATH)],
        capture_output=True, text=True, timeout=120, env=env,
    )
    if result.returncode != 0:
        logger.error(f"[vault] Clone fehlgeschlagen: {result.stderr[:200]}")
        return
    subprocess.run(["git", "-C", str(_VAULT_MIRROR_PATH), "-c", f"safe.directory={_VAULT_MIRROR_PATH}", "config", "user.name", "mein-assistent-bot"], check=False)
    subprocess.run(["git", "-C", str(_VAULT_MIRROR_PATH), "-c", f"safe.directory={_VAULT_MIRROR_PATH}", "config", "user.email", "bot@herbertgruppe.com"], check=False)
    inbox = _VAULT_MIRROR_PATH / "09 Lena Inbox"
    if not inbox.exists():
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / ".gitkeep").touch()
    logger.info("[vault] Mirror initialisiert.")


def _vault_pull_from_origin() -> None:
    """Zieht Svens neue Commits (alle 2 Min via APScheduler)."""
    if not (_VAULT_MIRROR_PATH / ".git").exists():
        return
    if not _VAULT_BOT_TOKEN:
        return
    try:
        auth = _vault_auth_env()
        fetch = _vault_run_git(["fetch", _VAULT_GITHUB_REPO, "master", "--quiet"], extra_env=auth)
        if fetch.returncode == 0:
            merge = _vault_run_git(["merge", "--ff-only", "FETCH_HEAD"])
            if merge.returncode != 0:
                logger.warning(f"[vault] ff-only merge fehlgeschlagen (lokale Commits vorhanden?): {merge.stderr[:200]}")
    except Exception as exc:
        logger.warning(f"[vault] pull fehlgeschlagen: {exc}")


def _vault_audit(path: str, mode: str, commit_sha: str, status: str) -> None:
    try:
        _VAULT_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        with _VAULT_AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{ts}\t{status}\t{mode}\t{commit_sha or '-'}\t{path}\n")
    except Exception:
        pass


class VaultWriteRequest(BaseModel):
    path: str = Field(..., description="Relativer Pfad im Vault, z.B. '05 Daily Notes/2026-06-12.md'")
    content: str = Field(..., description="Dateiinhalt (Markdown)")
    mode: Literal["create", "append", "overwrite"] = Field(..., description="create | append | overwrite")
    commit_message: str = Field(..., description="Git-Commit-Message")

    @field_validator("path")
    @classmethod
    def _no_traversal(cls, v: str) -> str:
        if ".." in v:
            raise ValueError("Pfad darf '..' nicht enthalten.")
        return v.strip()

    @field_validator("commit_message")
    @classmethod
    def _prefix_lena(cls, v: str) -> str:
        v = v.strip()
        return v if v.startswith("[Lena]") else f"[Lena] {v}"


class VaultWriteResponse(BaseModel):
    status: str
    commit_sha: str
    path: str


class VaultReadResponse(BaseModel):
    path: str
    content: str
    exists: bool


@app.get("/api/lena/vault/read", response_model=VaultReadResponse)
def lena_vault_read(
    path: str = Query(..., description="Relativer Pfad im Vault"),
    _key: str = Security(verify_api_key),
) -> VaultReadResponse:
    """Liest eine Datei aus dem Vault-Mirror (kein Schreibzugriff nötig)."""
    if not _VAULT_MIRROR_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail=f"Vault-Mirror nicht gefunden ({_VAULT_MIRROR_PATH}). Phase 1 (Hetzner-Setup) abschließen.",
        )
    target = _vault_resolve(path)
    if not target.exists():
        return VaultReadResponse(path=path, content="", exists=False)
    return VaultReadResponse(path=path, content=target.read_text(encoding="utf-8"), exists=True)


@app.post("/api/lena/vault/write", response_model=VaultWriteResponse)
def lena_vault_write(
    req: VaultWriteRequest,
    _key: str = Security(verify_api_key),
) -> VaultWriteResponse:
    """
    Schreibt eine Datei in den Vault-Mirror, commitet und pusht via Git.

    Pfad-Whitelist:
      05 Daily Notes/         — append-only (kein overwrite; Sven-Bereich schützen)
      09 Lena Inbox/          — voll
      01 Inbox/               — voll
      04 Ressourcen/Personen/ — append-only (kein overwrite)
    """
    if not _VAULT_MIRROR_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail=f"Vault-Mirror nicht gefunden ({_VAULT_MIRROR_PATH}). Phase 1 (Hetzner-Setup) abschließen.",
        )

    access_mode = _vault_check_write_access(req.path)

    if access_mode == "append_only" and req.mode == "overwrite":
        _vault_audit(req.path, req.mode, "", "REJECTED_OVERWRITE_APPEND_ONLY")
        raise HTTPException(
            status_code=403,
            detail="'overwrite' ist auf append-only Pfaden nicht erlaubt. Nutze mode='append' oder 'create'.",
        )

    target = _vault_resolve(req.path)
    target.parent.mkdir(parents=True, exist_ok=True)

    if req.mode == "create":
        if target.exists():
            raise HTTPException(
                status_code=409,
                detail=f"Datei existiert bereits: '{req.path}'. Nutze mode='overwrite' oder 'append'.",
            )
        target.write_text(req.content, encoding="utf-8")
    elif req.mode == "append":
        if target.exists():
            existing = target.read_text(encoding="utf-8")
            sep = "\n" if existing and not existing.endswith("\n") else ""
            target.write_text(existing + sep + req.content, encoding="utf-8")
        else:
            target.write_text(req.content, encoding="utf-8")
    else:  # overwrite
        target.write_text(req.content, encoding="utf-8")

    lena_env = {
        "GIT_AUTHOR_NAME": "Lena (HBE)",
        "GIT_AUTHOR_EMAIL": "lena@herbertgruppe.com",
        "GIT_COMMITTER_NAME": "Lena (HBE)",
        "GIT_COMMITTER_EMAIL": "lena@herbertgruppe.com",
    }

    add_result = _vault_run_git(["add", str(target)])
    if add_result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"git add fehlgeschlagen: {add_result.stderr[:200]}")

    commit_result = _vault_run_git(
        ["commit", "--author=Lena (HBE) <lena@herbertgruppe.com>", "-m", req.commit_message],
        extra_env=lena_env,
    )
    if commit_result.returncode != 0:
        _vault_audit(req.path, req.mode, "", "COMMIT_FAILED")
        raise HTTPException(status_code=500, detail=f"git commit fehlgeschlagen: {commit_result.stderr[:300]}")

    sha_result = _vault_run_git(["rev-parse", "--short", "HEAD"])
    commit_sha = sha_result.stdout.strip() if sha_result.returncode == 0 else "unknown"

    push_error = _vault_push_to_origin()
    if push_error:
        _vault_audit(req.path, req.mode, commit_sha, f"PUSH_FAILED")
        raise HTTPException(
            status_code=502,
            detail=f"Commit OK (SHA {commit_sha}), aber Push fehlgeschlagen: {push_error}",
        )

    _vault_audit(req.path, req.mode, commit_sha, "OK")
    return VaultWriteResponse(status="ok", commit_sha=commit_sha, path=req.path)

