# Chat-Historie Sicherheitsfunktionen

## Übersicht

Die Chat-Historie wurde um drei Sicherheitsfunktionen gegen Kontext-Verschmutzung erweitert:

1. **Reset-Befehl**: `clear` oder `reset` Befehl im interaktiven Modus
2. **Relevanz-Check**: Kritische Prüfung der Historie durch den ResearchAgent
3. **Zusammenfassungs-Logik**: Optionale Übertragung wichtiger Fakten ins permanente Gedächtnis

---

## 1. Reset-Befehl (`clear` / `reset`)

### Funktionsweise

Der neue Befehl löscht die `conversation_context` im MemoryManager (gespeichert in `user_profile.json`).

### Verwendung

```bash
💬 Ihre Anfrage: clear
```

oder

```bash
💬 Ihre Anfrage: reset
```

### Ablauf

1. System prüft, ob Chat-Historie Einträge enthält
2. Falls leer: Bestätigung und Abbruch
3. Falls Einträge vorhanden:
   - Zeigt Anzahl der Einträge an
   - Fragt, ob wichtige Erkenntnisse übertragen werden sollen
   - Standard: **Ja** (Enter drücken)
4. Löscht die `conversation_context`
5. Optional: Überträgt wichtige Erkenntnisse in `research_insights`

### Beispiel-Interaktion

```
💬 Ihre Anfrage: clear

💭 Chat-Historie enthält 15 Einträge
Wichtige Erkenntnisse in permanentes Gedächtnis übertragen? (ja/nein, Enter=ja):

✓ Chat-Historie gelöscht (3 Erkenntnisse übertragen)
```

---

## 2. Zusammenfassungs-Logik

### Implementierung

**Datei**: `utils/memory_manager.py`
**Methode**: `clear_conversation_history(transfer_insights: bool = True)`

### Logik

Beim Löschen der Chat-Historie werden automatisch wichtige Erkenntnisse identifiziert und übertragen:

**Kriterien für die Übertragung:**

1. **Inhaltliche Substanz**: Nur Konversationen mit mehr als 100 Zeichen Summary
2. **Workflow-Typ**: Nur Konversationen mit `research` im Workflow-Namen
3. **Limitierung**: Summary wird auf 500 Zeichen begrenzt

**Ziel**: Verhindern, dass wichtige Recherche-Erkenntnisse (z.B. neue Informationen über die KHS-Liste) verloren gehen.

### Code-Beispiel

```python
def clear_conversation_history(self, transfer_insights: bool = True) -> int:
    transferred = 0

    if transfer_insights and self.memory["conversation_context"]:
        for conv in self.memory["conversation_context"]:
            # Übertrage nur substantielle Research-Erkenntnisse
            if len(conv.get("summary", "")) > 100:
                if "research" in conv.get("workflow", "").lower():
                    self.add_research_insight(
                        query=conv["query"],
                        insight=conv["summary"][:500],
                        sources=["Konversations-Historie"]
                    )
                    transferred += 1

    # Lösche die Historie
    self.memory["conversation_context"] = []
    self._save_memory()

    return transferred
```

---

## 3. Relevanz-Check im ResearchAgent

### Implementierung

**Datei**: `agents/research_agent.py`
**Methode**: `_create_system_prompt()`

### System-Prompt Erweiterung

Der ResearchAgent erhält folgende kritische Anweisung:

```
⚠️ KRITISCH - KONTEXT-VERSCHMUTZUNG VERMEIDEN:
Wenn du Kontext aus vorherigen Konversationen erhältst:
- Prüfe KRITISCH, ob die Informationen DIREKT relevant für die aktuelle Anfrage sind
- Verwende NUR Informationen, die einen klaren Bezug zur aktuellen Frage haben
- Ignoriere veraltete oder thematisch irrelevante Informationen aus der Historie
- Bei Zweifeln: Führe eine NEUE Suche durch statt auf alte Informationen zu vertrauen
- Bevorzuge IMMER aktuelle Tool-Ergebnisse über historische Kontext-Informationen
```

### Zweck

1. **Vermeidung von Halluzinationen**: Agent soll nicht auf veraltete Informationen zurückgreifen
2. **Aktualität**: Neue Suchen werden bevorzugt gegenüber historischem Kontext
3. **Relevanz-Fokus**: Nur thematisch passende Informationen werden verwendet

