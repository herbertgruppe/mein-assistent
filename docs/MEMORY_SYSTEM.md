# Langzeitgedächtnis-System

Das Langzeitgedächtnis-System ermöglicht es dem Assistenten, sich Informationen über den Nutzer zu merken und diese als Kontext für zukünftige Anfragen zu nutzen.

## Features

### 1. Nutzerprofil
Der Assistent kann sich folgende Informationen über dich merken:
- **Name**: Dein Name
- **Beruf/Profession**: Was du beruflich machst
- **Interessen**: Deine Interessensgebiete
- **Schreibstil**: Bevorzugter Schreibstil für generierte Texte
- **Sprachen**: Sprachen die du sprichst
- **Freie Informationen**: Beliebige weitere Facts über dich

### 2. Recherche-Erkenntnisse
Wichtige Erkenntnisse aus vergangenen Recherchen werden automatisch gespeichert und können bei ähnlichen Anfragen wiederverwendet werden.

### 3. Konversations-Kontext
Die letzten Konversationen werden gespeichert, um kontinuierlichere Gespräche zu ermöglichen.

## Befehle im interaktiven Modus

### `remember` - Information speichern
Speichert eine Information über dich im Gedächtnis.

**Kategorien:**
1. Name
2. Beruf/Profession
3. Interesse
4. Schreibstil-Präferenz
5. Sprache
6. Freie Information

**Beispiel:**
```
💬 Ihre Anfrage: remember
Kategorie (1-6): 2
Information: Software-Entwickler
✓ Information gespeichert: Software-Entwickler
```

### `memory` - Gedächtnis anzeigen
Zeigt den gesamten Inhalt des Gedächtnisses an.

**Beispiel:**
```
💬 Ihre Anfrage: memory
======================================================================
📚 GEDÄCHTNIS-EXPORT
======================================================================

👤 NUTZERPROFIL
----------------------------------------------------------------------
Beruf: Software-Entwickler
Interessen: Python, Machine Learning
...
```

### `forget` - Gedächtnis löschen
Löscht das gesamte Gedächtnis nach Bestätigung.

**Beispiel:**
```
💬 Ihre Anfrage: forget
⚠️  WARNUNG: Gesamtes Gedächtnis löschen? (ja/nein): ja
✓ Gedächtnis wurde gelöscht
```

## Automatische Nutzung

Der Assistent nutzt das Gedächtnis automatisch:

1. **Vor jeder Anfrage**: Der relevante Kontext wird aus dem Gedächtnis abgerufen
2. **Während der Verarbeitung**: Der Kontext wird an die Agenten weitergegeben
3. **Nach erfolgreicher Recherche**: Wichtige Erkenntnisse werden automatisch gespeichert

## Datenspeicherung

Alle Daten werden lokal in der Datei `user_profile.json` gespeichert. Diese Datei befindet sich im Hauptverzeichnis des Projekts.

### Datenstruktur

```json
{
  "user_profile": {
    "name": "Dein Name",
    "interests": ["Python", "AI"],
    "preferred_writing_style": "Technisch und präzise",
    "profession": "Software-Entwickler",
    "languages": ["Deutsch", "Englisch"],
    "custom_facts": [
      {
        "content": "Arbeitet viel mit LangChain",
        "timestamp": "2025-01-25T10:30:00",
        "category": "custom"
      }
    ]
  },
  "research_insights": [
    {
      "query": "Was ist LangChain?",
      "insight": "LangChain ist ein Framework...",
      "sources": [],
      "timestamp": "2025-01-25T10:35:00"
    }
  ],
  "conversation_context": [
    {
      "query": "Erkläre mir Machine Learning",
      "workflow": "research_only",
      "summary": "Machine Learning ist...",
      "timestamp": "2025-01-25T10:40:00"
    }
  ]
}
```

## Implementierungsdetails

### MemoryManager Klasse
Die `MemoryManager` Klasse in `utils/memory_manager.py` verwaltet das gesamte Gedächtnis-System.

**Hauptmethoden:**
- `add_user_fact()`: Fügt eine Information über den Nutzer hinzu
- `add_research_insight()`: Speichert eine Recherche-Erkenntnis
- `get_relevant_context()`: Sucht relevanten Kontext für eine Anfrage
- `format_context_for_agent()`: Formatiert den Kontext für Agenten
- `export_memory()`: Exportiert das Gedächtnis als formatierten String

### Integration in Agenten

Beide Agenten (ResearchAgent und TaskAgent) erhalten automatisch den relevanten Kontext:

```python
# Im Orchestrator
memory_context = self.memory.get_relevant_context(user_input)
user_context_str = self.memory.format_context_for_agent()

research_result = self.research_agent.process(
    user_input,
    context={"memory": memory_context, "user_context": user_context_str}
)
```

Die Agenten nutzen diesen Kontext dann, um personalisierte und kontextbewusste Antworten zu generieren.

## Beispiel-Workflow

1. Nutzer gibt Information ein:
   ```
   💬 remember
   Kategorie: 2 (Beruf)
   Information: Software-Entwickler
   ```

2. System speichert die Information in `user_profile.json`

3. Bei nächster Anfrage:
   ```
   💬 Schreibe einen Blogpost über Python
   ```

4. System holt relevanten Kontext (Beruf: Software-Entwickler)

5. TaskAgent erhält den Kontext und kann den Blogpost entsprechend anpassen

6. Nach der Recherche werden wichtige Erkenntnisse automatisch gespeichert
