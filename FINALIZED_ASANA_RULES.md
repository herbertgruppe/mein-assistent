# ✅ Finalisierte Asana-Logik - Strikte Regeln

**Version:** 3.0 (Final)
**Datum:** 2026-01-25
**Status:** ✅ Implementiert & Getestet

---

## 📋 Übersicht

Die Asana-Integration wurde mit **strikten Regeln** finalisiert, um eine konsistente und fehlerfreie Aufgaben-Erstellung zu gewährleisten.

---

## 🔒 Die 5 Strikten Regeln

### 1. **Pflicht-Assignee (Default: Me)** ✅

**Regel:** Jede Aufgabe MUSS einen Assignee haben.

**Implementierung:**
- Wenn der Nutzer keinen Verantwortlichen nennt → automatisch `"me"` (aktueller Nutzer)
- Nutzt API-Call `users.get_user("me")` um eigene GID abzufragen
- **KEINE** Aufgaben ohne Assignee

**Code in `asana_agent.py`:**
```python
# STRIKTE REGEL: Assignee ist IMMER gesetzt
if self.detect_assignee_self(user_input):
    assignee_gid = "me"
else:
    # WICHTIG: Auch wenn nicht explizit erwähnt, setze "me" als Default
    assignee_gid = "me"
```

**Test-Ergebnis:**
```
Input: "Erstelle Aufgabe: Test ohne explizite Zuweisung"
Parsed Assignee: me
✅ KORREKT: Assignee ist automatisch auf 'me' gesetzt
```

---

### 2. **Titel- & Inhalts-Trennung** ✅

**Regel:** Trenne Befehl von Inhalt, entferne Füllwörter.

**Implementierung:**

**A) Mit Doppelpunkt:**
```
Input:  "Erstelle Aufgabe: Präsentation fertigstellen"
Output: "Präsentation fertigstellen"
```

**B) Ohne Doppelpunkt:**
```
Input:  "Erstelle eine Aufgabe Meeting vorbereiten"
Output: "Meeting vorbereiten"
```

**C) Cleanup von Zusatz-Infos:**
```
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

### 3. **Zeit-Intelligenz** ✅

**Regel:** Wandle relative Begriffe präzise in YYYY-MM-DD Format.

**Unterstützte Formate:**

| Eingabe | Ausgabe (Beispiel) |
|---------|-------------------|
| heute | 2026-01-25 |
| morgen | 2026-01-26 |
| übermorgen | 2026-01-27 |
| nächsten Freitag | 2026-01-30 |
| diese Woche | 2026-01-30 |
| nächste Woche | 2026-02-02 |
| in 3 Tagen | 2026-01-28 |
| 25.01.2026 | 2026-01-25 |
| 2026-01-30 | 2026-01-30 |

**Code:**
```python
def parse_relative_date(self, date_string: str) -> Optional[str]:
    """Wandelt relative Zeitangaben in YYYY-MM-DD Format"""
    # Implementierung mit datetime
    # Unterstützt: heute, morgen, Wochentage, "in X Tagen", etc.
```

**Test-Ergebnis:**
```
Input: 'Aufgabe für heute' → Datum: 2026-01-25 ✅
Input: 'bis morgen' → Datum: 2026-01-26 ✅
Input: 'nächsten Freitag' → Datum: 2026-01-30 ✅
```

---

### 4. **Interaktive Rückfragen** ✅

**Regel:** NICHT RATEN - Bei unklarem Ziel-Projekt NACHFRAGEN.

**Implementierung:**

Wenn Projekt fehlt:
```python
if not project_gid:
    missing_info.append("project")

if missing_info:
    return {
        "success": False,
        "needs_user_input": True,
        "missing_info": missing_info,
        "parsed_data": parsed_data
    }
```

**TaskAgent stellt Rückfrage:**
```markdown
📋 In welches Projekt soll ich die Aufgabe 'Meeting vorbereiten' erstellen?

Verfügbare Projekte:
1. 1:1 lhe / SH
2. IT Backlog
3. WPM / myTGA Arbeitskreise
...