---

## Datenstruktur

### Vorher (user_profile.json)

```json
{
  "conversation_context": [
    {
      "query": "Suche Dr. Müller in der KHS-Liste",
      "workflow": "research_only",
      "summary": "Dr. Müller wurde in der KHS-Liste gefunden...",
      "timestamp": "2024-01-20T10:30:00"
    },
    {
      "query": "Wie viele Einträge hat die Liste?",
      "workflow": "research_then_task",
      "summary": "Die KHS-Liste enthält 450 Einträge...",
      "timestamp": "2024-01-20T10:35:00"
    }
  ],
  "research_insights": []
}
```

### Nach `clear` mit Übertragung

```json
{
  "conversation_context": [],
  "research_insights": [
    {
      "query": "Wie viele Einträge hat die Liste?",
      "insight": "Die KHS-Liste enthält 450 Einträge...",
      "sources": ["Konversations-Historie"],
      "timestamp": "2024-01-20T10:40:00"
    }
  ]
}
```

**Beachte**: Die erste Konversation wurde NICHT übertragen (Summary zu kurz), die zweite wurde übertragen (>100 Zeichen + Research-Workflow).

---

## Vorteile

### 1. Kontext-Hygiene
- Verhindert Akkumulation irrelevanter Informationen
- Reduziert Token-Kosten durch kleineren Kontext

### 2. Präzision
- Agent fokussiert auf aktuelle, relevante Informationen
- Weniger Halluzinationen durch veraltete Daten

### 3. Flexibilität
- Benutzer entscheidet, wann Historie gelöscht wird
- Wichtige Erkenntnisse gehen nicht verloren

### 4. Transparenz
- Benutzer sieht, wie viele Erkenntnisse übertragen wurden
- Klare Bestätigung nach dem Löschen

---

## Best Practices

### Wann sollte man `clear` verwenden?

1. **Themenwechsel**: Nach Abschluss eines Projekts/Themas
2. **Fehlerhafte Informationen**: Wenn falsche Daten im Kontext gespeichert wurden
3. **Performance**: Bei zu großer Historie (>20 Einträge werden sowieso nur die letzten 20 verwendet)
4. **Neue Recherche**: Wenn eine völlig neue Fragestellung beginnt

### Wann sollte man Erkenntnisse NICHT übertragen?

Verwende `clear` ohne Übertragung (`nein` bei der Frage), wenn:
- Die gesamte Historie irrelevant war
- Fehlerhafte Informationen gespeichert wurden
- Ein kompletter Neustart gewünscht ist

---

## Integration mit bestehenden Befehlen

| Befehl | Funktion | Löscht |
|--------|----------|--------|
| `clear` / `reset` | Chat-Historie löschen | `conversation_context` |
| `forget` | Gesamtes Gedächtnis löschen | Alles (Profil, Insights, Historie) |
| `memory` | Gedächtnis anzeigen | Nichts (nur Anzeige) |

---

## Technische Details

### Geänderte Dateien

1. **`utils/memory_manager.py`**
   - Neue Methode: `clear_conversation_history(transfer_insights: bool = True)`
   - Logik für selektive Übertragung von Erkenntnissen

2. **`main.py`**
   - Neuer Befehl: `clear` / `reset` im interaktiven Modus
   - Integration der Zusammenfassungs-Logik
   - Aktualisierte Hilfe-Funktion

3. **`agents/research_agent.py`**
   - Erweiteter System-Prompt mit Relevanz-Check-Anweisungen

### Rückwärtskompatibilität

✅ Alle Änderungen sind rückwärtskompatibel:
- Bestehende Gedächtnis-Dateien funktionieren weiterhin
- Alte Befehle (`forget`, `memory`) bleiben unverändert
- Keine Breaking Changes in der API

---

## Zukünftige Erweiterungen

Mögliche weitere Verbesserungen:

1. **Automatisches Löschen**: Nach N Konversationen oder X Tagen
2. **Selektives Löschen**: Einzelne Einträge aus der Historie entfernen
3. **Komprimierung**: Automatische Zusammenfassung alter Einträge
4. **Tagging**: Kategorisierung von Konversationen für bessere Filterung
5. **Semantic Search**: Relevanz-Check basierend auf Embeddings statt Keywords
