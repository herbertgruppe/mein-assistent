# Email-System Deployment Guide
## Asynchrone Batch-Architektur - Deployment & Testing

Dieser Guide beschreibt das Deployment des refactorten Email-Systems mit asynchroner Batch-Architektur.

---

## 🎯 Übersicht

### Was wurde geändert?

**Vorher:**
- Email Worker machte Harvesting + LLM-Analyse in EINEM Schritt
- UI machte synchrone LLM-Aufrufe beim "Antworten"-Dialog
- Lange Wartezeiten bei Email-Verarbeitung

**Nachher:**
- **3 entkoppelte Tasks:**
  1. **Harvester** (alle 2 Min): Spiegelt Outlook → DB (status='synced')
  2. **Enrichment** (alle 5 Min): LLM-Analyse inkl. Draft-Reply (status='analyzed')
  3. **Executor** (alle 30 Sek): Führt Actions aus der Queue aus
- **UI lädt nur aus DB:** Keine LLM-Calls mehr, < 50ms Ladezeit
- **Draft-Replies vorgeneriert:** "Antworten"-Dialog öffnet sofort mit Vorschlag

### Neue Features

1. **Draft-Reply-Generierung:** Jede Email bekommt automatisch einen Antwortentwurf
2. **Optimierte Performance:** UI-Ladezeit < 100ms (getestet: ~3ms)
3. **Queue-Status-Dashboard:** Zeigt pending Actions und synced Emails
4. **Retry-Logik:** Automatische Wiederholung bei Fehlern (max 3x)

---

## 📦 Phase 1: Datenbank-Migration

### 1.1 Backup erstellen

```bash
# Backup der aktuellen Datenbank
cp data/email_cache.db data/email_cache.db.backup.$(date +%Y%m%d_%H%M%S)
```

### 1.2 Migration ausführen

```bash
# Fügt Spalte 'draft_reply' zur emails-Tabelle hinzu
python3 migrations/001_add_draft_reply.py
```

**Erwartete Ausgabe:**
```
[Migration] Starte Migration: Add draft_reply column
[Migration] Datenbank: data/email_cache.db
✓ Spalte 'draft_reply' hinzugefügt
✅ Migration abgeschlossen
```

### 1.3 Schema verifizieren

```bash
# Prüfe ob Spalte existiert
python3 -c "import sqlite3; conn = sqlite3.connect('data/email_cache.db'); cursor = conn.cursor(); cursor.execute('PRAGMA table_info(emails)'); cols = [row[1] for row in cursor.fetchall()]; print('draft_reply vorhanden:', 'draft_reply' in cols)"
```

**Erwartete Ausgabe:**
```
draft_reply vorhanden: True
```

---

## 🔧 Phase 2: Worker Deployment

### 2.1 Worker stoppen (falls aktiv)

```bash
# Prüfe ob Worker als systemd-Service läuft
sudo systemctl status email-worker

# Falls aktiv, stoppen
sudo systemctl stop email-worker
```

### 2.2 Code-Änderungen verifizieren

Die folgenden Dateien wurden geändert:

1. **`utils/database.py`:**
   - ✅ Neue Methode: `insert_raw_email()`
   - ✅ Neue Methode: `update_email_analysis()`
   - ✅ Neue Methode: `increment_retry_count()`

2. **`email_worker.py`:**
   - ✅ `poll_emails()` ersetzt durch `harvest_emails()` + `enrich_emails()`
   - ✅ Scheduler konfiguriert mit 3 separaten Tasks

3. **`utils/email_manager.py`:**
   - ✅ Neue Methode: `generate_draft_reply()`

4. **`app.py`:**
   - ✅ `render_email_action_chat()` vereinfacht (kein LLM mehr)
   - ✅ Worker-Status-Dashboard erweitert

### 2.3 Worker neu starten

```bash
# Starte Worker (falls systemd-Service)
sudo systemctl start email-worker

# Prüfe Status
sudo systemctl status email-worker

# Logs beobachten
tail -f email_worker.log
```

**Erwartete Log-Ausgaben:**

```
[Worker] 🚀 Starte Email Worker mit 3 separaten Tasks...
[Worker] ✓ Harvester-Job registriert (alle 2 Min)
[Worker] ✓ Enrichment-Job registriert (alle 5 Min)
[Worker] ✓ Executor-Job registriert (alle 30 Sek)
[Worker] ✓ Cleanup-Job registriert (täglich um 2 Uhr)
[Worker] Führe initialen Email-Harvest aus...
[Harvester] 📬 Starte Email-Harvesting...
```

### 2.4 Manuelle Test-Ausführung (optional)

```bash
# Worker im Vordergrund starten (für Testing)
python3 email_worker.py

# Alternativ: Einzelne Tasks testen
python3 -c "
from email_worker import EmailWorker
worker = EmailWorker()
worker.harvest_emails()  # Test Harvester
worker.enrich_emails(batch_size=1)  # Test Enrichment
"
```

