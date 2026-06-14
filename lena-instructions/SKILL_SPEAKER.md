# SKILL_SPEAKER — Sprecher-Aufloesung fuer Plaud-Transkripte

Dieses Skill-Dokument beschreibt wie Mara unbekannte Plaud-Speaker-Labels
(z. B. "Speaker 4", "Speaker 5") aufloest und was bei fehlgeschlagenem
Abgleich passiert.

---

## 1. Ablauf: Sprecher-Abgleich

Beim Verarbeiten eines Plaud-Transkripts fuer ein Meeting-Protokoll:

1. **Outlook-Termin-Attendees laden** — Mara holt die Teilnehmerliste des
   passenden Outlook-Termins via `GET /api/calendar/events`.

2. **Speaker-Matching** — Mara versucht jeden Plaud-Speaker-Label dem
   naechstpassenden Attendee zuzuordnen (Fuzzy-Match auf Name/Vorname).

3. **Wenn alle Speaker aufgeloest**: Protokoll-Generierung laeuft normal weiter.

4. **Wenn unbekannte Speaker verbleiben**: weiter gemaess Abschnitt 2.

---

## 2. Fallback bei unbekannten Sprechern

Das Verhalten wird durch die Env-Variable `MARA_SPEAKER_FALLBACK_DEFAULT`
gesteuert. Mara liest den aktuellen Wert via:

```
GET /api/telegram/speaker-fallback-config
Authorization: X-API-Key: <API_SECRET_KEY>
```

Antwort: `{"fallback_default": "ask" | "continue" | "pause"}`

### 2a. `ask` (Default — empfohlen)

Mara sendet eine strukturierte Telegram-Rueckfrage an Sven:

```
POST /api/telegram/speaker-question
Authorization: X-API-Key: <API_SECRET_KEY>
{
  "issue_id": "<ISSUE_ID>",
  "meeting_name": "<TERMIN_TITEL>",
  "unknown_speakers": ["Speaker 4", "Speaker 7"]
}
```

Sven sieht in Telegram eine Nachricht mit zwei Buttons:
- **🔄 In Plaud ergaenzen** — Sven benennt Speaker in Plaud-App nach
- **▶️ Weitermachen ohne** — Protokoll mit Platzhaltern erstellen

Mara setzt ihren Paperclip-Issue-Status entsprechend (siehe 2b/2c) und
wartet auf Svens Entscheidung. Die Entscheidung kommt als strukturierter
Kommentar auf dem Issue an (automatisch durch den Telegram-Webhook).

**Warten auf Entscheidung:** Mara pollt den Issue alle 60 Sekunden auf
einen Kommentar mit Prafix `TELEGRAM_CALLBACK: speaker_`.

### 2b. `pause` — automatisch blockieren

Mara setzt Issue auf `blocked` mit Grund `awaiting_plaud_update` und
sendet keinen Telegram-Prompt. Sven muss manuell "fertig" melden oder
den Issue entsperren.

### 2c. `continue` — automatisch weitermachen

Mara generiert das Protokoll sofort ohne Telegram-Frage. Unbekannte
Speaker werden als Platzhalter eingesetzt (Abschnitt 3).

---

## 3. Option "Weitermachen ohne" — Protokoll mit Platzhaltern

Wenn `TELEGRAM_CALLBACK: speaker_continue` auf dem Issue erscheint
(oder `MARA_SPEAKER_FALLBACK_DEFAULT=continue`):

### 3a. Speaker-Platzhalter

Jeder unbekannte Speaker im Transkript wird ersetzt durch:

```
Unbekannter Sprecher (Plaud Speaker X)
```

wobei X der originale Plaud-Label ist (z. B. "Plaud Speaker 4").

### 3b. Klaerungssektion am Protokoll-Ende

Am Ende des Protokolls wird eine eigene Sektion angehaengt:

```markdown
---

## Klaerung noetig

Folgende Sprecher konnten nicht dem Outlook-Termin zugeordnet werden:

- **Plaud Speaker 4** — Identitaet ungeklaert
- **Plaud Speaker 7** — Identitaet ungeklaert

Bitte Identitaet nachtraeglich klaeren und Protokoll aktualisieren.
```

### 3c. Asana-Subtask

Fuer jede Aufgabe eines unbekannten Sprechers wird in Asana ein Subtask
angelegt mit:
- Name: `[Zuweisung klaren] <Aufgaben-Titel>`
- Assignee: leer (noch zu klaeren)
- Notes: "Aufgabe von unbekanntem Sprecher (Plaud Speaker X) — Zuweisung klaren."

---

## 4. Option "In Plaud ergaenzen" — Re-Pull nach Plaud-Update

Wenn `TELEGRAM_CALLBACK: speaker_plaud_update` auf dem Issue erscheint:

1. Mara stellt den Issue-Status auf `blocked` / `awaiting_plaud_update`.
2. Sven benennt Speaker in der Plaud-App.
3. Sven drueckt den "✅ Fertig"-Button in Telegram.
4. Der Webhook postet `TELEGRAM_CALLBACK: speaker_ready` auf dem Issue.
5. Mara erkennt das Signal, setzt Issue auf `in_progress` und fuehrt durch:
   a. Transkript neu von Plaud-CLI einlesen
   b. Sprecher-Abgleich erneut ausfuehren (ab Schritt 1)
   c. Protokoll generieren

**Plaud-CLI Re-Pull** (sofern `/paperclip/.plaud/tokens.json` vorhanden):
Mara ruft das Plaud-CLI-Tool auf um das aktualisierte Transkript zu laden.

---

## 5. Konfiguration per Termin-Typ

Die Env-Variable `MARA_SPEAKER_FALLBACK_DEFAULT` setzt den globalen Default.
Fuer einzelne Termin-Typen kann Sven im Protokoll-Prompt explizit einen
Fallback-Modus angeben:

| Termin-Typ | Empfohlener Default |
|---|---|
| Sprint Review | `pause` (Speaker-Profile lohnen sich) |
| Ad-hoc Meetings | `continue` (schnelle Protokolle wichtiger) |
| Grosse Besprechungen | `ask` (Sven entscheidet je nach Relevanz) |

---

## 6. Telegram-Callback-Signale (fuer Mara)

| Kommentar auf Issue | Bedeutung |
|---|---|
| `TELEGRAM_CALLBACK: speaker_continue` | Sven hat "Weitermachen ohne" gewaehlt |
| `TELEGRAM_CALLBACK: speaker_plaud_update` | Sven hat "In Plaud ergaenzen" gewaehlt |
| `TELEGRAM_CALLBACK: speaker_ready` | Sven hat "Fertig" nach Plaud-Update gedrueckt |

Diese Kommentare werden automatisch durch den Telegram-Webhook in `api.py`
gesetzt — Mara muss nur darauf reagieren, nicht selbst setzen.