Bitte geben Sie die Nummer oder den Namen des Projekts an.
```

**Test-Ergebnis:**
```
Input: "Erstelle Aufgabe: Test-Aufgabe für Rückfragen"
Success: False
Needs User Input: True
Missing Info: ['project']
✅ KORREKT: Rückfrage nach Projekt wird gestellt
```

---

### 5. **Bestätigung mit Link** ✅

**Regel:** Nach Erstellung → Bestätigung inkl. direktem Link zur Asana-Aufgabe.

**Implementierung in `task_agent.py`:**

```python
def _handle_asana_task_creation(...):
    # Nach erfolgreicher Erstellung
    if result.get('success'):
        output = f"✅ **Asana-Aufgabe erfolgreich erstellt!**\n\n"
        output += f"**Titel:** {task_name}\n"

        if due_on:
            output += f"**Fällig:** {due_on}\n"

        output += f"**Zugewiesen:** Dir (aktueller Nutzer)\n\n"

        if permalink:
            output += f"🔗 **[Aufgabe in Asana öffnen]({permalink})**\n\n"
            output += f"_Direktlink: {permalink}_"
```

**Ausgabe-Beispiel:**
```markdown
✅ Asana-Aufgabe erfolgreich erstellt!

**Titel:** Präsentation fertigstellen
**Fällig:** 2026-01-26
**Zugewiesen:** Dir (aktueller Nutzer)

