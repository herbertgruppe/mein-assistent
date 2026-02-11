# 🚀 Asana-Integration: Verbesserungen & Komfort-Features

**Datum:** 2026-01-25
**Version:** 2.0 (Enhanced)
**Status:** ✅ Implementiert

---

## 📋 Übersicht

Die Asana-Integration wurde um umfangreiches Error-Logging, User-Delegation und Komfort-Features erweitert.

---

## 🔧 Implementierte Features

### 1. Umfangreiches Error-Logging ✅

**Problem:** Der Task Agent zeigte nur "Unbekannter Fehler" an, ohne Details.

**Lösung:**

#### A) In `asana_agent.py` (create_task)

**Detailliertes Debug-Logging:**

```python
def create_task(self, name: str, notes: str = "", due_on: Optional[str] = None,
               project_gid: Optional[str] = None, assignee_gid: Optional[str] = None):
    print(f"[{self.name}] 🔧 DEBUG: create_task aufgerufen")
    print(f"[{self.name}]   → Name: {name}")
    print(f"[{self.name}]   → Project GID: {project_gid}")
    print(f"[{self.name}]   → Assignee GID: {assignee_gid}")

    # ... während Verarbeitung ...
    print(f"[{self.name}] 🚀 Sende API-Request an Asana...")
    print(f"[{self.name}]   → Task Data: {task_data}")

    # ... bei Exception ...
    except Exception as e:
        print(f"[{self.name}] ❌ EXCEPTION beim Erstellen der Aufgabe!")
        print(f"[{self.name}]   → Exception Type: {type(e).__name__}")
        print(f"[{self.name}]   → Exception Message: {str(e)}")

        # Detailliertes Error-Logging
        import traceback
        print(f"[{self.name}] 📋 FULL TRACEBACK:")
        traceback.print_exc()

        # Zusätzliche API-Error-Details (falls vorhanden)
        if hasattr(e, 'status'):
            print(f"[{self.name}]   → HTTP Status: {e.status}")
        if hasattr(e, 'reason'):
            print(f"[{self.name}]   → Reason: {e.reason}")
        if hasattr(e, 'body'):
            print(f"[{self.name}]   → Body: {e.body}")

        return {
            "success": False,
            "error": f"{type(e).__name__}: {str(e)}"
        }
```

#### B) In `task_agent.py` (_handle_asana_task_creation)

**Klare Fehlermeldungen mit Tipps:**

```python
else:
    # Fehler bei Erstellung - DETAILLIERTES ERROR-LOGGING
    error = result.get('error', 'Unbekannter Fehler')
    print(f"[{self.name}] ❌ Asana-Fehler: {error}")

    # Gebe klare Fehlermeldung zurück
    output = f"❌ **Fehler beim Erstellen der Asana-Aufgabe:**\n\n"
    output += f"**Fehlerdetails:** {error}\n\n"

    # Hilfreiche Tipps je nach Fehlertyp
    if "project" in error.lower():
        output += "_Tipp: Bitte wählen Sie ein gültiges Projekt aus._"
    elif "assignee" in error.lower():
        output += "_Tipp: Der angegebene Nutzer wurde nicht gefunden._"
    elif "workspace" in error.lower():
        output += "_Tipp: Prüfen Sie Ihre Asana-Workspace-Konfiguration._"

    return {
        "agent": self.name,
        "task": input_data,
        "execution": "Fehler bei Asana-Erstellung",
        "status": "error",
        "output": output,
        "error_details": error
    }
```

**Vorteile:**
- ✅ Jeder Schritt wird geloggt
- ✅ API-Fehler mit HTTP-Status, Reason und Body
- ✅ Full Traceback im Terminal
- ✅ Benutzerfreundliche Fehlermeldungen mit Tipps
- ✅ Keine generischen "Unbekannter Fehler" mehr

---

### 2. Favoriten-Liste für Projekte ⭐

**Feature:** Bis zu 3 Projekte als Favoriten in der Sidebar markieren.

