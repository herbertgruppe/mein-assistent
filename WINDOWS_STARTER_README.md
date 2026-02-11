# Windows Starter für Mein Assistent

## 📋 Übersicht

Dieses Paket enthält drei .bat Dateien zum einfachen Starten, Stoppen und Überwachen deines Assistenten unter Windows mit WSL.

### Dateien

- **`start_assistant.bat`** - Startet alle Services (Email Worker + Streamlit)
- **`stop_assistant.bat`** - Stoppt alle Services
- **`status_assistant.bat`** - Zeigt Status aller Services

---

## ✅ Voraussetzungen

### 1. WSL (Windows Subsystem for Linux)

```powershell
# In PowerShell als Administrator
wsl --install
```

Nach Installation: Computer neu starten

### 2. Ubuntu in WSL

```powershell
# Standard-Distro ist meist Ubuntu
wsl --list --verbose
```

Falls Ubuntu nicht installiert ist:
```powershell
wsl --install -d Ubuntu
```

### 3. Python und Virtual Environment in WSL

```bash
# In WSL
cd /home/sherbert/mein-assistent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. .env Datei konfigurieren

```bash
# In WSL
cd /home/sherbert/mein-assistent
cp .env.example .env
nano .env
```

Erforderliche Einträge:
```bash
# LLM Provider
ANTHROPIC_API_KEY=dein-api-key-hier
LLM_PROVIDER=anthropic

# Email (optional)
OUTLOOK_EMAIL=deine-email@domain.de
OUTLOOK_PASSWORD=app-passwort

# Graph API (optional)
GRAPH_CLIENT_ID=...
GRAPH_CLIENT_SECRET=...
GRAPH_TENANT_ID=...
```

---

## 🚀 Verwendung

### Starten

Doppelklick auf `start_assistant.bat` oder:

```cmd
start_assistant.bat
```

Das Skript:
1. ✅ Prüft WSL und Projekt
2. ✅ Prüft ob Services bereits laufen
3. ✅ Startet Email Worker im Hintergrund
4. ✅ Startet Streamlit Web-Interface
5. ✅ Öffnet Browser (http://localhost:8501)
6. ✅ Erstellt separate Logs in `logs/`

### Status prüfen

```cmd
status_assistant.bat
```

Zeigt:
- Status des Email Workers (läuft/gestoppt)
- Status von Streamlit (läuft/gestoppt)
- PIDs der Prozesse
- Erreichbarkeit der UI
- Aktuelle Logs

### Stoppen

```cmd
stop_assistant.bat
```

Stoppt beide Services sauber.

---

## 📂 Log-Dateien

Alle Logs werden im `logs/` Verzeichnis mit Zeitstempel gespeichert:

```
logs/
├── email_worker_20260201_143022.log
├── email_worker_20260201_150815.log
├── streamlit_20260201_143025.log
└── streamlit_20260201_150818.log
```

### Logs live ansehen

**In WSL:**
```bash
cd /home/sherbert/mein-assistent
tail -f email_worker.log
tail -f logs/streamlit_*.log
```

**In Windows PowerShell:**
```powershell
wsl tail -f /home/sherbert/mein-assistent/email_worker.log
```

---

## ⚙️ Konfiguration anpassen

### WSL-Distro ändern

Falls du eine andere WSL-Distro verwendest:

1. Öffne die .bat Dateien in einem Editor
2. Ändere die Zeile:
   ```batch
   set "WSL_DISTRO=Ubuntu"
   ```
   zu z.B.:
   ```batch
   set "WSL_DISTRO=Debian"
   ```

### Projekt-Pfad ändern

Falls dein Projekt woanders liegt:

1. Öffne die .bat Dateien
2. Ändere:
   ```batch
   set "PROJECT_PATH=/home/sherbert/mein-assistent"
   ```

---

## 🔧 Troubleshooting

### Problem: "WSL ist nicht installiert"

**Lösung:**
```powershell
# PowerShell als Administrator
wsl --install
# Neustart erforderlich
```

### Problem: "Projekt nicht gefunden"

**Lösung:**
Prüfe den Pfad in WSL:
```bash
wsl ls -la /home/sherbert/mein-assistent
```

Falls anders, passe `PROJECT_PATH` in den .bat Dateien an.

### Problem: "Email Worker startet nicht"

**Prüfungen:**
```bash
# In WSL
cd /home/sherbert/mein-assistent

# 1. Virtual Environment aktivieren
source venv/bin/activate

