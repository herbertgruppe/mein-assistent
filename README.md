# Multi-Agenten-System

Ein modulares Multi-Agenten-System in Python, das verschiedene KI-Agenten orchestriert, um komplexe Aufgaben zu lösen.

## Projektstruktur

```
mein-assistent/
├── main.py                 # Orchestrator und Haupteinstiegspunkt
├── agents/                 # Agent-Module
│   ├── __init__.py
│   ├── base_agent.py      # Basis-Klasse für alle Agenten
│   ├── research_agent.py  # Agent für Informationsbeschaffung
│   └── task_agent.py      # Agent für Aufgabenausführung
├── requirements.txt        # Python-Abhängigkeiten
├── .env.example           # Beispiel für Umgebungsvariablen
└── README.md              # Diese Datei
```

## Features

- **Modulare Agent-Architektur**: Einfach erweiterbar mit neuen Agenten
- **Echte LLM-Integration**: Vollständig integriert mit OpenAI oder Anthropic
- **Automatische Agent-Auswahl**: System erkennt automatisch den passenden Workflow
- **Research Agent**: Führt Recherchen durch und sammelt Informationen
- **Task Agent**: Führt konkrete Aufgaben aus
- **Agent-Kommunikation**: Agenten können Kontext untereinander teilen
- **Flexible Workflows**: Verschiedene Execution-Strategien
- **Memory-System**: Jeder Agent hat ein eigenes Gedächtnis
- **Multi-Provider Support**: Wahlweise OpenAI oder Anthropic Claude

## Installation

### 1. Repository klonen oder herunterladen

```bash
cd mein-assistent
```

### 2. Virtuelle Umgebung erstellen (empfohlen)

```bash
python -m venv venv

# Aktivieren (Linux/Mac)
source venv/bin/activate

# Aktivieren (Windows)
venv\Scripts\activate
```

### 3. Abhängigkeiten installieren

```bash
pip install -r requirements.txt
```

### 4. Umgebungsvariablen konfigurieren

```bash
# .env Datei erstellen
cp .env.example .env

# .env bearbeiten und API-Key eintragen
nano .env
```

Beispiel `.env` Konfiguration:

```bash
# Wähle deinen Provider (anthropic oder openai)
LLM_PROVIDER=anthropic

# Anthropic Claude
ANTHROPIC_API_KEY=sk-ant-api03-xxx
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022

# Oder OpenAI
# OPENAI_API_KEY=sk-xxx
# OPENAI_MODEL=gpt-4

# Optionale Einstellungen
TEMPERATURE=0.7
```

## Verwendung

### Interaktiver Modus

```bash
python main.py
```

Der interaktive Modus startet eine Kommandozeilen-Schnittstelle, in der Sie:
- Anfragen eingeben können
- Verschiedene Workflows auswählen können
- Ergebnisse in Echtzeit sehen

### Verfügbare Workflows

1. **research_then_task** (Standard)
   - Führt zuerst eine Recherche durch
   - Nutzt die Ergebnisse für die Aufgabenausführung

2. **research_only**
   - Führt nur Recherche durch
   - Gut für Informationsbeschaffung

3. **task_only**
   - Führt nur die Aufgabe aus
   - Ohne vorherige Recherche

### Beispiel-Session

