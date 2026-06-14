# SKILL: Asana-Mentions & Task-Assignments (Lena)

## Trigger

Paperclip-Issue mit Titel-Prefix:
- `📬 Asana-Mention von X auf Task: Y` — jemand hat Lena in einem Kommentar per `@lena` erwähnt
- `📋 Asana-Zuweisung von X: Y` — ein Asana-Task wurde Lena neu zugewiesen

## Woher kommt das Issue?

Der `lena-asana-poller` Service in mein-assistent pollt alle 5 Min die Asana-API:
- Holt alle Tasks assigned to Lena (`/tasks?assignee=1214903090695663&modified_since=...`)
- Prüft Stories (Kommentare + System-Events) dieser Tasks seit letztem Check
- Bei `@lena`-Mention in Kommentar-Text → Issue-Typ `📬`
- Bei Zuweisung an Lena (Assignment-Story) → Issue-Typ `📋`

Idempotenz: verarbeitete Story-GIDs werden in `/app/data/lena-asana-poller.state` gespeichert.

## Triage-Workflow (SKILL_TRIAGE-Logik)

1. **Issue lesen** — Titel, Absender, Task-Link, Kommentar-Excerpt in der Issue-Description
2. **Asana-Task öffnen** — Link ist direkt in der Issue-Description verlinkt
3. **Kontext prüfen**:
   - Worum geht es in dem Task?
   - Was genau wird von Lena erwartet (`@lena bitte X`, `@lena kannst du Y`)?
   - Gibt es eine Deadline im Task?
4. **Reagieren**:
   - **Einfache Auskunft / kurze Antwort** → Asana-Kommentar direkt verfassen
   - **Aufgabe erledigen** → als neues Todo in Sven's Briefing oder eigenem Backlog anlegen
   - **Unklar / mehr Kontext nötig** → Rückfrage an Sven (Telegram oder Asana-Kommentar)
5. **Issue schließen** — nach Bearbeitung auf `done` setzen

## Priorisierung

| Situation | Priorität |
|---|---|
| `@lena bitte bis [Datum]` oder Deadline im Task | high |
| Allgemeines `@lena` ohne Deadline | medium |
| Neue Zuweisung ohne Kommentar | medium |
| FYI-Kommentar / keine Aktion nötig | low → direkt schließen |

## Asana-API-Zugang (für direkte Aktionen)

Das `asana_tool.py` und `agents/asana_agent.py` in mein-assistent können verwendet werden
um Asana-Kommentare zu posten, Tasks zu aktualisieren oder als erledigt zu markieren.

## Fehlerbehebung Poller

```bash
# Logs
docker compose logs lena-asana-poller --tail=50

# State zurücksetzen (Re-Scan letzte 24h)
docker compose exec lena-asana-poller rm /app/data/lena-asana-poller.state
docker compose restart lena-asana-poller

# Manueller Test
docker compose run --rm lena-asana-poller python3 lena_asana_poller.py
```
