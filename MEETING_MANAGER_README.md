# Meeting Manager - Automatische Transkript-Verarbeitung

## Übersicht

Der Meeting Manager überwacht automatisch den Ordner `transcripts/incoming` auf neue Transkript-Dateien und verarbeitet diese:

1. **Metadaten auslesen**: Erstellungsdatum und -zeit der Datei
2. **Outlook-Integration**: Sucht über Microsoft Graph API nach dem passenden Termin
3. **LLM-Fallback**: Falls kein Termin gefunden wird, generiert ein LLM einen Titel aus dem Transkript-Anfang
4. **Umbenennung & Verschiebung**: Datei wird nach Schema `YYYY-MM-DD_MeetingName.txt` benannt und nach `transcripts/processed` verschoben

## Voraussetzungen

### 1. Python-Pakete installieren

```bash
pip install -r requirements.txt
```

Neu hinzugefügt wurde:
- `watchdog` - Für Ordnerüberwachung

### 2. Microsoft Graph API Authentifizierung

Der Meeting Manager nutzt die **bestehende MSAL-Authentifizierung** aus `tools/outlook_graph_tool.py`.

#### Erste Authentifizierung (falls noch nicht geschehen):

```bash
python test_outlook_auth.py
```

Dies startet den Device Code Flow:
1. Ein Browser-Link und Code werden angezeigt
2. Öffne den Link und gib den Code ein
3. Melde dich mit deinem Microsoft-Konto an
4. Token wird in `.outlook_token.json` gespeichert

#### Benötigte Berechtigungen:
- `Calendars.Read` - Kalenderzugriff
- `User.Read` - Benutzerinformationen

#### Konfiguration in `.env`:
```bash
MICROSOFT_CLIENT_ID=309938fb-1d0c-40d2-ab4f-0786dc98e53c
MICROSOFT_TENANT_ID=1aced7e6-6941-4215-9c92-92f35a9f878e
# MICROSOFT_CLIENT_SECRET=optional
```

### 3. LLM-Konfiguration

Der Meeting Manager nutzt die **bestehende LLM-Integration** (Anthropic Claude oder OpenAI).

In `.env`:
```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-api03-...
RESEARCH_MODEL=claude-sonnet-4-5
TEMPERATURE=0.7
```

## Verwendung

### Option 1: Als Standalone-Anwendung

```bash
python meeting_manager.py
```

Der Manager startet die Ordnerüberwachung im Dauerbetrieb:
- Verarbeitet zunächst alle vorhandenen Dateien in `transcripts/incoming`
- Überwacht dann kontinuierlich auf neue Dateien
- Drücke `Ctrl+C` zum Beenden

### Option 2: Als Modul in eigenem Code

```python
from meeting_manager import MeetingManager

# Manager erstellen
manager = MeetingManager(
    incoming_dir="transcripts/incoming",
    processed_dir="transcripts/processed"
)

# Einzelne Datei verarbeiten
from pathlib import Path
file_path = Path("transcripts/incoming/meeting.txt")
manager.process_transcript(file_path)

# Alle vorhandenen Dateien verarbeiten
manager.process_existing_files()

# Ordnerüberwachung starten (blockiert)
manager.start_watching()
```

### Option 3: Integration in bestehende Anwendung

```python
from meeting_manager import MeetingManager

# Manager mit Custom-Parametern
manager = MeetingManager(
    incoming_dir="my/custom/path/incoming",
    processed_dir="my/custom/path/processed",
    llm_provider="openai",
    llm_model="gpt-4"
)

# Nur vorhandene Dateien verarbeiten (ohne Überwachung)
manager.process_existing_files()
```

## Ordnerstruktur

```
mein-assistent/
├── meeting_manager.py          # Hauptmodul
├── transcripts/
│   ├── incoming/               # Neue Transkripte hier ablegen
│   │   ├── meeting1.txt        # Wird automatisch verarbeitet
│   │   └── meeting2.txt
│   └── processed/              # Verarbeitete Transkripte
│       ├── 2024-01-15_Projektplanung_Q1.txt
│       └── 2024-01-16_Team_Standup.txt
└── .outlook_token.json         # Token-Cache (automatisch erstellt)
```

Die Ordner `transcripts/incoming` und `transcripts/processed` werden automatisch erstellt, falls sie nicht existieren.