🔗 [Aufgabe in Asana öffnen](https://app.asana.com/0/1205957746667869/1212966399100131)

Direktlink: https://app.asana.com/0/1205957746667869/1212966399100131
```

---

## 🔧 Implementierungs-Details

### Geänderte Dateien

#### 1. `agents/asana_agent.py`

**Neue/Geänderte Methoden:**

- `create_task_smart()` - PFLICHT-ASSIGNEE implementiert
  ```python
  # Default: "me" (IMMER gesetzt)
  assignee_gid = "me"
  ```

- `parse_task_title_from_input()` - Erweitert mit Cleanup
  ```python
  # Entfernt Zusatz-Phrasen wie "fällig morgen"
  cleanup_patterns = [...]
  ```

- `get_current_user()` - Holt GID des aktuellen Nutzers
  ```python
  user_response = self.users_api.get_user('me', opts)
  ```

#### 2. `agents/task_agent.py`

**Neue Methoden:**

- `__init__(asana_agent)` - Nimmt AsanaAgent als Parameter
- `_is_asana_command()` - Erkennt Asana-Befehle
- `_handle_asana_task_creation()` - Verarbeitet Asana-Aufgaben mit Rückfragen

**Erweiterte Methoden:**

- `process()` - Prüft zuerst auf Asana-Befehle
  ```python
  if self._is_asana_command(input_data):
      return self._handle_asana_task_creation(input_data, context)
  ```

#### 3. `app.py` (StreamlitOrchestrator)

**Geändert:**
```python
# AsanaAgent wird an TaskAgent übergeben
self.asana_agent = AsanaAgent()
self.task_agent = TaskAgent(
    llm_provider=self.llm_provider,
    asana_agent=self.asana_agent
)
```

#### 4. `main.py` (AgentOrchestrator)

**Geändert:**
```python
# AsanaAgent initialisieren und an TaskAgent übergeben
from agents.asana_agent import AsanaAgent
self.asana_agent = AsanaAgent()
self.task_agent = TaskAgent(
    llm_provider=self.llm_provider,
    asana_agent=self.asana_agent
)
```

---

## 🧪 Test-Script

**Datei:** `test_finalized_asana.py`

**Tests:**
1. ✅ Pflicht-Assignee (Default: Me)
2. ✅ Titel-Trennung & Cleanup
3. ✅ Zeit-Intelligenz (heute, morgen, Wochentage)
4. ✅ Interaktive Rückfragen bei fehlendem Projekt
5. ✅ TaskAgent erkennt Asana-Befehle
6. ✅ TaskAgent Flow mit Rückfrage
7. ✅ Echte Aufgaben-Erstellung mit formatiertem Link

**Ausführen:**
```bash
source venv/bin/activate
python test_finalized_asana.py
```

---

## 📊 Beispiel-Flow (End-to-End)

### Szenario: Nutzer erstellt Aufgabe ohne Projekt

**1. Nutzer-Eingabe:**
```
"Erstelle Aufgabe: Präsentation fertigstellen, fällig morgen"
```

**2. TaskAgent erkennt Asana-Befehl:**
```python
_is_asana_command() → True
```

**3. AsanaAgent parst Eingabe:**
```python
parse_task_title_from_input() → "Präsentation fertigstellen"
parse_relative_date() → "2026-01-26"
detect_assignee_self() → False → Default: "me"
```

**4. Projekt fehlt → Rückfrage:**
```markdown
📋 In welches Projekt soll ich die Aufgabe 'Präsentation fertigstellen' erstellen?

Verfügbare Projekte:
1. 1:1 lhe / SH
2. IT Backlog
3. WPM / myTGA Arbeitskreise

Bitte geben Sie die Nummer oder den Namen des Projekts an.
```

**5. Nutzer wählt Projekt:**
```
"Projekt 2" oder "IT Backlog"
```

**6. Aufgabe wird erstellt:**
```python
create_task(
    name="Präsentation fertigstellen",
    notes="",
    due_on="2026-01-26",
    project_gid="...",
    assignee_gid="me"  # PFLICHT
)
```

**7. Bestätigung mit Link:**
```markdown
✅ Asana-Aufgabe erfolgreich erstellt!

**Titel:** Präsentation fertigstellen
**Fällig:** 2026-01-26
**Zugewiesen:** Dir (aktueller Nutzer)

🔗 [Aufgabe in Asana öffnen](https://app.asana.com/...)

Direktlink: https://app.asana.com/...
```

---

## ✅ Checkliste

- [x] **Regel 1:** Pflicht-Assignee (Default: Me) implementiert
- [x] **Regel 2:** Titel-Trennung & Cleanup implementiert
- [x] **Regel 3:** Zeit-Intelligenz für alle gängigen Formate
- [x] **Regel 4:** Interaktive Rückfragen bei fehlendem Projekt
- [x] **Regel 5:** Bestätigung mit direktem Link
- [x] TaskAgent erkennt Asana-Befehle
- [x] TaskAgent nutzt AsanaAgent für Verarbeitung
- [x] StreamlitOrchestrator übergibt AsanaAgent an TaskAgent
- [x] AgentOrchestrator (main.py) übergibt AsanaAgent
- [x] Test-Script mit 7 Szenarien erstellt
- [x] Alle Tests erfolgreich

---

## 🚀 Verwendung

### Im Chat (Streamlit):

```
Nutzer: Erstelle Aufgabe: Meeting vorbereiten, fällig morgen

Assistent: 📋 In welches Projekt soll ich die Aufgabe 'Meeting vorbereiten' erstellen?

Verfügbare Projekte:
1. 1:1 lhe / SH
2. IT Backlog
...

Nutzer: Projekt 1

Assistent: ✅ Asana-Aufgabe erfolgreich erstellt!

**Titel:** Meeting vorbereiten
**Fällig:** 2026-01-26
**Zugewiesen:** Dir (aktueller Nutzer)

🔗 [Aufgabe in Asana öffnen](https://app.asana.com/...)
```

### Direkt (Python):

```python
from agents.asana_agent import AsanaAgent

agent = AsanaAgent()

# Mit Projekt (direkte Erstellung)
result = agent.create_task_smart(
    user_input="Erstelle Aufgabe: Test",
    notes="Beschreibung",
    project_gid="1205957746667869"
)

# Ohne Projekt (Rückfrage)
result = agent.create_task_smart(
    user_input="Erstelle Aufgabe: Test",
    notes="Beschreibung",
    project_gid=None
)

if result.get('needs_user_input'):
    print("Projekt fehlt - Rückfrage nötig")
```

---

## 📝 Zusammenfassung

### Was wurde finalisiert:

1. ✅ **Pflicht-Assignee** - Jede Aufgabe hat IMMER einen Assignee (Default: "me")
2. ✅ **Titel-Parsing** - Saubere Trennung von Befehl und Inhalt, Cleanup von Metadaten
3. ✅ **Datums-Intelligenz** - Präzise Umwandlung aller relativen Zeitangaben
4. ✅ **Rückfrage-Logik** - Kein Raten bei fehlendem Projekt, stattdessen Nachfragen
5. ✅ **Link-Bestätigung** - Formatierte Ausgabe mit direktem Asana-Link

### Integration:

- ✅ TaskAgent erkennt automatisch Asana-Befehle
- ✅ TaskAgent nutzt AsanaAgent für Verarbeitung
- ✅ Orchestrators (app.py & main.py) übergeben AsanaAgent
- ✅ Interaktive Rückfragen im Chat-Flow

---

**Status:** ✅ **FINALISIERT & PRODUKTIONSREIF**

**Version:** 3.0 (Final)
**Getestet:** 2026-01-25
**Zuletzt aktualisiert:** 2026-01-25
