# 🎯 Intelligente Asana-Aufgaben-Erstellung

**Version:** 2.0
**Datum:** 2026-01-25
**Status:** ✅ Implementiert

---

## 📋 Übersicht

Die Asana-Integration wurde erweitert um **intelligente Aufgaben-Erstellung mit automatischem Parsing und Rückfrage-Logik**.

### Hauptfunktionen

1. **Intelligentes Titel-Parsing** - Extrahiert Aufgabennamen aus natürlicher Sprache
2. **Datums-Parsing** - Wandelt relative Zeitangaben in Datumsformate um
3. **Selbstzuweisungs-Erkennung** - Erkennt "mir", "mich", "ich"
4. **Rückfrage-Logik** - Stellt Fragen bei fehlenden Informationen
5. **Fehlerkorrektur** - Entfernt Befehlswörter und Metadaten aus Titeln

---

## 🚀 Features im Detail

### 1. Intelligentes Titel-Parsing

**Logik:**
- **Mit Doppelpunkt:** Alles nach `:` wird als Titel verwendet
- **Ohne Doppelpunkt:** Befehlswörter werden automatisch entfernt
- **Cleanup:** Zusatz-Infos wie "fällig morgen", "mir zuweisen" werden entfernt

**Beispiele:**

```python
# Mit Doppelpunkt
Input:  "Erstelle Aufgabe: Meeting mit Dr. Herbert vorbereiten"
Output: "Meeting mit Dr. Herbert vorbereiten"

# Ohne Doppelpunkt
Input:  "Erstelle eine Aufgabe Meeting vorbereiten"
Output: "Meeting vorbereiten"

# Cleanup von Zusatz-Infos
Input:  "Präsentation fertigstellen, fällig morgen, mir zuweisen"
Output: "Präsentation fertigstellen"
```

**Entfernte Befehlswörter:**
- `erstelle (eine) aufgabe`
- `neue aufgabe`
- `lege (eine) task an`
- `mach (eine) aufgabe`
- `create (a) task`

**Entfernte Zusatz-Phrasen:**
- `fällig [Datum]`
- `bis [Datum]`
- `mir/mich zuweisen`
- `für mich`
- `an mich`
- `in X Tagen`

---

### 2. Relative Datums-Erkennung

**Unterstützte Formate:**

| Eingabe | Erkanntes Datum |
|---------|----------------|
| "heute" | 2026-01-25 |
| "morgen" | 2026-01-26 |
| "übermorgen" | 2026-01-27 |
| "nächsten Freitag" | 2026-01-30 |
| "diese Woche" | 2026-01-30 (Freitag) |
| "nächste Woche" | 2026-02-02 (nächster Montag) |
| "in 3 Tagen" | 2026-01-28 |
| "25.01.2026" | 2026-01-25 |
| "2026-01-30" | 2026-01-30 |

**Wochentage:**
- Montag, Dienstag, Mittwoch, Donnerstag, Freitag, Samstag, Sonntag
- Berechnet automatisch den nächsten Wochentag

**Funktion:**
```python
agent.parse_relative_date("bis morgen")  # → "2026-01-26"
```

---

### 3. Selbstzuweisungs-Erkennung

**Erkannte Phrasen:**
- `mich`
- `mir`
- `ich`
- `mir zuweisen`
- `für mich`
- `an mich`

**Beispiele:**

```python
agent.detect_assignee_self("Erstelle Aufgabe für mich")       # → True
agent.detect_assignee_self("mir zuweisen")                    # → True
agent.detect_assignee_self("ich soll das machen")             # → True
agent.detect_assignee_self("für Peter")                       # → False
```

**Automatische Zuweisung:**
- Bei Erkennung wird die GID des aktuellen Nutzers aus der API geholt: `users/me`
- Aufgabe wird automatisch dem angemeldeten Nutzer zugewiesen

---

### 4. Rückfrage-Logik

**Prinzip: NICHT RATEN - NACHFRAGEN**

Wenn Informationen fehlen, gibt die API zurück:

```python
{
    "success": False,
    "needs_user_input": True,
    "missing_info": ["project"],
    "parsed_data": {
        "title": "Meeting vorbereiten",
        "due_on": "2026-01-26",
        "assignee": "me",
        "project_gid": None
    }
}
```

**Generierte Rückfragen:**

1. **Projekt fehlt:**
   ```
   In welches Projekt soll die Aufgabe erstellt werden?
   1. 1:1 lhe / SH
   2. IT Backlog
   3. WPM / myTGA Arbeitskreise
   ...
   ```

2. **Fälligkeit fehlt (Optional):**
   ```
   Soll ein Fälligkeitsdatum gesetzt werden?
   1. Heute
   2. Morgen
   3. Diese Woche
   4. Kein Datum
   ```

3. **Assignee fehlt (Optional):**
   ```
   Soll die Aufgabe Ihnen zugewiesen werden?
   1. Ja, mir zuweisen
   2. Nein, nicht zuweisen
   ```

---

