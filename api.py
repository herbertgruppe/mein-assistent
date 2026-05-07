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
from fastapi import FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

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