# 2. Dependencies prüfen
pip list | grep -E "langchain|streamlit|asana|msal"

# 3. .env prüfen
cat .env | grep -E "ANTHROPIC|OUTLOOK|GRAPH"

# 4. Manuell starten zum Debuggen
python email_worker.py
```

### Problem: "Streamlit startet nicht"

**Prüfungen:**
```bash
# In WSL
cd /home/sherbert/mein-assistent
source venv/bin/activate

# Manuell starten
streamlit run app.py

# Port prüfen
netstat -tuln | grep 8501
```

### Problem: "Services laufen, aber Browser zeigt Fehler"

**Windows Firewall:**
- WSL-Ports müssen erlaubt sein
- Teste: `http://localhost:8501` im Browser

**Lösung:**
```powershell
# PowerShell als Administrator
New-NetFirewallRule -DisplayName "WSL Streamlit" -Direction Inbound -LocalPort 8501 -Protocol TCP -Action Allow
```

### Problem: "Logs werden nicht erstellt"

**Lösung:**
```cmd
# Logs-Verzeichnis manuell erstellen
mkdir logs
```

### Problem: "Prozess läuft, aber antwortet nicht"

```bash
# In WSL - Prozesse prüfen
ps aux | grep -E "streamlit|email_worker"

# Notfall-Neustart
pkill -f streamlit
pkill -f email_worker
```

---

## 🎯 Automatischer Start mit Windows

### Option 1: Verknüpfung im Autostart-Ordner

1. Rechtsklick auf `start_assistant.bat`
2. "Verknüpfung erstellen"
3. Verknüpfung verschieben nach:
   ```
   %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
   ```

### Option 2: Windows Task Scheduler

1. Task Scheduler öffnen (`taskschd.msc`)
2. "Einfache Aufgabe erstellen"
3. Trigger: "Bei Anmeldung"
4. Aktion: "Programm starten"
5. Programm: Pfad zu `start_assistant.bat`

---

## 📊 Service-Architektur

```
Windows
  │
  └─► start_assistant.bat
       │
       ├─► WSL: Email Worker (Hintergrund)
       │    └─► email_worker.py
       │         ├─► Harvester (alle 2 Min)
       │         ├─► Enrichment (alle 5 Min)
       │         └─► Executor (alle 30 Sek)
       │
       └─► WSL: Streamlit UI
            └─► streamlit run app.py
                 └─► http://localhost:8501
```

---

## 🔐 Sicherheit

- `.env` Datei niemals committen (bereits in `.gitignore`)
- API-Keys regelmäßig rotieren
- Logs können sensible Daten enthalten → Nicht teilen
- Windows Firewall-Regeln für WSL-Ports prüfen

---

## 📞 Support

Bei Problemen:

1. **Status prüfen:** `status_assistant.bat`
2. **Logs prüfen:**
   - `logs/email_worker_*.log`
   - `logs/streamlit_*.log`
3. **Services neu starten:**
   ```cmd
   stop_assistant.bat
   start_assistant.bat
   ```
4. **Manuelle Diagnose in WSL:**
   ```bash
   wsl
   cd /home/sherbert/mein-assistent
   source venv/bin/activate
   python email_worker.py  # Test Email Worker
   streamlit run app.py    # Test Streamlit
   ```

---

## 🎉 Erfolgreiches Setup

Nach erfolgreichem Start solltest du sehen:

```
============================================================
   Mein Assistent gestartet!
============================================================

Services:
  ✓ Email Worker    - Laeuft im Hintergrund
  ✓ Streamlit UI    - http://localhost:8501

Logs:
  - Email Worker: logs\email_worker_20260201_143022.log
  - Streamlit:    logs\streamlit_20260201_143025.log
```

Browser öffnet automatisch → UI ist bereit!

---

## 📝 Checkliste für den ersten Start

- [ ] WSL installiert und funktioniert
- [ ] Ubuntu in WSL verfügbar
- [ ] Projekt unter `/home/sherbert/mein-assistent` vorhanden
- [ ] Python Virtual Environment erstellt (`venv/`)
- [ ] Dependencies installiert (`pip install -r requirements.txt`)
- [ ] `.env` Datei konfiguriert mit API-Keys
- [ ] `start_assistant.bat` ausführen
- [ ] Browser öffnet http://localhost:8501
- [ ] Email Worker läuft (siehe `status_assistant.bat`)

---

**Viel Erfolg mit deinem Assistenten! 🚀**
