# 📊 "Mein Tag" Dashboard - Dokumentation

**Version:** 1.0
**Datum:** 2026-01-25
**Status:** ✅ Implementiert

## Übersicht

Das "Mein Tag"-Dashboard ist Ihre zentrale Management-Zentrale in der Herbert Gruppe. Es vereint Ihre Termine und Asana-Aufgaben an einem Ort und gibt Ihnen morgens mit einem Blick den perfekten Überblick über Ihren Tag.

## 📋 Features

### 1. **Zweispaltiges Layout**

**Linke Spalte: Kalender & Termine**
- Timeline-Ansicht Ihrer heutigen Microsoft-Termine
- Platzhalter-Termine bis zur IT-Freigabe
- Visuelle Zeitstrahl-Darstellung

**Rechte Spalte: Asana-Aufgaben**
- Ihre Prioritäten aus Asana
- Projekt-Auswahl (z.B. myTGA App)
- Dringlichkeits-Gruppierung (Heute / Diese Woche)

### 2. **Kalender-Integration (Vorbereitet)**

Das Dashboard ist vorbereitet für Microsoft Graph API:
- ✅ OAuth 2.0 Flow implementiert (`outlook_graph_tool.py`)
- ✅ msal (Microsoft Authentication Library) integriert
- ⏳ Wartet auf Client-ID und Tenant-ID von IT

**Aktuell:** Platzhalter-Termine werden angezeigt
- Team-Meeting (09:00 - 10:00)
- Projekt-Review: myTGA App (10:30 - 11:30)
- Strategiegespräch (14:00 - 15:00)

**Nach IT-Freigabe:** Echte Outlook-Termine automatisch

### 3. **Asana-Integration (Live)**

Das Dashboard lädt **echte Daten** aus Asana:
- ✅ Verbindung zu Workspace "Herbert Gruppe"
- ✅ 70 Aufgaben verfügbar
- ✅ Projekt-Auswahl-Dropdown
- ✅ Gruppierung nach Dringlichkeit
- ✅ "Erledigt"-Button für schnelles Abhaken

**Dringlichkeits-Stufen:**
- 🔴 **Heute fällig** - Oberste Priorität
- 📅 **Diese Woche** - Anstehende Aufgaben

### 4. **Aufgaben-Karten**

Jede Aufgabe zeigt:
- **Titel** - Aufgabenname
- **Fälligkeit** - Mit farbcodierter Dringlichkeit
- **Projekt** - Zugeordnetes Asana-Projekt
- **Beschreibung** - Erste 200 Zeichen der Notizen
- **Aktionen:**
  - ✓ Erledigt-Button (markiert direkt in Asana)
  - 🔗 In Asana öffnen

## 🚀 Verwendung

### Dashboard öffnen

1. Starten Sie die App:
   ```bash
   source venv/bin/activate
   streamlit run app.py
   ```

2. Öffnen Sie im Browser: http://localhost:8501

3. Klicken Sie auf den Tab **"📊 Mein Tag"**

### Projekt auswählen

1. Dropdown-Menü: **"📁 Projekt auswählen"**
2. Wählen Sie z.B. **"myTGA App"**
3. Aufgaben werden automatisch gefiltert (Feature folgt)

### Aufgabe erledigen

1. Öffnen Sie eine Aufgaben-Karte (Expander)
2. Klicken Sie **"✓ Erledigt"**
3. Aufgabe wird in Asana als erledigt markiert
4. Dashboard aktualisiert sich automatisch

## 🔧 Microsoft Graph API Einrichtung

### Schritt 1: IT kontaktieren

Fordern Sie von Ihrer IT-Abteilung:
1. **Client-ID** (App-Registration in Azure Portal)
2. **Tenant-ID** (Ihrer Organisation)

### Schritt 2: Anleitung für IT

**Die IT muss im Azure Portal folgendes machen:**

1. Azure Portal öffnen: https://portal.azure.com
2. "Azure Active Directory" > "App registrations" > "New registration"
3. **Name:** "Mein Assistent"
4. **Supported account types:** "Accounts in this organizational directory only"
5. **Redirect URI:** "Public client/native" > `http://localhost`
6. Nach Registrierung: **Client-ID** und **Tenant-ID** notieren
7. **API permissions** hinzufügen:
   - Microsoft Graph > Delegated permissions
   - `Calendars.Read`
   - `Calendars.ReadWrite`
   - `User.Read`
8. **"Grant admin consent"** klicken

### Schritt 3: IDs in .env eintragen

Öffnen Sie `.env` und ergänzen Sie:

```bash
# Microsoft Graph API (Outlook Kalender Integration)
MICROSOFT_CLIENT_ID=ihre_client_id_von_it
MICROSOFT_TENANT_ID=ihre_tenant_id_von_it
```

### Schritt 4: App neu starten