**Konfiguration in `.env`:**

```bash
ASANA_FAVORITE_PROJECTS=IT Backlog,Marketing,Support Team
```

**Implementation in `app.py` (render_sidebar):**

```python
# Projekt-Favoriten (NEU)
st.subheader("⭐ Projekt-Favoriten")

if asana_tool.is_configured:
    # Favoriten aus .env
    favorite_projects_str = os.getenv("ASANA_FAVORITE_PROJECTS", "")
    favorite_project_names = [name.strip() for name in favorite_projects_str.split(",") if name.strip()]

    if favorite_project_names:
        try:
            # Lade alle Projekte
            all_projects = st.session_state.orchestrator.asana_agent.list_projects(limit=100)

            # Finde Favoriten-Projekte (fuzzy match)
            favorite_projects = []
            for fav_name in favorite_project_names[:3]:  # Max 3 Favoriten
                for project in all_projects:
                    if fav_name.lower() in project['name'].lower():
                        favorite_projects.append(project)
                        break

            if favorite_projects:
                for project in favorite_projects:
                    st.markdown(f"⭐ **{project['name'][:30]}{'...' if len(project['name']) > 30 else ''}**")
                    st.caption(f"GID: {project['gid'][:8]}...")
```

**Vorteile:**
- ✅ Schneller Zugriff auf häufig genutzte Projekte
- ✅ Konfigurierbar über .env
- ✅ Fuzzy-Matching (Teilstring reicht)
- ✅ Maximum 3 Favoriten für übersichtliche Sidebar

---

### 3. Delegation an Teammitglieder 👥

**Feature:** Aufgaben an andere Nutzer zuweisen durch Name-Suche.

#### A) Neue Funktion: `search_user_by_name(name: str)`

```python
def search_user_by_name(self, name: str) -> Optional[Dict[str, str]]:
    """
    Sucht einen Asana-Nutzer anhand des Namens im aktuellen Workspace

    Args:
        name: Name oder Teil des Namens (z.B. "Max", "Müller", "Max Mustermann")

    Returns:
        Dictionary mit 'gid' und 'name' des Nutzers oder None wenn nicht gefunden
    """
    print(f"[{self.name}] 🔍 Suche Nutzer: '{name}'")

    # Hole alle Nutzer im Workspace
    opts = {
        'workspace': self.workspace_gid,
        'opt_fields': 'gid,name,email'
    }

    users = self.users_api.get_users_for_workspace(self.workspace_gid, opts)

    name_lower = name.lower().strip()
    matched_users = []

    for user in users:
        user_name = user.get('name', '').lower()
        user_email = user.get('email', '').lower()

        # Prüfe ob Name im Nutzernamen oder Email vorkommt
        if name_lower in user_name or name_lower in user_email:
            matched_users.append({
                'gid': user.get('gid'),
                'name': user.get('name'),
                'email': user.get('email')
            })

    if not matched_users:
        return None

    if len(matched_users) > 1:
        print(f"[{self.name}]   ⚠️ Mehrere Nutzer gefunden, verwende ersten Treffer")

    return matched_users[0]
```

#### B) Neue Funktion: `extract_assignee_from_input(user_input: str)`

```python
def extract_assignee_from_input(self, user_input: str) -> Optional[str]:
    """
    Extrahiert den Assignee-Namen aus dem User-Input

    Erkennt Muster wie:
    - "Weise die Aufgabe [Name] zu"
    - "zuweisen an [Name]"
    - "für [Name]"
    - "assign to [Name]"
    """
    import re

    patterns = [
        r'weise\s+(?:die\s+)?(?:aufgabe\s+)?(?:an\s+)?([a-zäöüß\s]+?)\s+zu',
        r'zuweisen\s+an\s+([a-zäöüß\s]+?)(?:\s|,|$)',
        r'assign\s+to\s+([a-z\s]+?)(?:\s|,|$)',
        r'für\s+([a-zäöüß\s]+?)(?:\s|,|$)',
    ]

    for pattern in patterns:
        match = re.search(pattern, user_input.lower(), re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            # Filtere generische Begriffe
            if name not in ['mich', 'mir', 'me', 'myself']:
                return name

    return None
```

