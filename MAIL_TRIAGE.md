# Lena Mail-Triage — Betrieb & Weiterentwicklung

> **Status:** Live auf Hetzner seit 2026-06-15 (v1/v1.5 — PRs #68, #69)
>
> Dieses Dokument beschreibt Architektur, Setup, operativen Betrieb und den
> geplanten Feature-Backlog (v3–v5) des Mail-Triage-Systems.

---

## Inhaltsverzeichnis

1. [Überblick](#1-überblick)
2. [Architektur](#2-architektur)
3. [Triage-Schema](#3-triage-schema)
4. [Setup (Erstinstallation)](#4-setup-erstinstallation)
5. [Operativer Betrieb](#5-operativer-betrieb)
6. [Re-Triage-Workflow](#6-re-triage-workflow)
7. [Persona-Config (Direktberichte)](#7-persona-config-direktberichte)
8. [Konfigurationsreferenz (Env-Vars)](#8-konfigurationsreferenz-env-vars)
9. [Outlook-Verhalten](#9-outlook-verhalten)
10. [Cost-Monitoring](#10-cost-monitoring)
11. [Logs & Troubleshooting](#11-logs--troubleshooting)
12. [Feature-Backlog v3–v5](#12-feature-backlog-v3v5)

---

## 1. Überblick

Der Mail-Triage-Poller kategorisiert automatisch alle eingehenden Mails in Svens
Outlook-Posteingang mit zwei Outlook-Kategorien:

- **1× Aktion:** `Lena: Antworten | Tun | Warten | Recherchieren | Weiterleiten | Ablegen`
- **1× Priorität:** `Priorität: Hoch | Mittel | Niedrig`

Sven sieht damit strukturiert im Posteingang, was er heute tun muss — ohne jede
Mail einzeln öffnen zu müssen.

**Produktiv seit:** 2026-06-15 — 75 Mails kategorisiert am ersten Tag, 2 Hoch-Prio-Telegram-Alerts gesendet.

---

## 2. Architektur

```
Svens Outlook-Posteingang (Microsoft Graph)
         │
         │  GET /inbox-for-triage (alle 10 Min)
         ▼
┌──────────────────────────────────────┐
│   lena_mail_triage_poller.py         │
│                                      │
│   Hybrid-Triage:                     │
│   1) Schnelle Regeln (kein LLM):     │
│      • Kalender-Notification?  ──►  Ablegen + Niedrig
│      • Newsletter/noreply?     ──►  Ablegen + Niedrig
│   2) Alles andere:                   │
│      • Claude-Haiku LLM-Call   ──►  Aktion + Priorität + Reasoning
│   3) Fallback (LLM-Fehler):    ──►  Antworten + Mittel / Hoch (Urgency)
│                                      │
│   State: processed_message_ids.json  │
└────────────────┬─────────────────────┘
                 │
                 │  POST /mail/categorize (pro Mail)
                 ▼
     api.py (FastAPI, localhost:8502)
                 │
                 │  PATCH /me/messages/{id} { categories: [...] }
                 ▼
     Microsoft Graph API
                 │
                 ▼
     Outlook-Kategorien auf Mail gesetzt
                 │
                 │  (bei Priorität: Hoch)
                 ▼
     Telegram-Alert an Sven
```

**Pattern:** Identisch zu `plaud-poller` und `lena-asana-poller` (systemd-Service + FastAPI-Endpoints). Keine zweite Parallelstruktur.

### Dateien

| Datei | Beschreibung |
|---|---|
| `lena_mail_triage_poller.py` | Polling-Loop, Hybrid-Triage-Logik, LLM-Aufruf |
| `mein-assistent-mail-triage-poller.service` | systemd-Unit (User=mein-assistent, voll gehärtet) |
| `api.py` | FastAPI-Endpoints (3 neue Endpoints, s. u.) |
| `config/lena-mail-triage.yaml` | Persona-Config: Direktberichte, externe Kontakte |

### FastAPI-Endpoints

| Methode | Pfad | Funktion |
|---|---|---|
| `POST` | `/api/lena/outlook/master-categories/sync` | Legt 9 MasterCategories idempotent an |
| `POST` | `/api/lena/mail/categorize` | Setzt Aktion + Priorität auf eine Mail |
| `GET` | `/api/lena/mail/inbox-for-triage` | Liefert un-kategorisierte Mails (Poller-Inbox) |

---

## 3. Triage-Schema

### Aktionen (6)

| Kategorie-Name | Farbe | Bedeutung |
|---|---|---|
| `Lena: Antworten` | Rot | Sven muss zurückschreiben |
| `Lena: Tun` | Orange | Sven muss aktiv handeln (kein Mail-Reply) |
| `Lena: Warten` | Gelb | Reine Info, Sven wartet auf Folge-Aktion anderer |
| `Lena: Recherchieren` | Grün | Sven muss erst Hintergrund klären |
| `Lena: Weiterleiten` | Blau | Geht an einen Direktbericht |
| `Lena: Ablegen` | Grau | Keine Aktion, archivieren |

### Prioritäten (3)

| Kategorie-Name | Farbe | Bedeutung |
|---|---|---|
| `Priorität: Hoch` | Lila | Frist heute/diese Woche, Eskalation |
| `Priorität: Mittel` | Türkis | Standard für Direktberichte, laufende Themen |
| `Priorität: Niedrig` | Hellblau | FYI, kann liegenbleiben |

### Entscheidungsbaum

```
Mail empfangen
    │
    ├─ Kalender-Subject (Einladung/Annahme/Absage) → Ablegen + Niedrig
    │
    ├─ Newsletter-Absender (noreply/@mailchimp etc.) → Ablegen + Niedrig
    │
    └─ Alles andere → Claude Haiku (Kontextuelle LLM-Entscheidung)
                          │
                          ├─ LLM erfolgreich → Aktion + Priorität + Reasoning
                          │
                          └─ LLM fehlgeschlagen →
                              ├─ Urgency-Keywords (dringend/Mahnung) → Antworten + Hoch
                              └─ Default → Antworten + Mittel
```

---

## 4. Setup (Erstinstallation)

Diese Schritte sind für eine **Neuinstallation** auf einem frischen Server. Auf dem
aktuellen Hetzner-Server (46.225.132.135) ist alles bereits live.

### 4.1 System-User anlegen

```bash
# Service-User (kein Login-Shell, kein Home)
useradd --system --no-create-home --shell /sbin/nologin mein-assistent
```

### 4.2 Code deployen und venv einrichten

```bash
cd /opt/mein-assistent
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

PyYAML wird für die Persona-Config benötigt und ist in `requirements.txt` enthalten.

### 4.3 Env-Vars konfigurieren

In `/opt/mein-assistent/.env` folgende Variablen ergänzen:

```bash
# Pflicht für Mail-Triage-Poller
API_SECRET_KEY=<interner API-Key für localhost:8502>
ANTHROPIC_API_KEY=<Anthropic API-Key>

# Optional: Modell-Override (Standard: claude-haiku-4-5)
# LENA_MAIL_TRIAGE_LLM_MODEL=claude-haiku-4-5

# Optional: Telegram-Alerts bei Hoch-Prio-Mails
TELEGRAM_BOT_TOKEN=<Bot-Token>
TELEGRAM_ADMIN_CHAT_ID=<Svens Chat-ID>

# Optional: Polling-Intervall (Standard: 600 Sekunden = 10 Min)
# LENA_MAIL_TRIAGE_POLL_INTERVAL_SEC=600

# Optional: Lookback beim Erststart (Standard: 7 Tage)
# LENA_MAIL_TRIAGE_LOOKBACK_DAYS=7

# Optional: Persona-Config-Override (Standard: config/lena-mail-triage.yaml)
# LENA_MAIL_TRIAGE_CONFIG_FILE=/opt/mein-assistent/config/lena-mail-triage.yaml
```

### 4.4 Log- und State-Verzeichnisse anlegen

```bash
# systemd StateDirectory + LogsDirectory erzeugen (macht systemd automatisch,
# aber für manuellen Erststart vorab anlegen):
mkdir -p /var/lib/mail-triage-poller /var/log/mail-triage-poller
chown mein-assistent:mein-assistent /var/lib/mail-triage-poller /var/log/mail-triage-poller
```

### 4.5 systemd-Service installieren

```bash
cp /opt/mein-assistent/mein-assistent-mail-triage-poller.service \
   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now mein-assistent-mail-triage-poller
```

### 4.6 Outlook-MasterCategories einmalig anlegen

Bevor der Poller Kategorien setzen kann, müssen die 9 MasterCategories in Svens Outlook existieren:

```bash
curl -X POST http://127.0.0.1:8502/api/lena/outlook/master-categories/sync \
     -H "X-API-Key: $API_SECRET_KEY"
```

Erwartete Antwort:
```json
{
  "success": true,
  "created": ["Lena: Antworten", "Lena: Tun", ...],
  "existing": []
}
```

Das ist idempotent — kann beliebig oft aufgerufen werden (erstellt nur fehlende Kategorien).

### 4.7 Service-Status prüfen

```bash
systemctl is-active mein-assistent-mail-triage-poller   # → active
systemctl status  mein-assistent-mail-triage-poller
journalctl -u mein-assistent-mail-triage-poller -f
```

---

## 5. Operativer Betrieb

### Status prüfen

```bash
systemctl status mein-assistent-mail-triage-poller
journalctl -u mein-assistent-mail-triage-poller --since "1 hour ago"
```

### Live-Logs mit Triage-Entscheidungen

Jede kategorisierte Mail erzeugt einen JSON-Log-Eintrag:

```json
{
  "event": "mail_categorized",
  "message_id": "AAMkA...",
  "subject": "Rückfrage zu Angebot 2024-089",
  "sender": "frank.herbert@herbert.de",
  "action": "antworten",
  "priority": "hoch",
  "rule": "llm:Direktbericht mit konkreter Frage und Fristbezug"
}
```

**`rule`-Präfixe:**
- `calendar_subject` — Kalender-Notification (kein LLM-Aufruf)
- `newsletter_sender` — Automated-Sender (kein LLM-Aufruf)
- `llm:<text>` — LLM-Entscheidung mit Begründung
- `llm_failed_urgency_fallback` — LLM-Fehler, Urgency-Keywords erkannt
- `llm_failed_default` — LLM-Fehler, Default-Fallback

### Neustart nach Config-Änderung

Nach Änderungen an `config/lena-mail-triage.yaml` (z.B. neue Direktberichte):

```bash
systemctl restart mein-assistent-mail-triage-poller
journalctl -u mein-assistent-mail-triage-poller -n 5 | grep -i "persona\|direktberichte"
```

Erwarteter Log: `Persona-Config geladen: 13 Direktberichte`

---

## 6. Re-Triage-Workflow

Der Re-Triage-Mode kategorisiert **bereits kategorisierte Mails neu** — nützlich
nach einer Logik-Verbesserung (z.B. Upgrade von Regelbasiert auf LLM).

### Einmaliger Re-Triage-Lauf

```bash
# 1. Drop-in-Override anlegen (NIEMALS die Service-Datei direkt bearbeiten)
systemctl edit mein-assistent-mail-triage-poller
```

Im Editor eintragen:
```ini
[Service]
Environment="LENA_MAIL_TRIAGE_RETRIAGE_ALL=1"
```

```bash
# 2. Service mit Override neustarten
systemctl restart mein-assistent-mail-triage-poller

# 3. Fortschritt beobachten
journalctl -u mein-assistent-mail-triage-poller -f
```

Nach einem Lauf erscheint im Log: `Cycle N done: {"categorized": X, "skipped_processed": 0, ...}`

### Override rückgängig machen (WICHTIG)

```bash
# Override-Datei löschen (entfernt das Drop-in)
systemctl edit mein-assistent-mail-triage-poller --drop-in=override.conf
# Alternativ: Datei löschen und daemon-reload
rm /etc/systemd/system/mein-assistent-mail-triage-poller.service.d/override.conf
systemctl daemon-reload
systemctl restart mein-assistent-mail-triage-poller
```

Prüfen: `systemctl cat mein-assistent-mail-triage-poller` darf kein `LENA_MAIL_TRIAGE_RETRIAGE_ALL` mehr zeigen.

---

## 7. Persona-Config (Direktberichte)

Die Direktbericht-Liste und Persona-Beschreibung liegen in:

```
config/lena-mail-triage.yaml
```

**Warum extern?** Wenn ein Direktbericht wechselt (Kündigung/Neueinstellung), muss
nur diese YAML-Datei aktualisiert werden — kein Code-PR, kein Rebuild nötig. Ein
Service-Neustart übernimmt die Änderung.

### Neuen Direktbericht hinzufügen

```yaml
# config/lena-mail-triage.yaml
direktberichte:
  # ... bestehende Einträge ...
  - name: "Max Mustermann"
    funktion: "NL-Leiter Neue Niederlassung"
    email_domain: "@herbert.de"
```

```bash
systemctl restart mein-assistent-mail-triage-poller
```

### Externen wichtigen Kontakt ergänzen

```yaml
externe_wichtige_kontakte:
  # ... bestehende Einträge ...
  - name: "Dr. Klaus Bauer"
    context: "Steuerberater"
    default_prioritaet: "hoch"
```

### Fallback-Verhalten

Falls `config/lena-mail-triage.yaml` nicht existiert oder PyYAML nicht installiert ist,
verwendet der Poller den Hardcoded-Fallback im Code. Der Start-Log zeigt dann:

```
[WARN] Persona-Config nicht gefunden oder PyYAML fehlt — nutze Hardcoded-Fallback.
```

---

## 8. Konfigurationsreferenz (Env-Vars)

| Variable | Standard | Beschreibung |
|---|---|---|
| `API_SECRET_KEY` | _(Pflicht)_ | X-API-Key für `/api/lena/*` |
| `ANTHROPIC_API_KEY` | _(Pflicht)_ | Anthropic API-Key für LLM-Triage |
| `MEIN_ASSISTENT_API_URL` | `http://127.0.0.1:8502` | API-Basis-URL |
| `LENA_MAIL_TRIAGE_LLM_MODEL` | `claude-haiku-4-5` | LLM-Modell |
| `LENA_MAIL_TRIAGE_POLL_INTERVAL_SEC` | `600` | Polling-Intervall (Sekunden) |
| `LENA_MAIL_TRIAGE_LOOKBACK_DAYS` | `7` | Erstlauf-Lookback (Tage) |
| `LENA_MAIL_TRIAGE_BATCH_LIMIT` | `50` | Max Mails pro Cycle |
| `LENA_MAIL_TRIAGE_RETRIAGE_ALL` | `0` | `1` = Re-Triage aller Mails (einmalig) |
| `LENA_MAIL_TRIAGE_STATE_FILE` | `/var/lib/mail-triage-poller/state.json` | State-File (systemd: via StateDirectory) |
| `LENA_MAIL_TRIAGE_LOG_FILE` | `/var/log/mail-triage-poller/lena-mail-triage-poller.log` | Log-Datei |
| `LENA_MAIL_TRIAGE_CONFIG_FILE` | _(auto)_ | Pfad zur Persona-YAML (überschreibt Such-Pfade) |
| `TELEGRAM_BOT_TOKEN` | _(leer)_ | Bot-Token für Hoch-Prio-Alerts |
| `TELEGRAM_ADMIN_CHAT_ID` | _(leer)_ | Svens Telegram Chat-ID |

---

## 9. Outlook-Verhalten

### Was passiert wenn Sven eine Kategorie manuell ändert?

Der Poller setzt Kategorien per `PATCH /me/messages/{id}` — er überschreibt dabei
nur `Lena:*`- und `Priorität:*`-Kategorien. Andere Kategorien (die Sven selbst
angelegt hat) bleiben erhalten.

**Sven kann jederzeit manuell überschreiben.** Der Poller respektiert das:
- Beim nächsten Cycle ist die Mail bereits in `processed_message_ids` → wird übersprungen
- Im Normal-Mode wird eine bereits kategorisierte Mail **nicht** neu kategorisiert
- Nur im Re-Triage-Mode (`RETRIAGE_ALL=1`) werden alle Mails erneut durchlaufen

**Hinweis für v3 (Hindsight-Lern-Loop):** Wenn Sven eine Kategorie nach der
automatischen Triage manuell ändert, soll das System das lernen. Das ist der
geplante `GET /api/lena/mail/categorized-overrides`-Endpoint (Teil B).

### Kategorie-Priorität in Outlook

Outlook zeigt Kategorien alphabetisch. Die `Lena:`-Kategorien erscheinen nach
`Priorität:`-Kategorien. Sven sieht beide Kategorien als Tags auf der Mail.

### Token-Refresh

Das Outlook-Token wird in `/opt/mein-assistent/auth/outlook_token.json` gespeichert.
Falls der Token abläuft, antwortet `api.py` mit HTTP 503 — der Poller loggt den
Fehler und versucht es im nächsten Cycle erneut (Exponential Backoff bis 300s).

Token erneuern: Sven oder ein Admin muss `authenticate_outlook.py` interaktiv ausführen.

---

## 10. Cost-Monitoring

### Erwartete Kosten

| Parameter | Wert |
|---|---|
| Neue Mails/Tag | ~30–50 |
| LLM-Calls/Tag | ~30–50 (Newsletter/Kalender treffen Regel-Path) |
| Input-Token/Call | ~500 (Persona + Mail-Inhalt) |
| Output-Token/Call | ~80 (JSON-Antwort) |
| Modell | Claude Haiku 4.5 |
| **Geschätzte Kosten/Monat** | **~$1.50** |

### Monitoring

Im Anthropic-Dashboard (console.anthropic.com) unter "Usage" den täglichen
Verbrauch nach Modell aufschlüsseln. Spike-Warnung: ein Re-Triage-Lauf über
90 Tage (bis zu 3.000 Mails) kostet ~$3–5 einmalig.

### Cost-Optimierungen bereits implementiert

- **Regel-Path vor LLM:** Newsletter und Kalender-Notifications werden ohne
  LLM-Aufruf triagiert (~30–40% der Mails)
- **processed_message_ids:** Jede Mail wird nur einmal triagiert (kein Re-Processing)
- **Rate-Limit-Pause:** 0.4s zwischen LLM-Calls verhindert 429-Bursts
- **Daily Cap:** Max 5 Telegram-Alerts/Tag (Anti-Spam)

---

## 11. Logs & Troubleshooting

### Häufige Fehlermuster

**Problem: Service startet nicht**
```bash
journalctl -u mein-assistent-mail-triage-poller -n 20 --no-pager
```
Häufige Ursachen:
- `API_SECRET_KEY nicht gesetzt` → `.env` auf dem Server prüfen
- `anthropic Python-Package nicht installiert` → `venv/bin/pip install anthropic`
- `ANTHROPIC_API_KEY nicht gesetzt` → `.env` auf dem Server prüfen

**Problem: Mails werden nicht kategorisiert**
```bash
# Prüfen ob Inbox-Endpoint erreichbar ist
curl -s "http://127.0.0.1:8502/api/lena/mail/inbox-for-triage?days=1" \
     -H "X-API-Key: $API_SECRET_KEY" | python3 -m json.tool
```
- HTTP 503 → Outlook-Token abgelaufen (`authenticate_outlook.py` ausführen)
- HTTP 401 → `API_SECRET_KEY` falsch

**Problem: LLM-Fehler im Log**
```
LLM triage failed for sender=... : ...
```
- Prüfe Anthropic-API-Status
- Prüfe ob `ANTHROPIC_API_KEY` in `.env` noch gültig ist
- Der Fallback (Antworten + Mittel) ist aktiv — keine Unterbrechung des Betriebs

**Problem: Telegram-Alerts kommen nicht an**
```bash
# Manueller Test
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
     -d "chat_id=$TELEGRAM_ADMIN_CHAT_ID&text=Test"
```

### State-File direkt lesen

```bash
cat /var/lib/mail-triage-poller/state.json | python3 -m json.tool | head -20
```

---

## 12. Feature-Backlog v3–v5

### v3 — Hindsight-Lern-Loop (Teil B, mittlere Prio)

Sven korrigiert Kategorien manuell → Lena lernt daraus und wendet das beim nächsten
ähnlichen Absender/Subject automatisch an.

**Architektur-Constraints (Lead Engineer):**
- State muss ins **bestehende Hindsight-System** (nicht in eine neue Parallel-Datei)
- Body-Embeddings sind v2-Scope — MVP: Absender-Domain + Subject-Prefix
- Threshold (3× gleicher Absender → 4. Mal automatisch) **muss konfigurierbar** sein
- Lern-Treffer im `rule_id` mit Prefix `llm+memory:` markieren

**Nötige Vorab-Klärung:**
1. Nutzt das Hindsight-System eine DB-Tabelle oder ein File-Backend?
2. Wie sieht der bestehende `hindsight_recall`-API-Call aus?

### v4 — Briefing-Integration (Teil C, mittlere Prio)

Triage-Status im täglichen Lena-Briefing:
```
📬 7 Antworten (2 Hoch), 3 Tun, 4 Warten, 1 Recherchieren, 6 Weiterleiten, 12 Ablegen
```

**Architektur-Constraints (Lead Engineer):**
- Neuer Endpoint `GET /api/lena/mail/triage-summary?since=<iso-utc>` liefert Zähler
- `since`-Parameter **muss UTC sein** (kein Server-Local-Time)
- Lena ruft Backend auf → Backend zählt → Lena formatiert (klare Layer-Trennung)

### v5 — Bestandsinbox-Bootstrap (Teil D, niedrige Prio)

Einmaliger Lauf für ältere Mails (>7 Tage, bis 90 Tage):

```bash
python3 tools/lena_mail_bootstrap.py --days 90
```

**Architektur-Constraints (Lead Engineer):**
- `tools/lena_mail_bootstrap.py` **muss** die `triage_mail()`-Funktion aus
  `lena_mail_triage_poller.py` importieren — kein Duplikat der Triage-Logik
- Rate-Limit: 30 Mails/Min (Anthropic-Rate-Limit)
- **Erst nach 3–4 Wochen Hindsight-Betrieb (v3)** starten — damit gelernten Patterns
  beim Bootstrap wirken

### Prio-Reihenfolge

| Teil | Feature | Prio | Timing |
|---|---|---|---|
| A | Doku + Persona-Externalisierung | Pflicht | Diese Woche (erledigt) |
| C | Briefing-Integration | Mittel | Diese/nächste Woche |
| B | Hindsight-Lern-Loop | Mittel | Nächste 2 Wochen |
| D | Bestandsinbox-Bootstrap | Niedrig | Nach 3–4 Wochen B-Betrieb |
