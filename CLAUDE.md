# Mein Assistent – Herbert Gruppe
## CLAUDE.md · Architektur, Konventionen & aktueller Stand

> Diese Datei ist der primäre Einstiegspunkt für Claude Code. Lies sie zuerst, bevor du Änderungen vornimmst.

---

## 1. Was ist diese App?

KI-gestützter interner Assistent für die Herbert Gruppe (Gebäudetechnik, ~550 MA).
Läuft unter **`mein-assistent.herbertgruppe.com`**, Login via **Authentik SSO** (OIDC).

**Kernfunktionen:**
- **Mein Tag** — Kalender (Outlook/Microsoft Graph), Asana-Aufgaben, Meeting-Vorbereitung
- **Meeting Manager** — Transkript-Upload → automatisches Protokoll (LLM) → Task-Extraktion → Asana-Export
- **Dokumente** — Datei-Upload & Suche (PDF, DOCX, XLSX)
- **Archiv** — Protokoll-Ablage nach Ordnern
- **Einstellungen** — Memory, User-Profil, Workflow-Modus
- **Admin** — User-Rollen-Verwaltung (nur für Admins)

---

## 2. Tech-Stack

| Schicht | Technologie |
|---|---|
| Web-Framework | Streamlit ≥ 1.42 (`st.login` für OIDC) |
| LLM | Anthropic Claude (via LangChain) oder OpenAI — konfigurierbar per `.env` |
| Kalender/Mail | Microsoft Graph API (Device Code Flow, MSAL) |
| Aufgaben | Asana REST API |
| PDF-Generierung | WeasyPrint + Markdown |
| Deployment | Docker + docker-compose, nginx Reverse Proxy |
| Auth | Authentik 2026.2.2 (OIDC), `st.login("authentik")` |

---

## 3. Projektstruktur

```
mein-assistent/
│
├── app.py                    # Entry Point (~234 Zeilen) — page config, CSS, auth gate, st.tabs
│
├── utils/                    # Shared Module (KEIN Streamlit-UI-Code hier)
│   ├── __init__.py           # Exportiert MemoryManager — NICHT ändern
│   ├── design.py             # Herbert Design System: HG_CSS, inject_hg_css(), hg_card(), hg_danger_zone(), hg_badge()
│   ├── auth.py               # User-Config YAML, get_user_role(), get_username()
│   ├── state.py              # initialize_session_state(), reset_chat_session(), _get_user_ctx()
│   ├── api_cache.py          # @st.cache_data Wrapper für Asana + Outlook API-Calls
│   ├── background.py         # BG-Thread für Protokoll-Generierung (_bg_protocol_jobs dict)
│   ├── orchestrator.py       # StreamlitOrchestrator — initialisiert alle Agenten
│   ├── protocol.py           # Protokoll/PDF/Agenda-Helfer (extract_tasks, convert_markdown_to_pdf, ...)
│   ├── database.py           # SQLite-Datenbankzugriff (E-Mail-Cache)
│   ├── email_manager.py      # E-Mail-Verwaltung (Outlook Graph)
│   └── memory_manager.py     # Memory/Profil-Persistenz pro User
│
├── pages/                    # Tab-Implementierungen (werden von app.py importiert)
│   ├── __init__.py
│   ├── sidebar.py            # render_sidebar() — Logo, Status, Logout, Workflow-Modus
│   ├── mein_tag.py           # render_dashboard_tab() + Kalender, Asana, Meeting-Vorbereitung
│   ├── meeting_manager.py    # render_transcripts_tab() + 4-Schritte-Workflow
│   ├── dokumente.py          # render_documents_tab()
│   ├── archiv.py             # render_archive_tab()
│   ├── einstellungen.py      # render_settings_tab()
│   ├── admin.py              # render_admin_panel()
│   ├── chat.py               # render_chat_tab() (Legacy, nicht aktiv im Tab-Menu)
│   └── inbox.py              # render_inbox_tab() (Legacy, nicht aktiv im Tab-Menu)
│
├── agents/                   # LangChain-Agenten (LLM-Logik)
│   ├── research_agent.py     # Web-Recherche (Tavily)
│   ├── task_agent.py         # Aufgaben-Ausführung
│   ├── asana_agent.py        # Asana-Operationen via LLM
│   ├── calendar_email_agent.py # Kalender/E-Mail via Microsoft Graph
│   └── communication_agent.py
│
├── tools/                    # Direkte API-Tools (kein LLM)
│   ├── asana_tool.py         # Asana REST API direkt
│   ├── outlook_graph_tool.py # Microsoft Graph direkt
│   ├── document_tool.py      # Datei-Parsing (PDF, DOCX, XLSX)
│   └── email_tool.py
│
├── assets/                   # Logos (3 Varianten — nicht ändern!)
│   ├── Logo Herbert Gruppe white ohne Hintergrund.png   # für dunkle Flächen (Sidebar)
│   ├── Herbert-Gruppe-Logo-Claim_RGB.jpg                # für helle Flächen (Login-Screen)
│   └── HG-Logo_RGB_100x900PX.png                        # Favicon / kompakt
│
├── .streamlit/
│   ├── config.toml           # Theme + server-Einstellungen (kein secrets hier!)
│   └── secrets.toml          # OIDC-Credentials — NICHT ins Git!
│
├── config/
│   └── users_config.yaml     # Rollen-Mapping email → admin|user (Docker Volume)
│
├── docker-compose.yml        # Produktiv-Deployment
├── Dockerfile                # Python 3.12-slim, COPY . .
├── requirements.txt
└── user_context.py           # UserContext-Klasse — per-User Pfade + Credentials
```

