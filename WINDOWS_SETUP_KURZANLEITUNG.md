# Windows Setup - Kurzanleitung

## Voraussetzungen prüfen

### 1. WSL installiert?
```powershell
# In PowerShell
wsl --version
```

Falls nicht installiert:
```powershell
# PowerShell als Administrator
wsl --install
# Computer neu starten
```

### 2. Python Dependencies installiert?
```bash
# In WSL
cd /home/sherbert/mein-assistent
source venv/bin/activate
pip install -r requirements.txt
```

### 3. .env Datei konfiguriert?
```bash
# Prüfen
cat .env | grep -E "ANTHROPIC_API_KEY|OUTLOOK"
```

## Verwendung

### Starten
```cmd
start_assistant.bat
```

### Status prüfen
```cmd
status_assistant.bat
```

### Stoppen
```cmd
stop_assistant.bat
```

## Troubleshooting

### Logs ansehen
- Windows: `logs\` Ordner im Projekt
- WSL: `tail -f email_worker.log`

### Neustart bei Problemen
```cmd
stop_assistant.bat
start_assistant.bat
```

### Firewall-Regel (falls Browser nicht verbindet)
```powershell
# PowerShell als Administrator
New-NetFirewallRule -DisplayName "WSL Streamlit" -Direction Inbound -LocalPort 8501 -Protocol TCP -Action Allow
```
