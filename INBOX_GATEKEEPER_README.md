# 📬 Inbox Gatekeeper - Implementierungsdokumentation

## Überblick

Der **Inbox Gatekeeper** ist ein intelligenter E-Mail-Manager, der ungelesene E-Mails automatisch mit KI analysiert, Asana-Projekte vorschlägt und verschiedene Aktionen ermöglicht (an Asana senden, weiterleiten, archivieren).

## ✅ Implementierter Stand (Phase 1 MVP)

### 1. Config-Erweiterung ✅
**Datei:** `config/mapping_config.json`

Neue Sektionen hinzugefügt:
- `people_mappings`: E-Mail-Absender → Asana-Projekt Mapping
- `project_mappings`: Keywords → Asana-Projekt Mapping
- `forwarding_rules`: Kategorie → Auto-Forward Regeln
- `email_categories`: Vordefinierte E-Mail-Kategorien

### 2. OutlookGraphTool erweitert ✅
**Datei:** `tools/outlook_graph_tool.py`

4 neue Methoden implementiert:
- `get_unread_emails(max_results, folder)` - Holt ungelesene E-Mails
- `mark_as_read(email_id)` - Markiert E-Mail als gelesen
- `move_to_folder(email_id, folder_name)` - Verschiebt E-Mail (mit hierarchischer Ordnersuche)
- `forward_email(email_id, to_recipients, comment)` - Leitet E-Mail weiter

### 3. EmailManager erstellt ✅
**Datei:** `utils/email_manager.py`

Hauptklasse mit folgenden Features:
- LLM-Integration (Anthropic/OpenAI)
- `fetch_unread_emails()` - Wrapper für Outlook-Abruf
- `analyze_email_with_llm()` - KI-Analyse mit JSON-Output
  - Summary, Priority (1-5), Category, Action Items, Deadline, Sentiment
  - Fallback-Analyse bei JSON-Parsing-Fehler
- `suggest_asana_target()` - Smart Resolver mit 3 Prioritätsstufen:
  1. People Mappings (exakter Match + Wildcard)
  2. Project Mappings (Keyword-Match)
  3. Fuzzy-Match (Betreff-Ähnlichkeit, Threshold 0.6)
- `send_to_asana()` - Erstellt formatierte Tasks
- `archive_email()` - Markiert als gelesen + verschiebt
- `forward_email()` - Leitet weiter
- `get_forwarding_rule()` - Findet passende Regel

### 4. UI-Tab erstellt ✅
**Datei:** `app.py`

Neue Funktionen:
- `cached_get_asana_projects()` - Cache-Funktion (10 Min TTL)
- `render_inbox_tab()` - Haupt-Tab-Renderer
  - Action Bar (Refresh, Anzahl, Sortierung)
  - Email-Laden mit Fortschrittsanzeige
  - LLM-Analyse mit Spinner
  - Session State Caching
- `render_email_card()` - Einzelne E-Mail-Karte
  - Prioritäts-Badge mit Farb-Coding
  - Sentiment-Emoji
  - Kategorie-Anzeige
  - Expander für Details (Summary, Action Items, Preview)
  - Asana-Projekt-Vorschlag mit Konfidenz
  - 4 Action-Buttons:
    - 📤 An Asana senden (mit Archivierung)
    - ↗️ Weiterleiten (mit/ohne Regel)
    - 🗄️ Archivieren
    - ✓ Als gelesen markieren

Tab-Integration:
- Neuer "📬 Posteingang"-Tab zwischen "Mein Tag" und "Meeting Manager"
- EmailManager-Import hinzugefügt

## 🧪 Tests

### Test 1: Config-Validierung
```bash
python3 -c "import json; json.load(open('config/mapping_config.json'))"
```
**Status:** ✅ Bestanden

### Test 2: EmailManager-Basistest
**Datei:** `test_email_manager.py`
- Import-Test
- Config-Loading
- Fallback-Analyse
- Asana-Target-Suggestion

**Status:** ✅ Bestanden

### Test 3: LLM-Analyse-Test
**Datei:** `test_email_llm_analysis.py`
- Realistische Test-E-Mail
- LLM-Analyse mit JSON-Output
- Validierung aller Felder