## Workflow-Details

### 1. Erkennung neuer Dateien

- Überwacht `transcripts/incoming` mit `watchdog`
- Reagiert auf neue `.txt`, `.md` und `.text` Dateien
- Wartet 2 Sekunden nach Dateierstellung (falls noch geschrieben wird)

### 2. Metadaten-Extraktion

```python
creation_time = manager.get_file_creation_time(file_path)
# Nutzt das ältere von st_ctime und st_mtime
```

### 3. Meeting-Suche

```python
meeting = manager.find_meeting_at_time(
    target_time=creation_time,
    tolerance_minutes=15  # ±15 Minuten Toleranz
)
```

**Suchlogik:**
- Ruft alle Events des entsprechenden Tags ab
- Prüft ob `creation_time` innerhalb eines Meetings liegt (± Toleranz)
- Gibt erstes passendes Meeting zurück

**Rückgabe-Format (aus OutlookGraphTool):**
```python
{
    "id": "AAMkADZm...",
    "title": "Projekt Kickoff Meeting",
    "start": "2024-01-15T14:00:00",
    "end": "2024-01-15T15:00:00",
    "location": "Konferenzraum A",
    "attendees": ["Max Mustermann", "Anna Schmidt"],
    "preview": "Agenda: 1. Projektübersicht..."
}
```

### 4. Titel-Generierung (Fallback)

Falls **kein Meeting** gefunden wurde:

```python
title = manager.generate_title_from_transcript(file_path)
```

**LLM-Prompt:**
- Liest erste 2000 Zeichen des Transkripts
- Generiert prägnanten Titel (max. 50 Zeichen)
- Deutsch bevorzugt, keine Sonderzeichen
- Beispiele: `Projektplanung_Q1_2024`, `Kundenbesprechung_Firma_X`

### 5. Umbenennung & Verschiebung

**Dateiname-Schema:**
```
YYYY-MM-DD_Titel.txt
```

**Beispiele:**
- `2024-01-15_Projekt_Kickoff_Meeting.txt`
- `2024-01-16_Team_Standup.txt`
- `2024-01-17_Kundengespraech_Firma_XYZ.txt`

**Kollisionsbehandlung:**
Falls Datei bereits existiert, wird eine Nummer angehängt:
- `2024-01-15_Meeting_1.txt`
- `2024-01-15_Meeting_2.txt`

## Konfiguration & Anpassung

### Toleranz bei Meeting-Suche anpassen

```python
# Standard: ±15 Minuten
manager = MeetingManager()

# Custom: ±30 Minuten
meeting = manager.find_meeting_at_time(
    target_time=creation_time,
    tolerance_minutes=30
)
```

### LLM-Modell wechseln

```python
# OpenAI GPT-4 verwenden
manager = MeetingManager(
    llm_provider="openai",
    llm_model="gpt-4"
)

# Anthropic Claude verwenden (Standard)
manager = MeetingManager(
    llm_provider="anthropic",
    llm_model="claude-sonnet-4-5"
)
```

### Logging anpassen

```python
import logging

# Debug-Level für detailliertere Ausgaben
logging.getLogger("meeting_manager").setLevel(logging.DEBUG)

# Nur Fehler anzeigen
logging.getLogger("meeting_manager").setLevel(logging.ERROR)
```

## Fehlerbehandlung

### Problem: "Nicht authentifiziert"

```bash
[OutlookGraphTool] ⚠️ Nicht authentifiziert
```

**Lösung:**
```bash
python test_outlook_auth.py
```

### Problem: "Token abgelaufen"

```bash
[OutlookGraphTool] Token abgelaufen, erneute Authentifizierung erforderlich
```

**Lösung:**
1. Lösche `.outlook_token.json`
2. Authentifiziere neu: `python test_outlook_auth.py`

### Problem: "Keine Events gefunden"

Mögliche Ursachen:
1. Kalender ist leer für den Tag
2. Zeitzone-Problem (Datei-Zeit vs. Kalender-Zeit)
3. Meeting liegt außerhalb der Toleranz

**Debug:**
```python
import logging
logging.basicConfig(level=logging.DEBUG)

manager = MeetingManager()
creation_time = manager.get_file_creation_time(file_path)
print(f"Suche Meeting um: {creation_time}")

# Test mit größerer Toleranz
meeting = manager.find_meeting_at_time(creation_time, tolerance_minutes=60)
```

