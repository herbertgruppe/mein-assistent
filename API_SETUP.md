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
