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

## Lizenz

MIT

## Beiträge

Beiträge sind willkommen! Erstellen Sie einen Pull Request oder öffnen Sie ein Issue.
