# REST-API Setup (`api.py`)

FastAPI-Endpunkt für den Meeting-Protokoll-Workflow. Wird vom Cowork-Skill
`meeting-protokoll` aufgerufen, sobald ein überarbeitetes Protokoll fertig ist.

## Endpoint

```
POST /api/process-reviewed-protocol
GET  /api/health    (kein Auth)
```

## Auth

`X-API-Key` Header. Key in `.env` als `API_SECRET_KEY` setzen.

```bash
# Schlüssel generieren
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Request

```json
{
  "markdown": "# Protokoll …",
  "meeting_name": "Führungskreis",
  "event_id": "AAMkAGI…",
  "asana_gid": "1234567890",
  "pdf_base64": null
}
```

- `markdown` – Pflicht, vollständiger Protokoll-Text
- `meeting_name` – Pflicht, für Dateinamen
- `event_id` – Outlook-Event-ID (aus Frontmatter `kalender_event`).
  Wenn leer, werden Outlook-Schritte übersprungen.
- `asana_gid` – reserviert, aktuell nicht verwendet
- `pdf_base64` – optional. Wenn gesetzt, wird die Markdown→PDF-Konvertierung
  übersprungen und das übergebene PDF direkt benutzt.

## Response

```json
{
  "success": true,
  "pdf_generated": true,
  "outlook_attachment": true,
  "outlook_category": true,
  "outlook_subject_prefix": true,
  "errors": [],
  "message": "OK"
}
```

## Was passiert intern

1. PDF aus Markdown generieren (Herbert-Blau `#1F4E79`, Arial, A4 mit Seitenzahlen)
2. PDF an Outlook-Termin anhängen
3. Kategorie „Protokoll" am Outlook-Termin setzen
4. Betreff-Prefix `📄 ` am Outlook-Termin setzen

## Deployment

### 1. `.env` ergänzen

```dotenv
API_SECRET_KEY=<generierter-key>
```

### 2. Container starten

```bash
ssh root@46.225.132.135 -i ~/.ssh/umfragetool
cd /opt/mein-assistent
git pull
docker compose up -d --build
docker compose logs --tail=30 api
```

### 3. Nginx-Config

Auf dem Server (`/etc/nginx/sites-available/mein-assistent` oder analog) **vor**
dem bestehenden `location /` Block einfügen:

```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8502;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # FastAPI braucht keine WebSocket-Upgrades
    proxy_read_timeout 120s;
    proxy_send_timeout 120s;

    # Authentik-Forward-Auth NICHT für /api/ — eigene API-Key-Auth
    # (falls auth_request für Streamlit gesetzt ist, hier explizit deaktivieren)
    auth_request off;
}
```

Aktivieren:

```bash
nginx -t && systemctl reload nginx
```

### 4. Test

```bash
# Health (kein Auth)
curl https://mein-assistent.herbertgruppe.com/api/health

# Endpoint (mit Auth)
curl -X POST https://mein-assistent.herbertgruppe.com/api/process-reviewed-protocol \
  -H "X-API-Key: $API_SECRET_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "markdown": "# Test-Protokoll\n\nDies ist ein Test.",
    "meeting_name": "Test-Meeting"
  }'
```

Erwartete Antwort:

```json
{
  "success": true,
  "pdf_generated": true,
  "outlook_attachment": null,
  "outlook_category": null,
  "outlook_subject_prefix": null,
  "errors": [],
  "message": "OK"
}
```

