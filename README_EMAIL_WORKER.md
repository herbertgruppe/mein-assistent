# Email Worker Management

## Verwaltungsbefehle

### Worker starten
```bash
cd /home/sherbert/mein-assistent
./start-email-worker.sh
```

### Worker stoppen
```bash
./stop-email-worker.sh
```

### Status prüfen
```bash
./status-email-worker.sh
```

### Worker neu starten
```bash
./stop-email-worker.sh && ./start-email-worker.sh
```

### Logs ansehen
```bash
# Letzte 50 Zeilen
tail -n 50 email_worker.log

# Live-Logs verfolgen
tail -f email_worker.log

# Nach Fehlern suchen
grep -i error email_worker.log
```

## Autostart-Methoden

### Methode 1: Cron @reboot (EMPFOHLEN)
```bash
crontab -e
# Hinzufügen:
@reboot /home/sherbert/mein-assistent/start-email-worker.sh >> /home/sherbert/mein-assistent/cron.log 2>&1
```

### Methode 2: Watchdog (automatischer Neustart bei Absturz)
```bash
crontab -e
# Hinzufügen:
@reboot /home/sherbert/mein-assistent/start-email-worker.sh >> /home/sherbert/mein-assistent/cron.log 2>&1
*/5 * * * * /home/sherbert/mein-assistent/watchdog-email-worker.sh
```

### Methode 3: Autostart via .bashrc
Bereits konfiguriert - startet automatisch bei Terminal-Login

### Methode 4: systemd Service (benötigt sudo)
```bash
sudo systemctl start email-worker
sudo systemctl stop email-worker
sudo systemctl status email-worker
sudo systemctl restart email-worker
```

## Troubleshooting

### Worker läuft nicht
```bash
# Status prüfen
./status-email-worker.sh

# Log ansehen
tail -n 100 email_worker.log

# PID-Datei löschen und neu starten
rm -f email_worker.pid
./start-email-worker.sh
```

### Worker verarbeitet keine Actions
```bash
# Prüfe ob Worker wirklich läuft
ps aux | grep email_worker

# Prüfe letzte Logs
tail -n 50 email_worker.log | grep -i "action\|error"

# Datenbank-Status prüfen
python3 -c "
import sqlite3
conn = sqlite3.connect('data/email_cache.db')
cursor = conn.cursor()
cursor.execute('SELECT id, action_type, status FROM action_queue WHERE status=\"pending\"')
print('Pending Actions:', cursor.fetchall())
conn.close()
"
```

### Worker startet nicht automatisch
```bash
# Prüfe Cron-Logs
cat cron.log

# Teste Start-Skript manuell
./start-email-worker.sh

# Prüfe Berechtigungen
ls -la *.sh
# Sollten alle ausführbar sein (chmod +x *.sh)
```

## Was der Worker tut

- **Alle 2 Minuten**: Neue Emails abrufen und mit KI analysieren
- **Alle 30 Sekunden**: Pending Actions verarbeiten (Asana, Weiterleiten, Antworten, Archivieren)
- **Täglich um 2 Uhr**: Alte archivierte Emails (>30 Tage) aus DB löschen

## Dateien

- `email_worker.py` - Hauptprogramm
- `start-email-worker.sh` - Start-Skript
- `stop-email-worker.sh` - Stop-Skript
- `status-email-worker.sh` - Status-Skript
- `watchdog-email-worker.sh` - Watchdog für automatischen Neustart
- `email_worker.log` - Log-Datei
- `email_worker.pid` - PID-Datei (automatisch erstellt)
- `watchdog.log` - Watchdog-Log
- `cron.log` - Cron-Autostart-Log
