# Meeting Manager - Aktueller Stand (27.01.2026)

## 📋 Überblick

Der Meeting Manager ist ein integriertes System zur automatischen Verarbeitung von Meeting-Transkripten mit Outlook- und Asana-Integration. Er unterstützt den kompletten Workflow von der Vorbereitung über die Nachbereitung bis zur Aufgabenverwaltung.

---

## 🔄 Prozessbeschreibung (User-Perspektive)

### **A) Vorbereitung (vor dem Meeting)**

**Zweck:** Vorbereitung auf anstehende Meetings mit Kontext aus Asana

**Workflow:**
1. **Meeting-Anzeige**
   - System zeigt nächstes anstehendes Meeting aus Outlook-Kalender
   - Anzeige von: Titel, Start-/Endzeit, Ort

2. **Task-Kontext**
   - Automatische Zuordnung zu Asana-Projekt basierend auf:
     - **Keyword-Matching:** Meeting-Titel enthält konfigurierte Keywords (z.B. "myTGA" → myTGA-Projekt)
     - **Fuzzy-Search Fallback:** Ähnlichkeitssuche über alle Asana-Projekte
   - Anzeige aller offenen Tasks des zugeordneten Projekts

3. **Ergebnis:** User geht informiert ins Meeting mit Übersicht über offene Aufgaben

---

### **B) Nachbereitung (nach dem Meeting)**

#### **B1: Transkript hochladen & verarbeiten**

**Zweck:** Automatische Umbenennung und Archivierung von Transkripten

**Workflow:**
1. **Upload**
   - User lädt Transkript-Datei hoch (.txt, .md, .pdf)
   - System zeigt Dateiname und Größe

2. **Vorschau-Analyse** (optional, empfohlen)
   - User klickt "🔍 Vorschau: Datum/Meeting ermitteln"
   - System analysiert und zeigt:
     - Erkanntes Datum/Zeit
     - Quelle (Transkript-Inhalt / PDF-Metadaten / Datei-Metadaten)
     - Gefundenes Outlook-Meeting (falls vorhanden)

3. **Manuelle Korrektur** (optional)
   - User kann Datum/Zeit manuell überschreiben
   - Nützlich bei Fehlerkennungen

4. **Verarbeitung**
   - User klickt "✅ Jetzt verarbeiten & umbenennen"
   - System:
     - Ermittelt Datum/Zeit (Priorität: User-Input > Transkript-Inhalt > PDF-Metadaten > Datei-Metadaten)
     - Sucht Meeting in Outlook (±15 Minuten Toleranz)
     - Nutzt Meeting-Titel ODER generiert Titel via LLM
     - Benennt Datei um: `YYYY-MM-DD_MeetingName.ext`
     - Verschiebt nach `transcripts/processed/`

5. **Ergebnis:** Strukturiert archiviertes Transkript mit sprechendem Namen

#### **B2: Task-Extraktion & Asana-Sync**

**Zweck:** Automatische Erkennung und Übertragung von Aufgaben ins Task-System

**Workflow:**
1. **Analyse starten**
   - System zeigt neustes verarbeitetes Transkript
   - User klickt "🔍 Transkript analysieren & Tasks extrahieren"

2. **LLM-Extraktion**
   - LLM (Claude/GPT) analysiert Transkript-Inhalt
   - Extrahiert konkrete Aufgaben mit:
     - Titel (actionable, max 80 Zeichen)
     - Beschreibung (Kontext aus Meeting)
     - Fälligkeitsdatum (falls im Transkript erwähnt)

3. **Interaktive Bearbeitung**
   - Tasks werden in editierbarem Tabellen-Editor angezeigt
   - User kann:
     - Tasks aktivieren/deaktivieren (Checkbox)
     - Titel/Beschreibung bearbeiten
     - Fälligkeitsdatum ändern
     - Tasks hinzufügen/löschen
     - Änderungen speichern

4. **Asana-Sync**
   - User klickt "✅ Finalisieren & in Asana erstellen"
   - System:
     - Erstellt alle aktivierten Tasks in Asana
     - Zeigt Erfolgs-/Fehlermeldungen pro Task
     - Löscht Session-Daten bei Erfolg

5. **Ergebnis:** Alle Meeting-Aufgaben strukturiert in Asana erfasst