(Ohne `event_id` werden Outlook-Schritte übersprungen — `success: true` heißt
nur „PDF wurde generiert".)

## Cowork-Skill anbinden

Der Skill `meeting-protokoll` ruft den Endpunkt am Ende von Teil 2 auf:

```python
import os, requests, base64

API_KEY = os.environ["MEIN_ASSISTENT_API_KEY"]   # geheim
API_URL = "https://mein-assistent.herbertgruppe.com/api/process-reviewed-protocol"

# Optional: PDF lokal mit reportlab erstellen und Base64-encoded mitsenden
pdf_b64 = base64.b64encode(pdf_path.read_bytes()).decode()

resp = requests.post(
    API_URL,
    headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
    json={
        "markdown": protocol_md,
        "meeting_name": meeting_name,
        "event_id": outlook_event_id,
        "asana_gid": asana_board_gid,
        "pdf_base64": pdf_b64,   # optional — sonst generiert die API selbst
    },
    timeout=60,
)
resp.raise_for_status()
result = resp.json()
```

## Outlook-Token

`OutlookGraphTool` lädt das Token aus `/app/auth/outlook_token.json` (gemountet
über das `auth`-Volume). Der `api`-Service teilt sich das Volume mit dem
`assistent`-Service — d.h. Login einmal in der Streamlit-UI reicht aus, der
Token wird beim Refresh automatisch erneuert.

Falls der Token abgelaufen ist und Refresh fehlschlägt, antwortet die API mit
`outlook_*: null` und einer entsprechenden `errors`-Meldung. Dann kurz in
`mein-assistent.herbertgruppe.com` einloggen → Token wird erneuert.

## Bekannte Einschränkungen

- **Single-User**: aktuell ist das Token-File hartkodiert auf
  `/app/auth/outlook_token.json` (Sven). Multi-User würde eine Erweiterung
  benötigen, bei der der API-Call auch den Username mitschickt.
- **Kein Rate-Limit**: nginx `limit_req` ggf. ergänzen, falls die API später
  öffentlich gemacht werden soll.
- **Keine Asana-Verarbeitung**: Asana-Aufgaben werden vom Cowork-Skill direkt
  angelegt; das `asana_gid`-Feld ist nur zur späteren Nutzung reserviert.

---

## Attendee-Semantik (`/api/calendar/events`)

Hauptkonsument: Cowork-Skill `meeting-protokoll` (Teil 1: Termin-Zuordnung
zum Transkript). Diese Sektion erklärt, wie der Pro-Person-Status der
Attendees zu interpretieren ist — Halluzinationen entstehen, wenn die
Felder als „Anwesenheits-Wahrheit" missverstanden werden.

### Response-Form

```json
{
  "id": "...",
  "title": "BL-Besprechung HRN",
  "start": "2026-05-21T09:00:00.0000000",
  "end": "2026-05-21T10:00:00.0000000",
  "location": "...",
  "attendees": [
    {"name": "Thomas Winzer", "email": "thomas.winzer@…", "response": "accepted",  "type": "required"},
    {"name": "Sven Herbert",  "email": "sven@…",          "response": "organizer", "type": "required"},
    {"name": "Frank Herbert", "email": "frank@…",         "response": "declined",  "type": "required"}
  ],
  "attendee_names": ["Thomas Winzer", "Sven Herbert", "Frank Herbert"],
  "preview": "…",
  "is_all_day": false
}
```

### Felder

- `response` ∈ `accepted` | `declined` | `tentative` | `notResponded` |
  `none` | `organizer` — MS-Graph-Werte 1:1 durchgereicht; `organizer`
  ist ergänzt für den Termin-Organisator.
- `type` ∈ `required` | `optional` | `resource` — MS-Graph-Werte
  (`attendeeType`).
- `email` ist neu und hilft bei Namens-Kollisionen.
- `attendee_names` ist eine flache String-Liste (Backwards-Compat für
  ältere Konsumenten). Neue Konsumenten sollen `attendees[].name`
  verwenden.

### Bedeutung (für Skills, Protokoll-Generierung etc.)

Outlook-Attendees sind **eingeladene** Personen — NICHT zwangsläufig
**anwesende**:

| `response`     | Bedeutung                                                              |
|----------------|------------------------------------------------------------------------|
| `accepted`     | hat zugesagt — KEIN Beweis der Anwesenheit                             |
| `declined`     | hat abgesagt — sicher **NICHT** anwesend → aus Teilnehmer-Liste ausschließen |
| `tentative`    | „vorläufig" zugesagt — Anwesenheit ungewiss                            |
| `notResponded` | hat noch nicht reagiert                                                |
| `none`         | kein Status verfügbar                                                  |
| `organizer`    | Termin-Organisator                                                     |

**Wichtig:** Für vergangene Termine liefert MS Graph `responseStatus`
häufig `none` oder leer. Das ist **kein Bug**, sondern Graph-Verhalten.
In diesem Fall ist der **Transkript-Sprecher** die Primärquelle für die
tatsächliche Anwesenheit; die Outlook-Einladung dient nur als
Hintergrund-Information.

### Empfehlung für Konsumenten

1. Default-Teilnehmer-Liste = `attendees` ohne Einträge mit
   `response == "declined"`.
2. Wenn das Transkript einen Sprecher nennt, der nicht in
   `attendees` ist, gilt das Transkript — **nicht** die Einladung
   ergänzen. Niemals eingeladene Personen als anwesend behandeln,
   wenn sie im Transkript nicht vorkommen.
3. `attendee_names` nur zur reinen Anzeige verwenden — alle
   semantischen Entscheidungen über die strukturierte `attendees`-Liste
   treffen.

### Hintergrund

Die Pro-Person-Struktur wurde mit
[HBE-274](/HBE/issues/HBE-274) eingeführt, nachdem Outlook-Einladungen
zu Halluzinationen in BL-HRN-Protokollen geführt hatten (eingeladene,
aber nicht anwesende Personen wurden als Sprecher protokolliert). Siehe
auch Parent-Issue [HBE-273](/HBE/issues/HBE-273).