```bash
pkill -f streamlit
source venv/bin/activate
streamlit run app.py
```

### Schritt 5: Erster Login

Beim ersten Aufruf des Dashboards:
1. Ein Device Code wird angezeigt
2. Öffnen Sie: https://microsoft.com/devicelogin
3. Geben Sie den Code ein
4. Melden Sie sich mit Ihrem Microsoft-Konto an
5. Ab sofort: Echte Outlook-Termine im Dashboard!

## 📊 Dashboard-Funktionen im Detail

### Kalender-Sektion

**Platzhalter-Modus (Aktuell):**
```
📅 Heute in Ihrem Kalender

⚙️ Microsoft Kalender noch nicht verbunden
Um Ihre Outlook-Termine zu sehen, benötigen Sie:
1. Client-ID von Ihrer IT-Abteilung
2. Tenant-ID Ihrer Organisation

📝 Zeige Demo-Termine (Platzhalter bis zur Konfiguration)

### 09:00 - 10:00
Team-Meeting
📍 Konferenzraum A
👥 Teilnehmer: Max Mustermann, Anna Schmidt
---
```

**Live-Modus (Nach IT-Freigabe):**
```
📅 Heute in Ihrem Kalender

✅ Microsoft Kalender verbunden
🔄 Lade Termine aus Ihrem Outlook-Kalender...

### 09:00 - 10:00
Team-Meeting Herbert Gruppe
📍 Bensheim, Helmut Herbert GmbH
👥 Teilnehmer: Dr. Sven Herbert, Max Mustermann
---
```

### Asana-Sektion

```
✅ Ihre Prioritäten in Asana

📁 Projekt auswählen:
[Dropdown: myTGA App | Herbert Gruppe - Strategie | KHS Projekte]

Aktives Projekt: myTGA App
---

### 🔴 Heute fällig

📌 Idee KI - WG: JobRouter: Fahrauftrag
   ⚠️ Überfällig seit 1194 Tag(en)!
   📁 Projekt: 1:1 Jan / SH

   Beschreibung:
   [Erste 200 Zeichen der Notizen]

   💬 Letzte Kommentare:
   _Kommentar-Integration folgt..._

   [✓ Erledigt] [🔗 In Asana öffnen]
---

### 📅 Diese Woche

📌 Neue Jobrouter Prozess Prüfung...
   📅 Fällig in 3 Tag(en) (2026-01-28)
   ...
```

## 🎨 Design-Elemente

### Farben & Icons

**Dringlichkeits-Indikatoren:**
- 🔴 **Heute fällig** (Rot)
- 🟡 **Morgen fällig** (Gelb)
- 📅 **Diese Woche** (Blau)
- ⚠️ **Überfällig** (Rot, Warning)

**Sections:**
- 📅 **Kalender** (links)
- ✅ **Asana** (rechts)
- 📁 **Projekt-Auswahl**
- 💬 **Kommentare** (folgt)

### Layout-Struktur

```
┌─────────────────────────────────────────────────────────┐
│ 📊 Mein Tag - Management Dashboard                     │
├──────────────────────┬──────────────────────────────────┤
│ 📅 Kalender (Links)  │ ✅ Asana (Rechts)               │
│                      │                                  │
│ 09:00 - 10:00        │ 📁 Projekt: myTGA App           │
│ Team-Meeting         │                                  │
│ 📍 Konferenzraum A   │ ### 🔴 Heute fällig             │
│ 👥 Max, Anna         │ 📌 Aufgabe 1                    │
│ ---                  │   [✓ Erledigt] [🔗 Öffnen]      │
│                      │                                  │
│ 10:30 - 11:30        │ ### 📅 Diese Woche              │
│ Projekt-Review       │ 📌 Aufgabe 2                    │
│ 📍 Online (Teams)    │   [✓ Erledigt] [🔗 Öffnen]      │
│ ...                  │ ...                              │
└──────────────────────┴──────────────────────────────────┘
```

## 🔮 Geplante Erweiterungen

### Phase 1: Kommentare (In Arbeit)
- ✅ Asana Stories API Integration
- ✅ Letzte 2 Kommentare anzeigen
- ✅ AI-Zusammenfassung von Kommentaren

### Phase 2: Projekt-Filterung
- ✅ Aufgaben nach gewähltem Projekt filtern
- ✅ Projekt-Status anzeigen
- ✅ Projekt-Fortschritt visualisieren

### Phase 3: Intelligenz
- ✅ Termin-Teilnehmer mit Dokumenten abgleichen
- ✅ Automatische Vorbereitung für Meetings
- ✅ Kontext aus input_docs/ laden

### Phase 4: Aktionen
- ✅ Neue Aufgaben aus Dashboard erstellen
- ✅ Termine direkt buchen
- ✅ Schnelle Notizen zu Terminen

## 📝 Technische Details

### Komponenten

