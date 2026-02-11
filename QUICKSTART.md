# Quick Start Guide

Schnellanleitung zum Starten des Multi-Agenten-Systems in 5 Minuten.

## 1. Installation (2 Minuten)

```bash
# Ins Projektverzeichnis wechseln
cd mein-assistent

# Virtual Environment erstellen
python -m venv venv

# Aktivieren
source venv/bin/activate  # Linux/Mac
# oder: venv\Scripts\activate  # Windows

# Dependencies installieren
pip install -r requirements.txt
```

## 2. API-Key konfigurieren (1 Minute)

### Option A: Anthropic Claude (empfohlen)

```bash
# .env Datei erstellen
cp .env.example .env

# Mit Editor öffnen
nano .env
```

Trage ein:
```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-api03-DEIN-KEY-HIER
```

API-Key erhalten: https://console.anthropic.com/

### Option B: OpenAI

```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-DEIN-KEY-HIER
```

API-Key erhalten: https://platform.openai.com/

## 3. Starten (1 Minute)

```bash
python main.py
```

## 4. Erste Anfragen (1 Minute)

### Beispiel 1: Research
```
💬 Ihre Anfrage: Was ist Python?
```

### Beispiel 2: Task
```
💬 Ihre Anfrage: Schreibe ein Gedicht über KI
```

### Beispiel 3: Kombiniert
```
💬 Ihre Anfrage: Erkläre Docker und schreibe eine Installations-Anleitung
```

## Modi

### AUTO Modus (Standard)
System wählt automatisch den passenden Workflow basierend auf deiner Anfrage.

### MANUAL Modus
Du wählst den Workflow manuell.

Wechseln mit: `mode`

## Befehle

- `help` - Hilfe anzeigen
- `mode` - Zwischen AUTO/MANUAL wechseln
- `quit` - Beenden

## Häufige Probleme

### "LLM nicht verfügbar"
**Lösung:** Prüfe ob API-Key in `.env` korrekt eingetragen ist

### "No module named 'langchain_anthropic'"
**Lösung:** `pip install langchain-anthropic`

### "No module named 'langchain_openai'"
**Lösung:** `pip install langchain-openai`

## Programmierung

Beispiel-Script ausführen:
```bash
python example.py
```

Eigenes Script:
```python
from main import AgentOrchestrator

orchestrator = AgentOrchestrator()
result = orchestrator.process_request("Deine Anfrage hier")
print(result)
```

## Kosten

**Anthropic Claude 3.5 Sonnet:**
- ~$3 per 1M Input-Tokens
- ~$15 per 1M Output-Tokens
- Durchschnitt: <$0.01 pro Anfrage

**OpenAI GPT-4:**
- ~$30 per 1M Input-Tokens
- ~$60 per 1M Output-Tokens
- Durchschnitt: ~$0.03-0.05 pro Anfrage

## Nächste Schritte

1. Lies die vollständige [README.md](README.md)
2. Probiere verschiedene Workflows aus
3. Erstelle eigene Agenten
4. Experimentiere mit Temperatur-Einstellungen

## Support

Bei Problemen:
1. Prüfe die [README.md](README.md) Troubleshooting-Sektion
2. Stelle sicher, dass alle Dependencies installiert sind
3. Prüfe die `.env` Konfiguration

Viel Erfolg! 🚀