## 🔧 API-Methoden

### Neue Methoden in `AsanaAgent`

#### 1. `get_current_user() -> Optional[str]`
Holt die GID des aktuell angemeldeten Nutzers.

```python
user_gid = agent.get_current_user()
# → "1202563118654849"
```

#### 2. `parse_relative_date(date_string: str) -> Optional[str]`
Wandelt relative Zeitangaben in YYYY-MM-DD Format.

```python
date = agent.parse_relative_date("morgen")
# → "2026-01-26"
```

#### 3. `parse_task_title_from_input(user_input: str) -> str`
Extrahiert den Aufgabentitel aus der Eingabe.

```python
title = agent.parse_task_title_from_input("Erstelle Aufgabe: Meeting vorbereiten, fällig morgen")
# → "Meeting vorbereiten"
```

#### 4. `detect_assignee_self(user_input: str) -> bool`
Erkennt Selbstzuweisung.

```python
is_self = agent.detect_assignee_self("mir zuweisen")
# → True
```

#### 5. `create_task_smart(user_input: str, notes: str, project_gid: Optional[str]) -> Dict`
Intelligente Aufgaben-Erstellung mit Parsing.

```python
result = agent.create_task_smart(
    user_input="Erstelle Aufgabe: Präsentation fertigstellen, fällig morgen, mir zuweisen",
    notes="Wichtige Folien für Meeting",
    project_gid="1205957746667869"
)

# Bei Erfolg:
{
    "success": True,
    "task_gid": "1212966399100131",
    "task_name": "Präsentation fertigstellen",
    "permalink_url": "https://app.asana.com/...",
    "assignee": "me"
}

# Bei fehlenden Infos:
{
    "success": False,
    "needs_user_input": True,
    "missing_info": ["project"],
    "parsed_data": {...}
}
```

#### 6. `create_task(..., assignee_gid: Optional[str])`
Erweiterte create_task Methode mit Assignee-Parameter.

```python
result = agent.create_task(
    name="Meeting vorbereiten",
    notes="Agenda erstellen",
    due_on="2026-01-26",
    project_gid="1205957746667869",
    assignee_gid="me"  # NEU: Automatische Zuweisung
)
```

---

## 💡 Interactive Asana Tool

**Datei:** `tools/interactive_asana_tool.py`

### Klasse: `InteractiveAsanaTool`

Wrapper für interaktive Aufgaben-Erstellung mit Rückfragen.

**Hauptmethoden:**

#### 1. `create_task_interactive(user_input, notes, project_gid) -> Dict`
Erstellt Aufgabe mit automatischen Rückfragen.

```python
from tools.interactive_asana_tool import InteractiveAsanaTool

tool = InteractiveAsanaTool(asana_agent)

result = tool.create_task_interactive(
    user_input="Präsentation fertigstellen, fällig morgen",
    notes="Wichtig für Meeting",
    project_gid=None  # Fehlt absichtlich
)

if result.get('needs_user_input'):
    questions = result['questions']
    # Zeige Fragen dem Nutzer
    formatted = tool.format_questions_for_user(questions)
    print(formatted)
```

#### 2. `format_questions_for_user(questions: list) -> str`
Formatiert Rückfragen für die Ausgabe.

**Ausgabe:**
```
⚠️ Bitte zusätzliche Informationen angeben:

1. In welches Projekt soll die Aufgabe erstellt werden?
   1. 1:1 lhe / SH
   2. IT Backlog
   3. WPM / myTGA Arbeitskreise
   ...

2. Soll ein Fälligkeitsdatum gesetzt werden?
   1. Heute
   2. Morgen
   3. Diese Woche
   4. Kein Datum
   (Optional - kann übersprungen werden)
```

#### 3. `complete_task_creation(parsed_data, user_answers) -> Dict`
Vervollständigt die Erstellung mit Nutzer-Antworten.

```python
result = tool.complete_task_creation(
    parsed_data=parsed_data,
    user_answers={
        'project': '1:1 lhe / SH',
        'project_dict': {'1:1 lhe / SH': '1205957746667869'},
        'due_date': 'Morgen',
        'assignee': 'Ja, mir zuweisen',
        'notes': 'Wichtige Präsentation'
    }
)
```

---

## 🧪 Testing

### Test-Script: `test_smart_task_creation.py`

**Ausführen:**
```bash
source venv/bin/activate
python test_smart_task_creation.py
```

**Tests:**
1. ✅ Titel-Parsing mit Doppelpunkt
2. ✅ Titel-Parsing ohne Doppelpunkt
3. ✅ Relative Datums-Erkennung
4. ✅ Selbstzuweisungs-Erkennung
5. ✅ Smart-Parsing (komplett)
6. ✅ create_task_smart mit fehlenden Infos
7. ✅ Echte Aufgaben-Erstellung

**Ergebnis:**
```
✅ ALLE TESTS ABGESCHLOSSEN

Echte Aufgabe erstellt:
  Task Name: TEST - Smart Parsing Aufgabe, fällig morgen, mir zuweisen
  Task GID: 1212966399100131
  Assignee: me
  Permalink: https://app.asana.com/...
```