---

### **C) Background Service (optional)**

**Zweck:** Automatische Batch-Verarbeitung ohne UI

**Workflow:**
1. User startet Background Service über UI
2. Service überwacht `transcripts/incoming/` auf neue Dateien
3. Automatische Verarbeitung neuer Dateien (wie B1)
4. Nützlich für: Bulk-Verarbeitung, Integration mit anderen Tools

---

## 🏗️ Technische Architektur

### **Komponenten-Übersicht**

```
┌─────────────────────────────────────────────────────────────┐
│                    Streamlit UI (app.py)                     │
│  ┌──────────────────────┐  ┌──────────────────────────────┐ │
│  │  Tab A: Vorbereitung │  │  Tab B: Nachbereitung        │ │
│  │  - Outlook Events    │  │  - Upload & Verarbeitung     │ │
│  │  - Asana Tasks       │  │  - Task-Extraktion (LLM)     │ │
│  └──────────────────────┘  └──────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌───────────────┐  ┌──────────────────┐  ┌─────────────┐
│ MeetingManager│  │ OutlookGraphTool │  │ AsanaAgent  │
│ (Core Logic)  │  │ (MS Graph API)   │  │ (Asana API) │
└───────────────┘  └──────────────────┘  └─────────────┘
        │
        ▼
┌────────────────────────────────────────┐
│  config/mapping_config.json            │
│  - Keyword → Asana-Projekt Mappings    │
└────────────────────────────────────────┘
```

---

### **Kern-Module**

#### **1. meeting_manager.py** (Core Business Logic)

**Klasse:** `MeetingManager`

**Hauptmethoden:**

- `__init__()` - Initialisierung
  - OutlookGraphTool (MS Graph API)
  - AsanaAgent (Asana API)
  - LLM (Claude/GPT)
  - Lädt Projekt-Mappings aus JSON

- `get_transcript_datetime(file_path, user_provided_datetime=None)` → `(datetime, source)`
  - **Priorität:**
    1. User-provided datetime (UI-Eingabe)
    2. Transkript-Inhalt (Regex-Suche nach Datums-/Zeitmustern)
    3. PDF-Metadaten (CreationDate)
    4. Datei-Metadaten (st_ctime/st_mtime)
  - **Returns:** Tuple (datetime-Objekt, Quell-String)

- `extract_datetime_from_content(content)` → `Optional[datetime]`
  - Regex-basierte Extraktion aus Text
  - Unterstützte Formate:
    - ISO: `2026-01-26 10:03:15`
    - DE: `26.01.2026 10:03`
    - US: `01/26/2026 10:03`

- `extract_pdf_creation_date(file_path)` → `Optional[datetime]`
  - Liest PDF-Metadaten via pypdf/PyPDF2
  - Parst `/CreationDate` im Format `D:YYYYMMDDHHmmSS`

- `find_meeting_at_time(target_time, tolerance_minutes=15)` → `Optional[Dict]`
  - Sucht Outlook-Meeting via Graph API
  - Zeitfenster: ±tolerance_minutes
  - Returns: Meeting-Dict mit title, start, end, location

- `get_asana_project_context(meeting_title, fuzzy_threshold=0.6)` → `Optional[Dict]`
  - **Schritt 1:** Keyword-Match in mapping_config.json
  - **Schritt 2:** Fuzzy-Search über alle Asana-Projekte (SequenceMatcher)
  - **Returns:** Dict mit:
    - `project_gid`: Asana-Projekt-ID
    - `project_name`: Projekt-Name
    - `open_tasks`: Liste offener Tasks
    - `match_type`: 'keyword' oder 'fuzzy'
    - `match_score`: Ähnlichkeits-Score (nur bei fuzzy)

- `generate_title_from_transcript(transcript_path, max_chars=2000)` → `str`
  - LLM-basierte Titel-Generierung
  - Liest erste 2000 Zeichen
  - Prompt: Kurzer, prägnanter Titel (max 50 Zeichen)

- `process_transcript(file_path, user_provided_datetime=None)` → `bool`
  - **Hauptverarbeitungs-Pipeline:**
    1. Datum/Zeit ermitteln
    2. Meeting suchen
    3. Titel bestimmen (Meeting-Titel oder LLM)
    4. Datei umbenennen
    5. Nach `transcripts/processed/` verschieben
  - **Returns:** True bei Erfolg

