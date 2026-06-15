# SKILL_BRIEFING — Tages-Briefing für Sven

Dieses Skill-Dokument beschreibt den Workflow für Lenas tägliches Morgen-Briefing,
das Sven über Telegram zugestellt wird.

---

## 1. Wann wird dieses Skill ausgelöst?

- `wake_reason=daily-briefing` — Lena wird planmäßig morgens geweckt
- Manuell via Telegram-Nachricht "Briefing" oder "Guten Morgen"

---

## 2. Briefing-Quellen

Das Tages-Briefing aggregiert mehrere Datenquellen zu einer kompakten Telegram-Nachricht.

### 2.1 Kalender-Termine

```
GET /api/calendar/events?date=<heute-YYYY-MM-DD>
```

Format in der Nachricht:
```
📅 *Heute, DD.MM.YYYY*
08:30 — Jour Fixe Geschäftsführung (Raum 1.04)
14:00 — Kundengespräch Mustermann GmbH
```

### 2.2 Asana-Aufgaben (fällig heute/überfällig)

```
GET /api/asana/tasks?assignee=me&due_on=<heute>&workspace=<workspace-id>
```

Format:
```
✅ *Offene Aufgaben*
• [DRINGEND] Angebot ABC prüfen
• Meeting-Protokoll finalisieren
```

### 2.3 Mail-Triage-Status (NEU — v4)

```
GET /api/lena/mail/triage-summary?since=<heute-00:00:00Z>
```

**Wichtig:** `since` muss ein UTC-Zeitstempel sein — immer `T00:00:00Z` für
Mitternacht UTC des aktuellen Tags (nicht Server-Lokalzeit).

Berechnung:
```
since = <heutiges Datum>T00:00:00Z
```

Beispiel: Am 15.06.2026 → `since=2026-06-15T00:00:00Z`

**Response-Felder:**
```json
{
  "antworten": 7, "tun": 3, "warten": 4,
  "recherchieren": 1, "weiterleiten": 6, "ablegen": 12,
  "hoch": 2, "mittel": 8, "niedrig": 23,
  "since": "2026-06-15T00:00:00Z",
  "total_categorized": 33
}
```

**Format in der Briefing-Nachricht** (nur wenn `total_categorized > 0`):
```
📬 *Mail-Triage*
7 Antworten (2 Hoch), 3 Tun, 4 Warten, 1 Recherchieren, 6 Weiterleiten, 12 Ablegen
```

Wenn `total_categorized == 0`:
```
📬 *Mail-Triage* — Noch keine Mails heute kategorisiert
```

Wenn `hoch > 0`, füge explizit hinzu:
```
⚠️ 2 Hoch-Priorität-Mails warten auf deine Aufmerksamkeit
```

---

## 3. Briefing-Aufbau (Telegram-Nachricht)

```
🌅 *Guten Morgen, Sven!*
_Montag, 15. Juni 2026_

📅 *Heute*
08:30 — Jour Fixe Geschäftsführung
14:00 — Kundengespräch Mustermann GmbH

📬 *Mail-Triage*
7 Antworten (2 Hoch), 3 Tun, 4 Warten, 1 Recherchieren, 6 Weiterleiten, 12 Ablegen

✅ *Offene Aufgaben*
• Angebot ABC prüfen [Fällig: heute]

—
_Lena_
```

---

## 4. Fehlerbehandlung

| Situation | Verhalten |
|---|---|
| Outlook nicht erreichbar (HTTP 503) | Kalender-Block weglassen, Hinweis: "Outlook nicht verfügbar" |
| triage-summary HTTP 503 | Mail-Block weglassen, kein Fehlerhinweis nötig (stört das Briefing) |
| triage-summary HTTP 4xx | Im Briefing: "Mail-Triage: Fehler beim Abrufen" |
| Asana nicht erreichbar | Aufgaben-Block weglassen |
| Keine Daten in allen Quellen | Briefing trotzdem senden: "Keine ausstehenden Termine oder Aufgaben" |

Bei jedem Fehler: Briefing so vollständig wie möglich senden, fehlerhafte Quellen
auslassen statt das gesamte Briefing zu unterdrücken.

---

## 5. Konfiguration

| Variable | Bedeutung |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot-Token für Briefing-Versand |
| `TELEGRAM_ADMIN_CHAT_ID` | Svens Chat-ID |
| `MEIN_ASSISTENT_API_URL` | API-Basis (Standard: `http://127.0.0.1:8502`) |
| `API_SECRET_KEY` | X-API-Key Header |

---

## 6. Verwandte Skills und Doku

- `SKILL_SPEAKER.md` — Sprecher-Auflösung für Meeting-Protokolle
- `SKILL_INTAKE.md` — Plaud-Transkript-Verarbeitung
- `MAIL_TRIAGE.md` — Vollständige Mail-Triage Betriebsdoku
