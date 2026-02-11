# Email-System Neustart - Strikt Asynchrone Architektur

**Datum:** 2026-01-31
**Status:** ✅ Komplett neu implementiert

## Das eiserne Gesetz

```
┌─────────────────────────────────────────────────────────────┐
│                                                               │
│  Die UI (app.py) darf NIEMALS direkt mit Outlook oder       │
│  Asana kommunizieren. Alle Kommunikation läuft über die     │
│  SQLite-Datenbank data/email_store.db                       │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

## Architektur-Überblick

### 3 Komponenten

```
┌─────────────────┐       ┌─────────────────┐       ┌──────────────────┐
│                 │       │                 │       │                  │
│   app.py (UI)   │◄─────►│  SQLite DB      │◄─────►│  email_worker.py │
│                 │       │  email_store.db │       │                  │
│                 │       │                 │       │                  │
└─────────────────┘       └─────────────────┘       └──────────────────┘
                                                              │
         NUR DB-Lesen                                        │
         & DB-Schreiben                         ┌────────────┴────────────┐
                                               │                         │
                                               ▼                         ▼
                                        ┌──────────┐             ┌──────────┐
                                        │ Outlook  │             │  Asana   │
                                        │   API    │             │   API    │
                                        └──────────┘             └──────────┘
```

### 1. Datenbank (`database/email_db.py`)

Zentrale SQLite-Datenbank mit folgenden Feldern:

- **Metadaten:** id, subject, sender_name, sender_email, received_dt, body_preview
- **KI-Analyse:** priority, category, summary, action_items, deadline, sentiment
- **Steuerung:** instruction ('none', 'archive', 'asana', 'forward'), instruction_payload
- **Status:** status ('unread', 'processing', 'done', 'error'), error_message

### 2. UI (`app.py` - Email Tab)

**Was die UI macht:**
- Emails aus DB lesen (instant, keine API-Calls)
- Anzeigen in übersichtlichen Karten
- Buttons die nur `instruction` in DB setzen
- Sofortiges `st.rerun()` ohne Spinner

**Was die UI NICHT macht:**
- ❌ Kein direkter Zugriff auf OutlookGraphTool
- ❌ Kein direkter Zugriff auf AsanaTool
- ❌ Keine LLM-Calls
- ❌ Keine langsamen API-Operationen

### 3. Worker (`email_worker.py`)

**Zwei separate Schleifen:**

#### Schleife 1: Fetch & Analyze (alle 2 Minuten)
```python
Outlook API → Neue Emails holen
    ↓
LLM Analyse → Priorität, Kategorie, Zusammenfassung
    ↓
SQLite DB → Email speichern mit status='unread'
```

#### Schleife 2: Execute Instructions (alle 30 Sekunden)
```python
SQLite DB → Hole alle Emails mit instruction != 'none'
    ↓
Für jede Email:
    - instruction='archive' → Outlook: markiere als gelesen, verschiebe in Ordner
    - instruction='asana'   → Asana: erstelle Task
    - instruction='forward' → Outlook: leite weiter
    ↓
SQLite DB → Setze status='done' oder status='error'
```

## Neue Dateien

```
database/
  ├── __init__.py           # Package init
  └── email_db.py           # Neue EmailDB Klasse