---

## 🧪 Phase 3: Testing & Verifikation

### 3.1 Performance-Tests

```bash
# Führe automatisierte Performance-Tests aus
python3 tests/test_performance.py
```

**Erwartete Ausgabe:**
```
============================================================
Email-System Performance-Tests
============================================================

[Test] UI-Ladezeit (50 Emails aus DB)...
  → Ladezeit: 3.4ms
  ✅ Performance OK (< 100ms)

[Test] Draft-Retrieval-Geschwindigkeit...
  → Ladezeit: 2.1ms
  ✅ Performance OK (< 10ms)

[Test] Queue-Status-Abfrage...
  → Ladezeit: 0.3ms
  ✅ Performance OK (< 50ms)

✅ Alle Performance-Tests erfolgreich!
```

### 3.2 End-to-End Test

**Test-Szenario:** Neue Email wird verarbeitet und Draft-Reply generiert

1. **Email senden:**
   ```bash
   # Sende Test-Email an deinen Outlook-Account
   ```

2. **Harvester-Task (max 2 Min warten):**
   ```bash
   # Prüfe ob Email in DB mit status='synced'
   python3 -c "
   from utils.database import EmailDatabase
   db = EmailDatabase()
   synced = db.get_emails_by_status(['synced'], limit=10)
   print(f'Synced Emails: {len(synced)}')
   if synced:
       print(f'Letzte: {synced[0][\"subject\"][:50]}')
   "
   ```

3. **Enrichment-Task (max 5 Min warten):**
   ```bash
   # Prüfe ob Email analysiert wurde mit draft_reply
   python3 -c "
   from utils.database import EmailDatabase
   db = EmailDatabase()
   analyzed = db.get_emails_by_status(['analyzed'], limit=10)
   print(f'Analyzed Emails: {len(analyzed)}')
   if analyzed:
       email = analyzed[0]
       print(f'Subject: {email[\"subject\"][:50]}')
       print(f'Priority: {email[\"priority\"]}/5')
       print(f'Draft-Reply: {len(email.get(\"draft_reply\", \"\"))} Zeichen')
   "
   ```

4. **UI-Test (Streamlit):**
   ```bash
   # Starte Streamlit (falls nicht bereits aktiv)
   streamlit run app.py
   ```

   - Öffne Tab "Posteingang"
   - Ladezeit sollte < 100ms sein (kein sichtbarer Spinner)
   - Klicke auf "Antworten"-Button bei einer Email
   - Dialog öffnet SOFORT (kein Warten)
   - Draft-Reply wird angezeigt unter "Vorgeschlagene Antwort"
   - Bearbeite Text und klicke "Senden"
   - UI kehrt sofort zurück (non-blocking)

5. **Action-Execution (max 30 Sek warten):**
   ```bash
   # Prüfe ob Action verarbeitet wurde
   python3 -c "
   from utils.database import EmailDatabase
   db = EmailDatabase()
   actions = db.get_pending_actions(limit=10)
   print(f'Pending Actions: {len(actions)}')
   "
   ```

   - Prüfe in Outlook, ob Antwort gesendet wurde
   - Email sollte im Archiv-Ordner landen

### 3.3 Worker-Logs prüfen

```bash
# Zeige letzte 50 Zeilen der Logs
tail -n 50 email_worker.log

# Filtere nach Task-Typen
grep -E "\[(Harvester|Enrichment|Executor)\]" email_worker.log | tail -20

# Zeige Fehler
grep -E "(ERROR|❌)" email_worker.log | tail -10
```

---

## 📊 Phase 4: Monitoring (erste 24h)

### 4.1 Worker-Stabilität

```bash
# Prüfe ob Worker läuft
sudo systemctl status email-worker

# Prüfe ob Prozess existiert
ps aux | grep email_worker
```

### 4.2 Queue-Länge überwachen

```bash
# Zeige Queue-Status alle 30 Sekunden
watch -n 30 '
python3 -c "
from utils.database import EmailDatabase
db = EmailDatabase()
pending = db.get_pending_actions(limit=100)
synced = db.get_emails_by_status([\"synced\"], limit=100)
analyzed = db.get_emails_by_status([\"analyzed\"], limit=100)
print(f\"Pending Actions: {len(pending)}\")
print(f\"Synced (wartend): {len(synced)}\")
print(f\"Analyzed: {len(analyzed)}\")
"
'
```

### 4.3 DB-Status

```bash
# Zeige Email-Statistiken
python3 -c "
from utils.database import EmailDatabase
db = EmailDatabase()

statuses = ['synced', 'analyzed', 'pending_reply', 'pending_forward', 'archived', 'error']
for status in statuses:
    emails = db.get_emails_by_status([status], limit=1000)
    print(f'{status:15s}: {len(emails):3d} Emails')
"
```

