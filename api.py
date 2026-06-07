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
import logging
import os
import re
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Tuple

import requests as _http
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field, field_validator

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
    if key != _API_SECRET_KEY:
        raise HTTPException(status_code=403, detail="Ungültiger API Key")
    return key


# ---------------------------------------------------------------------------
# Telegram-Bridge (HBE-402)
# ---------------------------------------------------------------------------
_TG_BOT_TOKEN        = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
_TG_WEBHOOK_SECRET   = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
_PC_API_URL          = os.getenv("PAPERCLIP_API_URL_MA", "https://paperclip.herbertgruppe.com").strip()
_PC_API_KEY          = os.getenv("PAPERCLIP_API_KEY_MA", "").strip()
_PC_COMPANY_ID       = os.getenv("PAPERCLIP_COMPANY_ID_MA", "").strip()
_PC_LENA_AGENT_ID    = os.getenv("PAPERCLIP_LENA_AGENT_ID", "").strip()

if os.getenv("TELEGRAM_BOT_TOKEN") and not os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip():
    raise RuntimeError(
        "TELEGRAM_WEBHOOK_SECRET muss gesetzt sein wenn TELEGRAM_BOT_TOKEN konfiguriert ist. "
        "Ohne das Secret ist der Webhook für beliebige Caller offen. "
        "Generierung: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
    )

_TELEGRAM_DB_PATH = Path(__file__).resolve().parent / "data" / "telegram.db"


@contextmanager
def _telegram_db():
    """SQLite context manager for Telegram bridge state."""
    _TELEGRAM_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_TELEGRAM_DB_PATH))
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
    conn.commit()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _tg_send_message(chat_id: str, text: str) -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    if not _TG_BOT_TOKEN:
        return False
    try:
        resp = _http.post(
            f"https://api.telegram.org/bot{_TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        return resp.ok
    except _http.exceptions.RequestException as exc:
        # Use type name only — str(exc) would include the full URL with the BOT_TOKEN
        logger.warning("[telegram] sendMessage failed: %s", type(exc).__name__)
        return False
    except Exception:
        logger.warning("[telegram] sendMessage failed: unexpected error", exc_info=False)
        return False


def _pc_create_issue(chat_id: str, message_id: int, username: str, text: str) -> Optional[str]:
    """Create a high-priority Paperclip issue assigned to Lena. Returns issue ID or None."""
    if not (_PC_API_URL and _PC_API_KEY):
        logger.warning("[telegram] PAPERCLIP_API_KEY_MA not set — skipping Paperclip issue creation")
        return None
    short = text[:50] + ("…" if len(text) > 50 else "")
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
                "title": f"Telegram von {username}: {short}",
                "description": description,
                "assigneeAgentId": _PC_LENA_AGENT_ID,
                "priority": "high",
            },
            headers={"Authorization": f"Bearer {_PC_API_KEY}"},
            timeout=15,
        )
    except _http.exceptions.RequestException as exc:
        logger.warning("[telegram] Paperclip request error: %s", type(exc).__name__)
        return None
    except Exception:
        logger.warning("[telegram] Paperclip request error: unexpected error", exc_info=False)
        return None
    if resp.status_code in (200, 201):
        return resp.json().get("id")
    logger.warning("[telegram] Paperclip issue creation failed: %s %s", resp.status_code, resp.text[:300])
    return None


def _poll_telegram_replies() -> None:
    """
    APScheduler background job (every 60 s).
    Scans Paperclip comments on tracked issues for TELEGRAM_REPLY: prefix
    and forwards the reply text to Telegram.
    """
    if not (_TG_BOT_TOKEN and _PC_API_URL and _PC_API_KEY):
        return
    with _telegram_db() as db:
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
            logger.error("[telegram] comment fetch error for %s: %s", issue_id, exc)
            continue

        if resp.status_code == 404:
            # Issue no longer exists — stop tracking it
            with _telegram_db() as db:
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
            with _telegram_db() as db:
                if db.execute(
                    "SELECT 1 FROM processed_comments WHERE comment_id = ?", (cid,)
                ).fetchone():
                    continue
            reply_text = body[len("TELEGRAM_REPLY:"):].strip()
            if _tg_send_message(chat_id, reply_text):
                with _telegram_db() as db:
                    db.execute(
                        "INSERT OR IGNORE INTO processed_comments (comment_id) VALUES (?)", (cid,)
                    )


# ── APScheduler lifespan hooks ────────────────────────────────────────────────
_tg_scheduler = None


@app.on_event("startup")  # type: ignore[attr-defined]
def _start_tg_scheduler() -> None:
    global _tg_scheduler
    if _TG_BOT_TOKEN and _PC_API_KEY:
        from apscheduler.schedulers.background import BackgroundScheduler
        _tg_scheduler = BackgroundScheduler(daemon=True)
        _tg_scheduler.add_job(_poll_telegram_replies, "interval", seconds=60, id="tg_poll")
        _tg_scheduler.start()
        logger.info("[telegram] reply-poll scheduler started (60 s interval)")