**Wichtig:** `pages/` ist kein Streamlit-MPA-Verzeichnis — es enthält normale Python-Module, die von `app.py` via `import` eingebunden werden. Die Navigation läuft über `st.tabs()`.

---

## 4. Architektur-Prinzipien

### Import-Hierarchie (keine Circular Imports)
```
app.py
  ↓ importiert
pages/*.py
  ↓ importiert
utils/*.py
  ↓ importiert
agents/, tools/, user_context.py
```
`utils/` importiert **nie** aus `pages/`. `pages/` importiert **nie** aus `app.py`.

### Session State — wichtige Keys

| Key | Typ | Bedeutung |
|---|---|---|
| `orchestrator` | `StreamlitOrchestrator` | Zentrale Instanz mit allen Agenten |
| `user_ctx` | `UserContext` | Per-User Pfade + Asana-Token |
| `username` | `str` | Interner Username (aus Email abgeleitet) |
| `email` | `str` | Authentik-Email |
| `role` | `str` | `'admin'` oder `'user'` |
| `workflow_mode` | `str` | `'auto'` \| `'research_only'` \| `'task_only'` \| ... |
| `chat_history` | `list` | Chat-Verlauf (Liste von dicts) |

### @st.cache_data Funktionen
Alle gecachten API-Calls liegen in `utils/api_cache.py`. TTL: Asana-Projekte 600s, Outlook-Events 600s, Asana-User 3600s. Cache-Invalidierung bei `.env`-Änderungen via `check_and_reset_cache_if_env_changed()`.

### Background-Threads
Protokoll-Generierung läuft in einem Daemon-Thread (`utils/background.py`). State wird in `_bg_protocol_jobs` (dict, thread-safe via Lock) und zusätzlich auf Disk (WIP-JSON) persistiert — überlebt Browser-Schließen.

---

## 5. Herbert Gruppe Design System

Spec: `C:\_claude\Umfragetool\HERBERT_DESIGN_SYSTEM.md`
Implementierung: `utils/design.py` → `HG_CSS` + Helfer-Funktionen

**Farben:**
- Brand (Marine-Blau): `#1B2D4F` (brand-600, Sidebar, Primary-Buttons)
- Akzent (Rot): `#9B1A1A` (accent-600, Danger, aktiver Nav)
- Hintergrund: `#ffffff` (Content), `#f9fafb` (Sidebar/Inputs)

**Komponenten:**
```python
from utils.design import hg_card, hg_danger_zone, hg_badge

with hg_card():
    st.subheader("Titel")
    st.write("Inhalt")

with hg_danger_zone():
    if st.button("Löschen", type="primary"): ...

hg_badge("Aktiv", color="success")  # brand | accent | success | muted
```

**CSS-Injection:** `inject_hg_css()` wird in `app.py` genau einmal aufgerufen (nach `st.set_page_config`).

---

## 6. Multi-User & Authentifizierung

- **Login:** Authentik OIDC via `st.login("authentik")` — Credentials in `.streamlit/secrets.toml`
- **Rollen:** `config/users_config.yaml` (Docker Volume, bleibt bei Rebuild erhalten)
  ```yaml
  roles:
    s.herbert@herbert.de: admin
  default_role: user
  username_map:
    s.herbert@herbert.de: sherbert  # optional: Email → interner Username
  ```
- **Per-User Verzeichnisse:** `/app/users/<username>/` — Transkripte, Dokumente, Memory, Asana-Token
- **Admin-Panel:** nur für `role == 'admin'` sichtbar (6. Tab)

---

## 7. Deployment

### Server
- **Hetzner VPS:** `46.225.132.135`
- **SSH:** `ssh root@46.225.132.135 -i C:\Users\SH_lokal\.ssh\umfragetool`
- **Pfad auf Server:** `/opt/mein-assistent/`

