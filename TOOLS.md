# TOOLS.md — Lena API-Referenz

> Zuletzt aktualisiert: 2026-06-13 (HBE-788 — Kalender-CRUD hinzugefügt)

Alle Endpoints erfordern den Header `X-Api-Key: {MEIN_ASSISTENT_API_KEY}`.
Bei abgelaufenem Outlook-Token: HTTP 503 `"Outlook nicht authentifiziert."`.

---

## Mail

### Posteingang lesen
```
GET /api/lena/mail/inbox?limit=20&folder=inbox&unread_only=false
```
Response: `{ "count": N, "messages": [ { "message_id", "subject", "from_name", "from_email", "received_at", "is_read", "importance", "body_preview", "has_attachments" } ] }`

### Entwurf erstellen
```
POST /api/lena/mail/draft
{ "to": [{"name":"...", "email":"..."}], "cc": [], "subject": "...", "body_html": "...", "body_text": "...", "reply_to_message_id": null }
```
Response: `{ "draft_id", "subject", "created_at" }`

### Mail versenden
```
POST /api/lena/send-mail
{ "to": [{"name":"...", "email":"..."}], "subject": "...", "body_text": "...", "body_html": null, "reply_to": null }
```
Response: `{ "success": true, "message_id": "..." }`

### Mail verschieben
```
POST /api/lena/mail/move
{ "message_id": "...", "target_folder": "Archive" }
```
Unterstützte Ordner: `inbox`, `drafts`, `sentitems`, `deleteditems`, `junkemail`, `archive` (DE + EN-Varianten) sowie beliebige Custom-Folder per displayName.
Response: `{ "success": true, "message_id": "...", "folder": "..." }`

### Mail als gelesen markieren
```
POST /api/lena/mail/mark-read
{ "message_id": "..." }
```
Response: `{ "success": true }`

---

## Kontakte

### Adressbuch durchsuchen
```
GET /api/lena/contacts/search?q={suchbegriff}
```
Sucht in Svens Outlook-Adressbuch. Primäre Suche via People API, Fallback via Contacts API.

- `q` — Suchbegriff (Name, E-Mail oder Firma; max. 100 Zeichen)

Response:
```json
{
  "contacts": [
    { "name": "Jannik Lorenz", "email": "jannik.lorenz@mytga.de", "title": "Projektleiter", "company": "myTGA" }
  ]
}
```

Leeres Array wenn keine Treffer. HTTP 400 wenn `q` zu lang. HTTP 503 bei abgelaufenem Token.

---

## Kalender lesen (bestehend)

### Termine lesen
```
GET /api/calendar/events?date=YYYY-MM-DD
GET /api/calendar/events?start=ISO&end=ISO&include_all_day=false
```
Response: `{ "date", "start", "end", "count", "events": [{ "id", "title", "start", "end", "location", "attendees", "attendee_names", "preview", "is_all_day" }] }`

Auth: Authentik-Session ODER X-Api-Key.

---

## Kalender-CRUD (HBE-788)

Auth: `X-Api-Key` (wie alle Lena-Endpoints). Vor jedem Schreib-Call Sven-Approval per Telegram einholen (SKILL_MEETING_OPERATIONS.md).

### Termin anlegen
```
POST /api/lena/calendar/events
{
  "subject": "...",
  "start": "2026-07-15T09:00:00",
  "end": "2026-07-15T10:30:00",
  "timezone": "Europe/Berlin",
  "location": "...",
  "body_html": "<p>...</p>",
  "attendees": [{"email": "...", "name": "...", "type": "required"}],
  "categories": ["M&A"],
  "is_online_meeting": false
}
```
Response: `{ "event_id", "web_link", "ical_uid" }`

### Termin aktualisieren / verschieben
```
PATCH /api/lena/calendar/events/{event_id}
{
  "start": "...",        // optional
  "end": "...",          // optional
  "subject": "...",      // optional
  "location": "...",     // optional
  "body_html": "...",    // optional
  "timezone": "...",     // optional, Default: Europe/Berlin
  "send_updates": true   // Teilnehmer per Mail benachrichtigen
}
```
Response: `{ "event_id", "subject", "start", "end" }`

### Termin löschen
```
DELETE /api/lena/calendar/events/{event_id}?send_cancellations=true
```
Response: `{ "success": true, "message": "..." }`

### Teilnehmer hinzufügen / entfernen
```
POST /api/lena/calendar/events/{event_id}/attendees
{
  "add":    [{"email": "...", "name": "...", "type": "required"}],
  "remove": ["email@example.com"]
}
```
Response: `{ "event_id", "attendees_count" }`

### Anhang an Termin hängen
```
POST /api/lena/calendar/events/{event_id}/attachments
Content-Type: multipart/form-data
  file: <binary>
  filename: "Dokument.pdf"    (optional)
  content_type: "application/pdf"  (optional)
```
Max. 3 MB. Response: `{ "attachment_id", "name", "size" }`

### Freien Slot finden (Multi-Personen)
```
POST /api/lena/calendar/find-free-slot
{
  "attendees": ["sven.herbert@herbert.de", "letschert@klw-gmbh.de"],
  "duration_minutes": 90,
  "earliest": "2026-07-14T08:00:00",
  "latest": "2026-07-18T18:00:00",
  "timezone": "Europe/Berlin",
  "working_hours_only": true
}
```
Response: `{ "slots": [{"start", "end", "score"}], "meeting_time_suggestions_result": "..." }`
Gibt bis zu 3 Vorschläge zurück, sortiert nach Confidence-Score.
