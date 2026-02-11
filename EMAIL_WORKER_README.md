# Email Background Worker - Deployment Guide

## Übersicht

Der Email Background Worker ermöglicht instant Email-Tab Performance durch asynchrone Verarbeitung:

- **Email-Tab lädt in <1 Sekunde** (vorher: 40-100 Sekunden)
- **Action-Buttons reagieren sofort** (<500ms UI-Feedback)
- **Background-Verarbeitung** alle 2 Minuten
- **Persistente Queue** mit Retry-Logic

## Architektur

```
Background Worker (email_worker.py)
    ↓ (poll alle 2 Min)
Outlook API → LLM-Analyse → SQLite DB
                                ↓ (read-only)
                         Streamlit UI (app.py)
```

## Installation

### 1. Dependencies installieren

```bash
cd /home/sherbert/mein-assistent
venv/bin/pip install apscheduler>=3.10.0
```

### 2. Datenbank initialisieren

```bash
venv/bin/python3 -c "from utils.database import EmailDatabase; db = EmailDatabase(); db.initialize_schema()"
```

Prüfen:
```bash
ls -lh data/email_cache.db
# Sollte existieren (~50KB)
```

### 3. Worker im Vordergrund testen

```bash
venv/bin/python3 email_worker.py
```

**Erwartete Ausgabe:**
```
============================================================
Email Background Worker
============================================================
[Worker] Initialisiere Outlook Tool...
[Worker] Initialisiere Asana Agent...
[Worker] Initialisiere EmailManager...
[Worker] ✓ Worker initialisiert
[Worker] 🚀 Starte Email Background Worker...
[EmailDatabase] ✓ Schema initialisiert
[Worker] Führe initialen Email-Poll aus...
[Worker] 📬 Starte Email-Polling...
[EmailManager] ✓ X ungelesene E-Mails abgerufen
[Worker] 🔍 Analysiere X neue Emails...
```

**Stoppen:** `Ctrl+C`

### 4. Systemd Service einrichten

#### Service-Datei installieren:

```bash
sudo cp email-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
```

#### Service aktivieren & starten:

```bash
sudo systemctl enable email-worker
sudo systemctl start email-worker
```

#### Status prüfen:

```bash
sudo systemctl status email-worker
```

**Erwartete Ausgabe:**
```
● email-worker.service - Email Background Worker
   Loaded: loaded (/etc/systemd/system/email-worker.service; enabled)
   Active: active (running) since ...
   Main PID: XXXXX (python3)
```

#### Logs live verfolgen:

```bash
sudo journalctl -u email-worker -f
```

**Oder Log-Datei:**
```bash
tail -f email_worker.log
```

## Verwendung

### UI verwenden

1. Starte Streamlit:
   ```bash
   venv/bin/streamlit run app.py
   ```

2. Navigiere zum **📬 Posteingang** Tab

3. Emails laden **instant** (<1 Sekunde)

4. Action-Buttons (z.B. "An Asana senden") reagieren sofort:
   - Erstellt Action in Queue
   - Email verschwindet aus Liste
   - Tatsächliche Verarbeitung im Hintergrund (30s)

### Auto-Refresh

Der UI-Tab aktualisiert sich automatisch alle 10 Sekunden:
- Neue analysierte Emails erscheinen
- Verarbeitete Actions verschwinden

**Deaktivieren:** Checkbox "🔄 Auto-Refresh" ausschalten

## Monitoring

### Worker Status prüfen

**Systemd:**
```bash
sudo systemctl status email-worker
```

**Log-Datei:**
```bash
tail -100 email_worker.log
```

**Letzte Errors:**
```bash
grep "ERROR" email_worker.log | tail -20
```

### Datenbank-Queries

**Hinweis:** Benötigt sqlite3:
```bash
sudo apt install sqlite3
```

**Email-Status:**
```bash
sqlite3 data/email_cache.db "SELECT status, COUNT(*) FROM emails GROUP BY status;"
```

**Pending Actions:**
```bash
sqlite3 data/email_cache.db "SELECT * FROM action_queue WHERE status = 'pending';"
```

**Worker State:**
```bash
sqlite3 data/email_cache.db "SELECT * FROM worker_state;"
```

**Recent Audit Log:**
```bash
sqlite3 data/email_cache.db "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT 10;"
```

## Troubleshooting

### Worker startet nicht

**Fehler:** "ModuleNotFoundError"
```bash
# Prüfe venv Path
which python3
# Sollte: /usr/bin/python3

# Nutze explizit venv Python:
venv/bin/python3 email_worker.py
```

**Fehler:** "Outlook nicht authentifiziert"
- Starte Streamlit UI
- Gehe zu Sidebar → "Microsoft Graph API konfigurieren"
- Authentifiziere mit Microsoft
- Worker nutzt Token aus `outlook_token.json`

### Emails werden nicht analysiert

**Check 1:** Worker läuft?
```bash
sudo systemctl status email-worker
```

**Check 2:** Letzte Poll-Zeit
```bash
sqlite3 data/email_cache.db "SELECT last_poll_time, last_successful_poll FROM worker_state;"
```

