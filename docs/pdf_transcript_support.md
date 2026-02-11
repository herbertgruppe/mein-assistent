# PDF-Transkript-Support & Datum/Zeit-Erkennung

## Übersicht

Das Meeting-Manager-System wurde erweitert um:
1. **PDF-Support**: Verarbeitung von Transkripten im PDF-Format
2. **Datum/Zeit-Extraktion aus Inhalt**: Automatisches Erkennen von Datum/Zeit im Transkript
3. **User-Prompt für Datum/Zeit**: Manuelle Eingabe bei fehlender automatischer Erkennung

## Neue Funktionen

### 1. PDF-Text-Extraktion

Die Methode `extract_text_from_pdf()` extrahiert Text aus PDF-Dateien:

```python
def extract_text_from_pdf(self, file_path: Path) -> str:
    """Extrahiert Text aus einer PDF-Datei"""
```

**Technische Details:**
- Nutzt `PyPDFLoader` von LangChain
- Unterstützt mehrseitige PDFs
- Kombiniert alle Seiten zu einem Text

### 2. Datum/Zeit-Extraktion aus Inhalt

Die Methode `extract_datetime_from_content()` sucht nach Datum/Zeit-Mustern:

```python
def extract_datetime_from_content(self, content: str) -> Optional[datetime]:
    """Extrahiert Datum und Uhrzeit aus dem Transkript-Inhalt"""
```

**Unterstützte Formate:**
- ISO-Format: `2026-01-26 10:03:15` oder `2026-01-26 10:03`
- DE-Format: `26.01.2026 10:03:15` oder `26.01.2026 10:03`
- US-Format: `01/26/2026 10:03:15`

**Suchbereich:**
- Erste 1000 Zeichen des Transkripts
- Typischerweise nach der Überschrift platziert

**Beispiel aus PDF:**
```
01-26 Einführung einer agilen Arbeitsweise
2026-01-26 10:03:15
00:00:01 Speaker 1
[Transkript beginnt hier...]
```

### 3. Fallback-Strategie für Datum/Zeit

Die Methode `get_transcript_datetime()` implementiert eine Prioritäts-Strategie:

```python
def get_transcript_datetime(
    self,
    file_path: Path,
    user_provided_datetime: Optional[datetime] = None
) -> Tuple[datetime, str]:
```

**Priorität:**
1. **User-Input** (höchste Priorität) - Manuell eingegebenes Datum/Zeit
2. **Content-Extraktion** - Aus Transkript-Inhalt extrahiert
3. **File-Metadata** (Fallback) - Datei-Erstellungszeit

**Rückgabewert:**
- Tuple: `(datetime, Quelle)`
- Quelle kann sein: `"user_input"`, `"content"`, `"file_metadata"`

## Streamlit UI-Erweiterungen

### PDF-Upload

Der File-Uploader akzeptiert jetzt auch PDFs:

```python
uploaded_file = st.file_uploader(
    "Wähle eine Transkript-Datei",
    type=['txt', 'md', 'text', 'pdf'],  # PDF hinzugefügt
    help="Unterstützte Formate: .txt, .md, .pdf"
)
```

### Datum/Zeit-Picker

Neuer optionaler Input für manuelles Datum/Zeit:

```python
manual_date = st.date_input("Meeting-Datum", value=None)
manual_time = st.time_input("Meeting-Uhrzeit", value=None)
```

**Features:**
- Optional - kann leer gelassen werden
- Wird als `.metadata.json` gespeichert
- Automatisch gelöscht nach Verarbeitung

### Metadaten-Datei

Bei manueller Datum/Zeit-Eingabe wird eine Metadaten-Datei erstellt:

**Dateiname:** `<transkript-name>.metadata.json`

**Inhalt:**
```json
{
  "user_provided_datetime": "2026-01-26T10:03:15",
  "original_filename": "meeting-transcript.pdf"
}
```

**Lebenszyklus:**
1. Erstellt beim Upload mit manuellem Datum/Zeit
2. Gelesen beim Verarbeiten durch MeetingManager
3. Gelöscht nach erfolgreicher Verarbeitung

## Workflow

### Automatische Verarbeitung

```
1. PDF wird hochgeladen
   ↓
2. System sucht Datum/Zeit:
   a) In Metadaten-Datei (.metadata.json)
   b) Im PDF-Inhalt (erste 1000 Zeichen)
   c) In Datei-Metadaten (Erstellungszeit)
   ↓
3. Meeting-Suche in Outlook (±15 Minuten)
   ↓
4. Titel-Generierung (Meeting-Name oder LLM)
   ↓
5. Umbenennung: YYYY-MM-DD_Titel.pdf
   ↓
6. Verschiebung nach transcripts/processed/
```

### Manuelle Datum/Zeit-Eingabe

```
1. PDF hochladen
   ↓
2. Optional: Datum/Zeit eingeben
   ↓
3. Bei Speichern: .metadata.json erstellt
   ↓
4. MeetingManager liest Metadaten
   ↓
5. Metadaten-Datei wird gelöscht
```

## File Watcher

Der File Watcher wurde erweitert um PDF-Support:

**Vor:**
```python
if f.suffix.lower() in ['.txt', '.md', '.text']
```

**Nach:**
```python
if f.suffix.lower() in ['.txt', '.md', '.text', '.pdf']
```

**Betroffene Stellen:**
- `process_existing_files()` (Zeile 371)
- `on_created()` im EventHandler (Zeile 442)

## Installation & Dependencies

### Erforderliche Pakete

