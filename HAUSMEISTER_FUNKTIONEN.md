# 🧹 Hausmeister-Funktionen - Organisations-Features

## Übersicht

Die Web-UI wurde um leistungsstarke Organisations-Features erweitert, die den Assistenten zu einem aufgeräumten Werkzeug für den Arbeitsalltag bei der Herbert Gruppe machen.

## ✨ Neue Features

### 1. 🔄 Neuer Chat / Thema wechseln

**Position:** Ganz oben in der Sidebar (prominent platziert)

**Funktion:**
- Startet einen neuen Chat mit frischem Kontext
- Löscht die aktuelle Chat-Historie (st.session_state.chat_history)
- Löscht die Konversations-Historie im Memory (conversation_context)
- **WICHTIG:** Behält das Langzeitgedächtnis (user_profile.json)
  - Name bleibt erhalten
  - Beruf bleibt erhalten
  - Wichtige Erkenntnisse bleiben erhalten

**Verwendung:**
1. Klicken Sie auf "🔄 Neuer Chat / Thema wechseln"
2. Chat wird sofort zurückgesetzt
3. Assistent kennt Sie weiterhin (Langzeitgedächtnis intakt)

**Wann verwenden:**
- Beim Wechsel zu einem neuen Thema
- Wenn die Chat-Historie zu lang wird
- Um einen "frischen Start" zu bekommen
- Bei thematischen Kontextwechseln

### 2. 📁 Archiv-Ordnerverwaltung

**Position:** Im Archiv-Tab unter "📁 Ordner-Verwaltung"

**Funktionen:**

#### Neuen Ordner erstellen
- Textfeld für Ordnernamen
- Automatische Bereinigung (Sonderzeichen werden entfernt)
- Button "📁 Erstellen" zum Anlegen

**Beispiele für Ordner:**
- Wärmepumpen
- Projekte-2026
- Analysen
- Kundenkommunikation
- Technische-Berichte

#### Ordner anzeigen
- Liste aller vorhandenen Unterordner
- Anzahl der Dateien pro Ordner sichtbar
- Übersichtliche Gruppierung

#### Ordner löschen
- 🗑️ Button neben jedem Ordner
- Prüfung ob Ordner leer ist
- Nur leere Ordner können gelöscht werden (Sicherheit)

**Verwendung:**
```
1. Öffnen Sie den Archiv-Tab
2. Klappen Sie "📁 Ordner-Verwaltung" auf
3. Geben Sie Ordnernamen ein (z.B. "Wärmepumpen")
4. Klicken Sie "📁 Erstellen"
5. Ordner erscheint in der Liste
```

### 3. 📦 Verschieben-Funktion

**Position:** Bei jedem Bericht im Archiv

**Funktionen:**
- Button "📦 Verschieben" bei jedem Bericht
- Dropdown-Menü mit allen verfügbaren Ordnern
- Auswahl zwischen:
  - 📂 Hauptverzeichnis
  - 📁 [Alle erstellten Unterordner]

**Ablauf:**
1. Klicken Sie auf "📦 Verschieben"
2. Dropdown erscheint mit Zielordnern
3. Wählen Sie Zielordner
4. Klicken Sie "✓ Verschieben" oder "✗ Abbrechen"
5. Datei wird verschoben
6. UI aktualisiert automatisch (st.rerun())

**Verwendung:**
- Berichte nach Themen organisieren
- Alte Berichte in Archiv-Ordner verschieben
- Projektbezogene Dokumente gruppieren

### 4. 🗑️ Löschen mit Sicherheitsabfrage

**Position:** Bei jedem Bericht im Archiv

**Funktionen:**
- Button "🗑️ Löschen" bei jedem Bericht
- Zweistufige Sicherheitsabfrage:
  1. Klick auf "🗑️ Löschen"
  2. Warnung erscheint: "⚠️ Wirklich löschen?"
  3. Bestätigung "✓ Ja, löschen" oder "✗ Abbrechen"

**Sicherheitsfeatures:**
- Deutliche Warnung vor dem Löschen
- Hinweis: "Diese Aktion kann nicht rückgängig gemacht werden!"
- Zweistufiger Prozess verhindert versehentliches Löschen
- Visuelles Feedback (rot/warning)