```
======================================================================
🤖 Multi-Agenten-System - Interaktiver Modus
======================================================================

LLM Provider: ANTHROPIC

Modi:
  • AUTO: System wählt passenden Workflow (Standard)
  • MANUAL: Workflow manuell wählen

Befehle: 'quit' zum Beenden, 'help' für Hilfe, 'mode' zum Wechseln


💬 Ihre Anfrage: Erkläre mir Quantencomputing
🔍 Gewählter Workflow: research_only

──────────────────────────────────────────────────────────────────────

[ResearchAgent] Führe Recherche durch...
[ResearchAgent] Provider: anthropic
[ResearchAgent] ✓ Recherche abgeschlossen

======================================================================
📊 ERGEBNISSE
======================================================================
Verwendete Agenten: ResearchAgent

🔍 RESEARCH AGENT
──────────────────────────────────────────────────────────────────────
Quantencomputing ist eine revolutionäre Computertechnologie...
[Detaillierte Erklärung vom LLM]

──────────────────────────────────────────────────────────────────────

💬 Ihre Anfrage: Schreibe einen kurzen Blogpost darüber
🔍 Gewählter Workflow: research_then_task

──────────────────────────────────────────────────────────────────────

[ResearchAgent] Führe Recherche durch...
[ResearchAgent] ✓ Recherche abgeschlossen

[TaskAgent] Führe Aufgabe aus...
[TaskAgent] Nutze Kontext vom ResearchAgent
[TaskAgent] ✓ Aufgabe abgeschlossen

======================================================================
📊 ERGEBNISSE
======================================================================
Verwendete Agenten: ResearchAgent, TaskAgent

🔍 RESEARCH AGENT
──────────────────────────────────────────────────────────────────────
[Recherche-Ergebnisse]

⚙️  TASK AGENT
──────────────────────────────────────────────────────────────────────
[Generierter Blogpost basierend auf Research]
```

## Programmatische Verwendung

```python
from main import AgentOrchestrator

# Orchestrator initialisieren
orchestrator = AgentOrchestrator()

# Anfrage verarbeiten
results = orchestrator.process_request(
    "Schreibe einen Blogpost über KI",
    workflow="research_then_task"
)

print(results)
```

## Erweiterung mit eigenen Agenten

### 1. Neuen Agent erstellen

```python
# agents/my_custom_agent.py
from .base_agent import BaseAgent

class MyCustomAgent(BaseAgent):
    def __init__(self):
        super().__init__("MyCustomAgent")

    def process(self, input_data, context=None):
        # Ihre Logik hier
        return {"result": "..."}
```

### 2. Agent registrieren

```python
# agents/__init__.py
from .my_custom_agent import MyCustomAgent

__all__ = ['ResearchAgent', 'TaskAgent', 'MyCustomAgent']
```

### 3. Im Orchestrator verwenden

```python
# main.py
from agents import MyCustomAgent

class AgentOrchestrator:
    def __init__(self):
        # ...
        self.my_agent = MyCustomAgent()
```

## LLM-Integration

Das System ist vollständig mit echten LLMs integriert und unterstützt zwei Provider:

### Anthropic Claude (Standard)

```bash
# .env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-api03-xxx
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
```

Verfügbare Modelle:
- `claude-3-5-sonnet-20241022` (empfohlen, bestes Preis-Leistungs-Verhältnis)
- `claude-3-opus-20240229` (höchste Qualität)
- `claude-3-haiku-20240307` (schnell und kostengünstig)

### OpenAI

```bash
# .env
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-xxx
OPENAI_MODEL=gpt-4
```

Verfügbare Modelle:
- `gpt-4` (höchste Qualität)
- `gpt-4-turbo-preview` (schneller)
- `gpt-3.5-turbo` (kostengünstig)

### Automatische Agent-Auswahl

Das System analysiert Ihre Eingabe automatisch und wählt den passenden Workflow:

**Research Keywords**: recherchiere, suche, finde, erkläre, was ist, wie funktioniert, analyse, vergleiche
- Löst `ResearchAgent` aus

**Task Keywords**: schreibe, erstelle, generiere, mache, entwickle, implementiere, verfasse
- Löst `TaskAgent` aus

**Kombinierte Anfragen**: Beide Keywords vorhanden
- Löst beide Agenten nacheinander aus (research_then_task)

## Konfiguration

### Umgebungsvariablen in `.env`

```bash
# LLM Provider (anthropic oder openai)
LLM_PROVIDER=anthropic

# API Keys
ANTHROPIC_API_KEY=sk-ant-api03-xxx
OPENAI_API_KEY=sk-xxx

# Modell-Auswahl
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
OPENAI_MODEL=gpt-4

# Weitere Einstellungen
TEMPERATURE=0.7  # Kreativität (0.0 = deterministisch, 1.0 = kreativ)
```

