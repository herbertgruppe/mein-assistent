# 🚀 Setup-Checkliste - Mein Assistent mit Windows Starter

## Phase 1: Windows-Voraussetzungen

### ✅ WSL installieren

```powershell
# PowerShell als Administrator öffnen
wsl --install
```

**Nach Installation:**
- Computer neu starten
- Ubuntu-Setup abschließen (Username & Passwort festlegen)

### ✅ WSL testen

```powershell
wsl --list --verbose
# Sollte Ubuntu oder eine andere Distro zeigen
```

---

## Phase 2: Linux-Umgebung einrichten (in WSL)

### ✅ Python und Dependencies

```bash
# WSL öffnen (Windows Terminal oder cmd: wsl)
cd /home/sherbert/mein-assistent

# Python-Version prüfen
python3 --version
# Sollte >= 3.8 sein

# Virtual Environment erstellen
python3 -m venv venv

# Aktivieren
source venv/bin/activate

# Dependencies installieren
pip install --upgrade pip
pip install -r requirements.txt
```

### ✅ Prüfe Installation

```bash
# Diese Befehle sollten ohne Fehler laufen
python -c "import streamlit; print('Streamlit OK')"
python -c "import langchain; print('LangChain OK')"
python -c "import asana; print('Asana OK')"
python -c "import msal; print('MSAL OK')"
```

---

## Phase 3: Konfiguration

### ✅ .env Datei erstellen

```bash
cd /home/sherbert/mein-assistent
cp .env.example .env
nano .env  # oder vim/vi
```

### ✅ Pflichtfelder in .env

```bash
# Minimum-Konfiguration
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxxxxx
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
```

### ✅ Optional: Email-Integration

```bash
# In .env hinzufügen
OUTLOOK_EMAIL=deine-email@domain.de
OUTLOOK_PASSWORD=dein-app-passwort

# Oder Graph API für Business-Konten
GRAPH_CLIENT_ID=xxx
GRAPH_CLIENT_SECRET=xxx
GRAPH_TENANT_ID=xxx
```

### ✅ Optional: Asana-Integration

```bash
# In .env hinzufügen
ASANA_ACCESS_TOKEN=dein-asana-token
```

### ✅ Optional: Web-Suche

```bash
# In .env hinzufügen
TAVILY_API_KEY=dein-tavily-key
```

---

## Phase 4: Datenbank initialisieren

### ✅ Verzeichnisse erstellen

```bash
cd /home/sherbert/mein-assistent

# Erstelle Datenverzeichnis falls nicht vorhanden
mkdir -p data
mkdir -p database
```

### ✅ Email-Datenbank Migration

```bash
# Falls Datenbank bereits existiert
python migrate_email_db.py

# Prüfe Schema
python -c "
import sqlite3
conn = sqlite3.connect('data/email_cache.db')
cursor = conn.cursor()
cursor.execute('PRAGMA table_info(emails)')
print([row[1] for row in cursor.fetchall()])
"
```

---

## Phase 5: Services testen (manuell in WSL)

### ✅ Test Email Worker

```bash
cd /home/sherbert/mein-assistent
source venv/bin/activate

# Starte Email Worker im Vordergrund zum Testen
python email_worker.py

# Erwartete Ausgabe:
# [Worker] 🚀 Starte Email Worker...
# [Worker] ✓ Harvester-Job registriert
# [Worker] ✓ Enrichment-Job registriert
# [Worker] ✓ Executor-Job registriert

# Beenden mit Strg+C
```

### ✅ Test Streamlit

```bash
cd /home/sherbert/mein-assistent
source venv/bin/activate

# Starte Streamlit
streamlit run app.py

# Erwartete Ausgabe:
# You can now view your Streamlit app in your browser.
# Local URL: http://localhost:8501

# Im Browser öffnen: http://localhost:8501
# Beenden mit Strg+C
```

---

## Phase 6: Windows .bat Dateien vorbereiten

### ✅ Dateien nach Windows kopieren (optional)

Die .bat Dateien liegen bereits im Projekt-Ordner:
- `start_assistant.bat`
- `stop_assistant.bat`
- `status_assistant.bat`

**Zugriff von Windows:**

Option 1: Über WSL-Pfad
```
\\wsl$\Ubuntu\home\sherbert\mein-assistent\
```

Option 2: In Windows Explorer die Dateien kopieren nach z.B.:
```
C:\Users\DeinName\mein-assistent\
```

### ✅ .bat Konfiguration prüfen

Öffne `start_assistant.bat` in Editor und prüfe:

```batch
set "WSL_DISTRO=Ubuntu"
set "PROJECT_PATH=/home/sherbert/mein-assistent"
```

**Falls dein Setup anders ist:**
- WSL-Distro: Prüfe mit `wsl --list`
- Projekt-Pfad: Prüfe mit `wsl pwd` im Projektordner

---

## Phase 7: Erster Start mit .bat

### ✅ Start

```cmd
# Windows CMD oder Doppelklick
start_assistant.bat
```