**Ablauf:**
1. Klicken Sie "🗑️ Löschen"
2. Warnung erscheint
3. Klicken Sie "✓ Ja, löschen" zum Bestätigen
4. Datei wird gelöscht
5. Erfolgsmeldung erscheint
6. UI aktualisiert automatisch

## 🎯 Workflow-Beispiele

### Beispiel 1: Projekt-Organisation

**Szenario:** Sie arbeiten an mehreren Wärmepumpen-Projekten

```
1. Erstellen Sie Ordner:
   - "Wärmepumpen-2026"
   - "Analysen-Energie"
   - "Kundendokumentation"

2. Generieren Sie Berichte im Chat

3. Im Archiv:
   - Verschieben Sie Wärmepumpen-Berichte nach "Wärmepumpen-2026"
   - Verschieben Sie Analysen nach "Analysen-Energie"

4. Ergebnis:
   - Übersichtliche Struktur
   - Schneller Zugriff auf relevante Dokumente
   - Thematische Gruppierung
```

### Beispiel 2: Themenwechsel im Chat

**Szenario:** Sie wechseln von Wärmepumpen zu Gebäudeautomation

```
1. Aktueller Chat über Wärmepumpen läuft

2. Klicken Sie "🔄 Neuer Chat / Thema wechseln"

3. Vorteile:
   - Sauberer Kontext für neues Thema
   - Keine Vermischung der Themen
   - Assistent kennt Sie weiterhin
   - Langzeitgedächtnis bleibt erhalten

4. Starten Sie neue Anfragen zur Gebäudeautomation
```

### Beispiel 3: Archiv aufräumen

**Szenario:** Archiv ist unübersichtlich geworden

```
1. Ordner erstellen:
   - "Archiv-2025"
   - "Aktuelle-Projekte"
   - "Technische-Dokumentation"

2. Berichte sortieren:
   - Alte Berichte → "Archiv-2025"
   - Aktive Projekte → "Aktuelle-Projekte"
   - Tech-Docs → "Technische-Dokumentation"

3. Aufräumen:
   - Veraltete/fehlerhafte Berichte löschen
   - Duplikate entfernen

4. Ergebnis:
   - Aufgeräumtes Archiv
   - Schneller Zugriff
   - Bessere Übersicht
```

## 🔄 Automatisches UI-Refresh

**Implementierung:** Alle Aktionen nutzen `st.rerun()`

**Automatisches Refresh nach:**
- ✅ Neuer Chat gestartet
- ✅ Ordner erstellt
- ✅ Ordner gelöscht
- ✅ Bericht verschoben
- ✅ Bericht gelöscht
- ✅ Datei hochgeladen (Dokumente-Tab)
- ✅ Datei gelöscht (Dokumente-Tab)

**Vorteil:**
- Sofortige Aktualisierung der Anzeige
- Keine manuellen Reloads nötig
- Konsistente Darstellung
- Professionelle User Experience

## 📊 Technische Details

### Dateistruktur

```
newsletter_archiv/
├── bericht1.md                    # Hauptverzeichnis
├── bericht2.md
├── Wärmepumpen-2026/             # Unterordner
│   ├── analyse1.md
│   └── projekt-x.md
├── Analysen-Energie/
│   └── studie.md
└── Archiv-2025/
    └── alte-berichte.md
```

### Session State Management

**Gespeichert in st.session_state:**
- `chat_history` - Aktuelle Chat-Nachrichten (wird bei "Neuer Chat" gelöscht)
- `orchestrator` - Orchestrator-Instanz (bleibt erhalten)
- `workflow_mode` - Aktueller Workflow
- `show_move_{unique_key}` - Verschieben-Dialog-Status
- `confirm_delete_{unique_key}` - Löschen-Bestätigungs-Status

**Persistiert in user_profile.json:**
- User-Profil (Name, Beruf, etc.)
- Research Insights
- Wichtige Erkenntnisse

**Wird bei "Neuer Chat" gelöscht:**
- Chat-Historie (st.session_state.chat_history)
- Konversations-Context (memory.conversation_context)

### Funktions-Übersicht