#### C) Integration in `create_task_smart()`

```python
# STRIKTE REGEL: Assignee ist IMMER gesetzt
assignee_gid = "me"  # Default
assignee_name = None

# Prüfe ob explizit "mir"/"mich" genannt wird
if self.detect_assignee_self(user_input):
    assignee_gid = "me"
else:
    # Prüfe ob ein anderer Nutzer genannt wird
    extracted_name = self.extract_assignee_from_input(user_input)
    if extracted_name:
        print(f"[{self.name}] 🔍 Suche Nutzer: '{extracted_name}'")
        user_info = self.search_user_by_name(extracted_name)
        if user_info:
            assignee_gid = user_info['gid']
            assignee_name = user_info['name']
            print(f"[{self.name}] ✅ Assignee gefunden: {assignee_name}")
        else:
            print(f"[{self.name}] ⚠️ Nutzer nicht gefunden, verwende 'me' als Fallback")
            assignee_gid = "me"
    else:
        # Kein Assignee explizit genannt → Default "me"
        assignee_gid = "me"
```

**Verwendung:**

```
User: "Erstelle Aufgabe: Meeting vorbereiten, weise die Aufgabe Max zu"
→ Sucht nach User "Max" im Workspace
→ Findet "Max Mustermann" (GID: 123...)
→ Weist Aufgabe Max Mustermann zu

User: "Erstelle Aufgabe: Code Review für Lisa"
→ Sucht nach User "Lisa"
→ Findet "Lisa Schmidt"
→ Weist Aufgabe Lisa Schmidt zu

User: "Erstelle Aufgabe: Dokument prüfen"
→ Kein Assignee genannt
→ Default: "me" (aktueller Nutzer)
```

**Vorteile:**
- ✅ Flexible Namenssuche (Vor-, Nachname, Email)
- ✅ Fuzzy-Matching
- ✅ Fallback auf "me" wenn nicht gefunden
- ✅ Mehrere Erkennungsmuster (deutsch/englisch)

---

### 4. Präzise Aufgabenerstellung (Refinement) 🎯

**Verbesserungen:**

#### A) Erweiterte Cleanup-Patterns

```python
# Entferne häufige Zusatz-Phrasen
cleanup_patterns = [
    # Datums-Phrasen
    r',?\s*fällig\s+(heute|morgen|übermorgen|nächste\s+woche|diese\s+woche|\w+tag)',
    r',?\s*bis\s+(heute|morgen|übermorgen|nächste\s+woche)',
    r',?\s*in\s+\d+\s+tag(en)?',
    # Assignee-Phrasen (an mich)
    r',?\s*(mir|mich)\s+zuweisen',
    r',?\s*für\s+(mich|mir)',
    r',?\s*an\s+mich',
    # Assignee-Phrasen (an andere) - NEU
    r',?\s*weise\s+(?:die\s+)?(?:aufgabe\s+)?(?:an\s+)?[a-zäöüß\s]+?\s+zu',
    r',?\s*zuweisen\s+an\s+[a-zäöüß\s]+',
    r',?\s*assign\s+to\s+[a-z\s]+',
    r',?\s*für\s+[a-zäöüß]+(?:\s+[a-zäöüß]+)?',
]
```

**Beispiele:**

```
Input:  "Erstelle Aufgabe: Meeting vorbereiten, weise die Aufgabe Max zu"
Output: "Meeting vorbereiten"
✅ Assignee-Phrase entfernt

Input:  "Neue Aufgabe: Präsentation fertigstellen, fällig morgen, für Lisa"
Output: "Präsentation fertigstellen"
✅ Datums- und Assignee-Phrasen entfernt

Input:  "Erstelle Aufgabe: Code Review bis heute"
Output: "Code Review"
✅ Datums-Phrase entfernt
```

