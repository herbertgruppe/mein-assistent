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

## 3. Fehlerbehandlung

| Situation | Verhalten |
|---|---|
| Outlook nicht erreichbar | Issue auf `blocked`, Kommentar mit Fehler |
| Kein passender Termin gefunden | Kommentar mit Liste verfuegbarer Termine, Sven-Rueckfrage |
| Unbekannte Speaker | Gemaess `SKILL_SPEAKER.md` (Telegram-Buttons oder Auto-Fallback) |
| Plaud-CLI nicht verfuegbar | Warnung in Issue-Kommentar, Re-Pull uebersprungen |
| LLM-Fehler bei Protokoll | Fehlermeldung in Issue-Kommentar, Status `blocked` |

---

## 4. Umgebungs-Konfiguration

Relevante Env-Variablen fuer den Intake-Workflow:

| Variable | Bedeutung |
|---|---|
| `MARA_SPEAKER_FALLBACK_DEFAULT` | Default-Verhalten bei unbekannten Sprechern (`ask`/`continue`/`pause`) |
| `TELEGRAM_ADMIN_CHAT_ID` | Svens Chat-ID fuer Speaker-Rueckfragen |
| `TELEGRAM_BOT_TOKEN` | Bot-Token fuer Telegram-Nachrichten |

Siehe auch: `.env.example` und `SKILL_SPEAKER.md`.