email_worker.py             # Komplett neu geschrieben
app.py                      # render_inbox_tab() komplett ersetzt
start_email_system.sh       # Worker starten
stop_email_system.sh        # Worker stoppen
status_email_system.sh      # Status prüfen
```

## Gelöschte/Ersetzte Funktionen

### In app.py:
- ❌ `render_inbox_tab()` - Alte komplexe Version (517 Zeilen)
- ✅ `render_inbox_tab()` - Neue einfache Version (75 Zeilen)
- ✅ `render_simple_email_card()` - Neue Card-Funktion (70 Zeilen)

### In email_worker.py:
- ❌ Alte komplexe Implementierung mit APScheduler
- ✅ Neue einfache Implementierung mit time.sleep() und 2 Schleifen

### Imports:
- ❌ `from utils.email_manager import EmailManager` (nicht mehr in app.py)

## Schnellstart

### 1. Worker starten

```bash
./start_email_system.sh
```

### 2. Status prüfen

```bash
./status_email_system.sh
```

### 3. Streamlit UI öffnen

```bash
streamlit run app.py
```

### 4. Worker stoppen

```bash
./stop_email_system.sh
```

## Workflow-Beispiel

### User-Perspektive (UI):

1. User öffnet Email-Tab
2. Sieht sofort alle analysierten Emails (aus DB)
3. Klickt auf "🗄️ Archivieren"
4. Email verschwindet sofort aus Liste
5. Kein Spinner, keine Wartezeit

### Was im Hintergrund passiert:

1. UI setzt in DB: `instruction='archive'`, `status='processing'`
2. UI macht `st.rerun()` → Email verschwindet
3. Worker findet nach ~30 Sekunden die pending instruction
4. Worker ruft Outlook API auf → Email archivieren
5. Worker setzt in DB: `status='done'`, `instruction='none'`

## Vorteile

### ✅ UI bleibt immer reaktiv
- Keine blockierenden API-Calls
- Alle Operationen sind DB-Writes (instant)
- User muss nie auf spinner warten

### ✅ Fehlerrobustheit
- Worker kann crashen, UI funktioniert weiter
- Pending instructions werden beim nächsten Worker-Start verarbeitet
- Fehler werden in DB gespeichert, nicht in UI

### ✅ Skalierbarkeit
- Worker kann auf anderem Server laufen
- Mehrere Worker möglich (mit Locking)
- DB kann auf shared Storage liegen

### ✅ Einfachheit
- Klare Trennung der Verantwortlichkeiten
- Leicht zu testen (DB mocken)
- Leicht zu verstehen

## Troubleshooting

### Problem: UI zeigt keine Emails

**Lösung:** Prüfe ob Worker läuft und Emails analysiert hat
```bash
./status_email_system.sh
```

### Problem: Worker crasht sofort

**Lösung:** Prüfe Logs
```bash
tail -f email_worker.log
```

Häufige Gründe:
- Outlook nicht authentifiziert
- LLM API-Key fehlt
- Asana Token fehlt

### Problem: Emails bleiben in "processing"

**Lösung:** Worker ist wahrscheinlich gestoppt
```bash
./stop_email_system.sh
./start_email_system.sh
```

### Problem: Worker findet keine neuen Emails

**Lösung:**
1. Prüfe Outlook-Authentifizierung
2. Prüfe ob Emails tatsächlich ungelesen sind
3. Warte 2 Minuten (Fetch-Interval)

## Nächste Schritte (Optional)

### Features die noch fehlen:

1. **Email-Antworten**: Button "✉️ Antworten" funktioniert noch nicht
2. **Email-Weiterleitung**: Button "↗️ Weiterleiten" funktioniert noch nicht
3. **Asana-Projekt-Auswahl**: Momentan nur Default-Projekt

### Verbesserungen:

1. **Web-Interface für Worker-Status**: Zeige Worker-Status in UI
2. **Retry-Logic**: Automatisches Retry bei Fehlern
3. **Bulk-Operations**: Mehrere Emails gleichzeitig verarbeiten
4. **Email-Suche**: Suche in analysierten Emails

## Code-Beispiele

### UI: Button-Handler (app.py)

```python
if st.button("🗄️ Archivieren", key=f"arch_{idx}"):
    # Setze instruction in DB
    db.set_instruction(email['id'], 'archive')
    db.hide_email(email['id'])
    st.success("✅ Wird archiviert...")
    st.rerun()  # Sofort rerun, kein Spinner!
```

### Worker: Instruction verarbeiten (email_worker.py)

```python
def _execute_archive(self, email: Dict[str, Any]):
    """Archiviert Email in Outlook"""
    outlook = self._init_outlook()

    # Markiere als gelesen
    outlook.mark_as_read(email['id'])

    # Verschiebe in Archiv
    outlook.move_to_folder(email['id'], "Posteingang erledigt 2026")

    # Markiere in DB als erledigt
    self.db.mark_as_done(email['id'])
```

### DB: Email einfügen (database/email_db.py)

```python
def insert_email(self, email_data: Dict[str, Any]) -> bool:
    """Fügt neue Email ein"""
    with self._get_connection() as conn:
        conn.execute("""
            INSERT INTO emails (
                id, subject, sender_name, sender_email, ...
            ) VALUES (?, ?, ?, ?, ...)
        """, (email_data['id'], ...))
        conn.commit()
    return True
```

## Zusammenfassung

Das neue Email-System folgt einem **strikten asynchronen Architektur-Prinzip**:

- **UI:** Nur DB lesen/schreiben, sofort reagieren
- **Worker:** Einziger Zugriff auf APIs, läuft im Hintergrund
- **DB:** Zentrale Kommunikation zwischen UI und Worker

Das Ergebnis: **Eine UI, die nie blockiert!** 🚀