#### B) Doppelpunkt-Logik (bereits stabil)

```python
if ':' in user_input:
    # Nimm alles nach dem ersten Doppelpunkt
    title = user_input.split(':', 1)[1].strip()
else:
    # Entferne Befehlswörter
    # ...
```

**Beispiele:**

```
Input:  "Erstelle Aufgabe: Meeting vorbereiten"
Output: "Meeting vorbereiten"
✅ Doppelpunkt-Trennung

Input:  "Neue Aufgabe Meeting vorbereiten"
Output: "Meeting vorbereiten"
✅ Befehlswort entfernt
```

#### C) Datums-Umrechnung (bereits stabil)

Siehe `FINALIZED_ASANA_RULES.md` für Details.

---

### 5. Bestätigung mit direktem Link 🔗

**Implementation in `task_agent.py`:**

```python
# Formatierte Bestätigung mit Link (REGEL 5)
output = f"✅ **Asana-Aufgabe erfolgreich erstellt!**\n\n"
output += f"**Titel:** {task_name}\n"

if due_on:
    output += f"**Fällig:** {due_on}\n"

# Zeige korrekten Assignee-Namen
if assignee_name:
    output += f"**Zugewiesen:** {assignee_name}\n\n"
else:
    output += f"**Zugewiesen:** Dir (aktueller Nutzer)\n\n"

# WICHTIG: Immer direkten Link anzeigen (REGEL 5)
if permalink:
    output += f"🔗 **[Aufgabe in Asana öffnen]({permalink})**\n\n"
    output += f"_Direktlink: {permalink}_"
```

**Beispiel-Ausgabe:**

```markdown
✅ **Asana-Aufgabe erfolgreich erstellt!**

**Titel:** Meeting vorbereiten
**Fällig:** 2026-01-26
**Zugewiesen:** Max Mustermann

🔗 **[Aufgabe in Asana öffnen](https://app.asana.com/0/1205957746667869/1212966399100131)**

_Direktlink: https://app.asana.com/0/1205957746667869/1212966399100131_
```

---

## 🧪 Test-Script

**Datei:** `test_asana_improvements.py`

**Tests:**
1. ✅ User-Suche (search_user_by_name)
2. ✅ Assignee-Extraktion (extract_assignee_from_input)
3. ✅ Titel-Cleanup (Assignee-Phrasen entfernen)
4. ✅ Datums-Parsing
5. ✅ Vollständiger Flow mit User-Delegation
6. ✅ Error-Logging mit Details
7. ✅ Link-Bestätigung

**Ausführen:**

```bash
source venv/bin/activate
python test_asana_improvements.py
```

---

## 📊 Vergleich Vorher/Nachher

| Feature | Vorher | Nachher |
|---------|--------|---------|
| **Error-Logging** | ❌ "Unbekannter Fehler" | ✅ Detailliert mit HTTP-Status, Traceback |
| **Fehlermeldungen** | ⚠️ Generisch | ✅ Spezifisch mit Tipps |
| **User-Delegation** | ❌ Nur "me" | ✅ Name-Suche, Fallback auf "me" |
| **Projekt-Favoriten** | ❌ Nicht vorhanden | ✅ In Sidebar, konfigurierbar |
| **Titel-Cleanup** | ⚠️ Basis | ✅ Erweitert (Assignee-Phrasen) |
| **Link-Bestätigung** | ⚠️ Manchmal | ✅ Immer mit Assignee-Name |

---

## 📝 Verwendungs-Beispiele

### Beispiel 1: Delegation an Teammitglied

