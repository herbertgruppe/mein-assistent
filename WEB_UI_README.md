# 🌐 Web-Interface für Mein Assistent

Streamlit-basiertes Web-Interface für das Multi-Agenten-System.

## 🚀 Schnellstart

### Einmalige Installation

Falls noch nicht geschehen, installieren Sie zuerst Streamlit:

```bash
# Virtual Environment aktivieren
source venv/bin/activate

# Streamlit installieren
pip install streamlit
```

Oder installieren Sie alle Dependencies neu:

```bash
pip install -r requirements.txt
```

### Starten der Web-UI

**Option 1: Mit dem Start-Skript (empfohlen)**

```bash
./start_web.sh
```

Das Skript:
- Aktiviert automatisch das Virtual Environment
- Prüft ob alle Dependencies installiert sind
- Startet die Streamlit-App
- Öffnet automatisch den Browser

**Option 2: Manuell**

```bash
# Virtual Environment aktivieren
source venv/bin/activate

# Streamlit starten
streamlit run app.py
```

Die App läuft dann auf: **http://localhost:8501**

## 📋 Features

### 1. Sidebar - Status & Einstellungen

Die Sidebar zeigt wichtige Informationen an:

- **LLM Provider:** Aktuell verwendeter Provider (Anthropic/OpenAI)
- **Gedächtnis-Status:**
  - User-Profil (Name, Beruf)
  - Anzahl gespeicherter Erkenntnisse
  - Anzahl gespeicherter Konversationen
- **Dokumente:**
  - Anzahl verfügbarer Dokumente in `input_docs/`
  - Details zu jedem Dokument (Name, Größe, Typ)
- **Workflow-Modus:**
  - Auto (empfohlen) - System wählt automatisch
  - Research → Task
  - Nur Research
  - Nur Task

### 2. Chat-Interface

- **Eingabefeld:** Am unteren Rand
- **Chat-Historie:** Zeigt alle bisherigen Konversationen
- **Agent-Antworten:** Schön formatiert mit Markdown
  - Research Agent: In ausklappbarem Bereich
  - Task Agent: In separatem ausklappbarem Bereich
  - Fehler werden klar angezeigt

### 3. Gedächtnis-Verwaltung

In der Sidebar finden Sie Buttons für:

- **🧹 Chat-Historie:** Löscht die Chat-Historie (mit optionaler Übertragung wichtiger Erkenntnisse ins permanente Gedächtnis)
- **💭 Gedächtnis:** Löscht das gesamte Gedächtnis (Sicherheitsabfrage: zweimal klicken)
- **📋 Gedächtnis anzeigen:** Zeigt den kompletten Gedächtnis-Export an

## 🎯 Verwendung

### Beispiel-Anfragen

**Research-Anfragen:**
```
Erkläre mir Quantencomputing
Was ist Machine Learning?
Vergleiche Python und JavaScript
Finde Informationen über Blockchain
```

**Task-Anfragen:**
```
Schreibe einen Blogpost über KI
Erstelle eine Produktbeschreibung für ein Smart Home System
Generiere Python-Code für die Fibonacci-Folge
Verfasse eine professionelle E-Mail
```

**Kombinierte Anfragen:**
```
Recherchiere React und schreibe ein Tutorial
Finde Infos über gesunde Ernährung und erstelle einen Meal Plan
Erkläre Photovoltaik und erstelle einen Vergleich verschiedener Systeme
```

## 🔧 Konfiguration

### LLM Provider wechseln

Bearbeiten Sie die `.env` Datei:

