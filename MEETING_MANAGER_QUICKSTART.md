# Meeting Manager - Quick Start Guide

## 🚀 In 5 Minuten startklar

### Schritt 1: Abhängigkeiten installieren

```bash
pip install watchdog
```

(Alle anderen Pakete sollten bereits installiert sein)

### Schritt 2: Microsoft Graph API authentifizieren

**Nur beim ersten Mal notwendig:**

```bash
python test_outlook_auth.py
```

1. Öffne den angezeigten Link im Browser
2. Gib den angezeigten Code ein
3. Melde dich mit deinem Microsoft-Konto an
4. Token wird in `.outlook_token.json` gespeichert

### Schritt 3: Tests durchführen

```bash
python test_meeting_manager.py
```

Dieser Test überprüft:
- ✓ Umgebungsvariablen
- ✓ Python-Pakete
- ✓ Ordnerstruktur
- ✓ Microsoft Graph API
- ✓ LLM-Integration
- ✓ Meeting Manager
- ✓ Transkript-Verarbeitung

### Schritt 4: Meeting Manager starten

```bash
python meeting_manager.py
```

**Das war's!** Der Manager läuft jetzt und überwacht `transcripts/incoming/`.

---

## 📝 Erste Schritte

### Transkript hinzufügen

1. Speichere eine `.txt` Datei in `transcripts/incoming/`
2. Der Manager erkennt die Datei automatisch
3. Datei wird verarbeitet und nach `transcripts/processed/` verschoben
4. Neuer Dateiname: `YYYY-MM-DD_MeetingName.txt`

### Beispiel-Transkript

Erstelle `transcripts/incoming/test.txt`:

```
Meeting Transcript

[10:00] Max: Willkommen zum Projekt Kickoff Meeting.
[10:02] Anna: Danke! Lass uns direkt starten.
[10:05] Thomas: Ich habe die Agenda vorbereitet...

... weiterer Inhalt ...
```

Der Manager wird:
1. Die Erstellungszeit der Datei auslesen
2. In deinem Outlook-Kalender nach einem Meeting zu dieser Zeit suchen
3. Falls gefunden: Datei nach Meeting-Titel benennen
4. Falls nicht gefunden: LLM generiert einen Titel aus dem Transkript
5. Datei wird verschoben nach `transcripts/processed/2024-01-27_Projekt_Kickoff_Meeting.txt`

---

## 🔧 Konfiguration

### Standard-Einstellungen (keine Änderung notwendig)

Der Meeting Manager nutzt automatisch die Einstellungen aus `.env`:

```bash
# LLM (bereits konfiguriert)
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-api03-...
RESEARCH_MODEL=claude-sonnet-4-5

# Microsoft Graph API (bereits konfiguriert)
MICROSOFT_CLIENT_ID=309938fb-...
MICROSOFT_TENANT_ID=1aced7e6-...
```

### Optional: Toleranz anpassen

Standardmäßig sucht der Manager ±15 Minuten um die Datei-Erstellungszeit.

Um die Toleranz zu ändern, bearbeite `meeting_manager.py`:

```python
# In der Methode process_transcript():
meeting = self.find_meeting_at_time(creation_time, tolerance_minutes=30)  # ±30 Minuten
```

---

## 📖 Weiterführende Dokumentation

- **Detaillierte Anleitung**: `MEETING_MANAGER_README.md`
- **Code-Beispiele**: `meeting_manager_example.py`
- **Test-Suite**: `test_meeting_manager.py`

---

## 🆘 Häufige Probleme

### Problem: "Nicht authentifiziert"

**Lösung:**
```bash
python test_outlook_auth.py
```

### Problem: "Token abgelaufen"

**Lösung:**
```bash
rm .outlook_token.json
python test_outlook_auth.py
```

### Problem: "Kein Meeting gefunden"

**Mögliche Ursachen:**
- Meeting liegt außerhalb der ±15 Minuten Toleranz
- Datei-Erstellungszeit stimmt nicht mit Meeting-Zeit überein
- Kalender ist leer

**Lösung:**
- Erhöhe `tolerance_minutes` auf 30 oder 60
- Prüfe Datei-Erstellungszeit mit `ls -l transcripts/incoming/`
- Verifiziere Kalender mit `python test_outlook_access.py`

### Problem: LLM-Fehler

**Lösung:**
```bash
# Prüfe API Key
cat .env | grep ANTHROPIC_API_KEY

# Teste LLM direkt
python test_meeting_manager.py
```

---

## 💡 Tipps

### Batch-Verarbeitung alter Transkripte

```bash
# Kopiere alte Transkripte in incoming
cp alte_transkripte/*.txt transcripts/incoming/

# Starte Manager (verarbeitet erst vorhandene Dateien)
python meeting_manager.py
```

### Nur einmalige Verarbeitung (ohne Überwachung)

```python
from meeting_manager import MeetingManager

manager = MeetingManager()
manager.process_existing_files()
```

### Custom Ordner verwenden

```python
from meeting_manager import MeetingManager

manager = MeetingManager(
    incoming_dir="meine/transkripte/neu",
    processed_dir="meine/transkripte/archiv"
)
manager.start_watching()
```

---

## 🎯 Nächste Schritte

1. ✅ **Tests durchführen**: `python test_meeting_manager.py`
2. ✅ **Manager starten**: `python meeting_manager.py`
3. ✅ **Transkript hinzufügen**: Kopiere `.txt` Datei in `transcripts/incoming/`
4. ✅ **Ergebnis prüfen**: Schaue in `transcripts/processed/`

**Viel Erfolg! 🚀**

Bei Fragen: Siehe `MEETING_MANAGER_README.md` für Details.