### Deploy-Ablauf
`app.py` ist per `COPY . .` im Docker-Image — bei Änderungen muss neu gebaut werden:

```bash
# Lokal → Server
scp -i ~/.ssh/umfragetool app.py root@46.225.132.135:/opt/mein-assistent/
scp -i ~/.ssh/umfragetool utils/design.py root@46.225.132.135:/opt/mein-assistent/utils/
# ... weitere geänderte Dateien

# Auf dem Server
ssh root@46.225.132.135
cd /opt/mein-assistent
docker compose up -d --build assistent
docker compose logs --tail=20 assistent
```

### Volumes (bleiben bei Rebuild erhalten)
| Volume | Mount | Inhalt |
|---|---|---|
| `transcripts` | `/app/transcripts` | Transkript-Dateien + Protokoll-Cache |
| `input_docs` | `/app/input_docs` | Hochgeladene Dokumente |
| `data` | `/app/data` | SQLite-Daten (E-Mail-Cache, Agendas) |
| `users_data` | `/app/users` | Per-User Verzeichnisse |
| `users_config` | `/app/config` | users_config.yaml |
| Bind-Mount | `/app/.streamlit/secrets.toml` | OIDC-Credentials (read-only) |

### Nicht ins Git
```
.env                        # API-Keys (LLM, Asana, Microsoft)
.streamlit/secrets.toml     # OIDC Client-ID + Secret
config/users_config.yaml    # User-Rollen
```

---

## 8. Lokale Entwicklung

```bash
# .env anlegen (aus .env.example)
cp .env.example .env
# API-Keys eintragen: ANTHROPIC_API_KEY, ASANA_ACCESS_TOKEN, MICROSOFT_*

# .streamlit/secrets.toml anlegen
cp .streamlit/secrets.example.toml .streamlit/secrets.toml
# OIDC-Credentials eintragen

# Starten
streamlit run app.py
```

Bei lokalem Start ohne Authentik-OIDC schlägt `st.login()` fehl — für lokale Entwicklung ggf. den Auth-Gate temporär überbrücken.

---

## 9. Bekannte Gotchas

1. **pages/-Verzeichnis:** Streamlit würde `pages/` normalerweise als MPA-Navigation interpretieren und eine Sidebar-Navigation einblenden. Das ist mit CSS unterdrückt (`[data-testid="stSidebarNav"] { display: none }`). Navigation läuft über `st.tabs()`.

2. **@st.cache_data mit Objekten:** Agenten-Instanzen werden mit `_`-Präfix übergeben (`_asana_agent`) damit Streamlit sie nicht hasht — nur für primitive Werte funktioniert Hashing.

3. **Background-Protocol-Jobs:** `_bg_protocol_jobs` in `utils/background.py` ist ein Modul-Level-Dict, geteilt zwischen UI-Thread und BG-Thread. Immer via `_bg_jobs_lock` zugreifen.

4. **Docker Volume vs. Image:** Konfigurationsdaten (`users_config.yaml`, Transkripte, User-Daten) liegen in Docker Volumes und überleben Rebuilds. Code (`app.py`, Module) ist im Image — braucht Rebuild.

5. **Authentik 2026.2.2 Reputation-Policy kaputt** (`ReevaluateMarker`-Bug): Rate-Limit für Login läuft über nginx `limit_req`, nicht über Authentik-native Policy.

6. **Streamlit `add_header`-Vererbung in CSS:** `section[data-testid="stSidebar"] * { color: ... }` überschreibt ALLES in der Sidebar inkl. Widget-Labels. Spezifischere Selektoren verwenden wo nötig.

---

## 10. Aktueller Stand (2026-04-21)

### Erledigt
- ✅ Phase 2.4: OIDC-Login via Authentik (Produktion)
- ✅ Herbert Design System vollständig implementiert (CSS, Login-Screen, Font, Farben)
- ✅ Monolith-Split: `app.py` 10.137 → 234 Zeilen, aufgeteilt in `utils/` + `pages/`
- ✅ Pentest-Remediation abgeschlossen (0 High/Medium/Low)
- ✅ DNSSEC aktiviert (Propagation läuft bis ca. 2026-04-22)

### Nächste Schritte (Backlog)
- [ ] DNSSEC-Propagation verifizieren: `dig +short DS herbertgruppe.com @1.1.1.1`
- [ ] DMARC-Reports beobachten (erste Reports nach 24-48h, evtl. `p=reject` hochziehen)
- [ ] Umfragetool Phase 2: Mehrsprachigkeit (DE/PL/SK/BG), Telegram Bot, Active Directory
- [ ] Authentifizierter App-Pentest (XSS/IDOR/CSRF) durch externen Pentester
- [ ] HSTS preload auf hstspreload.org einreichen (nur wenn alle Subdomains dauerhaft HTTPS)