```bash
# Für Anthropic (Claude)
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Für OpenAI (GPT)
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

Nach der Änderung die App neu starten.

### Dokumente hinzufügen

Legen Sie Dokumente in den `input_docs/` Ordner:

- **Unterstützte Formate:** PDF, DOCX, TXT, CSV, XLSX
- **Automatische Erkennung:** Nach dem Hochladen automatisch verfügbar
- **Status in Sidebar:** Zeigt Anzahl und Details der Dokumente

## 💡 Tipps & Tricks

### Workflow-Modi optimal nutzen

- **Auto-Modus:** Lassen Sie das System entscheiden - funktioniert in 95% der Fälle optimal
- **Research Only:** Nutzen Sie dies für reine Informationsabfragen
- **Task Only:** Nutzen Sie dies wenn Sie bereits alle Informationen haben
- **Research → Task:** Explizit beide Agenten nutzen für komplexe Aufgaben

### Gedächtnis effizient nutzen

Das System merkt sich automatisch:
- Ihr User-Profil (Name, Beruf, etc.)
- Wichtige Erkenntnisse aus Recherchen
- Kontext der letzten Konversationen

**Best Practice:**
1. Beim ersten Start Profil-Informationen eingeben (über CLI oder direkt in `user_profile.json`)
2. Regelmäßig Chat-Historie aufräumen (Button "🧹 Chat-Historie")
3. Wichtige Erkenntnisse werden dabei automatisch ins permanente Gedächtnis übertragen

### Performance-Optimierung

Für schnellere Antworten:
- Verwenden Sie spezifische Workflows statt Auto-Modus
- Halten Sie die Chat-Historie überschaubar (regelmäßig löschen)
- Bei langen Dokumenten: Stellen Sie spezifische Fragen

## 🐛 Troubleshooting

### Port bereits in Verwendung

Falls Port 8501 bereits belegt ist:

```bash
streamlit run app.py --server.port 8502
```

### Browser öffnet nicht automatisch

Öffnen Sie manuell: http://localhost:8501

### "ModuleNotFoundError: No module named 'streamlit'"

```bash
source venv/bin/activate
pip install streamlit
```

### API-Key Fehler

Prüfen Sie Ihre `.env` Datei:
- Existiert die Datei?
- Sind die API-Keys korrekt?
- Ist der richtige LLM_PROVIDER gesetzt?

## 📊 Vergleich: CLI vs. Web-UI

| Feature | CLI (main.py) | Web-UI (app.py) |
|---------|---------------|-----------------|
| Interface | Terminal | Browser |
| Chat-Historie | Im Gedächtnis | Session-basiert + Gedächtnis |
| Visualisierung | Text | Formatiertes Markdown |
| Status-Anzeige | Bei Start | Live in Sidebar |
| Bedienung | Befehle tippen | Klicken & Tippen |
| Gedächtnis-Verwaltung | Befehle | Buttons |
| Workflow-Wahl | Bei jeder Anfrage | In Einstellungen |

**Empfehlung:**
- **CLI:** Für schnelle Abfragen und Skripte
- **Web-UI:** Für längere Sessions und bessere Übersicht

## 🔐 Sicherheit

- **API-Keys:** Werden aus `.env` geladen, nie im Code gespeichert
- **Gedächtnis:** Lokal in `user_profile.json` gespeichert
- **Chat-Historie:** Session-basiert, wird nicht persistent gespeichert (nur im Gedächtnis via MemoryManager)

## 📝 Weiterentwicklung

Mögliche Erweiterungen:
- [ ] Exportieren von Chat-Verläufen als Markdown/PDF
- [ ] Direktes Hochladen von Dokumenten über die UI
- [ ] Visualisierung der Agent-Workflows
- [ ] Multi-User Support mit separaten Profilen
- [ ] Integration von Spracherkennung
- [ ] Dark Mode Toggle

## 🆘 Support

Bei Problemen:
1. Prüfen Sie die Logs in der Konsole wo Streamlit läuft
2. Prüfen Sie die `.env` Konfiguration
3. Stellen Sie sicher dass das Virtual Environment aktiviert ist
4. Prüfen Sie ob alle Dependencies installiert sind: `pip list`

---

**Viel Erfolg mit Ihrem Web-Interface!** 🚀