- `start_watching()` - Background Service
  - Watchdog-Observer für `transcripts/incoming/`
  - FileSystemEventHandler reagiert auf neue Dateien
  - Endlosschleife bis Ctrl+C

**Dependencies:**
- `watchdog` - Ordnerüberwachung
- `langchain_anthropic`/`langchain_openai` - LLM
- `langchain_community.document_loaders.PyPDFLoader` - PDF-Extraktion
- `pypdf`/`PyPDF2` - PDF-Metadaten
- `tools.outlook_graph_tool.OutlookGraphTool` - Outlook
- `agents.asana_agent.AsanaAgent` - Asana

---

#### **2. app.py** (Streamlit UI)

**Relevante Funktionen:**

- `extract_tasks_from_transcript(transcript_text, llm)` → `List[Dict]`
  - LLM-Prompt für Task-Extraktion
  - JSON-Parsing der LLM-Response
  - Returns: Liste von Tasks mit title, description, due_date

- `render_transcripts_tab()`
  - **Tab A: Vorbereitung**
    - Holt nächstes Outlook-Meeting
    - Zeigt Meeting-Details
    - Ruft `get_asana_project_context()` auf
    - Zeigt offene Tasks

  - **Tab B: Nachbereitung**
    - **Upload-Bereich:**
      - File Uploader
      - Preview-Button (Vorschau-Analyse)
      - Manuelle Datum/Zeit-Eingabe
      - Verarbeiten-Button
    - **Task-Extraktion:**
      - Neuestes Transkript laden
      - LLM-Analyse-Button
      - `st.data_editor` für interaktive Bearbeitung
      - Asana-Sync-Button
    - **Background Service:**
      - Start/Stop-Controls
      - Status-Anzeige
    - **Datei-Übersicht:**
      - Warteschlange (incoming)
      - Verarbeitete Dateien (processed)

**State Management:**
- `st.session_state['orchestrator']` - StreamlitOrchestrator-Instanz
- `st.session_state['extracted_tasks']` - Extrahierte Tasks
- `st.session_state['transcript_source']` - Quell-Dateiname
- `st.session_state['upload_preview']` - Preview-Analyse-Ergebnis

---

#### **3. config/mapping_config.json** (Konfiguration)

**Struktur:**
```json
{
  "project_mappings": {
    "myTGA": {
      "asana_project_gid": "1208590847953108",
      "keywords": ["myTGA", "mytga", "TGA", "tga"],
      "description": "myTGA Projekt"
    },
    "WPM": {
      "asana_project_gid": null,
      "keywords": ["WPM", "wpm", "Work Package Manager"],
      "description": "WPM Projekt"
    }
  },
  "fuzzy_search_threshold": 0.6
}
```

**Verwendung:**
- Keywords sind case-insensitive
- `asana_project_gid: null` → nur Fuzzy-Search
- `fuzzy_search_threshold`: 0.0 (keine Ähnlichkeit) bis 1.0 (perfekte Übereinstimmung)

---

### **Dateistruktur**

```
mein-assistent/
├── meeting_manager.py           # Core Business Logic
├── app.py                        # Streamlit UI
├── config/
│   └── mapping_config.json      # Projekt-Mappings
├── transcripts/
│   ├── incoming/                # Upload-Ordner
│   └── processed/               # Verarbeitete Transkripte
├── tools/
│   ├── outlook_graph_tool.py    # MS Graph API
│   └── asana_tool.py            # Asana Tool Wrapper
├── agents/
│   └── asana_agent.py           # Asana Agent (detaillierte Logic)
└── meeting_manager.log          # Log-Datei
```

---

### **Schnittstellen & APIs**

#### **Microsoft Graph API (Outlook)**
- **Authentifizierung:** OAuth2 Device Code Flow / MSAL
- **Endpunkt:** `/me/calendar/events`
- **Verwendung:**
  - `get_events_for_date_range(start_date, end_date)` → Events-Liste
- **Credentials:**
  - `MICROSOFT_CLIENT_ID` (Azure App Registration)
  - `MICROSOFT_TENANT_ID`
  - Token-Cache: `.outlook_token.json`