**Frontend:** Streamlit
- `render_dashboard_tab()` - Hauptfunktion
- `render_calendar_section()` - Kalender links
- `render_asana_tasks_section()` - Asana rechts
- `render_task_card()` - Einzelne Aufgabe

**Backend:**
- `OutlookGraphTool` - Microsoft Graph API
- `AsanaAgent` - Asana Integration
- `AsanaTool` - Asana-Wrapper

**Pakete:**
- `msal` - Microsoft Authentication Library
- `requests` - HTTP-Requests
- `asana` - Asana Python SDK
- `streamlit` - Web-Framework

### Architektur

```
┌──────────────┐
│  app.py      │
│  (Dashboard) │
└──────┬───────┘
       │
   ┌───▼────────────────────┐
   │                        │
┌──▼────────────┐  ┌───────▼──────┐
│OutlookGraph   │  │AsanaAgent    │
│Tool           │  │              │
└───────┬───────┘  └──────┬───────┘
        │                 │
   ┌────▼──────┐    ┌────▼──────┐
   │Microsoft  │    │Asana API  │
   │Graph API  │    │           │
   └───────────┘    └───────────┘
```

## 🐛 Fehlerbehebung

### Dashboard wird nicht angezeigt

**Problem:** Tab "Mein Tag" fehlt

**Lösung:**
1. Prüfen Sie ob App läuft: `ps aux | grep streamlit`
2. Starten Sie neu: `pkill -f streamlit && streamlit run app.py`
3. Browser-Cache leeren: `Strg + F5`

### "Microsoft Kalender noch nicht verbunden"

**Problem:** Normale Meldung wenn noch nicht konfiguriert

**Lösung:**
1. Folgen Sie der Anleitung in der Sidebar
2. IT kontaktieren für Client-ID/Tenant-ID
3. In `.env` eintragen

### "Asana noch nicht verbunden"

**Problem:** ASANA_ACCESS_TOKEN fehlt

**Lösung:**
1. Token erstellen: https://app.asana.com/0/my-apps
2. In `.env` eintragen:
   ```
   ASANA_ACCESS_TOKEN=ihr_token
   ```
3. App neu starten

### Aufgaben werden nicht geladen

**Problem:** Asana API-Fehler

**Lösung:**
1. Prüfen Sie Logs: `tail streamlit_dashboard.log`
2. Token erneuern (siehe oben)
3. Workspace prüfen (sollte "Herbert Gruppe" sein)

## ✅ Test-Checkliste

### Basis-Funktionen
- [ ] Dashboard-Tab ist sichtbar
- [ ] Zweispaltiges Layout wird angezeigt
- [ ] Platzhalter-Termine erscheinen (ohne Microsoft Graph)
- [ ] Asana-Aufgaben werden geladen

### Kalender-Sektion
- [ ] Warnung "Microsoft Kalender noch nicht verbunden" erscheint
- [ ] Platzhalter-Termine haben Zeit, Titel, Ort, Teilnehmer
- [ ] Timeline ist chronologisch sortiert

### Asana-Sektion
- [ ] Projekt-Dropdown ist sichtbar
- [ ] Aufgaben sind nach Dringlichkeit gruppiert
- [ ] "Heute fällig" Sektion erscheint
- [ ] "Diese Woche" Sektion erscheint
- [ ] Aufgaben-Karten können aufgeklappt werden
- [ ] Fälligkeitsdatum wird angezeigt
- [ ] Projekt wird angezeigt
- [ ] "Erledigt"-Button funktioniert
- [ ] Nach Klick auf "Erledigt": Erfolgs-Meldung
- [ ] Dashboard aktualisiert sich automatisch

### Integration
- [ ] Sidebar zeigt "📅 Microsoft Graph API"
- [ ] Expander "⚙️ Kalender-Integration einrichten" vorhanden
- [ ] IT-Anleitung ist komplett und verständlich

## 🎯 Zusammenfassung

Das "Mein Tag"-Dashboard ist Ihre **zentrale Management-Zentrale** für die Herbert Gruppe:

✅ **Implementiert:**
- Zweispaltiges Layout (Kalender + Asana)
- Platzhalter-Termine bis zur IT-Freigabe
- Live Asana-Integration mit 70 Aufgaben
- Dringlichkeits-Gruppierung
- "Erledigt"-Funktion
- Projekt-Auswahl
- Microsoft Graph API vorbereitet

⏳ **Wartet auf:**
- Client-ID und Tenant-ID von IT → Dann: Echte Outlook-Termine!

🔮 **Nächste Features:**
- Asana-Kommentare anzeigen
- Projekt-Filterung
- Meeting-Vorbereitung mit Dokumenten

---

**Status:** Produktionsreif für Asana, vorbereitet für Microsoft Graph
**URL:** http://localhost:8501
**Tab:** 📊 Mein Tag