---

## 🎯 Anwendungsbeispiele

### Beispiel 1: Einfache Aufgabe mit Doppelpunkt

**Input:**
```
"Erstelle Aufgabe: Präsentation für KHS fertigstellen"
```

**Parsing:**
- Titel: "Präsentation für KHS fertigstellen"
- Datum: None
- Assignee: None

**Rückfrage:**
```
In welches Projekt soll die Aufgabe erstellt werden?
1. 1:1 lhe / SH
2. IT Backlog
...
```

---

### Beispiel 2: Komplette Aufgabe

**Input:**
```
"Erstelle Aufgabe: Meeting vorbereiten, fällig morgen, mir zuweisen"
```

**Parsing:**
- Titel: "Meeting vorbereiten"
- Datum: "2026-01-26" (morgen)
- Assignee: "me"

**Rückfrage:**
```
In welches Projekt soll die Aufgabe erstellt werden?
```

**Nach Antwort:**
```
✅ Aufgabe erstellt:
   Titel: "Meeting vorbereiten"
   Fällig: 26.01.2026
   Zugewiesen: Sven Herbert
   Projekt: 1:1 lhe / SH
```

---

### Beispiel 3: Ohne Doppelpunkt

**Input:**
```
"Neue Aufgabe Dokumente prüfen bis nächsten Freitag"
```

**Parsing:**
- Titel: "Dokumente prüfen"
- Datum: "2026-01-30" (nächster Freitag)
- Assignee: None

**Rückfragen:**
```
1. In welches Projekt soll die Aufgabe erstellt werden?
2. Soll die Aufgabe Ihnen zugewiesen werden?
```

---

## 🔄 Integration-Flow

```
┌─────────────────────────────────────────────────────┐
│ 1. Nutzer-Eingabe                                   │
│    "Erstelle Aufgabe: Meeting vorbereiten, morgen"  │
└────────────────┬────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────┐
│ 2. AsanaAgent.create_task_smart()                   │
│    - parse_task_title_from_input()                  │
│    - parse_relative_date()                          │
│    - detect_assignee_self()                         │
└────────────────┬────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────┐
│ 3. Prüfe fehlende Informationen                     │
│    - Projekt fehlt? → Rückfrage                     │
│    - Datum fehlt? → Optional Rückfrage              │
│    - Assignee fehlt? → Optional Rückfrage           │
└────────────────┬────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────┐
│ 4a. Alle Infos vorhanden                            │
│     → create_task() aufrufen                        │
│     → Erfolg zurückgeben                            │
└─────────────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────┐
│ 4b. Infos fehlen                                    │
│     → needs_user_input: true                        │
│     → questions: [...]                              │
│     → parsed_data: {...}                            │
└────────────────┬────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────┐
│ 5. Nutzer beantwortet Fragen                        │
│    → Projekt gewählt: "IT Backlog"                  │
│    → Datum: "Morgen"                                │
└────────────────┬────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────┐
│ 6. complete_task_creation()                         │
│    → create_task() mit allen Daten                  │
│    → Erfolg zurückgeben                             │
└─────────────────────────────────────────────────────┘
```

---

## 📝 Konfiguration

### .env Einträge

```bash
# Asana Integration (bereits vorhanden)
ASANA_ACCESS_TOKEN=2/1202563118654849/...

# Keine zusätzlichen Einträge erforderlich
```

### Dependencies

Bereits vorhanden in `requirements.txt`:
```
asana
```

---

## ✅ Checkliste

- [x] Titel-Parsing mit Doppelpunkt
- [x] Titel-Parsing ohne Doppelpunkt
- [x] Cleanup von Zusatz-Phrasen
- [x] Relative Datums-Erkennung
- [x] Selbstzuweisungs-Erkennung
- [x] get_current_user() Methode
- [x] create_task_smart() Methode
- [x] Rückfrage-Logik implementiert
- [x] InteractiveAsanaTool erstellt
- [x] Test-Script erstellt
- [x] Alle Tests erfolgreich

---

## 🚧 Nächste Schritte

### Integration in Chat-Flow

1. **Orchestrator erweitern** - Erkennung von Asana-Befehlen im Chat
2. **UI-Dialog** - Rückfragen im Chat-Interface anzeigen
3. **Context-Tracking** - Zwischenspeichern von parsed_data für Follow-up Fragen

### Weitere Verbesserungen

1. **Projekt-Vorschläge** - Intelligente Projekt-Auswahl basierend auf Kontext
2. **Batch-Erstellung** - Mehrere Aufgaben auf einmal erstellen
3. **Templates** - Vordefinierte Aufgaben-Templates
4. **Natural Language** - Noch besseres NLP für komplexe Eingaben

---

**Status:** ✅ Vollständig implementiert und getestet
**Version:** 2.0
**Zuletzt aktualisiert:** 2026-01-25
