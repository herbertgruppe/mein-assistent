---
name: "SKILL_MEETING_OPERATIONS"
description: "Outlook-Kalender-CRUD für Lena: Termin anlegen, verschieben, löschen, Teilnehmer einladen, Anhänge anhängen, freie Slots finden. Wird geladen bei Titeln wie Termin, Meeting, Kalender, verschieben, einladen."
trigger:
  title_patterns:
    - "Termin"
    - "Meeting"
    - "Kalender"
    - "verschieben"
    - "einladen"
    - "Slot"
    - "Besprechung"
  telegram_patterns:
    - "Termin"
    - "Meeting"
    - "Kalender"
    - "verschieben"
    - "einladen"
    - "Slot"
    - "Besprechung"
---

# SKILL_MEETING_OPERATIONS — Lena Outlook-Kalender-CRUD

## Wann dieses Skill geladen wird

- `issue_assigned` mit Titel-Match auf: **Termin, Meeting, Kalender, verschieben, einladen, Slot, Besprechung**
- Telegram-Nachricht von Sven mit den gleichen Schlüsselwörtern

---

## Workflow: Schritt-für-Schritt

### Schritt 1 — Extraktion aus Svens Nachricht

Aus der Nachricht herausarbeiten:
- **Aktion**: anlegen / verschieben / löschen / Slot suchen / Teilnehmer verwalten / Anhang
- **Personen**: Name(n) + E-Mail(s) (ggf. via `/api/lena/contacts/search?q=...` nachschlagen)
- **Zeitraum / Datum + Uhrzeit**
- **Betreff / Thema**
- **Ort** (falls genannt)
- **Anhang** (falls Datei erwähnt)

Bei Unklarheiten: kurze Rückfrage per Telegram, nicht raten.

---

### Schritt 2 — Aktion „Slot suchen"

1. `POST /api/lena/calendar/find-free-slot` mit allen Teilnehmern, Dauer und Zeitfenster
2. Bis zu 3 Vorschläge als Telegram-Nachricht an Sven:
   ```
   Freie Slots für [Personen]:
   1. Mo 14.07. 09:00–10:30 Uhr
   2. Di 15.07. 14:00–15:30 Uhr
   3. Mi 16.07. 10:00–11:30 Uhr
   Welchen nehme ich?
   ```
3. Auf Svens Auswahl warten → weiter mit „Termin anlegen" (Schritt 3).

---

### Schritt 3 — Aktion „Termin anlegen"

**Approval-Gate (Pflicht):**
Telegram-Vorschau an Sven, bevor der API-Call erfolgt:

```
Termin-Entwurf:
• Betreff: [subject]
• Datum: [start] – [end] ([timezone])
• Ort: [location]
• Teilnehmer: [name1 <email1>, name2 <email2>]
• Kategorien: [categories]
• Anhang: [filename oder —]

Anlegen? Ja / Nein
```

Nach Svens „Ja":
1. `POST /api/lena/calendar/events` → erhält `event_id`, `web_link`
2. Falls Anhang vorhanden: `POST /api/lena/calendar/events/{event_id}/attachments`
3. Bestätigung per Telegram:
   ```
   ✅ Termin angelegt: [subject]
   [web_link]
   ```

---

### Schritt 4 — Aktion „Termin verschieben"

1. Alten Termin suchen: `GET /api/calendar/events?date=...` oder nach `event_id` fragen
2. Telegram-Vorschau:
   ```
   Termin verschieben:
   • [subject]
   • Alt: [alter Start] – [altes Ende]
   • Neu: [neuer Start] – [neues Ende]
   • Teilnehmer benachrichtigen: Ja

   OK?
   ```
3. Nach Freigabe: `PATCH /api/lena/calendar/events/{event_id}`
4. Bestätigung per Telegram.

---

### Schritt 5 — Aktion „Termin löschen"

1. Termin identifizieren (Subject + Datum zur Sicherheit anzeigen)
2. Telegram-Vorschau:
   ```
   Termin löschen:
   • [subject] am [start]
   • Absagen an Teilnehmer: Ja

   Löschen? Ja / Nein
   ```
3. Nach Freigabe: `DELETE /api/lena/calendar/events/{event_id}?send_cancellations=true`
4. Bestätigung per Telegram.

---

### Schritt 6 — Aktion „Teilnehmer einladen / entfernen"

1. Aktuellen Termin und Teilnehmerliste anzeigen
2. Änderungen per Telegram bestätigen lassen
3. `POST /api/lena/calendar/events/{event_id}/attendees`
4. Bestätigung per Telegram.

---

## Approval-Gate-Disziplin

Analog SKILL_EMAIL.md:

- **Jede schreibende Kalender-Aktion** (anlegen, ändern, löschen, Teilnehmer, Anhang) benötigt eine
  explizite Sven-Freigabe per Telegram, bevor der API-Call ausgeführt wird.
- Freigabe-Tokens: „Ja", „Ok", „Mach es", „Go", „👍" → Aktion ausführen.
- Ablehnung: „Nein", „Stop", „❌", „Abbrechen" → Aktion verwerfen, kurz bestätigen.
- Timeout (>30 Minuten ohne Antwort): Aktion **nicht** ausführen, Status-Note im Daily.

---

## Transparenz-Pflicht

Analog SKILL_DAILY_NOTE.md:

- Jede Kalender-Schreib-Aktion wird im Vault Daily Note dokumentiert:
  ```
  ## Kalender
  - [HH:MM] Termin angelegt: "[subject]" am [date] mit [attendees]
  - [HH:MM] Termin verschoben: "[subject]" → [new_date]
  ```
- Bei Fehlern (HTTP 502, 503, Scope-Problem): Sofort per Telegram melden + Detail im Daily.

---

## Scope-Hinweis

Die Endpoints benötigen `Calendars.ReadWrite` auf dem Microsoft Graph Token.
Dieser Scope ist in der App-Konfiguration enthalten. Falls beim ersten CRUD-Aufruf
HTTP 403 „Insufficient privileges" zurückkommt: Sven muss einen neuen Device-Flow-Login
durchführen (`authenticate_outlook.py`), damit der Token mit dem Schreib-Scope erstellt wird.

---

## API-Referenz (Kurzform)

Alle Endpoints: `X-Api-Key: {MEIN_ASSISTENT_API_KEY}` — vollständige Doku in TOOLS.md.

| Aktion | Methode | Pfad |
|---|---|---|
| Termin anlegen | POST | `/api/lena/calendar/events` |
| Termin aktualisieren | PATCH | `/api/lena/calendar/events/{event_id}` |
| Termin löschen | DELETE | `/api/lena/calendar/events/{event_id}?send_cancellations=true` |
| Teilnehmer verwalten | POST | `/api/lena/calendar/events/{event_id}/attendees` |
| Anhang hochladen | POST | `/api/lena/calendar/events/{event_id}/attachments` |
| Freien Slot finden | POST | `/api/lena/calendar/find-free-slot` |
| Termine lesen | GET | `/api/calendar/events?date=YYYY-MM-DD` |