### Problem: LLM-Fehler

```bash
ValueError: ANTHROPIC_API_KEY nicht in .env gefunden
```

**Lösung:**
Prüfe `.env` Datei:
```bash
cat .env | grep ANTHROPIC_API_KEY
```

## Architektur & Design

### Klassenstruktur

```
MeetingManager
├── __init__()                      # Initialisierung
├── _initialize_llm()               # LLM Setup (analog zu anderen Agenten)
├── get_file_creation_time()        # Metadaten
├── find_meeting_at_time()          # Outlook-Integration
├── generate_title_from_transcript() # LLM-Fallback
├── process_transcript()            # Hauptlogik
├── process_existing_files()        # Batch-Verarbeitung
└── start_watching()                # Ordnerüberwachung

TranscriptEventHandler
└── on_created()                    # Watchdog Event Handler
```

### Integration mit bestehender Codebase

Der Meeting Manager nutzt **konsistent** die Patterns der bestehenden Agenten:

**1. MSAL-Authentifizierung:**
```python
from tools.outlook_graph_tool import OutlookGraphTool
self.outlook_tool = OutlookGraphTool()  # Nutzt .outlook_token.json
```

**2. LLM-Initialisierung:**
```python
# Analog zu research_agent.py, task_agent.py, communication_agent.py
def _initialize_llm(self):
    if self.llm_provider == "anthropic":
        return ChatAnthropic(...)
    else:
        return ChatOpenAI(...)
```

**3. Konfiguration über .env:**
```python
self.llm_provider = os.getenv("LLM_PROVIDER", "anthropic")
self.llm_model = os.getenv("RESEARCH_MODEL", "claude-sonnet-4-5")
```

**4. Logging:**
```python
logger = logging.getLogger(__name__)
logger.info("Meeting Manager erfolgreich initialisiert")
```

## Test & Entwicklung

### Test-Dateien erstellen

```bash
# Ordner erstellen
mkdir -p transcripts/incoming

# Test-Transkript erstellen
cat > transcripts/incoming/test_meeting.txt << 'EOF'
Meeting Transcript - Projekt Kickoff

Teilnehmer: Max, Anna, Thomas
Datum: 15.01.2024

[14:05] Max: Willkommen zum Kickoff Meeting für Projekt Alpha.
[14:07] Anna: Danke, ich freue mich auf die Zusammenarbeit.
[14:10] Thomas: Ich habe die Projektpläne vorbereitet...

... [weiterer Transkript-Inhalt] ...
EOF

# Meeting Manager starten
python meeting_manager.py
```

### Manuelle Verarbeitung testen

```python
from meeting_manager import MeetingManager
from pathlib import Path

manager = MeetingManager()

# Einzelne Datei testen
file_path = Path("transcripts/incoming/test_meeting.txt")
success = manager.process_transcript(file_path)

print(f"Verarbeitung {'erfolgreich' if success else 'fehlgeschlagen'}")
```

### Outlook-Integration testen

```python
from datetime import datetime
manager = MeetingManager()

# Meeting zur aktuellen Zeit suchen
now = datetime.now()
meeting = manager.find_meeting_at_time(now, tolerance_minutes=30)

if meeting:
    print(f"Meeting gefunden: {meeting['title']}")
else:
    print("Kein Meeting gefunden")
```

### LLM-Titel-Generierung testen

```python
from pathlib import Path
manager = MeetingManager()

file_path = Path("transcripts/incoming/test_meeting.txt")
title = manager.generate_title_from_transcript(file_path)

print(f"Generierter Titel: {title}")
```

## Erweiterte Anwendungsfälle

### Batch-Verarbeitung alter Transkripte

```python
import shutil
from pathlib import Path

# Alte Transkripte in incoming verschieben
old_transcripts = Path("archive/old_meetings")
incoming = Path("transcripts/incoming")

for file in old_transcripts.glob("*.txt"):
    shutil.copy(file, incoming / file.name)

# Manager erstellen (ohne Überwachung)
manager = MeetingManager()
manager.process_existing_files()
```

### Integration in Streamlit App

