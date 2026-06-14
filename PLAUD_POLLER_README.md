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

---

## Erstkonfiguration auf dem Hetzner-Server

```bash
# 1. Dedizierter Service-User anlegen (kein Login, eigene Gruppe)
useradd --system --no-create-home --shell /usr/sbin/nologin --user-group plaud

# 2. Plaud-Token von root migrieren (falls bereits ein Token existiert)
mkdir -p /var/lib/plaud/.plaud
cp /root/.plaud/tokens.json /var/lib/plaud/.plaud/tokens.json
chown -R plaud:plaud /var/lib/plaud
chmod 700 /var/lib/plaud/.plaud
chmod 600 /var/lib/plaud/.plaud/tokens.json

# 3. Leserechte für plaud-User auf /opt/mein-assistent sicherstellen
#    (Verzeichnis sollte bereits 755/644 sein — nur prüfen)
ls -ld /opt/mein-assistent

# 4. Dateien bereitstellen
cp /opt/mein-assistent/plaud-poller.service /etc/systemd/system/
cp /opt/mein-assistent/plaud-poller.logrotate /etc/logrotate.d/plaud-poller

# 5. Env-Vars in /opt/mein-assistent/.env ergänzen/aktualisieren:
#   PAPERCLIP_API_KEY_MA=<mein-assistent App-Key>
#   PAPERCLIP_PROTOKOLL_AGENT_ID=<ID des Protokoll-Agenten>
#   TELEGRAM_BOT_TOKEN=<Bot-Token>
#   TELEGRAM_ADMIN_CHAT_ID=<Svens Chat-ID>
#   PLAUD_ACCOUNTS=/var/lib/plaud:<protokoll-agent-id>   # explizit setzen
#   Optional:
#   PLAUD_POLL_INTERVAL_SEC=600    # Default: 10 Min
#   PLAUD_MIN_DURATION_SEC=180     # Default: 3 Min

# 6. Service aktivieren
systemctl daemon-reload
systemctl enable plaud-poller
systemctl start plaud-poller

# 7. Status prüfen
systemctl status plaud-poller
journalctl -u plaud-poller -f
```

---

## Migration (root → plaud-User)

Falls der Service bereits als root läuft:

```bash
# 1. Service stoppen
systemctl stop plaud-poller

# 2. Service-User anlegen (falls noch nicht vorhanden)
useradd --system --no-create-home --shell /usr/sbin/nologin --user-group plaud

# 3. Token migrieren
mkdir -p /var/lib/plaud/.plaud
cp /root/.plaud/tokens.json /var/lib/plaud/.plaud/tokens.json
chown -R plaud:plaud /var/lib/plaud
chmod 700 /var/lib/plaud/.plaud
chmod 600 /var/lib/plaud/.plaud/tokens.json

# 4. Neue Unit-Datei einspielen
cp /opt/mein-assistent/plaud-poller.service /etc/systemd/system/plaud-poller.service
cp /opt/mein-assistent/plaud-poller.logrotate /etc/logrotate.d/plaud-poller
systemctl daemon-reload

# 5. PLAUD_ACCOUNTS in .env auf neuen Pfad aktualisieren
#    Alt:  PLAUD_ACCOUNTS=/root:<agent-id>
#    Neu:  PLAUD_ACCOUNTS=/var/lib/plaud:<agent-id>
# Alternativ: Variable weglassen — Default im Code ist nun /var/lib/plaud

# 6. Alten State in neuen Pfad übertragen (optional — vermeidet Duplikate)
mkdir -p /var/lib/plaud
cp /opt/plaud-poller/state.db /var/lib/plaud/state.db 2>/dev/null || true
chown plaud:plaud /var/lib/plaud/state.db 2>/dev/null || true

# 7. Service starten
systemctl start plaud-poller
```

---

## Verifikation nach Migration

```bash
# Nachweis: Service läuft NICHT als root
systemctl show plaud-poller -p User
# Erwartete Ausgabe: User=plaud

# Positivtest: Service-User kann Token lesen
sudo -u plaud cat /var/lib/plaud/.plaud/tokens.json | python3 -m json.tool | head -5

# Negativtest: Service-User kann alten Root-Token NICHT lesen
sudo -u plaud cat /root/.plaud/tokens.json
# Erwartete Ausgabe: Permission denied

# Smoke-Test: Service-Start/-Restart
systemctl restart plaud-poller
sleep 3
systemctl is-active plaud-poller
# Erwartete Ausgabe: active
journalctl -u plaud-poller --since "1 minute ago" | grep -i "start\|poll\|error"
```

---

## Umgebungsvariablen