#### **Asana API**
- **Authentifizierung:** Personal Access Token
- **Verwendung:**
  - `list_projects(limit=100)` → Alle Projekte
  - `get_project_tasks(project_gid, limit=20)` → Tasks eines Projekts
  - `create_task(name, notes, due_on)` → Neue Task erstellen
- **Credentials:**
  - `ASANA_ACCESS_TOKEN`

#### **LLM APIs**
- **Anbieter:** Anthropic (Claude) oder OpenAI (GPT)
- **Verwendung:**
  - Titel-Generierung aus Transkript
  - Task-Extraktion aus Transkript
- **Credentials:**
  - `ANTHROPIC_API_KEY` oder `OPENAI_API_KEY`
  - `LLM_PROVIDER` (anthropic/openai)

---

## 🔧 Konfiguration & Setup

### **Umgebungsvariablen (.env)**

```bash
# LLM
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
RESEARCH_MODEL=claude-sonnet-4-5

# Microsoft Graph API
MICROSOFT_CLIENT_ID=...
MICROSOFT_TENANT_ID=...

# Asana
ASANA_ACCESS_TOKEN=...
```

### **Dependencies (requirements.txt)**

```
streamlit
watchdog
langchain-anthropic
langchain-openai
langchain-community
pypdf
python-dotenv
asana
msal
```

---

## 🚀 Deployment & Nutzung

### **Lokale Entwicklung**

```bash
# Installation
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Konfiguration
cp .env.example .env
# .env bearbeiten mit API-Keys

# Streamlit UI starten
streamlit run app.py

# Background Service starten (optional)
python meeting_manager.py
```

### **Produktiv-Betrieb**

1. **Option A: Streamlit Cloud**
   - Repo auf GitHub pushen
   - Streamlit Cloud verbinden
   - Secrets in Streamlit UI konfigurieren

2. **Option B: Docker**
   ```dockerfile
   FROM python:3.12
   WORKDIR /app
   COPY . .
   RUN pip install -r requirements.txt
   EXPOSE 8502
   CMD ["streamlit", "run", "app.py"]
   ```

3. **Option C: Systemd Service** (Background)
   ```ini
   [Unit]
   Description=Meeting Manager Background Service

   [Service]
   Type=simple
   User=youruser
   WorkingDirectory=/path/to/mein-assistent
   ExecStart=/path/to/venv/bin/python meeting_manager.py
   Restart=always

   [Install]
   WantedBy=multi-user.target
   ```

---

## 📈 Erweiterungsmöglichkeiten

### **Kurzfristig (Quick Wins)**

1. **Benachrichtigungen**
   - Email-Benachrichtigung bei neuen Transkripten
   - Slack-Integration für Task-Erstellung

2. **Batch-Verarbeitung**
   - UI-Button "Alle verarbeiten" für incoming-Ordner
   - Fortschrittsbalken

3. **Filter & Suche**
   - Volltextsuche in verarbeiteten Transkripten
   - Filter nach Datum/Meeting-Typ

### **Mittelfristig (Features)**

4. **Template-System**
   - Vordefinierte Meeting-Typen mit Standard-Tasks
   - "1:1 Meeting" → Automatisch Tasks: "Feedback", "Nächste Schritte"

5. **Multi-User Support**
   - User-spezifische Kalender
   - Team-Workspaces in Asana

6. **Erweiterte Analytik**
   - Meeting-Statistiken (Häufigkeit, Dauer)
   - Task-Completion-Rate pro Projekt

### **Langfristig (Komplexe Features)**

7. **Echtzeit-Transkription**
   - Integration mit Meeting-Tools (Teams, Zoom)
   - Live-Transkription während Meeting

8. **KI-Assistent**
   - Automatische Meeting-Zusammenfassungen
   - Action-Item-Tracking über Zeit
   - Sentiment-Analyse

9. **Mobile App**
   - React Native / Flutter App
   - Push-Benachrichtigungen

---

## 🐛 Bekannte Limitierungen

1. **Datums-Erkennung**
   - Funktioniert nur mit strukturierten Formaten
   - Natürliche Sprache ("nächste Woche") nicht unterstützt

2. **Fuzzy-Search**
   - Keine semantische Suche (nur String-Matching)
   - Threshold manuell anpassbar, aber nicht adaptiv