```python
import streamlit as st
from meeting_manager import MeetingManager

st.title("Meeting Transkript Manager")

# Datei-Upload
uploaded_file = st.file_uploader("Transkript hochladen", type=['txt', 'md'])

if uploaded_file:
    # Speichern in incoming
    incoming = Path("transcripts/incoming")
    file_path = incoming / uploaded_file.name

    with open(file_path, 'wb') as f:
        f.write(uploaded_file.getbuffer())

    # Sofort verarbeiten
    manager = MeetingManager()
    success = manager.process_transcript(file_path)

    if success:
        st.success(f"✓ Transkript verarbeitet!")
    else:
        st.error("Fehler bei Verarbeitung")
```

### Geplante Verarbeitung mit Cron

```bash
# Crontab bearbeiten
crontab -e

# Täglich um 20:00 Uhr alle neuen Transkripte verarbeiten
0 20 * * * cd /pfad/zu/mein-assistent && python -c "from meeting_manager import MeetingManager; MeetingManager().process_existing_files()"
```

## Performance & Skalierung

### Verarbeitungsgeschwindigkeit

Pro Transkript:
- **Mit Meeting gefunden**: ~2-3 Sekunden (API-Aufruf + Verarbeitung)
- **Ohne Meeting (LLM)**: ~5-10 Sekunden (LLM-Generierung)

### Batch-Verarbeitung

Bei vielen Dateien:
```python
# Wartezeit zwischen Verarbeitungen reduzieren
for file in incoming_files:
    manager.process_transcript(file)
    time.sleep(0.5)  # Statt 1 Sekunde
```

### API Rate Limits

**Microsoft Graph API:**
- Standard: 1000 Requests pro Minute pro App
- Meeting Manager: ~1 Request pro Transkript

**LLM APIs:**
- Anthropic Claude: ~50 Requests pro Minute (je nach Plan)
- OpenAI GPT: ~60 Requests pro Minute (je nach Plan)

## Sicherheit & Datenschutz

### Sensible Daten

**Tokens & API Keys:**
- `.outlook_token.json` - Microsoft OAuth Token
- `.env` - LLM API Keys

**⚠️ Diese Dateien NIEMALS committen!**

```bash
# .gitignore sollte enthalten:
.env
.outlook_token.json
transcripts/incoming/*
transcripts/processed/*
```

### Transkript-Inhalte

Meeting-Transkripte können **sensible Informationen** enthalten:
- Geschäftsgeheimnisse
- Personenbezogene Daten
- Vertrauliche Diskussionen

**Empfehlungen:**
1. Ordner `transcripts/` außerhalb von Git halten
2. Backup-Strategie für verarbeitete Transkripte
3. Regelmäßige Bereinigung alter Transkripte
4. Zugriffskontrolle auf Ordner-Ebene

## Support & Weiterentwicklung

### Bekannte Einschränkungen

1. **Zeitzone-Handling**: Dateierstellungszeit ist immer lokal, Kalender-API verwendet UTC
2. **Toleranz-Logik**: Nur zeitbasiert, keine inhaltliche Überprüfung
3. **LLM-Kosten**: Jeder Fallback-Titel verursacht API-Kosten

### Geplante Features

- [ ] Unterstützung für Audio-Transkripte (.mp3, .wav)
- [ ] Multi-Kalender-Support (nicht nur "me/calendar")
- [ ] Konfidenz-Score für LLM-generierte Titel
- [ ] Web-Interface für manuelle Nachbearbeitung
- [ ] Export-Funktion (CSV, JSON)

### Fehler melden

Bei Problemen bitte folgende Informationen bereitstellen:

```bash
# Python-Version
python --version

# Installierte Pakete
pip list | grep -E "(msal|watchdog|langchain)"

# Log-Ausgabe
python meeting_manager.py 2>&1 | tee meeting_manager.log
```

## Lizenz & Credits

Teil des **mein-assistent** Projekts.

**Abhängigkeiten:**
- [MSAL](https://github.com/AzureAD/microsoft-authentication-library-for-python) - Microsoft Authentication
- [watchdog](https://github.com/gorakhargosh/watchdog) - File System Monitoring
- [LangChain](https://github.com/langchain-ai/langchain) - LLM Integration
- [Anthropic Claude](https://www.anthropic.com) / [OpenAI GPT](https://openai.com) - Language Models
