# TOOLS.md — Lena API-Referenz

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