**Ergebnis:**
- Summary: ✅ Präzise Zusammenfassung
- Priority: ✅ 5/5 (korrekt erkannt als kritisch)
- Category: ✅ "Auftrag"
- Sentiment: ✅ "dringend"
- Deadline: ✅ 2026-02-05 (extrahiert)
- Action Items: ✅ 4 konkrete Punkte

**Status:** ✅ Bestanden

### Test 4: OutlookGraphTool-Methoden
**Datei:** `test_outlook_methods.py`
- Methoden-Existenz
- Signaturen-Validierung
- Rückgabewerte-Typen

**Status:** ✅ Bestanden

## 📋 Konfiguration

### Beispiel: `config/mapping_config.json`

```json
{
  "people_mappings": {
    "mappings": [
      {
        "email_pattern": "kunde@firma-x.de",
        "name": "Kunde Firma X",
        "asana_project_gid": "1234567890",
        "priority_boost": 1
      },
      {
        "email_pattern": "*@wichtig-partner.com",
        "name": "Wichtiger Partner (Wildcard)",
        "asana_project_gid": "9876543210",
        "priority_boost": 2
      }
    ]
  },

  "project_mappings": {
    "mappings": [
      {
        "keywords": ["Projekt Alpha", "PA-"],
        "asana_project_gid": "1111111111",
        "project_name": "Projekt Alpha"
      }
    ]
  },

  "forwarding_rules": {
    "rules": [
      {
        "category": "Rechnung",
        "forward_to": "buchhaltung@firma.de",
        "auto_forward": false,
        "template": "Bitte um Bearbeitung der beigefügten Rechnung."
      }
    ]
  },

  "email_categories": [
    "Anfrage", "Auftrag", "Rechnung", "Bewerbung",
    "Newsletter", "Meeting", "Projekt-Update", "Sonstiges"
  ]
}
```

## 🚀 Verwendung

### 1. Streamlit starten
```bash
streamlit run app.py
```

### 2. Microsoft Graph API authentifizieren
- Sidebar → "Microsoft Graph API konfigurieren"
- Falls noch nicht geschehen: Client-ID und Tenant-ID in `.env` eintragen

### 3. Posteingang-Tab öffnen
- Navigiere zu "📬 Posteingang"
- Klicke "🔄 Posteingang aktualisieren"

### 4. E-Mails verarbeiten
- Wähle Asana-Projekt aus Dropdown
- Klicke "📤 An Asana senden"
- E-Mail wird automatisch archiviert

## 🎯 Features

### LLM-Analyse
- **Summary**: 1-2 Sätze Zusammenfassung
- **Priority**: 1-5 Skala
  - 5 = Kritisch (dringend + wichtig)
  - 4 = Dringend (schnelle Reaktion)
  - 3 = Normal (1-2 Tage)
  - 2 = Niedrig (Information)
  - 1 = Sehr niedrig (Newsletter)
- **Category**: Aus konfigurierbarer Liste
- **Action Items**: Extrahierte Handlungspunkte
- **Deadline**: Automatisch erkanntes Datum (YYYY-MM-DD)
- **Sentiment**: positiv/neutral/negativ/dringend

### Smart Resolver (Asana-Projekt-Vorschlag)
1. **People Mappings** (Konfidenz: 100%)
   - Exakter E-Mail-Match
   - Wildcard-Match (*@domain.com)
2. **Project Mappings** (Konfidenz: 85%)
   - Keyword-Suche in Betreff + Body
3. **Fuzzy-Match** (Konfidenz: 60-99%)
   - Betreff-Ähnlichkeit mit allen Projekten
   - Threshold: 0.6

### Task-Format in Asana
```markdown
**Von:** Max Mustermann <max@example.com>
**Empfangen:** 30.01.2026 14:30
**Kategorie:** Auftrag
**Priorität:** 5/5

**Zusammenfassung:**
[LLM-generierte Zusammenfassung]

**Email-Vorschau:**
[Erste 300 Zeichen]

**Handlungspunkte:**
- Punkt 1
- Punkt 2

[📧 Email in Outlook öffnen](link)
```

## ⚙️ Technische Details