### Provider wechseln

Um zwischen OpenAI und Anthropic zu wechseln, ändere einfach den `LLM_PROVIDER` in der `.env`:

```bash
# Für Anthropic Claude
LLM_PROVIDER=anthropic

# Für OpenAI GPT
LLM_PROVIDER=openai
```

## Troubleshooting

### Import-Fehler

```bash
# Sicherstellen, dass Sie im richtigen Verzeichnis sind
cd mein-assistent

# Virtual Environment aktiviert?
source venv/bin/activate
```

### API-Key-Fehler

Überprüfen Sie, dass:
- `.env` Datei existiert (kopieren Sie `.env.example` zu `.env`)
- API-Key für den gewählten Provider korrekt eingetragen ist
- Keine Leerzeichen im Key vorhanden sind
- Der `LLM_PROVIDER` korrekt gesetzt ist

### LLM-Provider Import-Fehler

```bash
# Anthropic
pip install langchain-anthropic

# OpenAI
pip install langchain-openai

# Oder beide
pip install -r requirements.txt
```

### API-Keys erhalten

**Anthropic Claude:**
1. Registrieren auf https://console.anthropic.com/
2. API-Key erstellen unter "API Keys"
3. Key in `.env` als `ANTHROPIC_API_KEY` eintragen

**OpenAI:**
1. Registrieren auf https://platform.openai.com/
2. API-Key erstellen unter "API Keys"
3. Key in `.env` als `OPENAI_API_KEY` eintragen

## Hintergrunddienste (systemd-Poller)

Die App selbst läuft als Docker-Container. Daneben gibt es eigenständige systemd-Services,
die als `mein-assistent`-User laufen und per FastAPI-Endpoints mit der App kommunizieren.

| Service | Datei | Funktion |
|---|---|---|
| `plaud-poller` | `plaud_poller.py` | Plaud-Transkripte aus OneDrive holen + verarbeiten |
| `lena-asana-poller` | `lena_asana_poller.py` | Asana @mentions + Assignments für Lena pollen |
| `lena-mail-triage-poller` | `lena_mail_triage_poller.py` | Outlook-Posteingang automatisch kategorisieren |
| `lena-imap-poller` | `lena_imap_poller.py` | Lenas IMAP-Postfach pollen + Paperclip-Issues erstellen |

Alle Poller teilen dasselbe Muster: systemd-Service (User=mein-assistent, voll gehärtet) +
FastAPI-Endpoints auf localhost:8502 für Graph/Outlook/Asana-Operationen.