**Check 3:** Errors in Log
```bash
grep "ERROR" email_worker.log | tail -20
```

### UI zeigt keine Emails

**Check 1:** Datenbank hat Emails?
```bash
sqlite3 data/email_cache.db "SELECT COUNT(*) FROM emails WHERE status IN ('analyzed', 'pending_asana', 'pending_forward');"
```

**Check 2:** Cache invalidieren
- Im UI: Klicke "🔄 Jetzt aktualisieren"

**Check 3:** Worker läuft und pollt?
- Siehe oben "Emails werden nicht analysiert"

### Actions werden nicht verarbeitet

**Check 1:** Pending Actions vorhanden?
```bash
sqlite3 data/email_cache.db "SELECT COUNT(*) FROM action_queue WHERE status = 'pending';"
```

**Check 2:** Worker verarbeitet Actions?
```bash
grep "Verarbeite Action" email_worker.log | tail -10
```

**Check 3:** Failed Actions?
```bash
sqlite3 data/email_cache.db "SELECT * FROM action_queue WHERE status = 'failed';"
```

## Performance-Benchmarks

### Vorher (Synchron):
- Email-Tab laden: **40-100 Sekunden** (20 Emails)
- Action-Button: **2-5 Sekunden** (blockiert UI)

### Nachher (Async):
- Email-Tab laden: **<1 Sekunde**
- Action-Button: **<500ms** (instant UI-Feedback)
- Tatsächliche Verarbeitung: 30 Sekunden (im Hintergrund)

## Maintenance

### Worker neu starten

```bash
sudo systemctl restart email-worker
```

### Worker stoppen

```bash
sudo systemctl stop email-worker
```

### Alte Emails löschen

Der Worker löscht automatisch archivierte Emails >30 Tage (täglich um 2 Uhr).

**Manuell:**
```bash
sqlite3 data/email_cache.db "DELETE FROM emails WHERE status = 'archived' AND received_at < date('now', '-30 days');"
```

### Datenbank zurücksetzen

```bash
rm data/email_cache.db
venv/bin/python3 -c "from utils.database import EmailDatabase; db = EmailDatabase(); db.initialize_schema()"
```

## Logs

**Worker Log:**
- Datei: `email_worker.log`
- Rotation: Keine (manuell mit logrotate)

**Systemd Journal:**
```bash
sudo journalctl -u email-worker
# Mit Zeitraum:
sudo journalctl -u email-worker --since "1 hour ago"
# Live:
sudo journalctl -u email-worker -f
```

## Migration von Alt zu Neu

**Schritt 1:** Worker stoppen (falls läuft)
```bash
sudo systemctl stop email-worker 2>/dev/null || true
```

**Schritt 2:** Pull neuen Code
```bash
git pull
```

**Schritt 3:** Dependencies installieren
```bash
venv/bin/pip install -r requirements.txt
```

**Schritt 4:** Datenbank initialisieren
```bash
venv/bin/python3 -c "from utils.database import EmailDatabase; db = EmailDatabase(); db.initialize_schema()"
```

**Schritt 5:** Worker testen
```bash
timeout 5 venv/bin/python3 email_worker.py || true
```

**Schritt 6:** Service installieren & starten
```bash
sudo cp email-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable email-worker
sudo systemctl start email-worker
```

**Schritt 7:** Status prüfen
```bash
sudo systemctl status email-worker
```

## Backup & Restore

### Backup

```bash
# Datenbank
cp data/email_cache.db data/email_cache.db.backup

# Mit Timestamp
cp data/email_cache.db "data/email_cache.db.backup.$(date +%Y%m%d_%H%M%S)"
```

### Restore

```bash
sudo systemctl stop email-worker
cp data/email_cache.db.backup data/email_cache.db
sudo systemctl start email-worker
```

## Security Notes

- **Token-File:** `outlook_token.json` enthält sensible OAuth2-Tokens
- **Permissions:** Nur User `sherbert` sollte Zugriff haben
- **Logging:** Logs enthalten keine Passwörter, aber Email-Metadaten

## Support

Bei Problemen:

1. **Logs prüfen:** `tail -100 email_worker.log`
2. **Status prüfen:** `sudo systemctl status email-worker`
3. **Datenbank prüfen:** Siehe "Monitoring" oben
4. **Worker neu starten:** `sudo systemctl restart email-worker`

## Weiterentwicklung

**Potentielle Erweiterungen:**

1. **Worker Dashboard** (Optional - siehe Plan Phase 5)
   - Neuer Tab "⚙️ Worker Status" in app.py
   - Zeigt Worker State, Pending Actions, Audit Log
   - Button: Worker neu starten

2. **Email-Prioritäten**
   - Dringende Emails zuerst analysieren
   - Separate Queue für High-Priority

3. **Multi-Worker**
   - Parallele Verarbeitung
   - Load-Balancing

4. **Notifications**
   - Desktop-Benachrichtigungen bei kritischen Emails
   - Slack/Discord Integration

5. **Analytics**
   - Email-Statistiken
   - Response-Time-Tracking
   - Category-Distribution