3. **LLM-Kosten**
   - Task-Extraktion kann bei langen Transkripten teuer werden
   - Kein Caching implementiert

4. **Concurrency**
   - Background Service ist Single-Threaded
   - Keine parallele Verarbeitung mehrerer Dateien

5. **Error Handling**
   - API-Fehler werden geloggt, aber User-Feedback ausbaufähig
   - Keine Retry-Logik bei temporären Fehlern

---

## 📝 Testing

### **Manuelle Tests**

```bash
# Unit Tests für einzelne Funktionen
python test_meeting_cockpit.py

# Integration Test (erfordert API-Keys)
python test_meeting_manager.py
```

### **Test-Szenarien**

1. **Transkript mit Datum im Inhalt**
   - Erstelle TXT mit "2026-01-27 14:00" am Anfang
   - Upload → Preview sollte Datum erkennen

2. **PDF ohne Datum**
   - Upload PDF ohne Datum im Text
   - Preview sollte PDF-Metadaten oder Datei-Zeit nutzen

3. **Meeting-Matching**
   - Erstelle Outlook-Event für heute
   - Upload Transkript mit passendem Zeitstempel
   - System sollte Meeting finden

4. **Asana-Projekt-Mapping**
   - Meeting-Titel enthält "myTGA"
   - System sollte myTGA-Projekt finden
   - Zeigt offene Tasks

5. **Task-Extraktion**
   - Upload Transkript mit Aufgaben (z.B. "Max soll Budget erstellen bis Freitag")
   - LLM sollte Task extrahieren mit Due-Date

---

## 📚 Dokumentation

- **Quickstart:** `MEETING_MANAGER_QUICKSTART.md`
- **Vollständige Doku:** `MEETING_MANAGER_README.md`
- **Status (dieses Dokument):** `MEETING_MANAGER_STATUS.md`
- **Test-Suite:** `test_meeting_cockpit.py`
- **Beispiel-Nutzung:** `meeting_manager_example.py`

---

## 🔐 Security & Compliance

### **Credentials**
- Alle API-Keys in `.env` (nicht in Git!)
- `.gitignore` enthält `.env`, `*.log`, `.outlook_token.json`

### **Daten**
- Transkripte lokal gespeichert (nicht in Cloud)
- Keine Verschlüsselung at-rest (TODO für Produktion)
- API-Kommunikation über HTTPS

### **DSGVO**
- Meeting-Daten werden lokal verarbeitet
- Asana: Cloud-Service (EU-Server verfügbar)
- Outlook: Microsoft Cloud (DSGVO-konform)

---

## 📊 Performance

### **Benchmarks (Durchschnitt)**

- PDF-Text-Extraktion (35 Seiten): ~3 Sekunden
- Datum-Extraktion aus Inhalt: <0.1 Sekunden
- Outlook Meeting-Suche: ~1 Sekunde
- Asana-Projekt-Suche (100 Projekte): ~1 Sekunde
- LLM-Task-Extraktion: 5-10 Sekunden (je nach Transkript-Länge)
- Komplette Verarbeitung: 10-15 Sekunden

### **Optimierungen**

- Caching in Streamlit (10 Min TTL für Asana/Outlook)
- PDF-Text wird nur einmal gelesen
- Fuzzy-Search wird nur bei Bedarf ausgeführt

---

## 🎯 Roadmap

### **v1.0 (aktuell) - MVP**
✅ Transkript-Upload & -Verarbeitung
✅ Outlook-Meeting-Matching
✅ Asana-Integration (Projekt-Mapping)
✅ LLM-basierte Task-Extraktion
✅ Interaktiver Task-Editor
✅ Background Service

### **v1.1 (nächste Version)**
- [ ] Benachrichtigungen (Email/Slack)
- [ ] Batch-Verarbeitung mit Progress
- [ ] Verbesserte Error Messages
- [ ] Retry-Logik für API-Fehler

### **v2.0 (große Features)**
- [ ] Template-System für Meeting-Typen
- [ ] Multi-User Support
- [ ] Meeting-Analytik Dashboard
- [ ] Semantische Suche (Embeddings)

---

**Erstellt:** 27.01.2026
**Version:** 1.0
**Autor:** Claude Sonnet 4.5 + User (sherbert)
**Lizenz:** Private/Intern