### Abhängigkeiten
- `streamlit` - UI Framework
- `langchain-anthropic` / `langchain-openai` - LLM
- `requests` - HTTP für Graph API
- `msal` - Microsoft Auth
- `asana` - Asana SDK
- `python-dotenv` - .env

### Session State
- `email_manager` - EmailManager-Instanz
- `inbox_emails` - Gecachte analysierte E-Mails
- Automatisches Refresh nach Aktionen

### Caching
- `cached_get_asana_projects()`: 10 Min TTL
- `inbox_emails`: Bis zum manuellen Refresh

### Error Handling
- LLM JSON-Parsing-Fehler → Fallback-Analyse
- Outlook API-Fehler → User-Feedback mit Details
- Asana API-Fehler → User-Feedback mit Details

## 🔮 Phase 2 Features (Optional)

### Geplante Erweiterungen
1. **Auto-Forwarding**
   - Regel-basierte automatische Weiterleitung
   - User-Bestätigung optional

2. **Erweiterte Project Mappings**
   - Regex-Support
   - Multi-Keyword-Kombinationen

3. **Batch-Aktionen**
   - Multiple E-Mails gleichzeitig verarbeiten
   - Bulk-Archivierung

4. **Analytics**
   - E-Mail-Volumen-Dashboard
   - Kategorie-Verteilung
   - Bearbeitungszeiten

5. **Machine Learning**
   - Lernende Mappings basierend auf User-Feedback
   - Automatische Keyword-Erkennung

## 📊 Geschätzte Implementierungszeit

### Phase 1 (MVP) - ✅ ABGESCHLOSSEN
- Config erweitern: ~15 Min ✅
- OutlookGraphTool: ~1-2h ✅
- EmailManager: ~3-4h ✅
- UI-Tab: ~2-3h ✅
- **Total:** ~7-10h

### Phase 2 (Optional)
- Auto-Forwarding: ~1h
- Erweiterte Mappings: ~1h
- Batch-Aktionen: ~2h
- Analytics: ~3h
- ML-Features: ~5h
- **Total:** ~12h

## 🐛 Bekannte Limitierungen

1. **Ordnersuche**: `move_to_folder()` sucht aktuell nur in:
   - Root-Ebene
   - Unterordner von "Posteingang"
   - Hardcoded auf "Posteingang erledigt 2026"

2. **LLM-Kosten**: Vollständiger E-Mail-Body wird analysiert
   - Pro E-Mail: ~1000-2000 Tokens
   - Bei 20 E-Mails: ~20k-40k Tokens

3. **Fuzzy-Match**: Threshold 0.6 kann zu ungenau sein
   - User-Feedback sammeln und anpassen

4. **Rate Limits**: Graph API hat Rate Limits
   - Aktuell keine Retry-Logik implementiert

## 📝 Changelog

### 2026-01-30 - v1.0.0 (MVP)
- ✅ Config-Struktur definiert
- ✅ OutlookGraphTool mit 4 neuen Methoden
- ✅ EmailManager mit LLM-Integration
- ✅ UI-Tab mit Email-Karten
- ✅ Smart Resolver (3 Prioritätsstufen)
- ✅ Tests für alle Komponenten

## 🤝 Beitragen

### Testing
Für Live-Tests mit echtem Outlook-Account:
1. Authentifiziere Outlook in der App
2. Führe aus: `./venv/bin/python test_email_manager.py`
3. Prüfe Console-Output

### Debugging
Bei Problemen:
1. Prüfe `.env` (MICROSOFT_CLIENT_ID, MICROSOFT_TENANT_ID, ANTHROPIC_API_KEY)
2. Prüfe Outlook-Authentifizierung in Sidebar
3. Öffne Browser-Console (F12) für Streamlit-Fehler
4. Prüfe Terminal-Output für Backend-Fehler

## 📚 Weitere Dokumentationen

- [Implementierungsplan](implementierungsplan.md) - Detaillierter technischer Plan
- [app.py](app.py) - Hauptanwendung mit UI
- [utils/email_manager.py](utils/email_manager.py) - EmailManager-Klasse
- [tools/outlook_graph_tool.py](tools/outlook_graph_tool.py) - Outlook-Integration