```python
# Neue Funktionen in app.py

reset_chat_session()
# Setzt Chat zurück, behält Langzeitgedächtnis

get_archive_subfolders(archive_dir)
# Gibt Liste aller Unterordner zurück

get_archive_files_grouped(archive_dir)
# Gibt Dateien gruppiert nach Ordnern zurück

render_report_list(reports, available_folders, archive_dir, current_folder)
# Rendert Berichte mit allen Aktions-Buttons
```

## 🎨 UI/UX Verbesserungen

### Visuelle Hierarchie
- 🔄 Neuer Chat Button - Primary Button, prominent
- 📁 Ordner-Verwaltung - Expandable, nicht aufdringlich
- 📦 Verschieben - Dialog on-demand
- 🗑️ Löschen - Zweistufige Warnung mit visueller Betonung

### Feedback-System
- ✅ Erfolgsmeldungen (grün)
- ⚠️ Warnungen (gelb/orange)
- ❌ Fehler (rot)
- Automatisches Refresh nach Aktionen

### Konsistenz
- Einheitliche Button-Beschriftungen
- Konsistente Icons
- Gleiche Spalten-Layouts
- Wiedererkennbare Patterns

## 🚀 Verwendung im Alltag

### Morgens beim Start
```
1. Öffnen Sie http://localhost:8501
2. Klicken Sie "🔄 Neuer Chat" für frischen Start
3. Beginnen Sie mit Ihrer ersten Anfrage
```

### Während der Arbeit
```
1. Nutzen Sie Chat für Recherchen und Aufgaben
2. Berichte werden automatisch im Archiv gespeichert
3. Bei Themenwechsel: "🔄 Neuer Chat"
```

### Abends beim Aufräumen
```
1. Wechseln Sie zum Archiv-Tab
2. Erstellen Sie thematische Ordner falls nötig
3. Verschieben Sie Berichte in passende Ordner
4. Löschen Sie fehlerhafte/veraltete Berichte
5. Archiv ist organisiert für nächsten Tag
```

## 💡 Tipps & Best Practices

### Ordner-Benennung
- ✅ Kurz und prägnant: "Wärmepumpen"
- ✅ Mit Jahr: "Projekte-2026"
- ✅ Nach Kategorie: "Analysen", "Berichte", "Kundendocs"
- ❌ Zu lange Namen vermeiden
- ❌ Keine Sonderzeichen (werden automatisch entfernt)

### Chat-Management
- Neuer Chat bei Themenwechsel
- Langzeitgedächtnis wird automatisch genutzt
- Regelmäßig aufräumen für Performance

### Archiv-Organisation
- Wöchentlich/monatlich aufräumen
- Thematische Ordner erstellen
- Alte Berichte in Archiv-Ordner
- Wichtige Berichte im Hauptverzeichnis

### Performance
- Nicht zu viele Berichte im Hauptverzeichnis
- Ordner für verschiedene Themenbereiche
- Regelmäßig aufräumen
- Alte Berichte löschen oder archivieren

## 🔐 Sicherheit

### Lösch-Schutz
- Zweistufige Bestätigung
- Deutliche Warnungen
- "Nicht rückgängig"-Hinweis
- Visuelle Betonung

### Daten-Integrität
- Langzeitgedächtnis geschützt
- Nur Chat-Historie wird bei Reset gelöscht
- Backups empfohlen (user_profile.json)

### Fehlerbehandlung
- Try-Except für alle Datei-Operationen
- Benutzerfreundliche Fehlermeldungen
- Automatisches Rollback bei Fehlern

## 📝 Zusammenfassung

Die Hausmeister-Funktionen machen den Assistenten zu einem professionellen Werkzeug:

✅ **Neuer Chat** - Saubere Themenwechsel
✅ **Ordnerverwaltung** - Strukturierte Organisation
✅ **Verschieben** - Flexible Dokumentenverwaltung
✅ **Sicheres Löschen** - Schutz vor Datenverlust
✅ **Auto-Refresh** - Moderne User Experience

**Ergebnis:** Ein aufgeräumtes, organisiertes System für den Arbeitsalltag bei der Herbert Gruppe.

---

**Version:** 1.0
**Datum:** 2026-01-25
**Status:** Produktiv
**URL:** http://localhost:8501
