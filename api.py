"""
FastAPI REST-Endpunkt für den Meeting-Protokoll-Workflow der Herbert Gruppe.

Wird vom Cowork-Skill `meeting-protokoll` aufgerufen, sobald Sven ein Protokoll
review't und nach `01 Inbox/Überarbeitete Protokolle/` verschoben hat.

Aufgaben des Endpunkts:
  1. PDF aus Markdown generieren (Herbert-Blau #1F4E79) — oder pdf_base64 nutzen
  2. PDF an Outlook-Termin anhängen
  3. Kategorie „Protokoll" am Outlook-Termin setzen
  4. Betreff-Prefix „📄 " am Outlook-Termin setzen

Auth:    X-API-Key Header (API_SECRET_KEY aus .env)
Port:    8502 (loopback, hinter nginx mit /api/-Proxy)
Token:   /app/auth/outlook_token.json (Docker-Volume `auth`)

Lokaler Start:
    uvicorn api:app --host 127.0.0.1 --port 8502 --reload
"""
import base64
import os
import re
import tempfile
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Security,
)
from fastapi.responses import HTMLResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from database.protocols_db import ProtocolsDB

load_dotenv()


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
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# protocols.db beim API-Start initialisieren (führt Migration automatisch aus)
_protocols_db = ProtocolsDB(db_path=str(_BASE_DIR / "data" / "protocols.db"))


def get_authenticated_user(
    x_authentik_username: Optional[str] = Header(None),
    x_forwarded_email: Optional[str] = Header(None),
    api_key: Optional[str] = Header(None, alias="X-API-Key"),
    token: Optional[str] = Query(None),
) -> str:
    """
    Dual-Auth für Browser-Endpoints (/review/, /api/asana/, /api/calendar/events).

    Akzeptiert (in dieser Reihenfolge):
      1. Authentik-Session — nginx injiziert X-Authentik-Username / X-Forwarded-Email
      2. X-API-Key (für Skill-/Skript-Zugriffe)
      3. Gültiger, nicht abgelaufener Reviewer-Token als ?token= Query-Param
         (review.js sendet den Token bei allen Dropdown-Calls mit)
    """
    if x_authentik_username:
        return x_authentik_username
    if x_forwarded_email:
        return x_forwarded_email
    if api_key and _API_SECRET_KEY and api_key == _API_SECRET_KEY:
        return "api-client"
    if token:
        protocol = _protocols_db.get_by_token(token)
        if protocol and not ProtocolsDB.is_expired(protocol):
            return "reviewer"
    raise HTTPException(status_code=401, detail="Unauthorized")


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
    margin: 2cm 2cm;
    @bottom-right {
        content: "Seite " counter(page) " / " counter(pages);
        font-family: Arial, Helvetica, sans-serif;
        font-size: 9pt;
        color: #888;
    }
}
body {
    font-family: Arial, Helvetica, sans-serif;
    font-size: 11pt;
    line-height: 1.55;
    color: #222;
}
h1 {
    color: #1F4E79;
    font-size: 18pt;
    border-bottom: 2px solid #1F4E79;
    padding-bottom: 6px;
    margin-top: 0;
    margin-bottom: 16px;
}
h2 {
    color: #1F4E79;
    font-size: 14pt;
    margin-top: 22px;
    margin-bottom: 10px;
    border-bottom: 1px solid #cdd9e8;
    padding-bottom: 4px;
}
h3 {
    color: #2e5d8c;
    font-size: 12pt;
    margin-top: 16px;
    margin-bottom: 8px;
}
p { margin: 6px 0; }
table {
    border-collapse: collapse;
    width: 100%;
    margin-bottom: 14px;
    font-size: 10pt;
}
th, td {
    border: 1px solid #cdd9e8;
    padding: 6px 9px;
    text-align: left;
    vertical-align: top;
}
th {
    background: #1F4E79;
    color: #ffffff;
    font-weight: bold;
}
tr:nth-child(even) td { background: #f3f7fc; }
ul, ol { margin: 6px 0 12px 22px; }
li { margin-bottom: 3px; }
strong { color: #1a3a5c; }
em { color: #444; }
hr {
    border: none;
    border-top: 1px solid #cdd9e8;
    margin: 18px 0;
}
code {
    background: #f0f5fb;
    padding: 1px 4px;
    border-radius: 3px;
    font-family: 'Courier New', monospace;
    font-size: 10pt;
}
blockquote {
    border-left: 3px solid #1F4E79;
    margin: 10px 0 10px 0;
    padding: 4px 14px;
    color: #555;
    background: #f6f9fc;
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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    """Health-Check — kein Auth erforderlich."""
    return {
        "status": "ok",
        "service": "mein-assistent-api",
        "version": "1.1.0",
        "api_key_configured": bool(_API_SECRET_KEY),
    }


# ---------------------------------------------------------------------------
# Transkripte (Outlook-Subfolder)
# ---------------------------------------------------------------------------
# Konfigurierbar via .env, Default: "Transkripte" unter Posteingang
_TRANSCRIPTS_FOLDER_NAME = os.getenv("TRANSCRIPTS_FOLDER_NAME", "Transkripte")
_TRANSCRIPTS_PARENT = os.getenv("TRANSCRIPTS_PARENT_FOLDER", "inbox")


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
        transcripts.append(
            PendingTranscript(
                message_id=msg.get("id", ""),
                subject=msg.get("subject", "") or "",
                received_at=msg.get("receivedDateTime", "") or "",
                sender_name=sender.get("name"),
                sender_email=sender.get("address"),
                body_preview=msg.get("bodyPreview", "") or "",
                body_text=body_obj.get("content", "") or "",
                has_attachments=bool(msg.get("hasAttachments")),
                attachments=attachments_meta,
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
class CalendarEvent(BaseModel):
    id: str
    title: str
    start: str
    end: str
    location: str = ""
    attendees: List[str] = []
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
        events.append(
            CalendarEvent(
                id=ev.get("id", ""),
                title=ev.get("title", "") or "",
                start=start_str,
                end=end_str,
                location=ev.get("location", "") or "",
                attendees=[a for a in (ev.get("attendees") or []) if a],
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
            pdf_generated = _markdown_to_pdf_standalone(req.markdown, pdf_path)
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

    markdown_text = protocol["current_markdown"]
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

    if allow_api_key and _API_SECRET_KEY and allow_api_key == _API_SECRET_KEY:
        return protocol

    if not token:
        raise HTTPException(status_code=401, detail="Token fehlt (?token=...)")
    if token != protocol["reviewer_token"]:
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