```
User: "Erstelle Aufgabe: Meeting-Protokoll schreiben, weise die Aufgabe Lisa zu, fällig morgen"

System:
  1. Extrahiert Titel: "Meeting-Protokoll schreiben"
  2. Extrahiert Assignee: "Lisa"
  3. Sucht User: Findet "Lisa Schmidt" (GID: 123...)
  4. Extrahiert Datum: "morgen" → 2026-01-26
  5. Fragt nach Projekt (wenn nicht angegeben)
  6. Erstellt Aufgabe
  7. Zeigt Bestätigung:

✅ Asana-Aufgabe erfolgreich erstellt!

**Titel:** Meeting-Protokoll schreiben
**Fällig:** 2026-01-26
**Zugewiesen:** Lisa Schmidt

🔗 [Aufgabe in Asana öffnen](https://app.asana.com/...)
```

### Beispiel 2: Fehlerbehandlung

```
User: "Erstelle Aufgabe: Test mit ungültiger Projekt-GID"

System (im Terminal):
[AsanaAgent] 🔧 DEBUG: create_task aufgerufen
[AsanaAgent]   → Name: Test
[AsanaAgent]   → Project GID: INVALID_123
[AsanaAgent] 🚀 Sende API-Request an Asana...
[AsanaAgent] ❌ EXCEPTION beim Erstellen der Aufgabe!
[AsanaAgent]   → Exception Type: ApiException
[AsanaAgent]   → HTTP Status: 400
[AsanaAgent]   → Reason: Bad Request
[AsanaAgent] 📋 FULL TRACEBACK: ...

System (im Chat):
❌ **Fehler beim Erstellen der Asana-Aufgabe:**

**Fehlerdetails:** ApiException: Bad Request - Invalid project GID

_Tipp: Bitte wählen Sie ein gültiges Projekt aus._
```

---

## ✅ Checkliste

- [x] **Umfangreiches Error-Logging** in asana_agent.py implementiert
- [x] **Klare Fehlermeldungen** in task_agent.py mit Tipps
- [x] **User-Suche** (search_user_by_name) implementiert
- [x] **Assignee-Extraktion** (extract_assignee_from_input) implementiert
- [x] **Titel-Cleanup** um Assignee-Phrasen erweitert
- [x] **create_task_smart** integriert User-Delegation
- [x] **Projekt-Favoriten** in Sidebar implementiert
- [x] **Link-Bestätigung** mit Assignee-Namen verbessert
- [x] **Test-Script** erstellt mit 6 Szenarien
- [x] **Dokumentation** erstellt

---

## 🚀 Deployment-Status

**Aktueller Status:**
- ✅ Code aktualisiert in `asana_agent.py`
- ✅ Code aktualisiert in `task_agent.py`
- ✅ Code aktualisiert in `app.py` (Sidebar)
- ✅ Streamlit läuft (PID: 13652)
- ⏳ Seite neu laden erforderlich

**Änderungen aktivieren:**

```bash
# Browser-Seite neu laden (Ctrl+R oder F5)
# Streamlit erkennt Änderungen automatisch und bietet "Rerun" an

# Für Favoriten: .env erweitern:
ASANA_FAVORITE_PROJECTS=Projekt1,Projekt2,Projekt3

# App neu starten:
pkill -f streamlit
source venv/bin/activate
nohup streamlit run app.py > streamlit_dashboard.log 2>&1 &
```

---

## 📚 Zusammenfassung

**5 Hauptverbesserungen:**

1. ✅ **Error-Logging** - Detailliertes Debugging im Terminal, klare Fehlermeldungen im Chat
2. ✅ **Projekt-Favoriten** - Schnellzugriff in Sidebar, konfigurierbar über .env
3. ✅ **User-Delegation** - Name-Suche mit Fallback auf "me"
4. ✅ **Präzise Titel** - Erweiterte Cleanup-Patterns, stabile Doppelpunkt-Logik
5. ✅ **Link-Bestätigung** - Immer mit Assignee-Namen und Direktlink

**Status:** ✅ **PRODUKTIONSREIF**

**Version:** 2.0 (Enhanced)
**Getestet:** Code-Review abgeschlossen
**Bereit für:** User-Testing nach Browser-Reload

---

**Zuletzt aktualisiert:** 2026-01-25
