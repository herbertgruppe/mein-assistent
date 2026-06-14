# Plaud Poller

Hintergrund-Service der alle 10 Minuten Plaud-Account(s) auf neue Aufnahmen prüft
und für jede neue Aufnahme (>3 Min) ein Paperclip-Issue anlegt.

## Architektur

```
plaud_poller.py  (Python 3, systemd-Service)
  → plaud recent --days 1          # neue IDs holen
  → SQLite Idempotenz-Check        # bereits verarbeitet?
  → plaud file <id>                # Metadaten (start_at, duration, …)
  → plaud summary <id>             # AI-Zusammenfassung (Herbert-Template)
  → POST /api/companies/.../issues # Paperclip-Issue an Protokoll-Agent
  → SQLite markieren
```

## Dateien

| Datei | Zweck |
|---|---|
| `plaud_poller.py` | Haupt-Service-Skript |
| `plaud-poller.service` | systemd-Unit |
| `plaud-poller.logrotate` | Log-Rotation (nach `/etc/logrotate.d/`) |

## Erstkonfiguration auf dem Hetzner-Server

```bash
# 1. Service-Verzeichnis anlegen
mkdir -p /opt/plaud-poller

# 2. Dateien bereitstellen (bereits in /opt/mein-assistent/ vorhanden)
cp /opt/mein-assistent/plaud-poller.service /etc/systemd/system/
cp /opt/mein-assistent/plaud-poller.logrotate /etc/logrotate.d/plaud-poller

# 3. Env-Vars in /opt/mein-assistent/.env ergänzen (falls noch nicht vorhanden):
#   PAPERCLIP_API_KEY_MA=<mein-assistent App-Key>
#   PAPERCLIP_PROTOKOLL_AGENT_ID=<ID des Protokoll-Agenten aus HBE-682>
#   TELEGRAM_BOT_TOKEN=<Bot-Token>
#   TELEGRAM_ADMIN_CHAT_ID=<Svens Chat-ID>
#   Optional:
#   PLAUD_POLL_INTERVAL_SEC=600    # Default: 10 Min
#   PLAUD_MIN_DURATION_SEC=180     # Default: 3 Min
#   PLAUD_ACCOUNTS=/root:<protokoll-agent-id>

# 4. Service aktivieren
systemctl daemon-reload
systemctl enable plaud-poller
systemctl start plaud-poller

# 5. Status prüfen
systemctl status plaud-poller
journalctl -u plaud-poller -f
```

## Umgebungsvariablen

| Variable | Standard | Beschreibung |
|---|---|---|
| `PLAUD_POLL_INTERVAL_SEC` | `600` | Polling-Intervall in Sekunden |
| `PLAUD_MIN_DURATION_SEC` | `180` | Aufnahmen kürzer als X Sek werden ignoriert |
| `PLAUD_DB_PATH` | `/opt/plaud-poller/state.db` | SQLite-Datenbank für Idempotenz |
| `PLAUD_LOG_FILE` | `/var/log/plaud-poller.log` | Log-Datei |
| `PLAUD_ACCOUNTS` | `/root:<protokoll-agent-id>` | Multi-Account-Konfiguration |
| `PLAUD_RECENT_DAYS` | `1` | Tage-Fenster für `plaud recent --days N` |
| `PAPERCLIP_API_URL` | `https://paperclip.herbertgruppe.com` | Paperclip-URL |
| `PAPERCLIP_API_KEY_MA` | — | **Pflicht.** Paperclip mein-assistent App-Key |
| `PAPERCLIP_COMPANY_ID_MA` | `9df4976b-…` | HBE Company-ID |
| `PAPERCLIP_PROTOKOLL_AGENT_ID` | `67d2dae0-…` (CEO Fallback) | Protokoll-Agent-ID |
| `TELEGRAM_BOT_TOKEN` | — | Telegram-Bot für Fehler-Alerts |
| `TELEGRAM_ADMIN_CHAT_ID` | — | Svens Telegram-Chat-ID |

## Multi-Account-Konfiguration

Weitere Plaud-Accounts (z.B. Florian, BL-Leiter) werden über `PLAUD_ACCOUNTS` nachgezogen:

```
PLAUD_ACCOUNTS=/root:<sven-agent-id>,/opt/plaud-florian:<florian-agent-id>
```

Jeder Eintrag enthält:
- **home_dir**: Verzeichnis in dem `~/.plaud/tokens.json` des Accounts liegt
- **agent_id**: Paperclip-Agent-ID der Assignee für diesen Account

Für Florian: `plaud login` als root mit `HOME=/opt/plaud-florian` ausführen, dann
`/opt/plaud-florian/.plaud/tokens.json` enthält seinen Token.

## Token-Erneuerung

Der Plaud-Token läuft periodisch ab. Bei Ablauf:
1. Telegram-Alert geht automatisch an Sven
2. Neuer Login via SSH:
   ```bash
   # SSH-Tunnel auf Port 443 (sslh) öffnen
   ssh root@46.225.132.135 -p 443
   # Token erneuern
   plaud login
   # Service neustarten
   systemctl restart plaud-poller
   ```

## Service-Verwaltung

```bash
# Status
systemctl status plaud-poller
journalctl -u plaud-poller --since "1 hour ago"

# Logs (JSON-Format)
tail -f /var/log/plaud-poller.log | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        d = json.loads(line)
        print(f'{d[\"time\"]} [{d[\"level\"]}] {d[\"msg\"]}')
    except: print(line, end='')
"

# Neustart
systemctl restart plaud-poller

# SQLite-Datenbank inspizieren
sqlite3 /opt/plaud-poller/state.db \
  "SELECT recording_id, start_at, processed_at, issue_identifier FROM plaud_processed_recordings ORDER BY processed_at DESC LIMIT 20;"
```

## Monitoring

Der Audit-Log-Format pro Polling-Zyklus (in `/var/log/plaud-poller.log`):

```json
{
  "timestamp": "2026-06-09T20:00:00Z",
  "cycle": 42,
  "new_recordings": ["abc123", "def456"],
  "created_issues": ["HBE-700", "HBE-701"],
  "skipped": ["xyz789"],
  "errors": []
}
```

Bei `"errors"` ≥ 3 aufeinander → automatischer Telegram-Alert an Sven.