@app.on_event("shutdown")  # type: ignore[attr-defined]
def _stop_tg_scheduler() -> None:
    if _tg_scheduler and _tg_scheduler.running:
        _tg_scheduler.shutdown(wait=False)


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

    model_config = {"populate_by_name": True}


class TelegramUpdate(BaseModel):
    update_id: int
    message: Optional[_TgMsg] = None


class TelegramSendRequest(BaseModel):
    chat_id: str = Field(..., description="Telegram Chat-ID (Empfänger)")
    text: str = Field(..., description="Nachrichtentext")


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
    _key: str = Security(verify_api_key),
):
    """
    Liefert Outlook-Kalender-Termine für ein Datum oder einen Zeitraum.

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
        "$select": "id,subject,from,receivedDateTime,isRead,importance,bodyPreview,hasAttachments",
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

    Checks well-known alias table first; falls back to GET /me/mailFolders query by displayName.
    Raises HTTPException 404 when the folder cannot be found.
    """
    import requests as _rq

    alias = _WELL_KNOWN_FOLDER_MAP.get(target_folder.strip().lower())
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


@app.post("/api/lena/mail/move", response_model=LenaMoveMailResponse)
def lena_mail_move(
    req: LenaMoveMailRequest,
    _key: str = Security(verify_api_key),
):
    """
    Verschiebt eine Mail in einen Outlook-Ordner (HBE-607).

    Unterstützt well-known Ordner-Aliase (Archive/Archiv, Deleted Items/Papierkorb,
    Junk Email/Spam, Inbox/Posteingang) sowie beliebige Custom-Folder per displayName.
    Implementierung: PATCH /me/messages/{id} mit parentFolderId.
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

    resp = _rq.patch(
        f"https://graph.microsoft.com/v1.0/me/messages/{req.message_id}",
        headers=headers,
        json={"parentFolderId": folder_id},
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=502,
            detail=f"Graph API Fehler beim Verschieben: HTTP {resp.status_code} — {resp.text[:300]}",
        )

    return LenaMoveMailResponse(
        success=True,
        message_id=req.message_id,
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


# ---------------------------------------------------------------------------
# Telegram-Bridge Endpoints (HBE-402)
# ---------------------------------------------------------------------------

@app.post("/api/telegram/lena/webhook")
async def telegram_lena_webhook(req: Request):
    """
    Telegram-Webhook für @HBE_Lena_bot.

    Auth: X-Telegram-Bot-Api-Secret-Token Header (gesetzt beim Webhook-Register).
    Kein X-API-Key — Telegram ruft diesen Endpoint direkt auf.
    Erstellt ein Paperclip-Issue (Assignee: Lena, Priority: high) pro Text-Nachricht.
    Antwortet immer HTTP 200 damit Telegram den Aufruf nicht wiederholt.
    """
    incoming_secret = req.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not _TG_WEBHOOK_SECRET:
        logger.warning("Telegram webhook received but TELEGRAM_WEBHOOK_SECRET is not set — rejecting.")
        return {"ok": True}
    if not hmac.compare_digest(incoming_secret, _TG_WEBHOOK_SECRET):
        logger.warning(
            "Telegram webhook auth failure: secret mismatch (client=%s)",
            req.client.host if req.client else "unknown",
        )
        return {"ok": True}

    try:
        body = await req.json()
        update = TelegramUpdate(**body)
    except Exception:
        return {"ok": True}

    msg = update.message
    if not msg or not msg.text:
        return {"ok": True}

    user = msg.from_ or _TgUser(id=0)
    username = user.username or user.first_name or "Unbekannt"
    chat_id = str(msg.chat.id)

    issue_id = _pc_create_issue(
        chat_id=chat_id,
        message_id=msg.message_id,
        username=username,
        text=msg.text,
    )
    if issue_id:
        with _telegram_db() as db:
            db.execute(
                "INSERT OR REPLACE INTO pending_issues (issue_id, chat_id) VALUES (?, ?)",
                (issue_id, chat_id),
            )

    return {"ok": True}


@app.post("/api/telegram/lena/send", response_model=SimpleResult)
def telegram_lena_send(
    req: TelegramSendRequest,
    _key: str = Security(verify_api_key),
):
    """
    Interne Schnittstelle: sendet eine Telegram-Nachricht an einen bestimmten Chat.
    Erfordert X-API-Key. Wird von Lena (via Paperclip-Skill) oder manuell aufgerufen.
    """
    if not _TG_BOT_TOKEN:
        raise HTTPException(status_code=503, detail="TELEGRAM_BOT_TOKEN nicht konfiguriert.")
    if not _tg_send_message(req.chat_id, req.text):
        raise HTTPException(status_code=502, detail="Telegram sendMessage fehlgeschlagen.")
    return SimpleResult(success=True, message=f"Nachricht an Chat {req.chat_id} gesendet.")