**Erwartete Ausgabe:**
```
============================================================
   Mein Assistent - Starter
============================================================

[1/5] Pruefe Voraussetzungen...
[OK] Voraussetzungen erfuellt

[2/5] Pruefe laufende Services...
[OK] Service-Check abgeschlossen

[3/5] Starte Email Worker...
[OK] Email Worker gestartet

[4/5] Starte Streamlit Web-Interface...
      Warte auf Streamlit...
[OK] Streamlit ist bereit

[5/5] Oeffne Browser...
[OK] Browser geoeffnet
```

### ✅ Status prüfen

```cmd
status_assistant.bat
```

**Erwartete Ausgabe:**
```
Email Worker:
  Status:  Laeuft
  PID:     12345

Streamlit Web-Interface:
  Status:  Laeuft
  PID:     12346
  URL:     http://localhost:8501 (bereit)
```

---

## Phase 8: Funktionstest

### ✅ UI testen

1. Browser öffnet automatisch → http://localhost:8501
2. Tabs sollten sichtbar sein:
   - "Assistent" / "Home"
   - "Posteingang" (wenn Email konfiguriert)
   - "Mein Tag" (wenn Asana konfiguriert)
3. Keine Fehlermeldungen in der UI

### ✅ Email Worker testen (falls konfiguriert)

```bash
# In WSL
cd /home/sherbert/mein-assistent
tail -f email_worker.log

# Sollte regelmäßig Aktivität zeigen:
# [Harvester] Checking for new emails...
# [Enrichment] Processing analyzed emails...
# [Executor] Processing pending actions...
```

### ✅ Logs prüfen

**Windows:**
```
logs\email_worker_YYYYMMDD_HHMMSS.log
logs\streamlit_YYYYMMDD_HHMMSS.log
```

Keine ERROR-Meldungen sollten erscheinen.

---

## Phase 9: Stoppen und Neustart

### ✅ Stoppen

```cmd
stop_assistant.bat
```

**Erwartete Ausgabe:**
```
[1/2] Stoppe Email Worker...
[OK] Email Worker gestoppt

[2/2] Stoppe Streamlit...
[OK] Streamlit gestoppt
```

### ✅ Status prüfen nach Stop

```cmd
status_assistant.bat
```

Sollte zeigen:
```
Email Worker:
  Status:  Gestoppt

Streamlit Web-Interface:
  Status:  Gestoppt
```

### ✅ Neustart

```cmd
start_assistant.bat
```

Sollte wieder erfolgreich starten.

---

## Troubleshooting-Matrix

| Problem | Symptom | Lösung |
|---------|---------|--------|
| **WSL fehlt** | "WSL ist nicht installiert" | `wsl --install` in PowerShell als Admin |
| **Python fehlt** | "python3: command not found" | In WSL: `sudo apt update && sudo apt install python3 python3-venv python3-pip` |
| **Dependencies fehlen** | Import-Fehler beim Start | `pip install -r requirements.txt` |
| **API-Key fehlt** | "No API key found" | `.env` prüfen, `ANTHROPIC_API_KEY` setzen |
| **Port belegt** | "Port 8501 already in use" | Anderen Streamlit-Prozess beenden: `pkill -f streamlit` |
| **Worker startet nicht** | Keine PID-Datei | Logs prüfen: `tail -f email_worker.log` |
| **UI zeigt Fehler** | "Connection error" in Browser | Firewall prüfen, Service neu starten |

---

## Quick Reference

### Häufige Befehle

```bash
# In WSL
cd /home/sherbert/mein-assistent
source venv/bin/activate

# Logs live ansehen
tail -f email_worker.log
tail -f streamlit.log

# Prozesse prüfen
ps aux | grep -E "streamlit|email_worker"

# Manueller Neustart
pkill -f streamlit
pkill -f email_worker
python email_worker.py &
streamlit run app.py
```

```cmd
REM In Windows CMD
start_assistant.bat
stop_assistant.bat
status_assistant.bat
```

---

## 🎉 Setup erfolgreich!

Wenn alle Punkte ✅ sind:

- [ ] WSL funktioniert
- [ ] Python-Environment läuft
- [ ] .env konfiguriert
- [ ] Services starten mit `start_assistant.bat`
- [ ] Status zeigt "Laeuft" für beide Services
- [ ] Browser öffnet UI unter http://localhost:8501
- [ ] Keine Fehler in Logs
- [ ] Stop funktioniert mit `stop_assistant.bat`

**→ Dein Assistent ist einsatzbereit! 🚀**

---

## Nächste Schritte

1. **Outlook authentifizieren** (falls noch nicht geschehen):
   ```bash
   cd /home/sherbert/mein-assistent
   source venv/bin/activate
   python authenticate_outlook.py
   ```

2. **Asana konfigurieren** (optional):
   - Token in `.env` eintragen
   - UI-Tab "Mein Tag" testen

3. **Autostart einrichten** (optional):
   - Verknüpfung in Windows Autostart-Ordner
   - Oder Task Scheduler verwenden

4. **Produktiv nutzen**:
   - UI für Tasks
   - Email-Management
   - Meeting-Planung
   - Asana-Integration

---

**Bei Fragen oder Problemen:** Siehe `WINDOWS_STARTER_README.md`
