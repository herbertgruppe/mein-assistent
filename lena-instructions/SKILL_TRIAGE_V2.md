# SKILL_TRIAGE_V2 — On-Demand Per-Kategorie Action Runner

Implementierung von HBE-1320. Beschreibt wie Lena auf Svens Telegram-Trigger reagiert
und die jeweilige Kategorie sequenziell abarbeitet.

---

## 1. Trigger-Erkennung

Wenn Sven per Telegram schreibt:

| Sven schreibt (Varianten) | Kategorie |
|---|---|
| "Lena, Ablegen" / "lena ablegen" / "ablegen starten" | `ablegen` |
| "Lena, Weiterleiten" / "weiterleiten abarbeiten" | `weiterleiten` |
| "Lena, Tun" / "tun abarbeiten" | `tun` |
| "Lena, Antworten" / "antworten abarbeiten" | `antworten` |
| "Lena, Warten" / "warten abarbeiten" | `warten` |
| "Lena, Recherchieren" / "recherchieren abarbeiten" | `recherchieren` |

**Erkennungs-Pattern (Python-Regex):**
```python
import re
TRIGGER_PATTERN = re.compile(
    r'(?i)\b(?:lena[,\s]+)?(ablegen|weiterleiten|tun|antworten|warten|recherchieren)\b'
)
```

Matcht auch ohne "Lena," wenn das Wort alleine steht ("ablegen starten").
Gross-/Kleinschreibung egal.

---

## 2. Vorgehen bei erkanntem Trigger

### Schritt 1 — Mails abrufen
```
POST /api/lena/mail/action-run
X-API-Key: <API_SECRET_KEY>
Content-Type: application/json

{ "category": "<kategorie-key>" }
```

Response: `{ mails: [...], category: "Lena: Weiterleiten", mail_count: N }`

Wenn `mail_count == 0`:
- Telegram-Antwort: "Keine Mails mit Kategorie 'Lena: [Kategorie]' im Posteingang. 👍"
- Kein Issue anlegen, fertig.

### Schritt 2 — Paperclip-Issue anlegen
```
POST /api/companies/{companyId}/issues
{
  "title": "Action-Run: [Kategorie] ([N] Mails)",
  "description": "Sven-Trigger: [Kategorie]\n\n## Mails\n[Mail-Liste als Tabelle]",
  "assigneeAgentId": "<lena-agent-id>",
  "priority": "medium"
}
```

Mail-Liste in der Beschreibung als Markdown-Tabelle:
```
| # | Betreff | Absender | Datum |
|---|---|---|---|
| 1 | Re: Angebot | max@example.com | 2026-06-27 |
...
```

### Schritt 3 — Kategorie-Flow abarbeiten

Lena arbeitet die Mails sequenziell ab. **Jede Mail = ein Comment-Thread im Issue.**
Sven antwortet per Telegram oder direkt im Issue. Lena wertet beides aus.

---

## 3. Kategorie-Flows

### ABLEGEN
```
1. POST /api/lena/mail/archive-by-category { "category": "Lena: Ablegen" }
2. Telegram-Antwort: "✅ [N] Mails archiviert."
3. Issue auf done setzen.
```
Kein Rueckfragen noetig — direkt archivieren und bestätigen.

---

### WEITERLEITEN
Fuer jede Mail mit Kategorie "Lena: Weiterleiten":

1. Pruefen ob die Kategorie-Notiz bereits einen Empfaenger enthaelt (z.B. "Lena: Weiterleiten → Frank"):
   - Wenn ja: direkt weiterleiten ohne Rueckfrage → `POST /api/lena/mail/draft` mit To=Frank
2. Wenn kein Empfaenger bekannt: Telegram-Rueckfrage senden:
   ```
   Weiterleiten an wen?

   📧 Von: [Absender]
   📌 Betreff: [Betreff]
   📅 Datum: [Datum]

   Antwort: Name oder E-Mail-Adresse
   ```
3. Sven antwortet mit Empfaenger-Name oder E-Mail
4. Lena erstellt Weiterleitungs-Entwurf via `POST /api/lena/mail/draft`
5. Naechste Mail

---

### TUN
Fuer jede Mail mit Kategorie "Lena: Tun":

1. Verfuegbare Asana-Boards aus `meeting_board_mapping.yaml` lesen
2. Wahrscheinlichstes Board vorschlagen basierend auf Betreff/Inhalt
3. Telegram-Rueckfrage:
   ```
   Asana-Task fuer: '[Betreff]'

   📧 Von: [Absender]
   💡 Vorschlag: Board '[Board-Name]'

   Antwort: 'Ja' oder anderes Board, oder 'Nein' zum Ueberspringen
   ```