### 4.4 Logs live verfolgen

```bash
# Live-Log mit farbiger Hervorhebung
tail -f email_worker.log | grep --color=auto -E "Harvester|Enrichment|Executor|✅|❌"
```

---

## 🔄 Rollback-Plan

Falls Probleme auftreten:

### 1. Worker sofort stoppen

```bash
sudo systemctl stop email-worker
```

### 2. Datenbank zurücksetzen

```bash
# Finde neuestes Backup
ls -lt data/email_cache.db.backup.* | head -1

# Stelle Backup wieder her (VORSICHT!)
cp data/email_cache.db.backup.YYYYMMDD_HHMMSS data/email_cache.db
```

### 3. Code zurücksetzen

```bash
# Falls Git verwendet wird
git log --oneline | head -5  # Finde letzten guten Commit
git revert HEAD  # Oder spezifischen Commit

# Manuell: Alte Dateien aus Backup wiederherstellen
```

### 4. Alte Version starten

```bash
sudo systemctl start email-worker
sudo systemctl status email-worker
```

---

## ✅ Erfolgskriterien

Nach erfolgreichem Deployment sollten folgende Kriterien erfüllt sein:

- [x] **Migration:** Spalte `draft_reply` existiert in DB
- [x] **Worker:** Läuft stabil ohne Restarts
- [x] **Logs:** Zeigen regelmäßig Harvester/Enrichment/Executor-Aktivität
- [x] **Performance:** UI-Ladezeit < 100ms (getestet: ~3ms)
- [x] **Queue:** Pending Actions werden innerhalb 30 Sek abgearbeitet
- [x] **Synced Emails:** Werden innerhalb 5 Min enriched
- [x] **UI:** "Antworten"-Dialog öffnet sofort ohne LLM-Warten
- [x] **Draft-Replies:** Werden für jede Email generiert

---

## 🐛 Troubleshooting

### Problem: Worker startet nicht

**Symptom:** `sudo systemctl status email-worker` zeigt "failed"

**Lösung:**
```bash
# Prüfe Worker-Logs
journalctl -u email-worker -n 50

# Teste Worker manuell
python3 email_worker.py
```

### Problem: Keine Emails werden enriched

**Symptom:** Viele Emails mit status='synced', keine mit 'analyzed'

**Lösung:**
```bash
# Prüfe Worker-Logs
grep "Enrichment" email_worker.log | tail -20

# Test Enrichment manuell
python3 -c "
from email_worker import EmailWorker
worker = EmailWorker()
worker.enrich_emails(batch_size=1)
"
```

### Problem: Draft-Replies fehlen

**Symptom:** Emails haben status='analyzed', aber `draft_reply` ist NULL

**Lösung:**
```bash
# Prüfe ob LLM verfügbar ist
python3 -c "
from utils.email_manager import EmailManager
from tools.outlook_graph_tool import OutlookGraphTool
from agents.asana_agent import AsanaAgent

outlook = OutlookGraphTool()
asana = AsanaAgent()
manager = EmailManager(outlook, asana)

print(f'LLM Provider: {manager.llm_provider}')
print(f'LLM Model: {manager.llm_model}')
print(f'LLM verfügbar: {manager.llm is not None}')
"

# Falls LLM fehlt, prüfe .env
grep -E "LLM_PROVIDER|RESEARCH_MODEL|ANTHROPIC_API_KEY" .env
```

### Problem: UI langsam

**Symptom:** Performance-Tests schlagen fehl

**Lösung:**
```bash
# Prüfe DB-Größe
ls -lh data/email_cache.db

# Cleanup alte Emails
python3 -c "
from utils.database import EmailDatabase
db = EmailDatabase()
db.delete_old_emails(days=30)
"

# Vacuum DB
python3 -c "
import sqlite3
conn = sqlite3.connect('data/email_cache.db')
conn.execute('VACUUM')
conn.close()
print('✓ DB optimiert')
"
```

---

## 📝 Nächste Schritte

Nach erfolgreichem Deployment:

1. **Monitoring einrichten:** Cron-Job für tägliche Status-Reports
2. **Alerts konfigurieren:** Email bei Worker-Crashes
3. **Backups automatisieren:** Tägliches DB-Backup
4. **Tuning:** Batch-Size für Enrichment optimieren (3-10 Emails)
5. **Features:** Weitere Draft-Templates für verschiedene Kategorien

---

## 📞 Support

Bei Fragen oder Problemen:
- Worker-Logs prüfen: `tail -f email_worker.log`
- Performance-Tests: `python3 tests/test_performance.py`
- DB-Status: Siehe Monitoring-Commands oben
