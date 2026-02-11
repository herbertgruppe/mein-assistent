# 📝 Asana-Integration - Dokumentation

## Übersicht

Die Asana-Integration ermöglicht es dem Multi-Agenten-Assistenten, direkt mit Ihrer Asana-Projektmanagement-Plattform zu interagieren. Sie können Aufgaben abrufen, erstellen und verwalten, ohne die Anwendung zu verlassen.

## ✨ Funktionen

### 1. **Sidebar: Asana-Status**
- Zeigt die nächsten 5 fälligen Aufgaben an
- Farbcodierung nach Dringlichkeit:
  - 🔴 **Heute fällig**
  - 🟡 **Morgen fällig**
  - 🟠 **In 1-3 Tagen**
  - ⚪ **Später**
  - ⚠️ **Überfällig**

### 2. **Chat-Integration**
Stellen Sie Fragen wie:
- "Was steht heute an?"
- "Zeige mir meine Aufgaben"
- "Welche Deadlines habe ich diese Woche?"

Der Assistent erkennt automatisch Asana-bezogene Anfragen und gibt Ihnen eine übersichtliche Liste.

### 3. **Berichte als Aufgaben**
Im **📚 Archiv-Tab** können Sie jeden Bericht mit einem Klick als Asana-Aufgabe anlegen:
- Button **✅ Asana** unter jedem Bericht
- Automatische Titelvorschläge basierend auf Dateinamen
- Beschreibung wird aus Berichtsinhalt übernommen
- Optionales Fälligkeitsdatum

### 4. **AsanaAgent**
Ein dedizierter Agent für Asana-Operationen:
- Liest Ihre Aufgaben
- Erstellt neue Aufgaben
- Aktualisiert bestehende Aufgaben
- Markiert Aufgaben als erledigt

## 🚀 Einrichtung

### Schritt 1: Personal Access Token erstellen