4. Bei 'Ja' oder Board-Angabe:
   - Asana-Task anlegen (`POST /api/asana/tasks`)
   - Kategorie auf 'Lena: Ablegen' setzen (`POST /api/lena/mail/categorize`)
5. Bei 'Nein': Kategorie auf 'Lena: Ablegen' setzen, Task nicht anlegen
6. Naechste Mail

---

### ANTWORTEN
Fuer jede Mail mit Kategorie "Lena: Antworten":

1. Mail-Inhalt lesen (`GET /api/lena/mail/inbox` oder bestehender body_preview)
2. Reply-Entwurf formulieren (LLM)
3. Telegram-Rueckfrage:
   ```
   Entwurf fuer: '[Betreff]'

   📧 An: [Absender]
   ---
   [Entwurf-Text]
   ---

   Senden? 'Ja' oder 'Aendern: [neuer Text]'
   ```
4. Bei 'Ja': Antwort senden (`POST /api/lena/mail/draft` mit send=true oder aequivalent)
5. Bei 'Aendern: [Text]': Sven-Text senden
6. Naechste Mail

---

### WARTEN
Fuer jede Mail mit Kategorie "Lena: Warten":

1. Telegram-Rueckfrage:
   ```
   Wann soll ich dich erinnern?

   📧 Von: [Absender]
   📌 Betreff: [Betreff]

   Antwort: Datum (YYYY-MM-DD) oder 'in X Tagen'
   ```
2. Reminder anlegen (Kalender-Eintrag via `POST /api/calendar/events` oder
   Asana-Task mit Faelligkeitsdatum)
3. Mail archivieren (`POST /api/lena/mail/archive-by-category` oder move)
4. Naechste Mail

---

### RECHERCHIEREN
Fuer jede Mail mit Kategorie "Lena: Recherchieren":

1. Mail-Inhalt lesen
2. Kurze Recherche (Web-Search oder Knowledge-Base)
3. Telegram-Ergebnis senden:
   ```
   🔍 Recherche fuer: '[Betreff]'

   [Recherche-Ergebnis]

   Genuegt das? 'Ja' / 'Mehr' / 'Ablegen'
   ```
4. Bei 'Ja': Mail archivieren
5. Bei 'Mehr': weitere Recherche, erneut fragen
6. Bei 'Ablegen': nur archivieren
7. Naechste Mail

---

## 4. Hilfs-Endpoints fuer Action Runner

| Endpoint | Zweck |
|---|---|
| `POST /api/lena/mail/action-run { "category": "ablegen" }` | Mails einer Kategorie abrufen |
| `GET /api/lena/mail/by-category/{kategorie}` | Mails einer Kategorie abrufen (GET-Variante) |
| `POST /api/lena/mail/clear-categories { "category": null }` | Alle Lena:-Kategorien loeschen |
| `POST /api/lena/mail/clear-categories { "category": "ablegen" }` | Spezifische Kategorie loeschen |
| `POST /api/lena/mail/archive-by-category { "category": "Lena: Ablegen" }` | Ablegen-Mails archivieren |
| `POST /api/lena/mail/categorize { "message_id": "...", "action": "ablegen" }` | Kategorie einer Mail setzen |

---

## 5. Fehlerbehandlung

| Situation | Verhalten |
|---|---|
| Outlook nicht authentifiziert | Telegram: "Outlook-Zugang nicht verfuegbar, bitte Sven informieren." |
| Keine Mails in Kategorie | Telegram: "Keine Mails mit Kategorie '[X]' — alles erledigt. 👍" |
| Sven antwortet nicht innerhalb 10 Minuten | Lena wartet — kein automatisches Weiterschalten |
| Asana-Board nicht gefunden | Telegram-Rueckfrage mit verfuegbaren Boards |
| Kein bekannter Trigger | Normale Telegram-Verarbeitung, kein Action-Run |

---

## 6. Wichtige Regeln

- **Kein automatisches Feuern** — immer auf Svens Trigger warten
- **Sven bestimmt Zeitpunkt und Kategorie** — Lena startet nie von sich aus
- **Sequenziell pro Kategorie** — eine Mail nach der anderen
- **Issue als Container** — pro Action-Run ein Paperclip-Issue fuer den Dialog
- **Lenas Auto-Triage (Phase 1) bleibt unveraendert** — dieser Skill betrifft nur Phase 3
