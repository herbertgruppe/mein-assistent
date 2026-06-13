# HEARTBEAT.md — Lena Heartbeat-Steuerung

Dieses Dokument definiert, welches Skill beim `issue_assigned`-Heartbeat automatisch geladen wird
und welche Quick-Paths für häufige Aktionen gelten.

---

## Trigger-Matrix: `issue_assigned`

| Titel enthält | Skill laden |
|---|---|
| Termin, Meeting, Kalender, verschieben, einladen, Slot, Besprechung | `SKILL_MEETING_OPERATIONS.md` |
| Mail, E-Mail, Posteingang, Entwurf, Antwort, weiterleiten | `SKILL_EMAIL.md` (sofern vorhanden) |
| Daily, Protokoll, Notiz, Tagesnotiz | `SKILL_DAILY_NOTE.md` (sofern vorhanden) |
| Vault, Obsidian, Datei lesen, Datei schreiben | Vault-Skill |

---

## Quick-Path: Termin-Aktionen

Wenn ein Telegram-Trigger oder ein `issue_assigned`-Heartbeat auf **Kalender-Schlüsselwörter** matched:

```
1. SKILL_MEETING_OPERATIONS.md laden
2. Aktion extrahieren (anlegen | verschieben | löschen | Slot suchen | Teilnehmer | Anhang)
3. Approval-Gate: Telegram-Vorschau → Sven-Freigabe abwarten
4. API-Call: POST/PATCH/DELETE /api/lena/calendar/...
5. Bestätigung per Telegram + Vault Daily Note eintragen
```

Stopp-Bedingungen:
- HTTP 503 → Token abgelaufen, Sven informieren (`authenticate_outlook.py` erneut ausführen)
- HTTP 403 → Scope fehlt, Sven muss Re-Consent durchführen
- HTTP 502 → Graph-Fehler, Details per Telegram + Daily Note

---

## Quick-Path: Mail-Aktionen

Beim Erkennen von Mail-Schlüsselwörtern in Sven-Telegram-Nachricht oder Issue-Titel:

```
1. SKILL_EMAIL.md laden (falls vorhanden)
2. Aktion extrahieren (Entwurf | Senden | Verschieben | Lesen)
3. Approval-Gate für Sende-Aktionen
4. API-Call: /api/lena/mail/...
5. Bestätigung per Telegram
```

---

## Allgemeine Regeln

- Jede outbound Aktion (Kalender schreiben, Mail senden) benötigt Sven-Telegram-Freigabe.
- Vault Daily Note dokumentiert alle ausgeführten Aktionen.
- Bei Fehlern: Telegram-Meldung + Stopp (nicht raten, nicht wiederholen ohne Freigabe).
- Graph-Token-Fehler (401) werden einmalig mit Token-Refresh behoben; bei erneutem 401 → Sven.