| Variable | Standard | Beschreibung |
|---|---|---|
| `PLAUD_POLL_INTERVAL_SEC` | `600` | Polling-Intervall in Sekunden |
| `PLAUD_MIN_DURATION_SEC` | `180` | Aufnahmen kürzer als X Sek werden ignoriert |
| `PLAUD_DB_PATH` | `/var/lib/plaud/state.db` | SQLite-Datenbank für Idempotenz |
| `PLAUD_LOG_FILE` | `/var/log/plaud/plaud-poller.log` | Log-Datei |
| `PLAUD_ACCOUNTS` | `/var/lib/plaud:<protokoll-agent-id>` | Multi-Account-Konfiguration |
| `PLAUD_RECENT_DAYS` | `1` | Tage-Fenster für `plaud recent --days N` |
| `PAPERCLIP_API_URL` | `https://paperclip.herbertgruppe.com` | Paperclip-URL |
| `PAPERCLIP_API_KEY_MA` | — | **Pflicht.** Paperclip mein-assistent App-Key |
| `PAPERCLIP_COMPANY_ID_MA` | `9df4976b-…` | HBE Company-ID |
| `PAPERCLIP_PROTOKOLL_AGENT_ID` | `67d2dae0-…` (CEO Fallback) | Protokoll-Agent-ID |
| `TELEGRAM_BOT_TOKEN` | — | Telegram-Bot für Fehler-Alerts |
| `TELEGRAM_ADMIN_CHAT_ID` | — | Svens Telegram-Chat-ID |

---

## Rechte-Modell

| Pfad | Owner | Mode | Zweck |
|---|---|---|---|
| `/var/lib/plaud/` | `plaud:plaud` | `750` | Service-Homeverzeichnis |
| `/var/lib/plaud/.plaud/` | `plaud:plaud` | `700` | Plaud-CLI-Konfiguration |
| `/var/lib/plaud/.plaud/tokens.json` | `plaud:plaud` | `600` | Auth-Token (kein Lese-Zugriff durch andere) |
| `/var/lib/plaud/state.db` | `plaud:plaud` | `600` | Idempotenz-Datenbank |
| `/var/log/plaud/` | `plaud:plaud` | `750` | Log-Verzeichnis (systemd via LogsDirectory) |
| `/var/log/plaud/plaud-poller.log` | `plaud:plaud` | `640` | Service-Log |

Systemd übernimmt die Erstellung und Rechte-Setzung für `/var/lib/plaud/` (`StateDirectory=plaud`)
und `/var/log/plaud/` (`LogsDirectory=plaud`) automatisch beim Service-Start.

---

## Multi-Account-Konfiguration

Weitere Plaud-Accounts (z.B. Florian, BL-Leiter) werden über `PLAUD_ACCOUNTS` nachgezogen:

```
PLAUD_ACCOUNTS=/var/lib/plaud:<sven-agent-id>,/var/lib/plaud-florian:<florian-agent-id>
```

Jeder Eintrag enthält:
- **home_dir**: Verzeichnis in dem `~/.plaud/tokens.json` des Accounts liegt
- **agent_id**: Paperclip-Agent-ID der Assignee für diesen Account

Für Florian: eigenes Verzeichnis anlegen und Token dort einrichten:
```bash
useradd --system --no-create-home --shell /usr/sbin/nologin plaud-florian || true
mkdir -p /var/lib/plaud-florian/.plaud
# plaud login mit HOME=/var/lib/plaud-florian ausführen:
sudo -u plaud-florian HOME=/var/lib/plaud-florian plaud login
chmod 600 /var/lib/plaud-florian/.plaud/tokens.json
chown -R plaud:plaud /var/lib/plaud-florian  # plaud-Service liest alle Accounts
```

---

## Token-Erneuerung

Der Plaud-Token läuft periodisch ab. Bei Ablauf:
1. Telegram-Alert geht automatisch an Sven
2. Neuer Login via SSH:
   ```bash
   ssh root@46.225.132.135 -p 443
   # Token erneuern (als plaud-User, mit korrektem HOME)
   sudo -u plaud HOME=/var/lib/plaud plaud login
   # Service neustarten
   systemctl restart plaud-poller
   ```

---

## Service-Verwaltung

```bash
# Status
systemctl status plaud-poller
journalctl -u plaud-poller --since "1 hour ago"

# Logs (JSON-Format)
tail -f /var/log/plaud/plaud-poller.log | python3 -c "
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
sqlite3 /var/lib/plaud/state.db \
  "SELECT recording_id, start_at, processed_at, issue_identifier FROM plaud_processed_recordings ORDER BY processed_at DESC LIMIT 20;"
```

---

## Monitoring

Der Audit-Log-Format pro Polling-Zyklus (in `/var/log/plaud/plaud-poller.log`):

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