Vollständige Doku:
- [Mail-Triage (Outlook)](MAIL_TRIAGE.md)
- [Plaud-Poller](PLAUD_POLLER_README.md)
- [IMAP-Poller (Lena)](README.md#lena-imap-poller)

---

## Lena IMAP-Poller

Hintergrund-Service, der Lenas IMAP-Postfach (`lena@herbertgruppe.com`) periodisch pollt und bei neuen Mails automatisch Paperclip-Issues für Lena erstellt.

### Setup

**1. Env-Vars in `.env` ergänzen:**

```bash
# IMAP-Zugang (SMTP_USER + SMTP_PASSWORD sind bereits vorhanden)
SMTP_USER=herbertgruppe-com-0003     # IMAP-Benutzername
SMTP_PASSWORD=...                     # IMAP-Passwort

# Paperclip-Integration
PAPERCLIP_API_KEY_MA=...             # Paperclip App-Key (nicht der Agent-Key)
PAPERCLIP_COMPANY_ID_MA=9df4976b-9ac8-4e8f-a156-c06c7fa40cdc

# Telegram-Alert bei IMAP-Fehlern (optional, aber empfohlen)
TELEGRAM_ADMIN_CHAT_ID=<svens-chat-id>   # Sven kennt seine Chat-ID

# Optionale Overrides (Defaults sind production-ready)
# LENA_IMAP_POLL_INTERVAL_SEC=300    # Polling alle 5 Min
# LENA_IMAP_HOST=imaps.udag.de
# LENA_IMAP_PORT=993
# LENA_IMAP_LOG_FILE=/app/data/lena-imap-poller.log  # Docker-Default
```

**2. Service starten (docker-compose):**

```bash
docker compose up -d --build lena-imap-poller
```

### Konfiguration

| Env-Var | Default | Bedeutung |
|---|---|---|
| `LENA_IMAP_POLL_INTERVAL_SEC` | `300` | Polling-Intervall in Sekunden |
| `LENA_IMAP_HOST` | `imaps.udag.de` | IMAP-Server |
| `LENA_IMAP_PORT` | `993` | IMAP-Port (SSL) |
| `LENA_IMAP_LOG_FILE` | `/var/log/lena-imap-poller.log` | Log-Datei (Docker: `/app/data/...`) |
| `LENA_IMAP_DB_PATH` | `data/lena_processed_mails.db` | SQLite-Idempotenz-DB |
| `LENA_POLLER_MAILBOXES` | _(leer)_ | Multi-Mailbox: `user:agent-id,user2:agent-id2` |
| `LENA_SVEN_SENDERS` | `s.herbert@herbert.de,...` | Absender für Transkript-Erkennung |
| `TELEGRAM_ADMIN_CHAT_ID` | _(leer)_ | Sven's Chat-ID für Fehler-Alerts |

**Multi-Mailbox (z.B. Florian hinzufügen):**

```bash
LENA_POLLER_MAILBOXES=herbertgruppe-com-0003:7517114f-e731-4df5-96cf-a044719e9318,herbertgruppe-com-0002:571688f4-d3e1-4efe-8170-f515da83f8e4
# Passwort je Mailbox: IMAP_PASSWORD_{USER} (uppercase, - zu _)
IMAP_PASSWORD_HERBERTGRUPPE_COM_0002=...
```

### Restart / Monitoring

```bash
# Status
docker compose ps lena-imap-poller

# Logs (letzten 50 Zeilen)
docker compose logs --tail=50 lena-imap-poller

# Log-Datei direkt (persistiert im data-Volume)
docker compose exec lena-imap-poller tail -f /app/data/lena-imap-poller.log

# Neustart
docker compose restart lena-imap-poller

# Nach Code-Änderung neu bauen
docker compose up -d --build lena-imap-poller
```

**Systemd-Alternative** (falls kein Docker gewünscht):

```bash
# Service-Datei installieren
cp mein-assistent-lena-imap-poller.service /etc/systemd/system/
cp scripts/logrotate-lena-imap-poller /etc/logrotate.d/lena-imap-poller
systemctl daemon-reload
systemctl enable --now mein-assistent-lena-imap-poller
systemctl status mein-assistent-lena-imap-poller
journalctl -u mein-assistent-lena-imap-poller -f
```

### Logik

1. Alle `LENA_IMAP_POLL_INTERVAL_SEC` Sekunden: `IMAP SEARCH UNSEEN` auf `INBOX`
2. Für jede unbekannte Mail (Idempotenz via SQLite `lena_processed_mails`): Paperclip-Issue für Lena erstellen
3. Mails werden **nicht** als gelesen markiert (`BODY.PEEK[]`)
4. **Transkript-Erkennung:** Mails von Sven mit Subject-Keyword `transkript`/`plaud` oder `.txt`/`.docx`-Anhang → `📝`-Prefix + `priority: high`
5. **Spam-Filter:** `noreply`/`no-reply`-Absender → `priority: low`
6. **Fehlerbehandlung:** Exponential Backoff (max. 5 Min), nach 3 aufeinanderfolgenden Fehlern → Telegram-Alert

## Lizenz

MIT

## Beiträge

Beiträge sind willkommen! Erstellen Sie einen Pull Request oder öffnen Sie ein Issue.
