# SKILL_INTAKE — Eingang und Verarbeitung von Plaud-Transkripten

Dieses Skill-Dokument beschreibt den Eingangs-Workflow fuer neue
Plaud-Aufnahmen die Mara per E-Mail (via `lena_imap_poller`) erhaelt.

---

## 1. Eingang: E-Mail mit Transkript-Anhang

Der IMAP-Poller (`lena_imap_poller.py`) erkennt eingehende E-Mails mit
Plaud-Transkripten (Stichworte: "transkript", "plaud"; Endungen: .txt, .docx)
und erstellt automatisch ein Paperclip-Issue (Priority: high, Assignee: Mara).

---

## 2. Verarbeitung: Meeting-Protokoll-Pipeline

Wenn Mara ein Plaud-Transkript-Issue aufnimmt, laeuft folgende Pipeline:

### Schritt 1: Outlook-Termin zuordnen
- `GET /api/calendar/events?date=YYYY-MM-DD` mit dem Meeting-Datum
  aus dem Transkript-Betreff oder dem E-Mail-Eingangszeitpunkt
- Passenden Termin identifizieren (Titel-Match oder Zeitraum-Ueberschneidung)

### Schritt 2: Sprecher-Aufloesung (SKILL_SPEAKER)
- Attendee-Liste des Termins als Grundlage fuer Speaker-Matching
- Unbekannte Speaker: gemaess `SKILL_SPEAKER.md` vorgehen

**Wichtig:** Mara blockiert die Pipeline NICHT lautlos.
Wenn Speaker unklar sind, wird Sven via Telegram informiert (Abschnitt 2 in
`SKILL_SPEAKER.md`). Das Issue landet nie stumm im `blocked`-Status ohne
dass Sven eine Handlungs-Option erhalten hat.

### Schritt 3: Protokoll generieren
- LLM-Protokoll aus Transkript erstellen
- Wenn unbekannte Speaker vorhanden und `continue`-Pfad: Platzhalter
  und `Klaerung noetig`-Sektion hinzufuegen (siehe SKILL_SPEAKER.md, Abschnitt 3)
- PDF generieren via WeasyPrint

### Schritt 4: Review-Link erstellen
- `POST /api/protocols/draft` mit Protokoll-Markdown
- Review-URL an Sven senden

### Schritt 5: Asana-Export (nach Protokoll-Freigabe)
- Tasks aus Protokoll extrahieren und in Asana anlegen
- Bei unbekannten Sprechern: Subtasks mit "noch zu klaeren"-Assignee

---

## 3. Plaud-Aufnahme abbrechen (Cancel-Schutz)

Wenn ein Plaud-Issue (Origin: `plaud:` oder `plaud-poller`) auf `cancelled` gesetzt wird
(z. B. weil die Aufnahme zu kurz oder unbeabsichtigt war), **muss Mara sofort danach**
den folgenden API-Call ausführen, damit der plaud_poller die Aufnahme dauerhaft überspringt:

```
POST /api/plaud/cancel
X-API-Key: <API_SECRET_KEY>
Content-Type: application/json

{
  "recording_id": "<32-Hex-ID aus der Issue-Beschreibung>",
  "issue_identifier": "<HBE-XXXX>"
}
```

**Wichtig:**
- Die `recording_id` steht in der Beschreibung des Plaud-Issues unter **Recording-ID**.
- Dieser Call ist idempotent — mehrfache Ausführung ist unschädlich.
- Ohne diesen Call kann der plaud_poller die Aufnahme beim nächsten Lauf erneut
  als „neu" erkennen und ein weiteres Issue anlegen → Re-Pull-Loop.
- Der Call schlägt fehl wenn `API_SECRET_KEY` nicht gesetzt ist → dann Fehler in
  Issue-Kommentar dokumentieren und auf `blocked` setzen.

**Ablauf bei Cancel:**
1. Paperclip-Issue auf `cancelled` setzen (`PATCH /api/issues/{id}`)
2. Sofort danach: `POST /api/plaud/cancel` mit `recording_id` und `issue_identifier`
3. Kurzen Kommentar im Issue hinterlassen: „Aufnahme als cancelled markiert — plaud_poller
   wird diese Aufnahme bei künftigen Läufen überspringen."

---

## 4. Telegram-Send-Regeln (Anti-Flood, HBE-1212)

**Genau EINE Telegram-Nachricht pro Aktion.** Niemals den Send-Endpoint mehrfach pro
Heartbeat aufrufen. Die folgenden Regeln sind verbindlich:

1. **Vollständige Nachricht puffern** — vor dem Aufruf von
   `POST /api/lena/telegram/send` oder `POST /api/mara/telegram/send`
   den gesamten Nachrichtentext in einer einzigen Variable aufbauen.
   Niemals Token-für-Token oder Abschnitt-für-Abschnitt senden.

2. **Maximal 3 Send-Calls pro Heartbeat** — falls mehr als 3 Telegram-Nachrichten
   nötig scheinen, in einer einzigen zusammenführen (durch Zeilenumbrüche getrennt).

3. **Kein Re-Send nach Fehler im gleichen Heartbeat** — wenn `success: false`
   zurückkommt, im Issue-Kommentar dokumentieren und auf `blocked` setzen.
   Nicht im gleichen Run erneut versuchen.

4. **Rate-Limit beachten** — der Endpoint gibt HTTP 429 zurück wenn mehr als
   10 Calls/Minute eingehen. Bei 429: sofort stoppen, Issue auf `blocked` setzen,
   Kommentar mit Grund hinterlassen.

**Erlaubte Send-Punkte** (maximal einer pro Issue-Verarbeitung):
- Schritt 4: Review-Link an Sven (eine Nachricht)
- SKILL_SPEAKER: Speaker-Rueckfrage (eine strukturierte Nachricht via `POST /api/telegram/speaker-question`)
- Fehler-Alert: wenn Pipeline-Fehler Sven-Aktion erfordert (eine Nachricht)

---

## 5. Fehlerbehandlung

| Situation | Verhalten |
|---|---|
| Outlook nicht erreichbar | Issue auf `blocked`, Kommentar mit Fehler |
| Kein passender Termin gefunden | Kommentar mit Liste verfuegbarer Termine, Sven-Rueckfrage |
| Unbekannte Speaker | Gemaess `SKILL_SPEAKER.md` (Telegram-Buttons oder Auto-Fallback) |
| Plaud-CLI nicht verfuegbar | Warnung in Issue-Kommentar, Re-Pull uebersprungen |
| LLM-Fehler bei Protokoll | Fehlermeldung in Issue-Kommentar, Status `blocked` |
| `POST /api/plaud/cancel` fehlgeschlagen | Fehler in Issue-Kommentar, Issue trotzdem auf `cancelled` lassen; Sven informieren dass Re-Pull-Schutz nicht aktiv ist |

---

## 6. Umgebungs-Konfiguration

Relevante Env-Variablen fuer den Intake-Workflow:

| Variable | Bedeutung |
|---|---|
| `MARA_SPEAKER_FALLBACK_DEFAULT` | Default-Verhalten bei unbekannten Sprechern (`ask`/`continue`/`pause`) |
| `TELEGRAM_ADMIN_CHAT_ID` | Svens Chat-ID fuer Speaker-Rueckfragen |
| `TELEGRAM_BOT_TOKEN` | Bot-Token fuer Telegram-Nachrichten |

Siehe auch: `.env.example` und `SKILL_SPEAKER.md`.