```bash
pip install pypdf
pip install langchain-community
```

### Bereits installiert

Sollte bereits vorhanden sein, da für `document_tool.py` benötigt:
- `langchain-community`
- `pypdf` (oder `PyPDF2`)

## Testing

Ein Test-Skript ist verfügbar: `test_pdf_processing.py`

**Ausführung:**
```bash
source venv/bin/activate
python test_pdf_processing.py
```

**Tests:**
1. PDF-Text-Extraktion
2. Datum/Zeit-Extraktion aus Inhalt
3. Komplette Verarbeitung (ohne tatsächliches Verschieben)

## Verwendung

### Kommandozeile

```bash
# Meeting Manager starten (verarbeitet automatisch neue Dateien)
python meeting_manager.py
```

### Streamlit-App

1. App starten: `streamlit run app.py`
2. Zum "Transcripts" Tab navigieren
3. PDF hochladen
4. Optional: Datum/Zeit eingeben
5. "Speichern" klicken

### Programmatisch

```python
from meeting_manager import MeetingManager
from pathlib import Path
from datetime import datetime

manager = MeetingManager()

# Mit automatischer Datum/Zeit-Erkennung
manager.process_transcript(Path("transcripts/incoming/meeting.pdf"))

# Mit manuellem Datum/Zeit
user_datetime = datetime(2026, 1, 26, 10, 3, 15)
manager.process_transcript(
    Path("transcripts/incoming/meeting.pdf"),
    user_provided_datetime=user_datetime
)
```

## Beispiel-Ausgabe

```
INFO - Verarbeite Transkript: 01-26 Einführung einer agilen Arbeitsweise-transcript.pdf
INFO - Extrahiere Text aus PDF: 01-26 Einführung einer agilen Arbeitsweise-transcript.pdf
INFO - ✓ 35000 Zeichen aus 35 Seite(n) extrahiert
INFO - ✓ Datum/Zeit aus Inhalt extrahiert: 2026-01-26 10:03:15
INFO - Datum/Zeit-Quelle: content
INFO - Suche Meeting um 2026-01-26 10:03:15 (±15 Minuten)...
INFO - Meeting gefunden: Einführung einer agilen Arbeitsweise zur verbesserten Zielerreichung und Transparenz
INFO - Nutze Meeting-Titel: Einfuehrung_einer_agilen_Arbeitsweise_zur_verbesserten_Zielerreichung_und_Transparenz
INFO - ✓ Datei verschoben: 2026-01-26_Einfuehrung_einer_agilen_Arbeitsweise_zur_verbesserten_Zielerreichung_und_Transparenz.pdf
```

## Fehlerbehebung

### Problem: PyPDF nicht installiert

**Fehler:**
```
ModuleNotFoundError: No module named 'pypdf'
```

**Lösung:**
```bash
pip install pypdf
```

### Problem: Kein Datum/Zeit gefunden

**Symptom:** System nutzt Datei-Metadaten statt Transkript-Inhalt

**Lösung:**
1. Prüfen, ob Datum/Zeit im Format `YYYY-MM-DD HH:MM:SS` vorhanden
2. Prüfen, ob Datum/Zeit in den ersten 1000 Zeichen steht
3. Manuell Datum/Zeit beim Upload eingeben

### Problem: Meeting nicht gefunden

**Symptom:** LLM generiert Titel statt Outlook-Meeting zu nutzen

**Mögliche Ursachen:**
1. Datum/Zeit im Transkript ist falsch
2. Kein Outlook-Termin zur angegebenen Zeit
3. Outlook-Authentifizierung fehlgeschlagen
4. Toleranz zu gering (Standard: ±15 Minuten)

**Lösung:**
- Datum/Zeit manuell prüfen und ggf. eingeben
- Outlook-Kalender überprüfen
- Toleranz erhöhen (in `find_meeting_at_time()`)

## Weitere Verbesserungen

### Zukünftige Features

1. **OCR-Support**: Für gescannte PDFs ohne Textebene
2. **Mehrere Datums-Formate**: Unterstützung für weitere Sprachen/Formate
3. **UI-Verbesserung**: Meeting-Liste anzeigen zur Auswahl
4. **Batch-Processing**: Mehrere Dateien auf einmal verarbeiten
5. **Datum/Zeit-Validierung**: Warnung bei unplausiblen Werten

### Erweiterungsmöglichkeiten

```python
# Weitere Datums-Formate hinzufügen
patterns = [
    r'(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})(?::(\d{2}))?',
    # Neu: Deutsches Format mit Monatsnamen
    r'(\d{1,2})\.\s*(Januar|Februar|März|...)\s*(\d{4})\s+(\d{2}):(\d{2})',
    # Neu: Relative Zeitangaben
    r'(heute|gestern|morgen)\s+um\s+(\d{2}):(\d{2})',
]
```

## Zusammenfassung

Das erweiterte System bietet:

✅ **PDF-Support** - Verarbeitung von PDF-Transkripten
✅ **Intelligente Datum/Zeit-Erkennung** - Aus Inhalt, Metadaten oder User-Input
✅ **Fallback-Strategie** - Mehrere Quellen für Datum/Zeit
✅ **User-Freundlich** - Optionale manuelle Eingabe in UI
✅ **Robust** - Fehlerbehandlung und Logging
✅ **Kompatibel** - Funktioniert mit bestehenden TXT/MD-Transkripten

---

**Dokumentation erstellt:** 2026-01-27
**Version:** 1.0
**Autor:** Meeting Manager System