1. Gehen Sie zu [https://app.asana.com/0/my-apps](https://app.asana.com/0/my-apps)
2. Klicken Sie auf **"New Access Token"**
3. Geben Sie einen Namen ein (z.B. "Mein Assistent")
4. Kopieren Sie den generierten Token

### Schritt 2: Token in .env-Datei eintragen

Öffnen Sie die `.env`-Datei und fügen Sie hinzu:

```bash
# Asana Integration
ASANA_ACCESS_TOKEN=2/1234567890/1234567890:abcdef1234567890
```

**Wichtig:** Der Token muss geheim bleiben!

### Schritt 3: Anwendung neu starten

```bash
pkill -f streamlit
source venv/bin/activate
streamlit run app.py
```

### Schritt 4: Testen

1. Öffnen Sie [http://localhost:8501](http://localhost:8501)
2. Sidebar sollte nun Asana-Aufgaben anzeigen
3. Testen Sie im Chat: "Was steht heute an?"

## 📋 Verwendung

### Im Chat

**Aufgaben abrufen:**
```
"Was steht heute an?"
"Zeige mir meine Aufgaben"
"Welche Deadlines habe ich diese Woche?"
```

**Aufgabe erstellen (experimentell):**
```
"Erstelle eine Aufgabe: Meeting vorbereiten"
```

### In der Sidebar

Die Sidebar zeigt automatisch:
- Nächste 5 fällige Aufgaben
- Fälligkeit und Dringlichkeit
- Projektzuordnung

### Im Archiv

1. Gehen Sie zum **📚 Archiv** Tab
2. Öffnen Sie einen Bericht
3. Klicken Sie auf **✅ Asana**
4. Passen Sie Titel und Beschreibung an
5. Wählen Sie optional ein Fälligkeitsdatum
6. Klicken Sie **✓ Aufgabe erstellen**

## 🛠️ Technische Details

### Architektur

```
┌─────────────────┐
│   app.py        │
│  (UI Layer)     │
└────────┬────────┘
         │
    ┌────▼────────────────┐
    │ StreamlitOrchestrator│
    └────────┬────────────┘
             │
     ┌───────┴───────┐
     │               │
┌────▼─────┐   ┌────▼─────┐
│AsanaAgent│   │AsanaTool │
└────┬─────┘   └────┬─────┘
     │              │
     └──────┬───────┘
            │
      ┌─────▼─────┐
      │Asana API  │
      │(Python SDK)│
      └───────────┘
```

### Komponenten

**1. AsanaAgent** (`agents/asana_agent.py`)
- Hauptlogik für Asana-Interaktionen
- Verarbeitet natürlichsprachliche Anfragen
- Formatiert Ergebnisse für Anzeige

**2. AsanaTool** (`tools/asana_tool.py`)
- Wrapper für grundlegende Asana-Operationen
- `get_asana_tasks()`: Holt Aufgaben
- `create_asana_task()`: Erstellt neue Aufgaben

**3. StreamlitOrchestrator** (`app.py`)
- Workflow-Erkennung für Asana-Anfragen
- Routing zu AsanaAgent
- Integration in Chat-Historie

### API-Methoden

**AsanaAgent:**
```python
# Aufgaben abrufen
tasks = asana_agent.get_my_tasks(limit=20)
tasks = asana_agent.get_upcoming_tasks(days=7)

# Aufgabe erstellen
result = asana_agent.create_task(
    name="Titel",
    notes="Beschreibung",
    due_on="2026-01-31"
)

# Aufgabe aktualisieren
result = asana_agent.update_task(
    task_gid="1234567890",
    completed=True
)
```

**AsanaTool:**
```python
# Aufgaben abrufen
text = asana_tool.get_asana_tasks(days_ahead=7, limit=20)

# Aufgabe erstellen
text = asana_tool.create_asana_task(
    title="Titel",
    description="Beschreibung",
    due_date="2026-01-31"
)
```

## 🔧 Konfiguration

### Workflow-Erkennung

Der Orchestrator erkennt Asana-Anfragen anhand von Keywords:
- "aufgaben", "to-do", "todo"
- "was steht an", "termine", "deadlines"
- "aufgabe erstellen", "asana"
- "fällig", "erledigen"

### Sidebar-Anzeige

Konfigurierbar in `render_sidebar()`:
```python
# Anzahl angezeigter Aufgaben ändern
tasks = asana_agent.get_upcoming_tasks(days=14, limit=5)  # 5 → 10

# Zeitraum ändern
tasks = asana_agent.get_upcoming_tasks(days=30, limit=5)  # 14 → 30
```

## 🐛 Fehlerbehebung

### "Asana nicht konfiguriert"

**Problem:** Token fehlt oder ist ungültig

**Lösung:**
1. Prüfen Sie `.env`-Datei
2. Stellen Sie sicher, dass `ASANA_ACCESS_TOKEN` korrekt ist
3. Token neu erstellen unter [https://app.asana.com/0/my-apps](https://app.asana.com/0/my-apps)
4. App neu starten

### "Keine Workspaces gefunden"

**Problem:** Asana-Account hat keine Workspaces

**Lösung:**
1. Gehen Sie zu [https://app.asana.com](https://app.asana.com)
2. Erstellen Sie einen Workspace
3. Weisen Sie sich dem Workspace zu

### "Keine Aufgaben gefunden"

**Problem:** Keine Aufgaben vorhanden oder alle erledigt

**Lösung:**
1. Erstellen Sie Testaufgaben in Asana
2. Weisen Sie sich selbst als Bearbeiter zu
3. Setzen Sie ein Fälligkeitsdatum

### Button "✅ Asana" ist deaktiviert

**Problem:** Asana nicht konfiguriert

**Lösung:** Siehe "Asana nicht konfiguriert"

## 📊 Datenschutz & Sicherheit

### Was wird übertragen?

- **Gelesen:** Aufgabentitel, Beschreibungen, Fälligkeitsdaten, Projektzuordnungen
- **Geschrieben:** Neue Aufgaben (Titel, Beschreibung, Fälligkeitsdatum)
- **NICHT übertragen:** Passwörter, andere Workspace-Daten

### Token-Sicherheit

⚠️ **Wichtig:**
- Teilen Sie Ihren Access Token niemals
- Der Token hat Zugriff auf Ihren gesamten Asana-Account
- Verwenden Sie `.gitignore`, um `.env` nicht zu committen
- Widerrufen Sie alte Tokens unter [https://app.asana.com/0/my-apps](https://app.asana.com/0/my-apps)

### Best Practices

1. **Token regelmäßig erneuern** (alle 3-6 Monate)
2. **Verwenden Sie dedizierte Tokens** pro Anwendung
3. **Logs prüfen** auf verdächtige Aktivitäten
4. **Workspace-Zugriffsrechte** minimal halten

## 🔄 Erweiterungen

### Neue Features hinzufügen

**Aufgaben filtern nach Projekt:**
```python
# In AsanaAgent
def get_tasks_by_project(self, project_gid: str, limit: int = 20):
    tasks = list(self.client.tasks.find_by_project(project_gid, limit=limit))
    return self._format_tasks(tasks)
```

**Kommentare hinzufügen:**
```python
# In AsanaAgent
def add_comment(self, task_gid: str, comment: str):
    self.client.stories.create_story_for_task(task_gid, {'text': comment})
```

**Subtasks erstellen:**
```python
# In AsanaAgent
def create_subtask(self, parent_gid: str, name: str):
    return self.client.tasks.create_subtask_for_task(parent_gid, {'name': name})
```

## 📚 Weiterführende Links

- [Asana API Dokumentation](https://developers.asana.com/docs)
- [Asana Python SDK](https://github.com/Asana/python-asana)
- [Personal Access Tokens](https://developers.asana.com/docs/personal-access-token)

## ✅ Checkliste

- [ ] Personal Access Token erstellt
- [ ] Token in `.env` eingetragen
- [ ] Anwendung neu gestartet
- [ ] Sidebar zeigt Asana-Aufgaben
- [ ] Chat-Test: "Was steht heute an?" funktioniert
- [ ] Asana-Button im Archiv funktioniert
- [ ] Testaufgabe erfolgreich erstellt

## 🎉 Fertig!

Die Asana-Integration ist jetzt einsatzbereit. Ihr Assistent kann:
- ✅ Aufgaben anzeigen
- ✅ Aufgaben erstellen
- ✅ Fälligkeiten überwachen
- ✅ Berichte in Aufgaben umwandeln

---

**Version:** 1.0
**Datum:** 2026-01-25
**Status:** Produktionsreif
**Autor:** Claude Sonnet 4.5
