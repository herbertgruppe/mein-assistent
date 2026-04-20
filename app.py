"""
Streamlit Web-Interface für das Multi-Agenten-System
"""

import os
import re
import shutil
import subprocess
import signal
import time
import threading
import yaml
import streamlit as st
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Any
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

# WICHTIG: load_dotenv() MUSS vor dem Import der Agenten aufgerufen werden!
load_dotenv()

from agents import ResearchAgent, TaskAgent, CommunicationAgent, AsanaAgent, CalendarEmailAgent
from utils import MemoryManager
from tools import DocumentTool, AsanaTool, OutlookGraphTool
from user_context import UserContext


# ============================================================================
# USERS CONFIG - Laden/Speichern der Benutzerkonfiguration
# ============================================================================
USERS_CONFIG_PATH = Path("config/users_config.yaml")


@st.cache_data(ttl=60, show_spinner=False)
def _load_users_config() -> dict:
    """Lädt Rollen-Mapping aus YAML.

    Seit Phase 2.4 (SSO) enthält die Datei nur noch Rollen + optionales
    Username-Mapping — Passwörter/Sessions liegen vollständig bei Authentik.

    Erwartetes Schema:
        roles:
          email@domain: admin|user
        default_role: user
        username_map:
          email@domain: interner_username   # optional

    Cache: 60s TTL. `_save_users_config()` ruft `.clear()` auf, damit
    Rollen-Änderungen sofort sichtbar sind.
    """
    if USERS_CONFIG_PATH.exists():
        with open(USERS_CONFIG_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    # Fallback: Datei aus dem App-Verzeichnis (erster Start)
    bundled = Path("users_config.yaml")
    if bundled.exists():
        USERS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled, USERS_CONFIG_PATH)
        with open(USERS_CONFIG_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    return {}


def _save_users_config(config: dict):
    """Speichert die User-Konfiguration und invalidiert den Load-Cache."""
    USERS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(USERS_CONFIG_PATH, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
    # Cache invalidieren, damit Änderungen sofort sichtbar sind
    _load_users_config.clear()


def get_user_role(email: str) -> str:
    """Liefert die App-Rolle (admin|user) für eine Authentik-Email."""
    cfg = _load_users_config()
    return cfg.get('roles', {}).get((email or '').lower(), cfg.get('default_role', 'user'))


def get_username(email: str) -> str:
    """Mapped Authentik-Email auf den internen Username (UserContext-Schlüssel).

    Priorität: explizite Zuordnung in `username_map:` der users_config.yaml,
    sonst Fallback auf "alles vor @, Punkte raus, lowercase" — so bleibt
    z.B. s.herbert@herbert.de → "sherbert" und die bestehenden
    per-User-Verzeichnisse (/app/users/sherbert/...) bleiben zugreifbar.
    """
    email = (email or '').lower()
    cfg = _load_users_config()
    mapped = cfg.get('username_map', {}).get(email)
    if mapped:
        return mapped
    local = email.split('@')[0] if '@' in email else email
    return local.replace('.', '').replace('-', '').replace('+', '') or 'anon'


# ============================================================================
# HELPER: User-Kontext aus Session State
# ============================================================================

def _get_user_ctx():
    """Holt den UserContext aus dem Session-State. Fallback auf Legacy-Pfade."""
    ctx = st.session_state.get('user_ctx')
    if ctx:
        return ctx
    # Fallback für Übergangszeitraum (kein Login aktiv)
    return UserContext("_default")


# ============================================================================
# BACKGROUND PROTOCOL GENERATION (Thread-basiert)
# ============================================================================
# Modul-Level: wird von Background-Threads und Streamlit-Thread geteilt
_bg_protocol_jobs: Dict[str, Any] = {}   # {item_id: {status, protocol, chunks, filename, error}}
_bg_jobs_lock = threading.Lock()


def _run_protocol_generation_bg(item_id: str, file_path_str: str, meeting_title: str, llm,
                                 attendees=None, meeting_date=None, agenda_text=None,
                                 protocol_cache_dir: str = None,
                                 wip_dir_str: str = None):
    """Läuft in einem Background-Thread. Erzeugt das Protokoll ohne den UI-Thread zu blockieren.

    Persistenz-Strategie (wichtig! — darf Browser-Schließen überleben):
    - Disk-Cache unter STABILEM Key `item_id` (nicht file_path.stem, der sich beim Rename ändert)
    - Zusätzlich: Protokoll direkt in die WIP-JSON schreiben, falls ein wip_dir_str übergeben wurde.
      Damit sieht Schritt 2 das Protokoll auch, wenn der UI-Thread nicht mehr läuft (geschlossener Browser).
    """
    try:
        file_path = Path(file_path_str)

        if file_path.suffix.lower() == '.pdf':
            from langchain_community.document_loaders import PyPDFLoader
            loader = PyPDFLoader(str(file_path))
            pages = loader.load()
            transcript_text = "\n\n".join([p.page_content for p in pages])
        else:
            transcript_text = file_path.read_text(encoding='utf-8')

        protocol_parts = []
        for chunk in extract_protocol_from_transcript_streaming(
            transcript_text, meeting_title, llm,
            attendees=attendees, meeting_date=meeting_date, agenda_text=agenda_text
        ):
            protocol_parts.append(chunk)
            with _bg_jobs_lock:
                _bg_protocol_jobs[item_id]['chunks'] = len(protocol_parts)

        protocol = ''.join(protocol_parts)

        # Cache auf Disk schreiben — STABILER Key (item_id), unabhängig von Rename
        cache_dir = Path(protocol_cache_dir) if protocol_cache_dir else Path("transcripts/protocol_cache")
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"{item_id}_protocol.md").write_text(protocol, encoding='utf-8')
        # Zusätzlich unter altem Stem-Key schreiben (Rückwärtskompat, falls UI sie so erwartet)
        try:
            (cache_dir / f"{file_path.stem}_protocol.md").write_text(protocol, encoding='utf-8')
        except Exception:
            pass

        # WIP-JSON direkt updaten — überlebt geschlossenen Browser
        if wip_dir_str:
            try:
                import json as _json
                wip_file = Path(wip_dir_str) / f"item_{item_id}.json"
                if wip_file.exists():
                    with open(wip_file, 'r', encoding='utf-8') as f:
                        wip_item = _json.load(f)
                    if not wip_item.get('protocol'):
                        wip_item['protocol'] = protocol
                        wip_item['status'] = 'processing'
                        with open(wip_file, 'w', encoding='utf-8') as f:
                            _json.dump(wip_item, f, indent=2, ensure_ascii=False, default=str)
            except Exception as _e:
                print(f"[BG-Protocol] WIP-Update fehlgeschlagen: {_e}")

        with _bg_jobs_lock:
            _bg_protocol_jobs[item_id]['status'] = 'done'
            _bg_protocol_jobs[item_id]['protocol'] = protocol

    except Exception as e:
        with _bg_jobs_lock:
            _bg_protocol_jobs[item_id]['status'] = 'error'
            _bg_protocol_jobs[item_id]['error'] = str(e)


def start_bg_protocol_generation(item_id: str, file_path_str: str, meeting_title: str, llm, filename: str,
                                   attendees=None, meeting_date=None, agenda_text=None,
                                   protocol_cache_dir: str = None,
                                   wip_dir_str: str = None):
    """Startet die Protokoll-Erstellung in einem Background-Thread."""
    with _bg_jobs_lock:
        _bg_protocol_jobs[item_id] = {
            'status': 'running',
            'protocol': '',
            'chunks': 0,
            'filename': filename,
            'error': ''
        }
    t = threading.Thread(
        target=_run_protocol_generation_bg,
        args=(item_id, file_path_str, meeting_title, llm),
        kwargs={
            'attendees': attendees,
            'meeting_date': meeting_date,
            'agenda_text': agenda_text,
            'protocol_cache_dir': protocol_cache_dir,
            'wip_dir_str': wip_dir_str,
        },
        daemon=True
    )
    t.start()


# ============================================================================
# CACHE MANAGEMENT & AUTO-RESET
# ============================================================================

def check_and_reset_cache_if_env_changed():
    """
    Prüft ob .env-Konfiguration geändert wurde und löscht Cache automatisch

    Speichert Hash der wichtigen .env-Werte in session_state.
    Bei Änderung wird der Cache geleert.
    """
    import hashlib

    # Hole relevante .env-Werte
    client_id = os.getenv("MICROSOFT_CLIENT_ID", "")
    tenant_id = os.getenv("MICROSOFT_TENANT_ID", "")
    asana_token = os.getenv("ASANA_ACCESS_TOKEN", "")

    # Erstelle Hash aus den Werten
    config_string = f"{client_id}|{tenant_id}|{asana_token}"
    current_hash = hashlib.md5(config_string.encode()).hexdigest()

    # Prüfe ob Hash sich geändert hat
    if "env_config_hash" not in st.session_state:
        # Erster Aufruf - speichere Hash
        st.session_state.env_config_hash = current_hash
        print("[Cache] Initiale .env-Konfiguration gespeichert")
    elif st.session_state.env_config_hash != current_hash:
        # Konfiguration hat sich geändert - lösche Cache
        print("[Cache] ⚠️ .env-Konfiguration hat sich geändert!")
        print(f"[Cache]   Alter Hash: {st.session_state.env_config_hash[:8]}...")
        print(f"[Cache]   Neuer Hash: {current_hash[:8]}...")
        print("[Cache] 🔄 Lösche Cache...")

        # Lösche alle gecachten Daten
        st.cache_data.clear()

        # Update Hash
        st.session_state.env_config_hash = current_hash

        print("[Cache] ✓ Cache erfolgreich geleert")

        # Zeige Hinweis in UI
        st.toast("🔄 Konfiguration geändert - Cache wurde geleert", icon="🔄")


# ============================================================================
# CACHED API FUNCTIONS (Performance-Optimierung)
# ============================================================================

@st.cache_data(ttl=600, show_spinner=False)
def cached_get_asana_projects(_asana_agent):
    """Cached: Lade Asana-Projekte (10 Min Cache)

    Note: _asana_agent mit _ prefix um Streamlit mitzuteilen,
    dass dieses Objekt nicht gehasht werden soll
    """
    try:
        return _asana_agent.list_projects()
    except Exception as e:
        print(f"[Cache] Fehler beim Laden der Projekte: {e}")
        return []

@st.cache_data(ttl=600, show_spinner=False)
def cached_get_asana_tasks(_asana_agent, project_gid=None, days=7):
    """Cached: Lade Asana-Aufgaben (10 Min Cache)

    Note: _asana_agent mit _ prefix um Streamlit mitzuteilen,
    dass dieses Objekt nicht gehasht werden soll
    """
    try:
        if project_gid:
            return _asana_agent.get_project_tasks(project_gid, limit=50)
        else:
            return _asana_agent.get_upcoming_tasks(days=days, limit=50)
    except Exception as e:
        print(f"[Cache] Fehler beim Laden der Aufgaben: {e}")
        return []

@st.cache_data(ttl=600, show_spinner=False)
def cached_get_outlook_events(_outlook_tool):
    """Cached: Lade Outlook-Termine (10 Min Cache)

    Note: _outlook_tool mit _ prefix um Streamlit mitzuteilen,
    dass dieses Objekt nicht gehasht werden soll
    """
    try:
        if not _outlook_tool.is_authenticated():
            return None
        return _outlook_tool.get_todays_events()
    except Exception as e:
        print(f"[Cache] Fehler beim Laden der Termine: {e}")
        return []

@st.cache_data(ttl=3600, show_spinner=False)
def cached_get_asana_user(_asana_agent):
    """Cached: Hole aktuellen Asana-User (1 Std Cache)"""
    try:
        import asana
        client = asana.Client.access_token(os.getenv("ASANA_ACCESS_TOKEN"))
        result = client.users.get_user("me")
        return result
    except Exception as e:
        print(f"[Cache] Fehler beim Laden des Users: {e}")
        return None


@st.cache_data(ttl=600, show_spinner=False)
def cached_find_asana_user(_agent_id: int, name: str, workspace_gid: Optional[str] = None):
    """Cached: Sucht einen Asana-Nutzer nach Namen im Workspace (10 Min Cache).

    Performance: Spart den API-Call get_users_for_workspace bei wiederholten
    Task-Erstellungen mit gleichem Assignee. Der Agent selbst wird nicht
    gehasht (per Streamlit-Konvention `_`-Präfix); stattdessen dient seine
    id() als Cache-Key, damit pro Agent-Instanz (= pro User) ein Eintrag.

    Args:
        _agent_id: id() des AsanaAgent — wird als Cache-Differentiator genutzt,
                   aber nicht an den Agent durchgereicht.
        name: Name des Nutzers (Vorname, Nachname oder Email)
        workspace_gid: Optionale Workspace GID

    Returns:
        Dict mit user info {gid, name, email} oder None wenn nicht gefunden.
    """
    agent = st.session_state.orchestrator.asana_agent
    return agent.find_user_by_name(name, workspace_gid=workspace_gid)


# ============================================================================
# PAGE CONFIG — Herbert Gruppe Corporate Design
# ============================================================================
_PAGE_ICON_PATH = Path(__file__).parent / "assets" / "HG-Logo_RGB_100x900PX.png"
st.set_page_config(
    page_title="Mein Assistent – Herbert Gruppe",
    page_icon=str(_PAGE_ICON_PATH) if _PAGE_ICON_PATH.exists() else "🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============================================================================
# HERBERT GRUPPE DESIGN SYSTEM — globales CSS
# Siehe HERBERT_DESIGN_SYSTEM.md (im Umfragetool-Repo) fuer die Spec.
# ============================================================================
st.markdown("""
<style>
/* === Farb-Tokens === */
:root {
  --brand-50:  #f0f3f8;
  --brand-100: #d9e0ec;
  --brand-500: #4064a0;
  --brand-600: #1B2D4F;   /* Primaer: Marine-Blau */
  --brand-700: #162540;
  --brand-800: #111d32;
  --accent-50:  #fdf2f2;
  --accent-400: #c94444;
  --accent-500: #b52020;
  --accent-600: #9B1A1A;  /* Akzent: Rot */
  --accent-700: #7d1515;
  --gray-50:  #f9fafb;
  --gray-100: #f3f4f6;
  --gray-200: #e5e7eb;
  --gray-300: #d1d5db;
  --gray-500: #6b7280;
  --gray-700: #374151;
  --gray-900: #111827;
}

/* === Sidebar: dunkel, wie Umfragetool === */
section[data-testid="stSidebar"] {
  background-color: var(--brand-600) !important;
  border-right: none;
}
section[data-testid="stSidebar"] * {
  color: rgba(255,255,255,0.85) !important;
}
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3,
section[data-testid="stSidebar"] h4 {
  color: #ffffff !important;
  font-weight: 600;
}
section[data-testid="stSidebar"] .hg-tagline {
  color: rgba(255,255,255,0.4) !important;
  font-size: 0.6rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  margin-top: 0.25rem;
}
section[data-testid="stSidebar"] hr {
  border-color: rgba(255,255,255,0.1) !important;
  margin: 1rem 0;
}
/* Sidebar-Links + Buttons: neutral mit Hover-Highlight */
section[data-testid="stSidebar"] .stButton > button {
  background-color: transparent !important;
  color: rgba(255,255,255,0.8) !important;
  border: 1px solid rgba(255,255,255,0.1) !important;
  text-align: left !important;
  padding: 0.5rem 0.75rem !important;
  border-radius: 0.5rem !important;
  font-weight: 500 !important;
  transition: background 0.15s, color 0.15s;
}
section[data-testid="stSidebar"] .stButton > button:hover {
  background-color: rgba(255,255,255,0.08) !important;
  color: #ffffff !important;
  border-color: rgba(255,255,255,0.2) !important;
}
/* Primary-Button in Sidebar: akzent-rot (z.B. Abmelden/Aktion) */
section[data-testid="stSidebar"] .stButton > button[kind="primary"] {
  background-color: var(--accent-600) !important;
  color: #ffffff !important;
  border-color: var(--accent-600) !important;
}
section[data-testid="stSidebar"] .stButton > button[kind="primary"]:hover {
  background-color: var(--accent-700) !important;
}

/* === Haupt-Content === */
.main .block-container {
  max-width: 1400px;
  padding-top: 2rem;
  padding-bottom: 3rem;
}
h1, h2, h3, h4 {
  color: var(--gray-900);
  font-weight: 600;
  letter-spacing: -0.01em;
}
h1 { font-size: 1.875rem; margin-bottom: 0.25rem; }
h2 { font-size: 1.5rem;   margin-top: 1.5rem; }
h3 { font-size: 1.125rem; }

/* === Cards (hg-card + Rueckwaertskompat alte user-/assistant-message) === */
.hg-card, .user-message, .assistant-message {
  background: #ffffff;
  border: 1px solid var(--gray-200);
  border-radius: 0.75rem;
  box-shadow: 0 1px 2px rgba(0,0,0,0.04);
  padding: 1.25rem 1.5rem;
  margin-bottom: 0.75rem;
}
.agent-header {
  font-weight: 600;
  color: var(--brand-600);
  margin-top: 0.5rem;
}
.sidebar-metric {
  padding: 0.75rem;
  background-color: rgba(255,255,255,0.06) !important;
  border-radius: 0.5rem;
  margin: 0.375rem 0;
}

/* === Buttons im Haupt-Content === */
.stButton > button[kind="primary"] {
  background-color: var(--brand-600) !important;
  color: #ffffff !important;
  border: 1px solid var(--brand-600) !important;
  border-radius: 0.5rem !important;
  padding: 0.5rem 1rem !important;
  font-weight: 500 !important;
  transition: background 0.15s, border-color 0.15s;
}
.stButton > button[kind="primary"]:hover {
  background-color: var(--brand-700) !important;
  border-color: var(--brand-700) !important;
}
.stButton > button[kind="secondary"] {
  background-color: #ffffff !important;
  color: var(--gray-700) !important;
  border: 1px solid var(--gray-300) !important;
  border-radius: 0.5rem !important;
  font-weight: 500 !important;
}
.stButton > button[kind="secondary"]:hover {
  background-color: var(--gray-50) !important;
  border-color: var(--gray-500) !important;
}
/* Danger-Zone: Primary-Button wird rot */
.hg-danger-zone .stButton > button[kind="primary"] {
  background-color: var(--accent-600) !important;
  border-color: var(--accent-600) !important;
}
.hg-danger-zone .stButton > button[kind="primary"]:hover {
  background-color: var(--accent-700) !important;
  border-color: var(--accent-700) !important;
}

/* === Inputs === */
.stTextInput > div > div > input,
.stTextArea textarea,
.stNumberInput input,
.stDateInput input,
.stSelectbox > div > div {
  border-radius: 0.5rem !important;
  border: 1px solid var(--gray-300) !important;
  font-size: 0.875rem !important;
}
.stTextInput > div > div > input:focus,
.stTextArea textarea:focus {
  border-color: var(--brand-500) !important;
  box-shadow: 0 0 0 1px var(--brand-500) !important;
}

/* === Tabs === */
.stTabs [data-baseweb="tab-list"] {
  gap: 0.5rem;
  border-bottom: 1px solid var(--gray-200);
}
.stTabs [data-baseweb="tab"] {
  padding: 0.5rem 1rem;
  color: var(--gray-500);
  font-weight: 500;
}
.stTabs [aria-selected="true"] {
  color: var(--brand-600) !important;
  border-bottom: 2px solid var(--brand-600) !important;
}

/* === Expander === */
.streamlit-expanderHeader, div[data-testid="stExpander"] summary {
  font-weight: 500;
  color: var(--gray-700);
}

/* === Notifications / Info-Boxes === */
div[data-testid="stNotification"] {
  border-radius: 0.5rem;
}

/* === Chat-Messages === */
div[data-testid="stChatMessage"] {
  border-radius: 0.75rem;
  background: #ffffff;
  border: 1px solid var(--gray-200);
  box-shadow: 0 1px 2px rgba(0,0,0,0.04);
}

/* === Streamlit-Branding dezent ausblenden === */
#MainMenu { visibility: hidden; }
footer { visibility: hidden; }
header[data-testid="stHeader"] { background: transparent; }
</style>
""", unsafe_allow_html=True)


# ============================================================================
# HERBERT DESIGN HELPERS
# ============================================================================
from contextlib import contextmanager as _hg_contextmanager

@_hg_contextmanager
def hg_card():
    """Context Manager fuer eine Herbert-Card (weisser Hintergrund, abgerundet, subtiler Schatten).

    Verwendung:
        with hg_card():
            st.subheader("Letzte Transkripte")
            st.write("...")
    """
    st.markdown('<div class="hg-card">', unsafe_allow_html=True)
    try:
        yield
    finally:
        st.markdown('</div>', unsafe_allow_html=True)


@_hg_contextmanager
def hg_danger_zone():
    """Context Manager fuer rote Danger-Buttons. Alle Primary-Buttons
    innerhalb werden akzent-rot dargestellt statt brand-blau.
    """
    st.markdown('<div class="hg-danger-zone">', unsafe_allow_html=True)
    try:
        yield
    finally:
        st.markdown('</div>', unsafe_allow_html=True)


def hg_badge(label: str, color: str = "brand"):
    """Rendert ein kleines Pill-Badge.

    color: 'brand' | 'accent' | 'success' | 'muted'
    """
    palette = {
        "brand":   ("#d9e0ec", "#1B2D4F"),
        "accent":  ("#fce4e4", "#9B1A1A"),
        "success": ("#d1fae5", "#065f46"),
        "muted":   ("#f3f4f6", "#374151"),
    }
    bg, fg = palette.get(color, palette["muted"])
    st.markdown(
        f'<span style="display:inline-block; padding:0.125rem 0.625rem; '
        f'border-radius:9999px; background:{bg}; color:{fg}; '
        f'font-size:0.75rem; font-weight:500;">{label}</span>',
        unsafe_allow_html=True,
    )


class StreamlitOrchestrator:
    """Orchestrator für Streamlit Web-Interface"""

    def __init__(self, user_ctx=None):
        """Initialisiere Orchestrator

        Args:
            user_ctx: Optional UserContext für Multi-User-Support.
                      Falls None, werden Legacy-Pfade verwendet.
        """
        self.user_ctx = user_ctx

        # LLM Provider aus Umgebungsvariablen
        self.llm_provider = os.getenv("LLM_PROVIDER", "anthropic")

        # Memory Manager initialisieren
        self.memory = MemoryManager()

        # Document Tool initialisieren
        self.document_tool = DocumentTool()

        # Asana Tool initialisieren
        self.asana_tool = AsanaTool()

        # Asana Agent zuerst initialisieren - mit per-User Token falls vorhanden
        asana_token = user_ctx.get_asana_token() if user_ctx else os.getenv("ASANA_ACCESS_TOKEN", "")
        self.asana_agent = AsanaAgent(api_key=asana_token) if asana_token else AsanaAgent()

        # Outlook Graph Tool initialisieren - mit per-User Token-Datei falls vorhanden
        from tools.outlook_graph_tool import OutlookGraphTool
        outlook_token_file = str(user_ctx.outlook_token_file) if user_ctx else None
        self.outlook_tool = OutlookGraphTool(token_file=outlook_token_file)

        # Email Tool initialisieren
        from tools.email_tool import EmailTool
        self.email_tool = EmailTool()

        # Agenten initialisieren (TaskAgent bekommt AsanaAgent)
        self.research_agent = ResearchAgent(llm_provider=self.llm_provider)
        self.task_agent = TaskAgent(llm_provider=self.llm_provider, asana_agent=self.asana_agent)
        self.communication_agent = CommunicationAgent(llm_provider=self.llm_provider)
        self.calendar_email_agent = CalendarEmailAgent(llm_provider=self.llm_provider, outlook_tool=self.outlook_tool)

        # Keywords für automatische Agent-Auswahl
        self.research_keywords = [
            "recherchiere", "suche", "finde", "informationen", "erkläre",
            "was ist", "wie funktioniert", "analyse", "vergleiche"
        ]

        self.task_keywords = [
            "schreibe", "erstelle", "generiere", "mache", "entwickle",
            "implementiere", "verfasse", "produziere", "baue"
        ]

        self.asana_keywords = [
            "aufgaben", "to-do", "todo", "was steht an", "termine", "deadlines",
            "aufgabe erstellen", "asana", "fällig", "erledigen"
        ]

        self.calendar_email_keywords = [
            "kalender", "termin", "meeting", "besprechung", "event", "events",
            "e-mail", "email", "mail", "nachricht", "entwurf", "draft",
            "sende email", "schicke email", "email suchen", "termine heute",
            "termine morgen", "kalendereinträge", "outlook"
        ]

    def detect_agent_type(self, user_input: str) -> str:
        """
        Erkennt automatisch welcher Agent basierend auf der Eingabe verwendet werden soll

        Returns:
            "research_only", "task_only", "research_then_task", "asana", or "calendar_email"
        """
        input_lower = user_input.lower()

        has_calendar_email_intent = any(keyword in input_lower for keyword in self.calendar_email_keywords)
        has_asana_intent = any(keyword in input_lower for keyword in self.asana_keywords)
        has_research_intent = any(keyword in input_lower for keyword in self.research_keywords)
        has_task_intent = any(keyword in input_lower for keyword in self.task_keywords)

        # Calendar/Email hat höchste Priorität bei direkten Anfragen
        if has_calendar_email_intent:
            return "calendar_email"
        # Asana hat Priorität bei direkten Anfragen
        elif has_asana_intent:
            return "asana"
        elif has_research_intent and has_task_intent:
            return "research_then_task"
        elif has_research_intent:
            return "research_only"
        elif has_task_intent:
            return "task_only"
        else:
            # Default: Beide nutzen für umfassende Antwort
            return "research_then_task"

    def process_request(self, user_input: str, workflow: str = "auto"):
        """Verarbeitet Anfrage und gibt strukturierte Ergebnisse zurück"""
        import traceback

        # Auto-detect workflow wenn gewünscht
        if workflow == "auto":
            workflow = self.detect_agent_type(user_input)

        results = {
            "input": user_input,
            "workflow": workflow,
            "agents_used": [],
            "timestamp": datetime.now().isoformat()
        }

        # Hole relevanten Kontext aus dem Gedächtnis
        memory_context = self.memory.get_relevant_context(user_input)
        user_context_str = self.memory.format_context_for_agent()

        try:
            if workflow == "research_then_task":
                # Schritt 1: Recherche
                with st.spinner("🔍 Research Agent arbeitet..."):
                    try:
                        research_result = self.research_agent.process(
                            user_input,
                            context={"memory": memory_context, "user_context": user_context_str}
                        )
                        results["research"] = research_result
                        results["agents_used"].append("ResearchAgent")

                        # Debug-Logging
                        print(f"\n[DEBUG] Research Agent Status: {research_result.get('status')}")
                        print(f"[DEBUG] Research Agent Keys: {research_result.keys()}")

                    except Exception as e:
                        print(f"\n❌ [ERROR] Research Agent Fehler: {e}")
                        print(f"[ERROR] Traceback:\n{traceback.format_exc()}")
                        results["research"] = {
                            "status": "error",
                            "error": str(e),
                            "findings": f"Fehler beim Research Agent: {e}"
                        }

                # Speichere wichtige Erkenntnisse
                if results.get("research", {}).get("status") == "success":
                    insight = results["research"].get("findings", "")[:500]
                    self.memory.add_research_insight(user_input, insight)

                # Schritt 2: Aufgabe mit Task Agent
                try:
                    # Erstelle stabilen Task-Kontext aus Session State
                    task_context = {
                        "memory": memory_context,
                        "user_context": user_context_str
                    }

                    # Füge Research-Ergebnisse hinzu falls vorhanden
                    if "research" in results and results["research"].get("status") == "success":
                        research_findings = results["research"].get("findings", "")

                        # Prüfe ob lokale Dokumente verwendet wurden
                        if any(doc["name"] in research_findings for doc in self.document_tool.scan_documents()):
                            task_context["findings"] = f"""=== LOKALER DOKUMENTEN-INHALT ===
Diese Informationen stammen aus den lokalen Dokumenten des Nutzers im input_docs/ Ordner.
Du musst NICHT auf Dateien zugreifen - der Inhalt ist bereits hier verfügbar.

{research_findings}

=== ENDE LOKALER DOKUMENTEN-INHALT ==="""
                        else:
                            task_context["findings"] = research_findings

                    # Debug-Logging
                    print(f"\n[DEBUG] Task Agent wird aufgerufen...")
                    print(f"[DEBUG] Task Context Keys: {task_context.keys()}")
                    print(f"[DEBUG] LLM Provider: {self.llm_provider}")
                    print(f"[DEBUG] Task Agent LLM: {self.task_agent.llm}")

                    with st.spinner("⚙️ Task Agent arbeitet..."):
                        task_result = self.task_agent.process(user_input, context=task_context)
                        results["task"] = task_result
                        results["agents_used"].append("TaskAgent")

                        # Debug-Logging
                        print(f"\n[DEBUG] Task Agent Status: {task_result.get('status')}")
                        print(f"[DEBUG] Task Agent Keys: {task_result.keys()}")
                        if task_result.get("status") == "error":
                            print(f"[ERROR] Task Agent Error: {task_result.get('output')}")

                except Exception as e:
                    print(f"\n❌ [ERROR] Task Agent Fehler: {e}")
                    print(f"[ERROR] Traceback:\n{traceback.format_exc()}")
                    results["task"] = {
                        "status": "error",
                        "error": str(e),
                        "output": f"Fehler beim Task Agent: {e}\n\nDetails:\n{traceback.format_exc()}"
                    }

            elif workflow == "research_only":
                with st.spinner("🔍 Research Agent arbeitet..."):
                    try:
                        research_result = self.research_agent.process(
                            user_input,
                            context={"memory": memory_context, "user_context": user_context_str}
                        )
                        results["research"] = research_result
                        results["agents_used"].append("ResearchAgent")
                    except Exception as e:
                        print(f"\n❌ [ERROR] Research Agent Fehler: {e}")
                        print(f"[ERROR] Traceback:\n{traceback.format_exc()}")
                        results["research"] = {
                            "status": "error",
                            "error": str(e),
                            "findings": f"Fehler: {e}"
                        }

                # Speichere wichtige Erkenntnisse
                if results.get("research", {}).get("status") == "success":
                    insight = results["research"].get("findings", "")[:500]
                    self.memory.add_research_insight(user_input, insight)

            elif workflow == "task_only":
                with st.spinner("⚙️ Task Agent arbeitet..."):
                    try:
                        task_result = self.task_agent.process(
                            user_input,
                            context={"memory": memory_context, "user_context": user_context_str}
                        )
                        results["task"] = task_result
                        results["agents_used"].append("TaskAgent")

                        print(f"\n[DEBUG] Task Only - Status: {task_result.get('status')}")
                    except Exception as e:
                        print(f"\n❌ [ERROR] Task Agent Fehler: {e}")
                        print(f"[ERROR] Traceback:\n{traceback.format_exc()}")
                        results["task"] = {
                            "status": "error",
                            "error": str(e),
                            "output": f"Fehler: {e}"
                        }

            elif workflow == "asana":
                # Asana-Anfragen
                with st.spinner("✅ Asana Agent arbeitet..."):
                    try:
                        asana_result = self.asana_agent.process(user_input)
                        results["asana"] = asana_result
                        results["agents_used"].append("AsanaAgent")

                        print(f"\n[DEBUG] Asana Agent Status: {asana_result.get('status')}")
                    except Exception as e:
                        print(f"\n❌ [ERROR] Asana Agent Fehler: {e}")
                        print(f"[ERROR] Traceback:\n{traceback.format_exc()}")
                        results["asana"] = {
                            "status": "error",
                            "error": str(e),
                            "result": f"Fehler: {e}"
                        }

            elif workflow == "calendar_email":
                # Kalender- und E-Mail-Anfragen
                with st.spinner("📅 CalendarEmail Agent arbeitet..."):
                    try:
                        calendar_email_result = self.calendar_email_agent.process(
                            user_input,
                            context={"memory": memory_context, "user_context": user_context_str}
                        )
                        results["calendar_email"] = calendar_email_result
                        results["agents_used"].append("CalendarEmailAgent")

                        print(f"\n[DEBUG] CalendarEmail Agent Status: {calendar_email_result.get('status')}")
                    except Exception as e:
                        print(f"\n❌ [ERROR] CalendarEmail Agent Fehler: {e}")
                        print(f"[ERROR] Traceback:\n{traceback.format_exc()}")
                        results["calendar_email"] = {
                            "status": "error",
                            "error": str(e),
                            "result": f"Fehler: {e}"
                        }

            # Speichere Konversations-Kontext
            summary = ""
            if "research" in results:
                summary = results["research"].get("findings", "")[:200]
            elif "task" in results:
                summary = results["task"].get("output", "")[:200]
            elif "asana" in results:
                summary = results["asana"].get("result", "")[:200]
            elif "calendar_email" in results:
                summary = results["calendar_email"].get("result", "")[:200]

            self.memory.add_conversation_context(user_input, workflow, summary)

        except Exception as e:
            print(f"\n❌ [CRITICAL ERROR] Unerwarteter Fehler in process_request: {e}")
            print(f"[ERROR] Traceback:\n{traceback.format_exc()}")
            results["error"] = str(e)
            st.error(f"Kritischer Fehler bei der Verarbeitung: {e}")

        return results


def initialize_session_state():
    """Initialisiere Session State"""
    # Prüfe ob Orchestrator neu erstellt werden muss (anderer User)
    user_ctx = st.session_state.get('user_ctx')
    current_orch = st.session_state.get('orchestrator')
    needs_reinit = (
        current_orch is not None
        and user_ctx is not None
        and getattr(current_orch, 'user_ctx', None) is not None
        and current_orch.user_ctx.username != user_ctx.username
    )
    if needs_reinit:
        del st.session_state['orchestrator']

    if 'orchestrator' not in st.session_state:
        # Validiere API-Keys vor der Initialisierung
        llm_provider = os.getenv("LLM_PROVIDER", "anthropic")

        if llm_provider == "anthropic":
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                st.error("❌ ANTHROPIC_API_KEY fehlt in der .env-Datei!")
                st.stop()
        else:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                st.error("❌ OPENAI_API_KEY fehlt in der .env-Datei!")
                st.stop()

        print(f"\n[INIT] Initialisiere Orchestrator mit Provider: {llm_provider}")
        print(f"[INIT] API-Key vorhanden: {'Ja' if api_key else 'Nein'}")

        user_ctx = st.session_state.get('user_ctx')
        st.session_state.orchestrator = StreamlitOrchestrator(user_ctx=user_ctx)

        # Validiere Agenten-Initialisierung
        if not st.session_state.orchestrator.task_agent.llm:
            st.error("❌ Task Agent konnte nicht initialisiert werden! Prüfen Sie die API-Keys.")
            st.stop()

        print(f"[INIT] Task Agent LLM initialisiert: {st.session_state.orchestrator.task_agent.llm}")

    if 'chat_history' not in st.session_state:
        st.session_state.chat_history = []

    if 'workflow_mode' not in st.session_state:
        st.session_state.workflow_mode = "auto"

    # Email-Chat States
    if 'email_chat_active' not in st.session_state:
        st.session_state.email_chat_active = False

    if 'email_chat_data' not in st.session_state:
        st.session_state.email_chat_data = None

    if 'email_chat_history' not in st.session_state:
        st.session_state.email_chat_history = []

    # Email-Selektion für Bulk-Aktionen
    if 'selected_emails' not in st.session_state:
        st.session_state.selected_emails = set()


def reset_chat_session():
    """Setzt die Chat-Session zurück ohne Langzeitgedächtnis zu löschen"""
    # Lösche nur Chat-Historie in Session State
    st.session_state.chat_history = []

    # Lösche nur conversation_context im Memory, NICHT user_profile
    memory = st.session_state.orchestrator.memory
    memory.memory["conversation_context"] = []
    memory._save_memory()

    print("[RESET] Chat-Session zurückgesetzt (Langzeitgedächtnis behalten)")


def render_sidebar():
    """Rendert die Sidebar mit Status-Informationen"""
    with st.sidebar:
        # Herbert Gruppe Logo (weiss auf brand-600 Hintergrund — vom globalen CSS gesteuert)
        username = st.session_state.get('username', '')
        display_name = st.session_state.get('name', username)
        _logo_path = Path(__file__).parent / "assets" / "Logo Herbert Gruppe white ohne Hintergrund.png"
        if _logo_path.exists():
            st.image(str(_logo_path), width=180)
        st.markdown(
            '<p class="hg-tagline">MEIN ASSISTENT</p>',
            unsafe_allow_html=True,
        )
        if display_name:
            st.markdown(
                f'<p style="color:rgba(255,255,255,0.55); font-size:0.75rem; '
                f'margin-top:0.75rem;">Angemeldet als <strong style="color:rgba(255,255,255,0.85);">{display_name}</strong></p>',
                unsafe_allow_html=True,
            )
        st.markdown("---")

        # Logout Button
        col_chat, col_logout = st.columns([3, 1])
        with col_chat:
            if st.button("🔄 Neuer Chat", type="primary", use_container_width=True):
                reset_chat_session()
                st.success("✓ Neuer Chat gestartet!")
                st.rerun()
        with col_logout:
            if st.button("🚪", help="Abmelden", use_container_width=True):
                # Session-State räumen, dann Authentik-Logout über Streamlit-OIDC
                for key in ['username', 'email', 'name', 'role', 'user_ctx']:
                    if key in st.session_state:
                        del st.session_state[key]
                st.logout()

        st.markdown("---")

        # Graph API Status (Outlook/Microsoft)
        outlook_tool = st.session_state.orchestrator.outlook_tool
        if outlook_tool and hasattr(outlook_tool, 'is_authenticated') and outlook_tool.is_authenticated():
            st.success("✅ **Microsoft Graph API:** Aktiv")
        else:
            st.error("❌ **Microsoft Graph API:** Nicht verbunden")
            with st.expander("ℹ️ Graph API Info"):
                st.caption("Die Microsoft Graph API wird für Kalender und E-Mail benötigt. Bitte konfigurieren Sie die API in der .env-Datei.")

        st.markdown("---")

        # LLM Provider
        llm_provider = st.session_state.orchestrator.llm_provider
        st.info(f"**LLM Provider:** {llm_provider.upper()}")

        st.markdown("---")

        # Gedächtnis-Status
        st.subheader("📚 Gedächtnis-Status")

        memory = st.session_state.orchestrator.memory
        profile = memory.get_user_profile()

        # User Profile Info
        if profile.get("name"):
            st.markdown(f"**Name:** {profile['name']}")

        if profile.get("profession"):
            with st.expander("👔 Beruf"):
                st.write(profile['profession'])

        # Memory Statistiken
        insights_count = len(memory.memory.get("research_insights", []))
        conversations_count = len(memory.memory.get("conversation_context", []))

        col1, col2 = st.columns(2)
        with col1:
            st.metric("Erkenntnisse", insights_count)
        with col2:
            st.metric("Konversationen", conversations_count)

        st.markdown("---")

        # Dokumente-Status mit Verwaltung
        st.subheader("📁 Dokumente")

        doc_tool = st.session_state.orchestrator.document_tool
        doc_count = doc_tool.count_documents()

        st.metric("Verfügbare Dokumente", doc_count)

        if doc_count > 0:
            with st.expander("📋 Dokument-Verwaltung", expanded=False):
                documents = doc_tool.scan_documents()

                for idx, doc in enumerate(documents):
                    size_kb = doc['size'] / 1024
                    col1, col2 = st.columns([3, 1])

                    with col1:
                        st.markdown(f"**{doc['name']}**")
                        st.caption(f"{size_kb:.1f} KB • {doc['type']}")

                    with col2:
                        if st.button("🗑️", key=f"delete_{idx}", help="Datei löschen"):
                            try:
                                file_path = os.path.join(str(_get_user_ctx().input_docs), doc['name'])
                                os.remove(file_path)
                                st.success(f"✓ {doc['name']} gelöscht")
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ Fehler: {e}")

                    st.markdown("---")

        st.markdown("---")

        # Workflow-Modus
        st.subheader("⚙️ Einstellungen")

        workflow_options = {
            "auto": "🤖 Auto (Empfohlen)",
            "research_then_task": "🔍➡️⚙️ Research → Task",
            "research_only": "🔍 Nur Research",
            "task_only": "⚙️ Nur Task"
        }

        selected_workflow = st.selectbox(
            "Workflow-Modus",
            options=list(workflow_options.keys()),
            format_func=lambda x: workflow_options[x],
            index=0
        )
        st.session_state.workflow_mode = selected_workflow

        st.markdown("---")

        # Microsoft Graph Konfiguration
        st.subheader("📅 Microsoft Graph API")

        graph_configured = bool(os.getenv("MICROSOFT_CLIENT_ID")) and bool(os.getenv("MICROSOFT_TENANT_ID"))

        if graph_configured:
            outlook_tool = st.session_state.orchestrator.outlook_tool

            if outlook_tool.is_authenticated():
                st.success("✅ Authentifiziert & bereit")
                client_id = os.getenv("MICROSOFT_CLIENT_ID", "")
                st.caption(f"Client ID: {client_id[:8]}...")

                # Abmelde-Button
                if st.button("🔓 Abmelden", use_container_width=True):
                    outlook_tool.logout()
                    st.success("✓ Abgemeldet")
                    st.rerun()
            else:
                st.warning("⚠️ Authentifizierung erforderlich")
                client_id = os.getenv("MICROSOFT_CLIENT_ID", "")
                tenant_id = os.getenv("MICROSOFT_TENANT_ID", "")
                st.caption(f"Client ID: {client_id[:8]}...")
                st.caption(f"Tenant ID: {tenant_id[:8]}...")

                # Prüfe ob Flow bereits initiiert wurde
                if "outlook_device_code" not in st.session_state:
                    # Zeige Anmelde-Button
                    if st.button("🔐 Mit Microsoft anmelden", type="primary", use_container_width=True):
                        # Initiiere Device Code Flow
                        with st.spinner("Initiiere Anmeldung..."):
                            result = outlook_tool.initiate_device_flow()

                            if result.get("success"):
                                # Speichere Device Info in Session
                                st.session_state.outlook_device_code = result["device_info"]
                                st.rerun()
                            else:
                                st.error(f"❌ Fehler: {result.get('error')}")
                else:
                    # Device Code wurde generiert - zeige Anweisungen
                    device_info = st.session_state.outlook_device_code

                    st.success("✅ **Anmeldecode generiert!**")

                    # Zeige Code prominent mit zuverlässigem Copy-Button
                    user_code = device_info["user_code"]
                    st.markdown("### 📋 Ihr Anmeldecode:")
                    import streamlit.components.v1 as components
                    components.html(f"""
                    <div style="display:flex; align-items:center; gap:8px; font-family:sans-serif;">
                        <code style="font-size:1.4rem; font-weight:bold; padding:8px 16px; background:#f0f2f6; border-radius:6px; letter-spacing:2px;">{user_code}</code>
                        <button id="copyBtn" style="padding:6px 12px; cursor:pointer; border:1px solid #ccc; border-radius:5px; background:white; font-size:0.85rem;">📋 Kopieren</button>
                    </div>
                    <script>
                    document.getElementById('copyBtn').addEventListener('click', function() {{
                        var ta = document.createElement('textarea');
                        ta.value = '{user_code}';
                        ta.style.position = 'fixed';
                        ta.style.left = '-9999px';
                        document.body.appendChild(ta);
                        ta.select();
                        document.execCommand('copy');
                        document.body.removeChild(ta);
                        document.getElementById('copyBtn').innerText = '✅ Kopiert!';
                        setTimeout(function() {{ document.getElementById('copyBtn').innerText = '📋 Kopieren'; }}, 2000);
                    }});
                    </script>
                    """, height=50)

                    # URL zum Anklicken
                    verification_url = device_info["verification_uri"]
                    st.markdown(f"### 🔗 [{verification_url}]({verification_url})")
                    st.caption("Klicken Sie auf den Link oben oder öffnen Sie ihn manuell")

                    st.info("""
**So funktioniert's:**

1. Klicken Sie auf den Link oben (öffnet in neuem Tab)
2. Geben Sie den Code ein
3. Melden Sie sich mit Ihrem Microsoft-Konto an
4. Erteilen Sie die Berechtigungen
5. Kehren Sie hierher zurück und klicken Sie auf "Anmeldung abschließen"
                    """)

                    col1, col2 = st.columns(2)

                    with col1:
                        if st.button("✅ Anmeldung abschließen", type="primary", use_container_width=True):
                            with st.spinner("⏳ Prüfe Anmeldung..."):
                                result = outlook_tool.complete_device_flow_wait()

                                if result.get("success"):
                                    st.success("✅ Erfolgreich authentifiziert!")
                                    st.balloons()
                                    # Lösche Device Code aus Session
                                    del st.session_state.outlook_device_code
                                    st.rerun()
                                else:
                                    st.error(f"❌ Anmeldung fehlgeschlagen")
                                    st.caption(f"Fehler: {result.get('error')}")
                                    st.caption("Haben Sie den Code eingegeben und die Anmeldung abgeschlossen?")

                    with col2:
                        if st.button("❌ Abbrechen", use_container_width=True):
                            # Lösche Device Code aus Session
                            del st.session_state.outlook_device_code
                            st.rerun()
        else:
            with st.expander("⚙️ Kalender-Integration einrichten"):
                st.markdown("""
**Microsoft Graph API konfigurieren:**

1. **Client-ID** von Ihrer IT anfordern
2. **Tenant-ID** Ihrer Organisation erfragen
3. In `.env` Datei eintragen:

```bash
MICROSOFT_CLIENT_ID=ihre_client_id
MICROSOFT_TENANT_ID=ihre_tenant_id
```

4. App neu starten

**Danach haben Sie Zugriff auf:**
- 📅 Outlook-Kalender
- 📧 E-Mails (erweitert)
- 👥 Kontakte
- 📁 OneDrive

**Hinweis:** Dies erfordert eine App-Registrierung im Azure Portal durch Ihre IT-Abteilung.
                """)

                if st.button("📖 Anleitung für IT", use_container_width=True):
                    st.info("""
**Für die IT-Abteilung:**

1. Azure Portal öffnen: https://portal.azure.com
2. "Azure Active Directory" > "App registrations" > "New registration"
3. Name: "Mein Assistent"
4. Supported account types: "Accounts in this organizational directory only"
5. Redirect URI: "Public client/native" > http://localhost
6. Nach Registrierung: Client-ID und Tenant-ID notieren
7. API permissions hinzufügen:
   - Microsoft Graph > Delegated permissions
   - Calendars.Read, Calendars.ReadWrite
   - User.Read
8. "Grant admin consent" klicken

Die IDs dann dem Nutzer geben.
                    """)

        st.markdown("---")

        # Gedächtnis-Management
        st.subheader("🗑️ Gedächtnis-Verwaltung")

        col1, col2 = st.columns(2)

        with col1:
            if st.button("🧹 Chat-Historie", use_container_width=True):
                conv_count = conversations_count
                if conv_count > 0:
                    transferred = memory.clear_conversation_history(transfer_insights=True)
                    st.success(f"✓ Historie gelöscht\n({transferred} übertragen)")
                    st.rerun()
                else:
                    st.info("Historie ist leer")

        with col2:
            if st.button("💭 Gedächtnis", use_container_width=True):
                if st.session_state.get('confirm_forget'):
                    memory.clear_memory()
                    st.success("✓ Gedächtnis gelöscht")
                    st.session_state.confirm_forget = False
                    st.rerun()
                else:
                    st.session_state.confirm_forget = True
                    st.warning("Erneut klicken zum Bestätigen")

        # Gedächtnis anzeigen
        if st.button("📋 Gedächtnis anzeigen", use_container_width=True):
            st.session_state.show_memory = not st.session_state.get('show_memory', False)


def render_memory_display():
    """Zeigt das Gedächtnis im Detail an"""
    if st.session_state.get('show_memory', False):
        with st.expander("📚 Gedächtnis-Export", expanded=True):
            memory_export = st.session_state.orchestrator.memory.export_memory()
            st.text(memory_export)


def render_chat_message(message):
    """Rendert eine einzelne Chat-Nachricht"""
    if message["role"] == "user":
        with st.chat_message("user"):
            st.markdown(message["content"])
    else:
        with st.chat_message("assistant"):
            # Zeige verwendete Agenten
            if "agents_used" in message:
                agents_str = ", ".join(message["agents_used"])
                st.caption(f"🤖 Verwendete Agenten: {agents_str}")

            # Research Results
            if "research" in message:
                with st.expander("🔍 Research Agent", expanded=True):
                    research = message["research"]
                    # Akzeptiere sowohl "success" als auch andere Status
                    status = research.get("status", "unknown")
                    if status == "success":
                        st.markdown(research.get("findings", ""))
                    elif status == "error":
                        error_msg = research.get("error") or research.get("findings", "Unbekannter Fehler")
                        st.error(f"❌ Fehler: {error_msg}")
                    else:
                        # Fallback: Zeige Findings auch wenn Status unbekannt ist
                        findings = research.get("findings", "")
                        if findings:
                            st.markdown(findings)
                        else:
                            st.warning(f"⚠️ Status: {status}")

            # Task Results
            if "task" in message:
                with st.expander("⚙️ Task Agent", expanded=True):
                    task = message["task"]
                    # Akzeptiere "success" oder "completed" als erfolgreichen Status
                    status = task.get("status", "unknown")

                    # Debug-Info anzeigen
                    print(f"[DEBUG UI] Task Status: {status}")
                    print(f"[DEBUG UI] Task Keys: {task.keys()}")

                    if status in ["success", "completed"]:
                        output = task.get("output", "")
                        if output:
                            st.markdown(output)
                        else:
                            st.info("✓ Aufgabe abgeschlossen (keine Ausgabe)")
                    elif status == "error":
                        error_msg = task.get("error") or task.get("output", "Unbekannter Fehler")
                        st.error(f"❌ Fehler: {error_msg}")
                        # Zeige zusätzliche Details wenn vorhanden
                        if "output" in task and task["output"] != error_msg:
                            with st.expander("🔍 Fehler-Details"):
                                st.code(task["output"])
                    else:
                        # Fallback: Zeige Output auch wenn Status unbekannt ist
                        output = task.get("output", "")
                        if output:
                            st.markdown(output)
                        else:
                            st.warning(f"⚠️ Status: {status} - Keine Ausgabe vorhanden")
                            st.json(task)  # Zeige komplettes Task-Objekt für Debugging

            # Asana Results
            if "asana" in message:
                with st.expander("✅ Asana Agent", expanded=True):
                    asana = message["asana"]
                    status = asana.get("status", "unknown")

                    if status == "success":
                        result = asana.get("result", "")
                        if result:
                            st.markdown(result)
                        else:
                            st.info("✓ Asana-Aktion abgeschlossen")
                    elif status == "error":
                        error_msg = asana.get("error") or asana.get("result", "Unbekannter Fehler")
                        st.error(f"❌ Fehler: {error_msg}")
                    else:
                        # Fallback
                        result = asana.get("result", "")
                        if result:
                            st.markdown(result)
                        else:
                            st.warning(f"⚠️ Status: {status}")

            # CalendarEmail Results
            if "calendar_email" in message:
                with st.expander("📅 CalendarEmail Agent", expanded=True):
                    calendar_email = message["calendar_email"]
                    status = calendar_email.get("status", "unknown")

                    if status == "success":
                        result = calendar_email.get("result", "")
                        if result:
                            st.markdown(result)
                        else:
                            st.info("✓ Kalender/E-Mail-Aktion abgeschlossen")

                        # Zeige verwendete Tools
                        if "tools_used" in calendar_email and calendar_email["tools_used"]:
                            tools_str = ", ".join(calendar_email["tools_used"])
                            st.caption(f"🔧 Verwendete Tools: {tools_str}")
                    elif status == "error":
                        error_msg = calendar_email.get("error") or calendar_email.get("result", "Unbekannter Fehler")
                        st.error(f"❌ Fehler: {error_msg}")
                    else:
                        # Fallback
                        result = calendar_email.get("result", "")
                        if result:
                            st.markdown(result)
                        else:
                            st.warning(f"⚠️ Status: {status}")

            # Error
            if "error" in message:
                st.error(f"❌ Fehler: {message['error']}")


def render_chat_tab():
    """Rendert den Chat-Tab"""
    st.header("💬 Chat mit Ihrem Assistenten")

    # Gedächtnis-Anzeige (optional)
    render_memory_display()

    # Chat-Historie anzeigen
    for message in st.session_state.chat_history:
        render_chat_message(message)

    # Chat-Eingabe
    user_input = st.chat_input("Stellen Sie Ihre Frage oder geben Sie eine Aufgabe ein...")

    if user_input:
        # User Message zur Historie hinzufügen
        user_message = {
            "role": "user",
            "content": user_input,
            "timestamp": datetime.now().isoformat()
        }
        st.session_state.chat_history.append(user_message)

        # Anfrage verarbeiten
        workflow = st.session_state.workflow_mode
        results = st.session_state.orchestrator.process_request(user_input, workflow)

        # Assistant Message zur Historie hinzufügen
        assistant_message = {
            "role": "assistant",
            "timestamp": results.get("timestamp"),
            "workflow": results.get("workflow"),
            "agents_used": results.get("agents_used", [])
        }

        if "research" in results:
            assistant_message["research"] = results["research"]

        if "task" in results:
            assistant_message["task"] = results["task"]

        if "asana" in results:
            assistant_message["asana"] = results["asana"]

        if "calendar_email" in results:
            assistant_message["calendar_email"] = results["calendar_email"]

        if "error" in results:
            assistant_message["error"] = results["error"]

        st.session_state.chat_history.append(assistant_message)

        # Seite neu laden um neue Nachrichten anzuzeigen
        st.rerun()

    # Hilfe-Bereich im Footer
    with st.expander("❓ Hilfe & Befehle"):
        st.markdown("""
        ### Workflows
        - **Auto (Empfohlen):** System wählt automatisch den passenden Workflow
        - **Research → Task:** Recherche durchführen, dann Aufgabe ausführen
        - **Nur Research:** Nur Recherche durchführen
        - **Nur Task:** Nur Aufgabe ausführen

        ### Beispiel-Anfragen

        **Research-Anfragen:**
        - "Erkläre mir Quantencomputing"
        - "Was ist Machine Learning?"
        - "Vergleiche Python und JavaScript"

        **Task-Anfragen:**
        - "Schreibe einen Blogpost über KI"
        - "Erstelle eine Produktbeschreibung"
        - "Generiere einen Python-Code für Fibonacci"

        **Kombinierte Anfragen:**
        - "Recherchiere React und schreibe ein Tutorial"
        - "Finde Infos über gesunde Ernährung und erstelle einen Meal Plan"

        ### Gedächtnis-System
        Der Assistent merkt sich Informationen über Sie und nutzt diese als Kontext
        für zukünftige Anfragen. Mit den Buttons in der Sidebar können Sie:
        - **Chat-Historie löschen:** Kontext-Verschmutzung vermeiden
        - **Gedächtnis löschen:** Komplett neu starten
        """)


@st.fragment
def render_documents_tab():
    """Rendert den Dokumente-Tab mit Upload-Funktion.

    Performance: @st.fragment verhindert, dass Interaktionen in diesem Tab
    ein Re-Rendering der übrigen 5 Tabs auslösen.
    """
    st.header("📁 Dokumente verwalten")

    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader("📤 Dokumente hochladen")

        uploaded_files = st.file_uploader(
            "Laden Sie Dokumente hoch (PDF, DOCX, TXT, CSV, XLSX)",
            accept_multiple_files=True,
            type=["pdf", "docx", "txt", "csv", "xlsx"],
            help="Wählen Sie eine oder mehrere Dateien zum Hochladen"
        )

        if uploaded_files:
            if st.button("📥 Dateien speichern", type="primary", use_container_width=True):
                # Stelle sicher dass input_docs/ existiert
                os.makedirs(str(_get_user_ctx().input_docs), exist_ok=True)

                success_count = 0
                error_count = 0

                for uploaded_file in uploaded_files:
                    try:
                        # Speichere Datei
                        file_path = os.path.join(str(_get_user_ctx().input_docs), uploaded_file.name)
                        with open(file_path, "wb") as f:
                            f.write(uploaded_file.getbuffer())
                        success_count += 1
                    except Exception as e:
                        st.error(f"❌ Fehler bei {uploaded_file.name}: {e}")
                        error_count += 1

                if success_count > 0:
                    st.success(f"✅ {success_count} Datei(en) erfolgreich hochgeladen!")
                    st.rerun()

                if error_count > 0:
                    st.warning(f"⚠️ {error_count} Datei(en) konnten nicht hochgeladen werden")

    with col2:
        st.subheader("📊 Statistik")
        doc_tool = st.session_state.orchestrator.document_tool
        doc_count = doc_tool.count_documents()

        if doc_count > 0:
            documents = doc_tool.scan_documents()
            total_size = sum(doc['size'] for doc in documents)
            total_size_mb = total_size / (1024 * 1024)

            st.metric("Anzahl Dokumente", doc_count)
            st.metric("Gesamtgröße", f"{total_size_mb:.2f} MB")

            # Verteilung nach Typ
            type_counts = {}
            for doc in documents:
                doc_type = doc['type']
                type_counts[doc_type] = type_counts.get(doc_type, 0) + 1

            st.write("**Dateitypen:**")
            for doc_type, count in type_counts.items():
                st.write(f"- {doc_type}: {count}")
        else:
            st.info("Noch keine Dokumente vorhanden")

    st.markdown("---")

    # Dokumenten-Liste
    st.subheader("📋 Verfügbare Dokumente")

    doc_tool = st.session_state.orchestrator.document_tool
    documents = doc_tool.scan_documents()

    if documents:
        # Tabellen-Header
        cols = st.columns([3, 1.5, 1, 1])
        cols[0].markdown("**Dateiname**")
        cols[1].markdown("**Typ**")
        cols[2].markdown("**Größe**")
        cols[3].markdown("**Aktion**")

        st.markdown("---")

        # Dateien anzeigen
        for idx, doc in enumerate(documents):
            cols = st.columns([3, 1.5, 1, 1])

            size_kb = doc['size'] / 1024
            size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.2f} MB"

            cols[0].write(doc['name'])
            cols[1].write(doc['type'])
            cols[2].write(size_str)

            if cols[3].button("🗑️ Löschen", key=f"del_doc_{idx}"):
                try:
                    file_path = os.path.join(str(_get_user_ctx().input_docs), doc['name'])
                    os.remove(file_path)
                    st.success(f"✓ {doc['name']} gelöscht")
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Fehler beim Löschen: {e}")
    else:
        st.info("📭 Keine Dokumente vorhanden. Laden Sie Dateien hoch, um zu beginnen.")


def get_archive_subfolders(archive_dir: str = "newsletter_archiv") -> list:
    """Gibt Liste aller Unterordner im Archiv zurück"""
    subfolders = []
    if os.path.exists(archive_dir):
        for item in os.listdir(archive_dir):
            item_path = os.path.join(archive_dir, item)
            if os.path.isdir(item_path):
                subfolders.append(item)
    return sorted(subfolders)


def get_archive_files_grouped(archive_dir: str = "newsletter_archiv") -> dict:
    """Gibt Archiv-Dateien gruppiert nach Ordnern zurück"""
    grouped_files = {
        "root": []  # Dateien im Hauptverzeichnis
    }

    # Lade Dateien aus Hauptverzeichnis
    if os.path.exists(archive_dir):
        for file in os.listdir(archive_dir):
            file_path = os.path.join(archive_dir, file)
            if os.path.isfile(file_path) and file.endswith('.md'):
                file_stat = os.stat(file_path)
                grouped_files["root"].append({
                    'name': file,
                    'path': file_path,
                    'folder': None,
                    'size': file_stat.st_size,
                    'modified': datetime.fromtimestamp(file_stat.st_mtime)
                })

        # Lade Dateien aus Unterordnern
        for folder in os.listdir(archive_dir):
            folder_path = os.path.join(archive_dir, folder)
            if os.path.isdir(folder_path):
                grouped_files[folder] = []
                for file in os.listdir(folder_path):
                    if file.endswith('.md'):
                        file_path = os.path.join(folder_path, file)
                        file_stat = os.stat(file_path)
                        grouped_files[folder].append({
                            'name': file,
                            'path': file_path,
                            'folder': folder,
                            'size': file_stat.st_size,
                            'modified': datetime.fromtimestamp(file_stat.st_mtime)
                        })

    # Sortiere Dateien in jedem Ordner nach Datum
    for folder in grouped_files:
        grouped_files[folder].sort(key=lambda x: x['modified'], reverse=True)

    return grouped_files


@st.fragment
def render_archive_tab():
    """Rendert den Archiv-Tab mit Berichten und Ordnerverwaltung.

    Performance: @st.fragment isoliert Re-Renders auf diesen Tab.
    """
    st.header("📚 Berichte-Archiv")

    archive_dir = "newsletter_archiv"

    # Stelle sicher dass Archiv-Ordner existiert
    os.makedirs(archive_dir, exist_ok=True)

    # Ordnerverwaltung
    with st.expander("📁 Ordner-Verwaltung", expanded=False):
        st.subheader("Neuen Ordner erstellen")

        col1, col2 = st.columns([3, 1])

        with col1:
            new_folder_name = st.text_input(
                "Ordnername",
                placeholder="z.B. Wärmepumpen, Projekte, Analysen...",
                help="Erstellen Sie Unterordner, um Ihre Berichte zu organisieren"
            )

        with col2:
            st.write("")  # Spacer
            st.write("")  # Spacer
            if st.button("📁 Erstellen", use_container_width=True):
                if new_folder_name:
                    # Bereinige Ordnernamen
                    safe_folder_name = re.sub(r'[^\w\s-]', '', new_folder_name)
                    safe_folder_name = re.sub(r'[-\s]+', '-', safe_folder_name).strip('-')

                    if safe_folder_name:
                        folder_path = os.path.join(archive_dir, safe_folder_name)
                        try:
                            os.makedirs(folder_path, exist_ok=True)
                            st.success(f"✅ Ordner '{safe_folder_name}' erstellt!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ Fehler beim Erstellen: {e}")
                    else:
                        st.warning("⚠️ Ungültiger Ordnername")
                else:
                    st.warning("⚠️ Bitte Ordnernamen eingeben")

        # Zeige vorhandene Ordner
        subfolders = get_archive_subfolders(archive_dir)
        if subfolders:
            st.markdown("---")
            st.write("**Vorhandene Ordner:**")
            for folder in subfolders:
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.write(f"📁 {folder}")
                with col2:
                    if st.button("🗑️", key=f"del_folder_{folder}", help=f"Ordner '{folder}' löschen"):
                        folder_path = os.path.join(archive_dir, folder)
                        try:
                            # Prüfe ob Ordner leer ist
                            if len(os.listdir(folder_path)) == 0:
                                os.rmdir(folder_path)
                                st.success(f"✓ Ordner '{folder}' gelöscht")
                                st.rerun()
                            else:
                                st.warning(f"⚠️ Ordner '{folder}' ist nicht leer!")
                        except Exception as e:
                            st.error(f"❌ Fehler: {e}")

    st.markdown("---")

    # HIERARCHISCHE ANZEIGE - Gruppiere Dateien nach Ordnern
    folders_data = {}

    # Sammle Dateien aus Hauptverzeichnis
    main_reports = []
    try:
        for file in os.listdir(archive_dir):
            file_path = os.path.join(archive_dir, file)
            if os.path.isfile(file_path) and file.endswith('.md'):
                try:
                    file_stat = os.stat(file_path)
                    main_reports.append({
                        'name': file,
                        'path': file_path,
                        'folder': None,
                        'size': file_stat.st_size,
                        'modified': datetime.fromtimestamp(file_stat.st_mtime)
                    })
                except Exception as e:
                    print(f"Fehler beim Lesen von {file}: {e}")
    except Exception as e:
        print(f"Fehler beim Scannen des Archivs: {e}")

    folders_data['Hauptverzeichnis'] = sorted(main_reports, key=lambda x: x['modified'], reverse=True)

    # Sammle Dateien aus Unterordnern
    subfolders = get_archive_subfolders(archive_dir)
    for folder in subfolders:
        folder_reports = []
        folder_path = os.path.join(archive_dir, folder)
        try:
            for file in os.listdir(folder_path):
                if file.endswith('.md'):
                    file_path = os.path.join(folder_path, file)
                    try:
                        file_stat = os.stat(file_path)
                        folder_reports.append({
                            'name': file,
                            'path': file_path,
                            'folder': folder,
                            'size': file_stat.st_size,
                            'modified': datetime.fromtimestamp(file_stat.st_mtime)
                        })
                    except Exception as e:
                        print(f"Fehler beim Lesen von {file}: {e}")
        except Exception as e:
            print(f"Fehler beim Scannen des Ordners {folder}: {e}")

        folders_data[folder] = sorted(folder_reports, key=lambda x: x['modified'], reverse=True)

    # Gesamtanzahl berechnen
    total_reports = sum(len(reports) for reports in folders_data.values())

    # Statistik
    col1, col2 = st.columns([3, 1])
    with col1:
        st.subheader(f"📄 {total_reports} Berichte gefunden")
    with col2:
        st.metric("Gesamt", total_reports)

    st.markdown("---")

    # Zeige Ordner-Hierarchie
    if total_reports > 0:
        # Verfügbare Zielordner für Verschieben
        available_folders = ["📂 Hauptverzeichnis"] + [f"📁 {folder}" for folder in subfolders]

        # Hauptverzeichnis zuerst
        if folders_data['Hauptverzeichnis']:
            render_folder_section("📂 Hauptverzeichnis", folders_data['Hauptverzeichnis'],
                                None, available_folders, archive_dir)

        # Dann Unterordner alphabetisch
        for folder in sorted(subfolders):
            if folders_data[folder]:
                render_folder_section(f"📁 {folder}", folders_data[folder],
                                    folder, available_folders, archive_dir)

    else:
        st.info("📭 Noch keine Berichte im Archiv vorhanden.")
        st.markdown("""
        Berichte werden automatisch erstellt und hier gespeichert, wenn Sie im Chat-Tab
        Anfragen stellen, die zu Recherche-Ergebnissen oder generierten Inhalten führen.
        """)


def render_folder_section(folder_name: str, reports: list, folder_key: str,
                         available_folders: list, archive_dir: str):
    """Rendert einen Ordner mit seinen Berichten"""
    report_count = len(reports)

    # Ordner-Expander
    with st.expander(f"{folder_name} ({report_count} Berichte)", expanded=False):
        # Zeige jeden Bericht in diesem Ordner
        for idx, report in enumerate(reports):
            unique_key = f"{folder_key}_{idx}_{report['name'][:20]}"

            # Bericht-Expander (innerhalb des Ordner-Expanders)
            with st.expander(f"📄 {report['name']}", expanded=False):
                # Meta-Informationen
                st.caption(f"📅 {report['modified'].strftime('%d.%m.%Y %H:%M')} | 📦 {report['size']/1024:.1f} KB")

                # Inhalt laden und anzeigen
                try:
                    with open(report['path'], 'r', encoding='utf-8') as f:
                        content = f.read()

                    st.markdown(content)
                    st.markdown("---")

                    # Aktions-Buttons in 5 Spalten
                    col1, col2, col3, col4, col5 = st.columns(5)

                    with col1:
                        st.download_button(
                            "📥 Download",
                            data=content,
                            file_name=report['name'],
                            mime="text/markdown",
                            key=f"dl_{unique_key}",
                            use_container_width=True
                        )

                    with col2:
                        if st.button("📧 E-Mail", key=f"mail_{unique_key}", use_container_width=True):
                            send_report_email(report['name'], content, unique_key)

                    with col3:
                        # Asana-Aufgabe erstellen
                        asana_enabled = st.session_state.orchestrator.asana_tool.is_configured
                        if st.button("✅ Asana", key=f"asana_{unique_key}", use_container_width=True,
                                   disabled=not asana_enabled,
                                   help="Asana nicht konfiguriert" if not asana_enabled else "Als Asana-Aufgabe anlegen"):
                            st.session_state[f"show_asana_{unique_key}"] = True
                            st.rerun()

                    with col4:
                        # Verschieben nur wenn andere Zielordner vorhanden
                        folder_display = f"📂 Hauptverzeichnis" if folder_key is None else f"📁 {folder_key}"
                        can_move = len(available_folders) > 1
                        if st.button("📦 Verschieben", key=f"move_{unique_key}",
                                   disabled=not can_move, use_container_width=True,
                                   help="Erstellen Sie zuerst Ordner" if not can_move else None):
                            st.session_state[f"show_move_{unique_key}"] = True
                            st.rerun()

                    with col5:
                        if st.button("🗑️ Löschen", key=f"del_{unique_key}", use_container_width=True):
                            st.session_state[f"confirm_del_{unique_key}"] = True
                            st.rerun()

                    # Asana-Dialog
                    if st.session_state.get(f"show_asana_{unique_key}", False):
                        st.markdown("---")
                        st.write("**✅ Als Asana-Aufgabe anlegen:**")

                        # Vorschlag für Aufgabentitel (aus Dateinamen)
                        suggested_title = report['name'].replace('.md', '').replace('-', ' ').replace('_', ' ')

                        task_title = st.text_input("Aufgabentitel", value=suggested_title, key=f"asana_title_{unique_key}")

                        # Projekt-Auswahl (PFLICHT gemäß strikten Regeln) - cached
                        asana_projects = cached_get_asana_projects(st.session_state.orchestrator.asana_agent)

                        if asana_projects:
                            project_options = {p['name']: p['gid'] for p in asana_projects}
                            selected_project_name = st.selectbox(
                                "Projekt *",
                                options=list(project_options.keys()),
                                key=f"asana_project_{unique_key}"
                            )
                            selected_project_gid = project_options[selected_project_name]
                        else:
                            st.warning("⚠️ Keine Asana-Projekte verfügbar")
                            selected_project_gid = None

                        # Beschreibung (erste 500 Zeichen des Berichts)
                        task_description = st.text_area(
                            "Beschreibung (optional)",
                            value=content[:500] + "..." if len(content) > 500 else content,
                            height=100,
                            key=f"asana_desc_{unique_key}"
                        )

                        # Fälligkeitsdatum
                        task_due = st.date_input("Fälligkeitsdatum (optional)", value=None, key=f"asana_due_{unique_key}")

                        col1, col2 = st.columns(2)
                        with col1:
                            if st.button("✓ Aufgabe erstellen", key=f"conf_asana_{unique_key}",
                                       type="primary", use_container_width=True):
                                if not selected_project_gid:
                                    st.error("❌ Bitte wählen Sie ein Projekt aus")
                                elif not task_title.strip():
                                    st.error("❌ Bitte geben Sie einen Aufgabentitel ein")
                                else:
                                    try:
                                        due_date_str = task_due.strftime('%Y-%m-%d') if task_due else None

                                        # STRIKTE REGELN: Verwende create_task mit allen Pflicht-Parametern
                                        result = st.session_state.orchestrator.asana_agent.create_task(
                                            name=task_title.strip(),
                                            notes=task_description,
                                            due_on=due_date_str,
                                            project_gid=selected_project_gid,
                                            assignee_gid="me"  # PFLICHT-ASSIGNEE Regel
                                        )

                                        if result.get('success'):
                                            permalink = result.get('permalink_url', '')
                                            success_msg = f"✅ Asana-Aufgabe '{task_title}' erstellt!"
                                            if permalink:
                                                st.success(success_msg)
                                                st.markdown(f"🔗 [Aufgabe in Asana öffnen]({permalink})")
                                            else:
                                                st.success(success_msg)

                                            # Cleanup session state
                                            del st.session_state[f"show_asana_{unique_key}"]
                                            if f'asana_projects_{unique_key}' in st.session_state:
                                                del st.session_state[f'asana_projects_{unique_key}']
                                            st.rerun()
                                        else:
                                            st.error(f"❌ Fehler: {result.get('error', 'Unbekannter Fehler')}")
                                    except Exception as e:
                                        st.error(f"❌ Fehler: {e}")

                        with col2:
                            if st.button("✗ Abbrechen", key=f"canc_asana_{unique_key}", use_container_width=True):
                                del st.session_state[f"show_asana_{unique_key}"]
                                if f'asana_projects_{unique_key}' in st.session_state:
                                    del st.session_state[f'asana_projects_{unique_key}']
                                st.rerun()

                    # Verschieben-Dialog
                    if st.session_state.get(f"show_move_{unique_key}", False):
                        st.markdown("---")
                        st.write("**📦 Verschieben nach:**")

                        # Filtere aktuelle Position aus
                        current_folder_display = f"📂 Hauptverzeichnis" if folder_key is None else f"📁 {folder_key}"
                        target_options = [f for f in available_folders if f != current_folder_display]

                        if target_options:
                            target = st.selectbox("Zielordner", target_options, key=f"sel_{unique_key}")

                            col1, col2 = st.columns(2)
                            with col1:
                                if st.button("✓ Verschieben", key=f"conf_move_{unique_key}",
                                           type="primary", use_container_width=True):
                                    # Bestimme Zielpfad
                                    if target == "📂 Hauptverzeichnis":
                                        target_path = os.path.join(archive_dir, report['name'])
                                    else:
                                        folder_name_clean = target.replace("📁 ", "")
                                        target_path = os.path.join(archive_dir, folder_name_clean, report['name'])

                                    try:
                                        if os.path.exists(target_path):
                                            st.error("❌ Datei existiert bereits am Zielort!")
                                        else:
                                            shutil.move(report['path'], target_path)
                                            st.success(f"✅ Nach {target} verschoben!")
                                            del st.session_state[f"show_move_{unique_key}"]
                                            st.rerun()
                                    except Exception as e:
                                        st.error(f"❌ Fehler: {e}")

                            with col2:
                                if st.button("✗ Abbrechen", key=f"canc_move_{unique_key}", use_container_width=True):
                                    del st.session_state[f"show_move_{unique_key}"]
                                    st.rerun()
                        else:
                            st.warning("Keine anderen Zielordner verfügbar")
                            if st.button("✗ Schließen", key=f"close_{unique_key}", use_container_width=True):
                                del st.session_state[f"show_move_{unique_key}"]
                                st.rerun()

                    # Löschen-Dialog
                    if st.session_state.get(f"confirm_del_{unique_key}", False):
                        st.markdown("---")
                        st.warning(f"⚠️ **'{report['name']}' wirklich löschen?**")
                        st.caption("Diese Aktion kann nicht rückgängig gemacht werden!")

                        col1, col2 = st.columns(2)
                        with col1:
                            if st.button("✓ Ja, löschen", key=f"conf_del_{unique_key}",
                                       type="primary", use_container_width=True):
                                try:
                                    os.remove(report['path'])
                                    st.success("✓ Gelöscht!")
                                    del st.session_state[f"confirm_del_{unique_key}"]
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"❌ Fehler: {e}")

                        with col2:
                            if st.button("✗ Abbrechen", key=f"canc_del_{unique_key}", use_container_width=True):
                                del st.session_state[f"confirm_del_{unique_key}"]
                                st.rerun()

                except Exception as e:
                    st.error(f"❌ Fehler beim Laden: {e}")


def send_report_email(filename: str, content: str, idx: int):
    """Sendet einen Bericht per E-Mail"""
    email_receiver = os.getenv("EMAIL_RECEIVER", "")

    if not email_receiver:
        st.warning("⚠️ EMAIL_RECEIVER nicht in .env konfiguriert")
        return

    try:
        # Erstelle Betreff aus Dateinamen
        subject = f"Bericht: {filename.replace('.md', '').replace('-', ' ')}"

        # Sende E-Mail über CommunicationAgent
        with st.spinner("📤 Sende E-Mail..."):
            result = st.session_state.orchestrator.communication_agent.send_email(
                to=email_receiver,
                subject=subject,
                body=content
            )

        if result.get("status") == "success":
            st.success(f"✅ E-Mail erfolgreich an {email_receiver} gesendet!")
        else:
            st.error(f"❌ {result.get('result', 'E-Mail-Versand fehlgeschlagen')}")

    except Exception as e:
        st.error(f"❌ Fehler beim E-Mail-Versand: {e}")


def render_connection_status():
    """Zeigt den Verbindungsstatus für Outlook und Asana an"""

    col1, col2 = st.columns(2)

    with col1:
        # OUTLOOK STATUS
        outlook_tool = st.session_state.orchestrator.outlook_tool
        graph_configured = bool(os.getenv("MICROSOFT_CLIENT_ID")) and bool(os.getenv("MICROSOFT_TENANT_ID"))

        with st.status("📅 **Microsoft Outlook**", expanded=False) as outlook_status:
            if not graph_configured:
                st.write("❌ **Nicht konfiguriert**")
                st.caption("Client-ID und Tenant-ID fehlen in .env")

                if st.button("⚙️ Zur Konfiguration", key="outlook_config_btn", use_container_width=True):
                    st.info("""
**Benötigte Schritte:**
1. Fordern Sie Client-ID und Tenant-ID von Ihrer IT an
2. Tragen Sie die Werte in die .env Datei ein:
   ```
   MICROSOFT_CLIENT_ID=ihre_client_id
   MICROSOFT_TENANT_ID=ihre_tenant_id
   ```
3. Starten Sie die App neu
4. Klicken Sie in der Sidebar auf "Mit Microsoft anmelden"
                    """)

                outlook_status.update(label="📅 **Microsoft Outlook**", state="error")

            elif not outlook_tool.is_authenticated():
                st.write("⚠️ **Konfiguriert, aber nicht angemeldet**")
                st.caption("Authentifizierung erforderlich")

                if st.button("🔐 Jetzt anmelden", key="outlook_login_btn", use_container_width=True):
                    st.info("""
**So melden Sie sich an:**
1. Öffnen Sie die **Sidebar** (links)
2. Scrollen Sie zu **"📅 Microsoft Graph API"**
3. Klicken Sie auf **"🔐 Mit Microsoft anmelden"**
4. Folgen Sie den Anweisungen im Device Code Flow
                    """)

                outlook_status.update(label="📅 **Microsoft Outlook**", state="running")

            else:
                st.write("✅ **Verbunden und authentifiziert**")
                st.caption("Termine werden geladen")
                outlook_status.update(label="📅 **Microsoft Outlook**", state="complete")

    with col2:
        # ASANA STATUS
        asana_agent = st.session_state.orchestrator.asana_agent

        with st.status("✅ **Asana**", expanded=False) as asana_status:
            if not asana_agent.is_connected():
                st.write("❌ **Nicht verbunden**")
                st.caption("Access Token fehlt oder ist ungültig")

                if st.button("⚙️ Zur Konfiguration", key="asana_config_btn", use_container_width=True):
                    st.info("""
**Benötigte Schritte:**
1. Erstellen Sie einen Personal Access Token in Asana:
   https://app.asana.com/0/my-apps
2. Tragen Sie den Token in die .env Datei ein:
   ```
   ASANA_ACCESS_TOKEN=ihr_token
   ```
3. Starten Sie die App neu
                    """)

                asana_status.update(label="✅ **Asana**", state="error")

            else:
                st.write("✅ **Verbunden**")
                try:
                    # Versuche ein einfaches API-Call um Verbindung zu testen
                    projects = cached_get_asana_projects(asana_agent)
                    project_count = len(projects) if projects else 0
                    st.caption(f"{project_count} Projekt(e) verfügbar")
                    asana_status.update(label="✅ **Asana**", state="complete")
                except Exception as e:
                    st.caption(f"⚠️ Fehler: {str(e)[:50]}")
                    asana_status.update(label="✅ **Asana**", state="running")


@st.fragment
def render_dashboard_tab():
    """Rendert den Mein Tag Dashboard-Tab mit optimierter Typografie.

    Performance: @st.fragment isoliert Re-Renders auf diesen Tab.
    """

    # Custom CSS für bessere Lesbarkeit bei langen Texten
    st.markdown("""
    <style>
    /* Verbesserte Lesbarkeit für lange Texte */
    .stExpander {
        border: 1px solid #e0e0e0;
        border-radius: 5px;
        margin-bottom: 10px;
    }

    /* Bessere Schriftgröße und Zeilenabstand */
    .stMarkdown p {
        line-height: 1.6;
        font-size: 1rem;
    }

    /* Scrollbare Text-Bereiche mit sanftem Scrolling */
    textarea[disabled] {
        background-color: #f8f9fa !important;
        color: #333 !important;
        cursor: text !important;
        opacity: 1 !important;
        border: 1px solid #e0e0e0 !important;
    }

    /* Kommentar-Boxen */
    .comment-box {
        background-color: rgba(240, 242, 246, 0.5);
        padding: 10px;
        border-radius: 5px;
        margin-bottom: 10px;
        border-left: 3px solid #4A90E2;
    }

    /* Verbesserte Buttons */
    .stButton button {
        font-weight: 500;
    }

    /* Dokumenten-Suche */
    .search-results {
        margin-top: 15px;
    }
    </style>
    """, unsafe_allow_html=True)

    st.header("📊 Mein Tag - Management Dashboard")

    # ========================================================================
    # VERBINDUNGS-STATUS ANZEIGE
    # ========================================================================
    render_connection_status()

    st.markdown("---")

    # Info-Banner
    st.info("""
    **Willkommen in Ihrer Management-Zentrale!**

    Hier sehen Sie auf einen Blick:
    - 📅 Ihre heutigen Termine (Microsoft Kalender)
    - ✅ Ihre Asana-Aufgaben mit Prioritäten
    - 👤 Intelligente Kontext-Infos zu Gesprächspartnern
    """)

    st.markdown("---")

    # Prüfe ob zur Dashboard-Ansicht zurückgekehrt werden soll
    should_return_to_dashboard = st.session_state.get('return_to_dashboard', False)
    if should_return_to_dashboard:
        # Lösche den Flag für den nächsten Durchlauf
        del st.session_state['return_to_dashboard']

    # Prüfe ob Meeting-Vorbereitung aktiv ist
    # Zeige Meeting-Prep NICHT wenn explizit zur Dashboard zurückgekehrt werden soll
    if 'preparing_event' in st.session_state and not should_return_to_dashboard:
        # Meeting-Vorbereitung Modus
        render_meeting_preparation_view()
    else:
        # Standard Zwei-Spalten-Layout
        col_left, col_right = st.columns([1, 1])

        with col_left:
            render_calendar_section()

        with col_right:
            render_asana_tasks_section()


def render_document_search_section():
    """Rendert die Dokumenten-Suchleiste im Dashboard"""
    st.subheader("🔍 Dokumenten-Suche")

    # Zugriff auf document_tool
    document_tool = st.session_state.orchestrator.document_tool

    # Zeige Anzahl verfügbarer Dokumente
    doc_count = document_tool.count_documents()
    st.caption(f"Durchsuchen Sie {doc_count} Dokument(e) in input_docs/")

    # Suchleiste - Quick Search (ohne Button, sofortige Suche)
    search_query = st.text_input(
        "🔍 Suchbegriff eingeben (Enter drücken)",
        placeholder="z.B. 'KHS', 'Dr. Herbert', 'Gesellschafterliste'...",
        key="dashboard_document_search",
        label_visibility="collapsed"
    )

    # Sofortige Suche wenn Text eingegeben wird
    if search_query:
        if len(search_query.strip()) < 2:
            st.caption("💡 Geben Sie mindestens 2 Zeichen ein um zu suchen")
        else:
            with st.spinner(f"Durchsuche {doc_count} Dokumente..."):
                try:
                    # Suche in allen Dokumenten
                    results = document_tool.search_in_documents(search_query)

                    if results:
                        st.success(f"✅ {len(results)} Ergebnis(se) gefunden")

                        # Zeige Ergebnisse in Expander
                        for i, result in enumerate(results, 1):
                            doc_name = result.get('document', 'Unbekannt')
                            match_type = result.get('match_type', 'content')
                            snippet = result.get('snippet', '')
                            match_count = result.get('match_count', 1)

                            # Icon basierend auf Match-Typ
                            icon = "📄" if match_type == "filename" else "📝"

                            with st.expander(f"{icon} {doc_name} ({match_count} Treffer)", expanded=(i == 1)):
                                # Match-Typ Badge
                                if match_type == "filename":
                                    st.info("📄 **Treffer im Dateinamen**")
                                else:
                                    st.info(f"📝 **Treffer im Inhalt** ({match_count}x)")

                                # Zeige Snippet mit Hervorhebung
                                st.markdown("**Textausschnitt:**")
                                # Versuche den Suchbegriff hervorzuheben
                                highlighted_snippet = snippet
                                if search_query.lower() in snippet.lower():
                                    # Finde Position (case-insensitive)
                                    import re
                                    pattern = re.compile(re.escape(search_query), re.IGNORECASE)
                                    highlighted_snippet = pattern.sub(
                                        lambda m: f"**{m.group(0)}**",
                                        snippet
                                    )

                                st.markdown(highlighted_snippet)

                                # Weitere Treffer anzeigen
                                if result.get('all_matches') and len(result['all_matches']) > 1:
                                    st.markdown("**Weitere Fundstellen:**")
                                    for j, match in enumerate(result['all_matches'][1:], start=2):
                                        match_snippet = match.get('snippet', '')[:150]
                                        st.caption(f"{j}. ...{match_snippet}...")

                                # Button zum vollständigen Dokument
                                if st.button(f"📖 Vollständiges Dokument lesen", key=f"read_doc_{i}"):
                                    full_text = result.get('full_text', '')
                                    if full_text:
                                        st.markdown("---")
                                        st.markdown(f"**Vollständiger Inhalt von {doc_name}:**")
                                        st.text_area(
                                            "Dokument-Inhalt",
                                            full_text,
                                            height=400,
                                            key=f"full_text_{i}"
                                        )
                    else:
                        st.warning(f"❌ Keine Ergebnisse für '{search_query}' gefunden.")
                        st.caption("Tipp: Versuchen Sie andere Suchbegriffe oder prüfen Sie die Schreibweise.")

                except Exception as e:
                    st.error(f"❌ Fehler bei der Suche: {e}")


def convert_to_berlin_time(dt):
    """
    Konvertiert ein datetime-Objekt nach Europe/Berlin Zeitzone

    Args:
        dt: datetime-Objekt (naive oder timezone-aware)

    Returns:
        datetime-Objekt in Europe/Berlin Zeitzone
    """
    if dt is None:
        return None

    # Wenn naive datetime, nehme an dass es UTC ist
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo('UTC'))

    # Konvertiere zu Berlin-Zeit
    return dt.astimezone(ZoneInfo('Europe/Berlin'))


def get_attendee_names(attendees):
    """Extrahiert Teilnehmer-Namen aus verschiedenen Formaten"""
    names = []
    for att in attendees:
        if isinstance(att, str):
            names.append(att)
        elif isinstance(att, dict):
            names.append(att.get('name', att.get('email', 'Unbekannt')))
        else:
            names.append(str(att))
    return names


def render_calendar_section():
    """Rendert die Kalender-Sektion mit Datumsnavigation und Meeting-Vorbereitung"""

    # Initialisiere Session State für ausgewähltes Datum
    if 'dashboard_date' not in st.session_state:
        st.session_state['dashboard_date'] = datetime.now().date()

    # Datumsnavigation
    col1, col2, col3 = st.columns([1, 4, 1])

    with col1:
        if st.button("◀", key="prev_day", help="Vorheriger Tag"):
            st.session_state['dashboard_date'] -= timedelta(days=1)
            st.rerun()

    with col2:
        selected_date = st.session_state['dashboard_date']
        # Zeige Datum mit Wochentag
        weekday_names = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']
        weekday = weekday_names[selected_date.weekday()]

        # Markiere "heute" besonders
        is_today = selected_date == datetime.now().date()
        if is_today:
            st.markdown(f"### 📅 Heute - {weekday}, {selected_date.strftime('%d.%m.%Y')}")
        else:
            st.markdown(f"### 📅 {weekday}, {selected_date.strftime('%d.%m.%Y')}")

    with col3:
        if st.button("▶", key="next_day", help="Nächster Tag"):
            st.session_state['dashboard_date'] += timedelta(days=1)
            st.rerun()

    # Button zum Zurücksetzen auf heute
    if not is_today:
        if st.button("🏠 Zurück zu Heute"):
            st.session_state['dashboard_date'] = datetime.now().date()
            st.rerun()

    # Prüfe ob Microsoft Graph konfiguriert ist
    graph_configured = bool(os.getenv("MICROSOFT_CLIENT_ID")) and bool(os.getenv("MICROSOFT_TENANT_ID"))

    if not graph_configured:
        st.warning("⚙️ **Microsoft Kalender noch nicht verbunden**")
        st.markdown("""
        Um Ihre Outlook-Termine zu sehen, benötigen Sie:
        1. Client-ID von Ihrer IT-Abteilung
        2. Tenant-ID Ihrer Organisation

        Diese können Sie in der Sidebar unter "Microsoft Graph Konfiguration" eintragen.
        """)

    else:
        # Prüfe ob authentifiziert
        outlook_tool = st.session_state.orchestrator.outlook_tool

        if not outlook_tool.is_authenticated():
            st.warning("⚠️ **Microsoft Kalender verbunden, aber nicht authentifiziert**")
            st.markdown("""
Bitte authentifizieren Sie sich in der Sidebar unter "Microsoft Graph API",
um Ihre echten Outlook-Termine zu sehen.
            """)

        else:
            st.success("✅ Microsoft Kalender authentifiziert")

            # WICHTIG: Nur laden wenn User explizit klickt!
            if 'show_calendar_events_dashboard' not in st.session_state:
                st.session_state.show_calendar_events_dashboard = False

            if st.button("🔄 Termine laden", key="load_calendar_dashboard", use_container_width=True):
                st.session_state.show_calendar_events_dashboard = True
                st.rerun()

            if not st.session_state.show_calendar_events_dashboard:
                st.info("💡 Klicke auf 'Termine laden' um deine Events zu sehen")
                return

            with st.spinner("📅 Lade Termine..."):
                try:
                    # Lade Termine für ausgewähltes Datum
                    start_of_day = datetime.combine(selected_date, datetime.min.time())
                    end_of_day = datetime.combine(selected_date, datetime.max.time())
                    events = outlook_tool.get_events_for_date_range(start_of_day, end_of_day)

                    if events:
                        st.info(f"**{len(events)} Termin(e) am {selected_date.strftime('%d.%m.%Y')}**")

                        # Zeige jeden Termin mit Auswahl-Button
                        for idx, event in enumerate(events):
                            # Parse Zeit
                            try:
                                start_dt = event.get('start')
                                end_dt = event.get('end')
                                if isinstance(start_dt, str):
                                    start_dt = datetime.fromisoformat(start_dt.replace('Z', '+00:00'))
                                if isinstance(end_dt, str):
                                    end_dt = datetime.fromisoformat(end_dt.replace('Z', '+00:00'))
                                # Konvertiere zu Berlin-Zeit
                                start_dt = convert_to_berlin_time(start_dt)
                                end_dt = convert_to_berlin_time(end_dt)
                                time_str = f"{start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}"
                            except:
                                time_str = "Zeit nicht verfügbar"

                            # Container für jeden Termin
                            with st.container():
                                col_time, col_select = st.columns([5, 1])

                                with col_time:
                                    st.markdown(f"### {time_str}")
                                    st.write(f"**{event.get('title', 'Ohne Titel')}**")

                                    if event.get('location'):
                                        st.caption(f"📍 {event['location']}")

                                    if event.get('attendees'):
                                        attendee_names = get_attendee_names(event['attendees'])
                                        st.caption(f"👥 Teilnehmer: {', '.join(attendee_names[:3])}" +
                                                 (f" (+{len(attendee_names)-3} weitere)" if len(attendee_names) > 3 else ""))

                                with col_select:
                                    # Button zur Meeting-Vorbereitung
                                    if st.button("📝", key=f"prepare_{idx}", help="Meeting vorbereiten"):
                                        st.session_state['preparing_event'] = event
                                        st.session_state['preparing_event_idx'] = idx
                                        st.rerun()

                                st.markdown("---")

                    else:
                        st.info(f"📭 **Keine Termine am {selected_date.strftime('%d.%m.%Y')}**")

                except Exception as e:
                    st.error(f"❌ **Fehler beim Laden der Termine**")
                    st.caption(f"Details: {str(e)}")


def render_meeting_preparation_view():
    """Rendert die Meeting-Vorbereitungs-Ansicht mit Chat-Interface"""

    event = st.session_state.get('preparing_event')
    if not event:
        st.error("Kein Termin ausgewählt")
        return

    # Header mit Zurück-Button
    col1, col2 = st.columns([5, 1])
    with col1:
        st.header("📝 Meeting-Vorbereitung")
    with col2:
        if st.button("← Zurück"):
            # Cleanup
            if 'preparing_event' in st.session_state:
                del st.session_state['preparing_event']
            if 'preparing_event_idx' in st.session_state:
                del st.session_state['preparing_event_idx']
            if 'preparation_messages' in st.session_state:
                del st.session_state['preparation_messages']
            st.rerun()

    st.markdown("---")

    # Termin-Details anzeigen
    try:
        start_dt = event.get('start')
        end_dt = event.get('end')
        if isinstance(start_dt, str):
            start_dt = datetime.fromisoformat(start_dt.replace('Z', '+00:00'))
        if isinstance(end_dt, str):
            end_dt = datetime.fromisoformat(end_dt.replace('Z', '+00:00'))
        # Konvertiere zu Berlin-Zeit
        start_dt = convert_to_berlin_time(start_dt)
        end_dt = convert_to_berlin_time(end_dt)
        time_str = f"{start_dt.strftime('%d.%m.%Y %H:%M')} - {end_dt.strftime('%H:%M')}"
    except:
        time_str = "Zeit nicht verfügbar"

    st.subheader(f"📅 {event.get('title', 'Ohne Titel')}")
    st.caption(f"🕐 {time_str}")

    if event.get('location'):
        st.caption(f"📍 {event['location']}")

    if event.get('attendees'):
        attendee_names = get_attendee_names(event['attendees'])
        st.caption(f"👥 Teilnehmer: {', '.join(attendee_names)}")

    if event.get('body'):
        with st.expander("📄 Termin-Beschreibung"):
            st.write(event['body'])

    # Zeige Anhänge falls vorhanden
    if event.get('attachments'):
        with st.expander(f"📎 Anhänge ({len(event['attachments'])})"):
            for att in event['attachments']:
                st.text(f"• {att.get('name', 'Unbekannt')}")

    st.markdown("---")

    # Chat-Interface für Vorbereitung
    st.subheader("💬 Assistent zur Meeting-Vorbereitung")
    st.caption("Der Assistent kann Dokumente erstellen und direkt an den Termin anhängen. Sagen Sie z.B. 'Erstelle eine Agenda' oder 'Hänge diese Recherche als Dokument an'.")

    # Asana-Projekt-Auswahl für Kontext (mit automatischer Vorbefüllung)
    st.markdown("---")
    st.caption("📋 **Asana-Kontext (optional):**")

    orch = st.session_state.orchestrator
    asana_agent = orch.asana_agent

    selected_project_gid = None
    selected_project_name = None
    auto_matched_project = None

    if asana_agent.is_connected():
        try:
            projects = asana_agent.list_projects()
            if projects:
                # Automatisches Matching: Suche nach Projekt mit ähnlichem Namen wie Termin
                event_title = event.get('title', '').lower().strip()

                # Finde exakte oder partielle Übereinstimmungen
                for p in projects:
                    project_name_lower = p['name'].lower()
                    # Exakte Übereinstimmung
                    if event_title == project_name_lower:
                        auto_matched_project = p['name']
                        break
                    # Partielle Übereinstimmung (Termin-Titel enthält Projekt-Name oder umgekehrt)
                    elif event_title in project_name_lower or project_name_lower in event_title:
                        # Nur wenn mindestens 5 Zeichen übereinstimmen (zu kurze Matches vermeiden)
                        if len(event_title) >= 5 or len(project_name_lower) >= 5:
                            auto_matched_project = p['name']
                            break

                project_options = ["[Kein Projekt]"] + [p['name'] for p in projects]

                # Bestimme Default-Index
                default_index = 0
                if auto_matched_project:
                    try:
                        default_index = project_options.index(auto_matched_project)
                        st.info(f"✨ Asana-Projekt automatisch erkannt: **{auto_matched_project}**")
                    except ValueError:
                        default_index = 0

                selected_project_name = st.selectbox(
                    "Relevantes Asana-Projekt",
                    project_options,
                    index=default_index,
                    key="prep_asana_project",
                    help="Automatisch vorausgewählt basierend auf Terminnamen. Sie können jederzeit ein anderes Projekt wählen."
                )

                if selected_project_name != "[Kein Projekt]":
                    for p in projects:
                        if p['name'] == selected_project_name:
                            selected_project_gid = p['gid']
                            break
        except:
            pass

    st.markdown("---")

    # ========================================================================
    # AGENDA-ERSTELLUNG WORKFLOW (3 Schritte)
    # ========================================================================

    st.subheader("📝 Agenda erstellen")

    # Initialisiere Session State für Agenda-Workflow
    if 'agenda_workflow_step' not in st.session_state:
        st.session_state['agenda_workflow_step'] = 1
    if 'agenda_sections_loaded' not in st.session_state:
        st.session_state['agenda_sections_loaded'] = False
    if 'agenda_generated_content' not in st.session_state:
        st.session_state['agenda_generated_content'] = ""
    if 'agenda_preview_data' not in st.session_state:
        st.session_state['agenda_preview_data'] = {}

    # WICHTIG: Der folgende Code muss IMMER ausgeführt werden, nicht nur beim ersten Mal!
    # Daher verwenden wir "if True:" als Workaround, um die Einrückung beizubehalten
    if True:
        # ====================================================================
        # TEMPLATE-VERWALTUNG
        # ====================================================================
        with st.expander("📚 Agenda-Vorlagen verwalten", expanded=False):
            st.caption("Erstellen und verwalten Sie wiederverwendbare Agenda-Vorlagen für verschiedene Meeting-Typen.")

            # Lade Templates
            templates = load_agenda_templates()

            # Tabs für Übersicht und Editor
            tab_list, tab_create = st.tabs(["📋 Meine Vorlagen", "➕ Neue Vorlage"])

            with tab_list:
                if templates:
                    st.markdown(f"**{len(templates)} Vorlage(n) verfügbar:**")

                    for idx, template in enumerate(templates):
                        with st.container():
                            col1, col2, col3 = st.columns([3, 1, 1])

                            with col1:
                                st.markdown(f"**{template['name']}**")
                                if template.get('description'):
                                    st.caption(template['description'])
                                st.caption(f"📝 {len(template.get('sections', []))} Abschnitt(e)")

                            with col2:
                                if st.button("✏️ Bearbeiten", key=f"edit_template_{idx}"):
                                    st.session_state['editing_template_idx'] = idx
                                    st.session_state['editing_template'] = template.copy()
                                    st.rerun()

                            with col3:
                                if st.button("🗑️ Löschen", key=f"delete_template_{idx}"):
                                    templates.pop(idx)
                                    save_agenda_templates(templates)
                                    st.success("✅ Vorlage gelöscht")
                                    st.rerun()

                            st.markdown("---")
                else:
                    st.info("📭 Noch keine Vorlagen vorhanden. Erstellen Sie Ihre erste Vorlage im Tab 'Neue Vorlage'!")

            with tab_create:
                # Import datetime für Template-Erstellung
                from datetime import datetime

                # Prüfe ob wir im Edit-Modus sind
                editing_mode = 'editing_template_idx' in st.session_state
                if editing_mode:
                    st.info("✏️ **Bearbeitungsmodus** - Ändern Sie die Vorlage unten")
                    edit_template = st.session_state.get('editing_template', {})
                else:
                    st.markdown("**Erstellen Sie eine neue Agenda-Vorlage:**")
                    edit_template = {}

                # Initialisiere Sections im Session State
                if 'template_sections' not in st.session_state:
                    st.session_state['template_sections'] = edit_template.get('sections', [{'title': '', 'content': ''}])

                # Button zum Hinzufügen weiterer Sections (VOR dem Form!)
                col_add1, col_add2 = st.columns([3, 1])
                with col_add1:
                    if st.button("➕ Weiteren Abschnitt hinzufügen", use_container_width=True, key="add_section_btn"):
                        # Speichere aktuelle Werte bevor neuer Abschnitt hinzugefügt wird
                        for idx in range(len(st.session_state['template_sections'])):
                            if f'section_title_{idx}' in st.session_state:
                                st.session_state['template_sections'][idx]['title'] = st.session_state[f'section_title_{idx}']
                            if f'section_content_{idx}' in st.session_state:
                                st.session_state['template_sections'][idx]['content'] = st.session_state[f'section_content_{idx}']

                        st.session_state['template_sections'].append({'title': '', 'content': ''})
                        st.rerun()

                with col_add2:
                    st.caption(f"📝 {len(st.session_state['template_sections'])} Abschnitt(e)")

                # Form für die Template-Daten
                with st.form(key="template_form", clear_on_submit=False):
                    # Template-Name
                    template_name = st.text_input(
                        "Vorlagen-Name",
                        value=edit_template.get('name', ''),
                        placeholder="z.B. Weekly Team Meeting",
                        key="new_template_name"
                    )

                    # Template-Beschreibung
                    template_desc = st.text_area(
                        "Beschreibung (optional)",
                        value=edit_template.get('description', ''),
                        placeholder="Kurze Beschreibung des Meeting-Typs",
                        height=80,
                        key="new_template_desc"
                    )

                    st.markdown("---")
                    st.markdown("**📝 Tagesordnungspunkte:**")

                    # Sections
                    sections_data = []
                    for idx, section in enumerate(st.session_state['template_sections']):
                        st.markdown(f"**Abschnitt {idx + 1}**")

                        col1, col2 = st.columns([5, 1])

                        with col1:
                            section_title = st.text_input(
                                "Titel",
                                value=section.get('title', ''),
                                placeholder="z.B. Status Updates",
                                key=f"section_title_{idx}",
                                label_visibility="collapsed"
                            )

                            section_content = st.text_area(
                                "Inhalt",
                                value=section.get('content', ''),
                                placeholder="Beschreibung oder Stichpunkte für diesen Abschnitt...",
                                height=100,
                                key=f"section_content_{idx}",
                                label_visibility="collapsed"
                            )

                            sections_data.append({'title': section_title, 'content': section_content})

                        with col2:
                            st.markdown("<br>", unsafe_allow_html=True)
                            # Buttons können nicht in Forms verwendet werden für Delete
                            if len(st.session_state['template_sections']) > 1:
                                st.caption(f"🗑️ Zum Löschen: Außerhalb des Forms")

                        st.markdown("---")

                    st.markdown("---")

                    # Speichern-Button (innerhalb Form)
                    col1, col2 = st.columns(2)

                    with col1:
                        if editing_mode:
                            submit_label = "💾 Änderungen speichern"
                        else:
                            submit_label = "💾 Vorlage speichern"

                        submitted = st.form_submit_button(submit_label, type="primary", use_container_width=True)

                    with col2:
                        # Cancel-Button wird außerhalb des Forms platziert (siehe unten)
                        st.caption("")  # Platzhalter für Ausrichtung

                # Handle Form Submission (außerhalb des Forms!)
                if submitted:
                    # Lese Werte aus Session State (wurden vom Form gesetzt)
                    form_name = st.session_state.get('new_template_name', '').strip()
                    form_desc = st.session_state.get('new_template_desc', '').strip()

                    # Sammle Section-Daten aus Session State
                    form_sections = []
                    for idx in range(len(st.session_state['template_sections'])):
                        section_title = st.session_state.get(f'section_title_{idx}', '').strip()
                        section_content = st.session_state.get(f'section_content_{idx}', '').strip()
                        form_sections.append({'title': section_title, 'content': section_content})

                    if not form_name:
                        st.error("❌ Bitte geben Sie einen Namen ein")
                    else:
                        if editing_mode:
                            # Aktualisiere Template
                            idx = st.session_state['editing_template_idx']
                            templates[idx] = {
                                'id': edit_template.get('id', f"template-{int(time.time())}"),
                                'name': form_name,
                                'description': form_desc,
                                'created': edit_template.get('created', datetime.now().strftime('%Y-%m-%d')),
                                'modified': datetime.now().strftime('%Y-%m-%d %H:%M'),
                                'sections': form_sections
                            }

                            if save_agenda_templates(templates):
                                st.success("✅ Vorlage aktualisiert!")
                                # Cleanup
                                del st.session_state['editing_template_idx']
                                del st.session_state['editing_template']
                                del st.session_state['template_sections']
                                time.sleep(1)
                                st.rerun()
                            else:
                                st.error("❌ Fehler beim Speichern")
                        else:
                            # Erstelle neues Template
                            new_template = {
                                'id': f"template-{int(time.time())}",
                                'name': form_name,
                                'description': form_desc,
                                'created': datetime.now().strftime('%Y-%m-%d'),
                                'sections': form_sections
                            }

                            templates.append(new_template)

                            if save_agenda_templates(templates):
                                st.success("✅ Vorlage gespeichert!")
                                # Cleanup
                                del st.session_state['template_sections']
                                time.sleep(1)
                                st.rerun()
                            else:
                                st.error("❌ Fehler beim Speichern")

                # Cancel Button (außerhalb des Forms im Edit-Modus)
                if editing_mode:
                    if st.button("❌ Bearbeitung abbrechen", use_container_width=True, key="cancel_edit_btn"):
                        del st.session_state['editing_template_idx']
                        del st.session_state['editing_template']
                        del st.session_state['template_sections']
                        st.rerun()

                # Delete-Buttons für Sections (außerhalb des Forms)
                st.markdown("---")
                st.caption("**Abschnitte verwalten:**")
                if len(st.session_state['template_sections']) > 1:
                    cols = st.columns(len(st.session_state['template_sections']))
                    for idx, col in enumerate(cols):
                        with col:
                            if st.button(f"🗑️ Abschnitt {idx+1}", key=f"delete_section_{idx}"):
                                # Speichere alle aktuellen Werte vor dem Löschen
                                for i in range(len(st.session_state['template_sections'])):
                                    if f'section_title_{i}' in st.session_state:
                                        st.session_state['template_sections'][i]['title'] = st.session_state[f'section_title_{i}']
                                    if f'section_content_{i}' in st.session_state:
                                        st.session_state['template_sections'][i]['content'] = st.session_state[f'section_content_{i}']

                                # Lösche den Abschnitt
                                st.session_state['template_sections'].pop(idx)

                                # Räume Session State Keys auf
                                for i in range(len(st.session_state['template_sections']), len(st.session_state['template_sections']) + 5):
                                    if f'section_title_{i}' in st.session_state:
                                        del st.session_state[f'section_title_{i}']
                                    if f'section_content_{i}' in st.session_state:
                                        del st.session_state[f'section_content_{i}']

                                st.rerun()
                else:
                    st.caption("ℹ️ Mindestens ein Abschnitt ist erforderlich.")

        st.markdown("---")

        # ====================================================================
        # SCHRITT 1: Section-Auswahl & Preview (nur wenn Asana verfügbar)
        # ====================================================================
        if selected_project_gid and asana_agent.is_connected():
            with st.expander("📋 Schritt 1: Asana-Kontext wählen (optional)", expanded=(st.session_state['agenda_workflow_step'] == 1)):
                st.caption("Wählen Sie die Asana-Sections aus denen die Agenda-Inhalte geladen werden sollen.")

                # Hole alle Sections des Projekts
                try:
                    all_sections = asana_agent.get_project_sections(selected_project_gid)

                    if all_sections:
                        section_names = [s['name'] for s in all_sections]

                        col1, col2 = st.columns(2)

                        with col1:
                            # Finde Default für Protokolle
                            protokolle_default = 0
                            for i, name in enumerate(section_names):
                                if 'protokoll' in name.lower():
                                    protokolle_default = i
                                    break

                            selected_protokolle_section = st.selectbox(
                                "📋 Section für Protokolle",
                                section_names,
                                index=protokolle_default,
                                key="agenda_protokolle_section",
                                help="Aus diesem Section werden offene Punkte für den Rückblick geladen"
                            )

                        with col2:
                            # Finde Default für Agenda
                            agenda_default = 0
                            for i, name in enumerate(section_names):
                                if 'agenda' in name.lower():
                                    agenda_default = i
                                    break

                            selected_agenda_section = st.selectbox(
                                "📝 Section für Agenda-Themen",
                                section_names,
                                index=agenda_default,
                                key="agenda_agenda_section",
                                help="Aus diesem Section werden neue Themen für die Agenda geladen"
                            )

                        # Button zum Laden des Kontexts
                        if st.button("🔄 Kontext laden", use_container_width=True):
                            print(f"\n{'='*60}")
                            print(f"[AGENDA-WORKFLOW] Button 'Kontext laden' geklickt!")
                            print(f"{'='*60}")

                            with st.spinner("Lade Asana-Daten..."):
                                # Finde Section-GIDs
                                protokolle_section_gid = None
                                agenda_section_gid = None

                                print(f"[AGENDA-WORKFLOW] Suche Section-GIDs...")
                                print(f"[AGENDA-WORKFLOW] Ausgewählt: Protokolle='{selected_protokolle_section}', Agenda='{selected_agenda_section}'")
                                print(f"[AGENDA-WORKFLOW] Verfügbare Sections ({len(all_sections)}):")
                                for section in all_sections:
                                    print(f"[AGENDA-WORKFLOW]   - '{section['name']}' (GID: {section['gid']})")
                                    if section['name'] == selected_protokolle_section:
                                        protokolle_section_gid = section['gid']
                                        print(f"[AGENDA-WORKFLOW]     → MATCH für Protokolle!")
                                    if section['name'] == selected_agenda_section:
                                        agenda_section_gid = section['gid']
                                        print(f"[AGENDA-WORKFLOW]     → MATCH für Agenda!")

                                print(f"[AGENDA-WORKFLOW] Ergebnis:")
                                print(f"[AGENDA-WORKFLOW]   → Protokolle GID: {protokolle_section_gid}")
                                print(f"[AGENDA-WORKFLOW]   → Agenda GID: {agenda_section_gid}")

                                # Debug-Ausgabe
                                st.write(f"🔍 **Debug:**")
                                st.write(f"- Protokoll-Section: `{selected_protokolle_section}` (GID: `{protokolle_section_gid}`)")
                                st.write(f"- Agenda-Section: `{selected_agenda_section}` (GID: `{agenda_section_gid}`)")

                                # Lade Daten
                                preview_data = {
                                    'open_protocols': [],
                                    'agenda_items': [],
                                    'protokolle_section_name': selected_protokolle_section,
                                    'agenda_section_name': selected_agenda_section
                                }

                                # Lade offene Punkte aus Protokollen
                                if protokolle_section_gid:
                                    st.write(f"📋 Lade Protokolle aus Section `{selected_protokolle_section}`...")

                                    # Hole erstmal ALLE Tasks aus dem Protokoll-Section
                                    all_protocol_tasks = asana_agent.get_tasks_from_section(
                                        section_gid=protokolle_section_gid,
                                        limit=50,
                                        include_completed=False
                                    )
                                    st.write(f"   → {len(all_protocol_tasks)} Task(s) im Protokoll-Section gefunden")

                                    # Versuche offene Punkte (Subtasks) zu finden
                                    preview_data['open_protocols'] = asana_agent.find_protocol_tasks_with_open_items(
                                        project_gid=selected_project_gid,
                                        protocol_section_name=selected_protokolle_section
                                    )
                                    st.write(f"   → Davon {len(preview_data['open_protocols'])} mit offenen Subtasks")

                                    # Debug: Zeige die gefundenen Protokoll-Tasks
                                    if all_protocol_tasks:
                                        with st.expander("🔍 Debug: Alle Protokoll-Tasks"):
                                            for task in all_protocol_tasks:
                                                st.caption(f"• {task['name']}")

                                # Lade Agenda-Items
                                if agenda_section_gid:
                                    st.write(f"📝 Lade Agenda-Items aus Section `{selected_agenda_section}`...")
                                    preview_data['agenda_items'] = asana_agent.get_tasks_from_section(
                                        section_gid=agenda_section_gid,
                                        limit=50,
                                        include_completed=False
                                    )
                                    st.write(f"   → Gefunden: {len(preview_data['agenda_items'])} Agenda-Item(s)")

                                    # Debug: Zeige die gefundenen Agenda-Items
                                    if preview_data['agenda_items']:
                                        with st.expander("🔍 Debug: Alle Agenda-Items"):
                                            for item in preview_data['agenda_items']:
                                                st.caption(f"• {item['name']}")
                                    else:
                                        st.warning("⚠️ Keine Agenda-Items gefunden. Prüfen Sie:")
                                        st.caption("1. Sind Tasks im Asana-Section vorhanden?")
                                        st.caption("2. Sind die Tasks als 'erledigt' markiert? (werden ausgefiltert)")
                                        st.caption("3. Ist der richtige Section ausgewählt?")

                                st.session_state['agenda_preview_data'] = preview_data
                                st.session_state['agenda_sections_loaded'] = True
                                st.session_state['agenda_workflow_step'] = 2
                                st.rerun()

                        # Zeige Preview wenn Daten geladen
                        if st.session_state['agenda_sections_loaded']:
                            st.success("✅ Kontext erfolgreich geladen!")

                            preview = st.session_state['agenda_preview_data']

                            # Zähle Items
                            open_points_count = sum(len(p['open_items']) for p in preview['open_protocols'])
                            agenda_items_count = len(preview['agenda_items'])

                            st.info(f"**Gefunden:** {open_points_count} offene Punkte, {agenda_items_count} neue Themen")

                            # Zeige Details in Expander
                            with st.expander("🔍 Details anzeigen"):
                                if preview['open_protocols']:
                                    st.markdown("**📋 Offene Punkte aus Protokollen:**")
                                    for protocol in preview['open_protocols']:
                                        st.caption(f"• {protocol['protocol_name']}: {len(protocol['open_items'])} Punkte")
                                else:
                                    st.caption("Keine offenen Punkte gefunden")

                                st.markdown("---")

                                if preview['agenda_items']:
                                    st.markdown("**📝 Neue Agenda-Themen:**")
                                    for item in preview['agenda_items']:
                                        st.caption(f"• {item['name']}")
                                else:
                                    st.caption("Keine Agenda-Themen gefunden")

                    else:
                        st.warning("Keine Sections in diesem Projekt gefunden.")

                except Exception as e:
                    st.error(f"Fehler beim Laden der Sections: {e}")
        else:
            # Kein Asana-Projekt ausgewählt
            st.info("💡 **Hinweis:** Kein Asana-Projekt ausgewählt. Sie können trotzdem eine Agenda aus einer Vorlage erstellen (siehe Schritt 2).")

        # ====================================================================
        # SCHRITT 2: Agenda generieren & bearbeiten
        # ====================================================================
        with st.expander("✏️ Schritt 2: Agenda generieren & bearbeiten", expanded=(st.session_state['agenda_workflow_step'] == 2)):
            st.caption("Generieren Sie die Agenda mit den geladenen Daten oder verwenden Sie eine Vorlage.")

            # ----------------------------------------------------------------
            # TEMPLATE-AUSWAHL
            # ----------------------------------------------------------------
            templates = load_agenda_templates()

            use_template = st.checkbox(
                "📚 Vorlage verwenden",
                value=False,
                key="use_template_checkbox",
                help="Verwenden Sie eine vordefinierte Vorlage als Basis für die Agenda"
            )

            selected_template = None
            combine_with_asana = False

            if use_template:
                if templates:
                    template_options = ["[Keine Vorlage]"] + [t['name'] for t in templates]
                    selected_template_name = st.selectbox(
                        "Vorlage auswählen",
                        template_options,
                        key="selected_template"
                    )

                    if selected_template_name != "[Keine Vorlage]":
                        # Finde Template
                        for t in templates:
                            if t['name'] == selected_template_name:
                                selected_template = t
                                break

                        # Zeige Template-Info
                        if selected_template:
                            st.info(f"📋 **{selected_template['name']}**")
                            if selected_template.get('description'):
                                st.caption(selected_template['description'])
                            st.caption(f"📝 {len(selected_template.get('sections', []))} Abschnitt(e)")

                            # Option: Mit Asana-Daten kombinieren
                            if st.session_state['agenda_sections_loaded']:
                                combine_with_asana = st.checkbox(
                                    "🔄 Mit Asana-Daten kombinieren",
                                    value=False,
                                    key="combine_with_asana_checkbox",
                                    help="Fügt Asana-Rückblick und Agenda-Items zur Vorlage hinzu"
                                )
                else:
                    st.warning("⚠️ Noch keine Vorlagen vorhanden. Erstellen Sie zuerst eine Vorlage oben unter '📚 Agenda-Vorlagen verwalten'.")

            st.markdown("---")

            # ----------------------------------------------------------------
            # GENERIERUNGS-BUTTONS
            # ----------------------------------------------------------------
            if not st.session_state['agenda_sections_loaded'] and not use_template:
                st.info("👆 Bitte zuerst Schritt 1 abschließen oder eine Vorlage auswählen")
            else:
                col1, col2 = st.columns(2)

                with col1:
                    # Button für Template-basierte Generierung
                    if use_template and selected_template:
                        if st.button("🔄 Aus Vorlage generieren", use_container_width=True, type="primary"):
                            with st.spinner("Generiere Agenda aus Vorlage..."):
                                event = st.session_state.get('preparing_event')
                                meeting_title = event.get('title', 'Meeting') if event else 'Meeting'

                                # Hole Asana-Daten falls kombiniert werden soll
                                asana_data = None
                                if combine_with_asana and st.session_state.get('agenda_preview_data'):
                                    asana_data = st.session_state['agenda_preview_data']

                                # Generiere Agenda aus Template
                                agenda_content = create_agenda_from_template(
                                    template=selected_template,
                                    meeting_title=meeting_title,
                                    asana_data=asana_data,
                                    combine_with_asana=combine_with_asana
                                )

                                st.session_state['agenda_generated_content'] = agenda_content
                                st.session_state['agenda_workflow_step'] = 3
                                st.success("✅ Agenda aus Vorlage generiert!")
                                time.sleep(1)
                                st.rerun()

                with col2:
                    # Button für Asana-basierte Generierung (nur wenn Daten geladen)
                    if st.session_state['agenda_sections_loaded'] and not use_template:
                        if st.button("🔄 Aus Asana generieren", use_container_width=True, type="primary"):
                            with st.spinner("Generiere Agenda aus Asana..."):
                                # Generiere Agenda mit den Session-Daten
                                event = st.session_state.get('preparing_event')
                                meeting_title = event.get('title', 'Meeting') if event else 'Meeting'

                                # Generiere Agenda-Content manuell (ohne die Function zu nutzen, da wir eigene Section-Namen haben)
                                from datetime import datetime
                                date_str = datetime.now().strftime("%d.%m.%Y")

                                agenda_content = f"""# Agenda: {meeting_title}
**Datum:** {date_str}

⚠️ **Keine Besprechung ohne Protokoll - Aufzeichnung aktivieren!**

---

"""

                                preview = st.session_state['agenda_preview_data']

                                # TOP-Counter für Nummerierung
                                top_number = 1

                                # 1. Rückblick auf offene Punkte (ALLE in einem Block) - als TOP 1
                                if preview['open_protocols']:
                                    agenda_content += f"""## TOP {top_number}: Rückblick - Offene Punkte aus vorherigen Besprechungen

"""
                                    for protocol in preview['open_protocols']:
                                        protocol_name_short = protocol['protocol_name'].replace('📄 Protokoll ', '')
                                        for item in protocol['open_items']:
                                            assignee_str = f" - Zuständig: {item['assignee']}" if item['assignee'] else ""
                                            due_str = f" - Fällig: {item['due_on']}" if item['due_on'] else ""
                                            agenda_content += f"""- [ ] {item['name']}{assignee_str}{due_str} *(aus {protocol_name_short})*
"""

                                    agenda_content += "\n---\n\n"
                                    top_number += 1

                                # 2. Tagesordnungspunkte aus Agenda-Section - mit TOP-Nummerierung
                                if preview['agenda_items']:
                                    agenda_content += """## 📝 Tagesordnungspunkte

"""
                                    for item in preview['agenda_items']:
                                        assignee_str = f" (Themenverantwortlich: {item['assignee']})" if item['assignee'] else ""
                                        agenda_content += f"""### TOP {top_number}: {item['name']}{assignee_str}
"""
                                        if item['notes']:
                                            agenda_content += f"""{item['notes']}

"""
                                        agenda_content += "\n"
                                        top_number += 1

                                    agenda_content += "---\n\n"
                                else:
                                    agenda_content += """## 📝 Tagesordnungspunkte

*Noch keine Tagesordnungspunkte im Asana-Board hinterlegt.*

---

"""

                                # 3. Standardabschnitte
                                agenda_content += """## 💬 Diskussion & Entscheidungen

*Notizen während des Meetings:*
-

---

## ✅ Weitere Schritte & Aufgaben

*Neue Aufgaben aus diesem Meeting:*
-

---

## 📅 Nächstes Meeting

**Termin:**
**Themen:**

"""

                                st.session_state['agenda_generated_content'] = agenda_content
                                st.session_state['agenda_workflow_step'] = 3
                                st.success("✅ Agenda aus Asana generiert!")
                                time.sleep(1)
                                st.rerun()

            # Zeige bearbeitbare Agenda wenn generiert (außerhalb des if/else-Blocks!)
            if st.session_state['agenda_generated_content']:
                st.markdown("**📄 Generierte Agenda** (vollständig bearbeitbar):")
                st.info("💡 **Tipp:** Sie können den Text direkt im Feld unten bearbeiten. Änderungen werden automatisch gespeichert.")

                edited_content = st.text_area(
                    "Agenda bearbeiten",
                    value=st.session_state['agenda_generated_content'],
                    height=500,
                    key="agenda_editor",
                    label_visibility="collapsed",
                    help="Bearbeiten Sie die Agenda nach Bedarf. Änderungen werden automatisch übernommen."
                )

                # Speichere Änderungen
                st.session_state['agenda_generated_content'] = edited_content

                # Zeichenzähler
                char_count = len(edited_content)
                st.caption(f"📊 {char_count:,} Zeichen | ✏️ Änderungen werden automatisch gespeichert")

        # ====================================================================
        # SCHRITT 3: Speichern & Anhängen
        # ====================================================================
        with st.expander("💾 Schritt 3: Speichern & an Termin anhängen", expanded=(st.session_state['agenda_workflow_step'] == 3)):
            if not st.session_state['agenda_generated_content']:
                st.info("👆 Bitte zuerst Schritt 2 abschließen")
            else:
                st.caption("Speichern Sie die finale Agenda und hängen Sie sie als PDF an den Outlook-Termin an.")

                # Preview der Agenda
                with st.expander("📄 Agenda-Vorschau"):
                    st.markdown(st.session_state['agenda_generated_content'])

                # Button zum Speichern & Anhängen
                if st.button("✅ Agenda speichern & an Termin anhängen", type="primary", use_container_width=True):
                    with st.spinner("Speichere und hänge Agenda an..."):
                        try:
                            from pathlib import Path
                            from datetime import datetime

                            # Hole benötigte Tools
                            orch = st.session_state.orchestrator
                            outlook_tool = orch.outlook_tool

                            # Speichere Dokument
                            prep_dir = _get_user_ctx().meeting_prep
                            prep_dir.mkdir(parents=True, exist_ok=True)

                            event = st.session_state.get('preparing_event')
                            event_title = event.get('title', 'Meeting') if event else 'Meeting'

                            safe_title = event_title.replace(' ', '_').replace('/', '-')[:50]
                            date_str = datetime.now().strftime("%Y-%m-%d_%H%M")
                            filename = f"{date_str}_Agenda_{safe_title}.md"
                            filepath = prep_dir / filename

                            with open(filepath, 'w', encoding='utf-8') as f:
                                f.write(st.session_state['agenda_generated_content'])

                            # Konvertiere zu PDF
                            pdf_filename = filename.replace('.md', '.pdf')
                            pdf_path = prep_dir / pdf_filename

                            if convert_markdown_to_pdf(filepath, pdf_path):
                                # Speichere zusätzlich in data/agendas/
                                agenda_dir = _get_user_ctx().data_dir / "agendas"
                                agenda_dir.mkdir(parents=True, exist_ok=True)

                                # Datum aus Termin
                                event_date_str = datetime.now().strftime("%Y-%m-%d")
                                if event:
                                    event_start = event.get('start')
                                    if event_start:
                                        try:
                                            if isinstance(event_start, str):
                                                event_start_dt = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
                                            else:
                                                event_start_dt = event_start
                                            event_date_str = event_start_dt.strftime("%Y-%m-%d")
                                        except:
                                            pass

                                sanitized_title = sanitize_filename(event_title, max_length=50)
                                agenda_filename = f"Agenda_{event_date_str}_{sanitized_title}.pdf"
                                agenda_path = agenda_dir / agenda_filename

                                # Kopiere PDF
                                import shutil
                                shutil.copy2(pdf_path, agenda_path)

                                # Hänge an Termin an
                                event_id = event.get('id') if event else None

                                if event_id and outlook_tool.is_authenticated():
                                    result = outlook_tool.add_attachment_to_event(
                                        event_id=event_id,
                                        file_path=str(pdf_path),
                                        file_name=pdf_filename
                                    )

                                    if result.get('success'):
                                        st.success(f"✅ Agenda erfolgreich erstellt und angehängt!")
                                        st.info(f"📁 Gespeichert in: `data/agendas/{agenda_filename}`")

                                        # Automatisch zur Terminübersicht zurückkehren
                                        st.info("🔄 Kehre automatisch zur Terminübersicht zurück...")
                                        time.sleep(2)

                                        # Cleanup - lösche alle Meeting-Prep Variablen
                                        for key in ['preparing_event', 'preparing_event_idx', 'preparation_messages',
                                                   'agenda_sections_loaded', 'agenda_preview_data', 'agenda_workflow_step',
                                                   'agenda_generated_content', 'last_preparing_event_id']:
                                            if key in st.session_state:
                                                del st.session_state[key]

                                        st.rerun()
                                    else:
                                        st.error(f"❌ Fehler beim Anhängen: {result.get('error')}")
                                else:
                                    st.error("❌ Termin-ID nicht verfügbar oder Outlook nicht authentifiziert")
                            else:
                                st.error("❌ PDF-Konvertierung fehlgeschlagen")

                        except Exception as e:
                            st.error(f"❌ Fehler: {e}")
                            import traceback
                            with st.expander("Debug Info"):
                                st.code(traceback.format_exc())

        st.markdown("---")

    # Relevante E-Mails anzeigen
    st.caption("📧 **Relevante E-Mails:**")

    outlook_tool = orch.outlook_tool
    if outlook_tool.is_authenticated():
        try:
            # Suche E-Mails basierend auf Termintitel
            event_title = event.get('title', '')
            if event_title and len(event_title) >= 3:
                emails = outlook_tool.search_emails(
                    search_query=event_title,
                    max_results=5,
                    days_back=60
                )

                if emails:
                    with st.expander(f"📬 {len(emails)} E-Mail(s) gefunden", expanded=False):
                        for email in emails:
                            st.markdown(f"**{email['subject']}**")
                            st.caption(f"Von: {email['from']} | {email['received']}")
                            st.text(email['preview'])
                            if email['web_link']:
                                st.markdown(f"[In Outlook öffnen]({email['web_link']})")
                            st.markdown("---")
                else:
                    st.caption("ℹ️ Keine relevanten E-Mails gefunden")
            else:
                st.caption("ℹ️ Termintitel zu kurz für E-Mail-Suche")
        except Exception as e:
            st.caption(f"⚠️ E-Mail-Suche nicht verfügbar: {str(e)}")
    else:
        st.caption("ℹ️ Outlook nicht authentifiziert - E-Mail-Suche nicht verfügbar")

    st.markdown("---")

    # Initialisiere Nachrichten für diese Vorbereitung
    if 'preparation_messages' not in st.session_state:
        st.session_state['preparation_messages'] = []

        # System-Prompt mit Kontext und Tool-Instruktionen
        attendee_list = ', '.join(get_attendee_names(event.get('attendees', []))) if event.get('attendees') else 'Keine Teilnehmer'

        asana_context = ""
        if selected_project_gid:
            asana_context = f"\n\n**Asana-Kontext:** Projekt '{selected_project_name}' ist ausgewählt. Du kannst Aufgaben aus diesem Projekt abfragen."

        system_context = f"""Du bist ein Meeting-Vorbereitungs-Assistent für folgendes Meeting:

**Titel:** {event.get('title', 'Unbekannt')}
**Zeit:** {time_str}
**Ort:** {event.get('location', 'Nicht angegeben')}
**Teilnehmer:** {attendee_list}{asana_context}

Du hast Zugriff auf spezielle Funktionen:
- Du kannst Dokumente erstellen und DIREKT an den Outlook-Termin anhängen
- Du kannst mehrere separate Dokumente erstellen (z.B. Agenda, Recherche, Notizen)
- Du kannst Asana-Projekte und Aufgaben abfragen
- Du kannst INTELLIGENTE AGENDAS mit Asana-Kontext erstellen

ASANA-TOOLS RICHTIG VERWENDEN:
- generate_asana_agenda(project_name): **NEU!** Erstellt automatisch eine Agenda mit:
  * Rückblick auf offene Punkte aus alten Protokollen
  * Tagesordnungspunkten aus dem "Agenda"-Section des Boards
  * Professioneller Struktur mit allen Standard-Abschnitten
  → NUTZE DIES IMMER wenn der Nutzer eine Agenda für ein Meeting mit Asana-Board möchte!
- get_asana_project_tasks(project_name): Nutze dies wenn der Nutzer nach Aufgaben für ein BESTIMMTES PROJEKT/BOARD fragt
- get_my_asana_tasks(): Nutze dies NUR wenn der Nutzer explizit nach ALLEN seinen eigenen Aufgaben fragt (z.B. "Was sind meine Aufgaben?")
- list_asana_projects(): Nutze dies nur wenn der Nutzer eine Liste aller Projekte sehen möchte

WORKFLOW FÜR AGENDA-ERSTELLUNG:
1. Wenn Nutzer nach Agenda fragt UND ein Asana-Projekt verknüpft ist:
   → Verwende generate_asana_agenda() für intelligente Agenda mit vollem Kontext
2. Dann verwende create_and_attach_document() um die Agenda anzuhängen
3. Falls kein Asana-Projekt: Erstelle manuelle Agenda mit create_and_attach_document()

WICHTIG:
- Erstelle Dokumente NUR wenn der Nutzer danach fragt
- Nutze die Funktion proaktiv sobald der Nutzer einen Wunsch äußert
- Gib dem Dokument einen aussagekräftigen Titel
- Formatiere den Inhalt professionell in Markdown
- Bei Fragen nach "Aufgaben für Board X" oder "Tasks im Projekt Y" → verwende get_asana_project_tasks()

AGENDA-ERSTELLUNG (PFLICHT):
- JEDE Agenda MUSS ganz oben mit diesem Hinweis beginnen:
  "⚠️ **Keine Besprechung ohne Protokoll - Aufzeichnung aktivieren!**"
- Dieser Hinweis erscheint VOR allen anderen Inhalten

Deine Aufgaben:
- Meeting-Agenden erstellen (intelligente Agendas mit Asana-Kontext bevorzugen!)
- Themen recherchieren
- Informationen zu Teilnehmern bereitstellen
- Asana-Aufgaben für das Meeting identifizieren
- Dokumente aus dem Gespräch erstellen und anhängen"""

        st.session_state['preparation_messages'].append({
            'role': 'system',
            'content': system_context
        })

    # Speichere ausgewähltes Projekt in Session State und aktualisiere System-Prompt
    if selected_project_gid:
        # Prüfe ob sich das Projekt geändert hat
        if (st.session_state.get('prep_selected_project_gid') != selected_project_gid and
            'preparation_messages' in st.session_state and
            len(st.session_state['preparation_messages']) > 0):
            # Aktualisiere System-Nachricht mit neuem Projekt
            attendee_list = ', '.join(get_attendee_names(event.get('attendees', []))) if event.get('attendees') else 'Keine Teilnehmer'
            asana_context = f"\n\n**Asana-Kontext:** Projekt '{selected_project_name}' ist jetzt ausgewählt. Bei Fragen zu 'diesem Projekt' oder 'diesem Board' beziehst du dich auf '{selected_project_name}'."

            system_context = f"""Du bist ein Meeting-Vorbereitungs-Assistent für folgendes Meeting:

**Titel:** {event.get('title', 'Unbekannt')}
**Zeit:** {time_str}
**Ort:** {event.get('location', 'Nicht angegeben')}
**Teilnehmer:** {attendee_list}{asana_context}

Du hast Zugriff auf spezielle Funktionen:
- Du kannst Dokumente erstellen und DIREKT an den Outlook-Termin anhängen
- Du kannst mehrere separate Dokumente erstellen (z.B. Agenda, Recherche, Notizen)
- Du kannst Asana-Projekte und Aufgaben abfragen
- Du kannst INTELLIGENTE AGENDAS mit Asana-Kontext erstellen

ASANA-TOOLS RICHTIG VERWENDEN:
- generate_asana_agenda(project_name): **NEU!** Erstellt automatisch eine Agenda mit:
  * Rückblick auf offene Punkte aus alten Protokollen
  * Tagesordnungspunkten aus dem "Agenda"-Section des Boards
  * Professioneller Struktur mit allen Standard-Abschnitten
  → NUTZE DIES IMMER wenn der Nutzer eine Agenda für ein Meeting mit Asana-Board möchte!
- get_asana_project_tasks(project_name): Nutze dies wenn der Nutzer nach Aufgaben für ein BESTIMMTES PROJEKT/BOARD fragt
- get_my_asana_tasks(): Nutze dies NUR wenn der Nutzer explizit nach ALLEN seinen eigenen Aufgaben fragt (z.B. "Was sind meine Aufgaben?")
- list_asana_projects(): Nutze dies nur wenn der Nutzer eine Liste aller Projekte sehen möchte

WORKFLOW FÜR AGENDA-ERSTELLUNG:
1. Wenn Nutzer nach Agenda fragt UND ein Asana-Projekt verknüpft ist:
   → Verwende generate_asana_agenda() für intelligente Agenda mit vollem Kontext
2. Dann verwende create_and_attach_document() um die Agenda anzuhängen
3. Falls kein Asana-Projekt: Erstelle manuelle Agenda mit create_and_attach_document()

WICHTIG:
- Erstelle Dokumente NUR wenn der Nutzer danach fragt
- Nutze die Funktion proaktiv sobald der Nutzer einen Wunsch äußert
- Gib dem Dokument einen aussagekräftigen Titel
- Formatiere den Inhalt professionell in Markdown
- Bei Fragen nach "Aufgaben für Board X" oder "Tasks im Projekt Y" → verwende get_asana_project_tasks()

AGENDA-ERSTELLUNG (PFLICHT):
- JEDE Agenda MUSS ganz oben mit diesem Hinweis beginnen:
  "⚠️ **Keine Besprechung ohne Protokoll - Aufzeichnung aktivieren!**"
- Dieser Hinweis erscheint VOR allen anderen Inhalten

Deine Aufgaben:
- Meeting-Agenden erstellen (intelligente Agendas mit Asana-Kontext bevorzugen!)
- Themen recherchieren
- Informationen zu Teilnehmern bereitstellen
- Asana-Aufgaben für das Meeting identifizieren
- Dokumente aus dem Gespräch erstellen und anhängen"""

            # Ersetze System-Nachricht
            st.session_state['preparation_messages'][0] = {
                'role': 'system',
                'content': system_context
            }

        st.session_state['prep_selected_project_gid'] = selected_project_gid
        st.session_state['prep_selected_project_name'] = selected_project_name

    # Zeige Chat-Historie
    for msg in st.session_state['preparation_messages']:
        if msg['role'] == 'system':
            continue  # System-Nachrichten nicht anzeigen
        elif msg['role'] == 'user':
            with st.chat_message("user"):
                st.write(msg['content'])
        elif msg['role'] == 'assistant':
            with st.chat_message("assistant"):
                if msg['content']:
                    st.write(msg['content'])
                # Zeige Tool-Calls falls vorhanden
                if 'tool_calls' in msg and msg['tool_calls']:
                    for tool_call in msg['tool_calls']:
                        tool_name = tool_call.get('name', 'tool')
                        # Freundlichere Namen
                        display_names = {
                            'create_and_attach_document': '📄 Erstelle Dokument',
                            'list_asana_projects': '📁 Liste Asana-Projekte',
                            'get_asana_project_tasks': '📋 Hole Projekt-Aufgaben',
                            'get_my_asana_tasks': '✅ Hole meine Aufgaben'
                        }
                        display_name = display_names.get(tool_name, f"🔧 {tool_name}")
                        st.caption(f"{display_name}")
        elif msg['role'] == 'tool':
            with st.chat_message("assistant"):
                # Zeige Tool-Ergebnis
                st.info(msg['content'])

    # Prüfe ob die letzte Nachricht eine unverarbeitete User-Nachricht ist
    # (passiert wenn ein Button geklickt wurde)
    needs_processing = False
    if len(st.session_state['preparation_messages']) > 0:
        last_msg = st.session_state['preparation_messages'][-1]
        if last_msg['role'] == 'user':
            # Letzte Nachricht ist vom User und wurde noch nicht verarbeitet
            needs_processing = True
            user_input = last_msg['content']
        else:
            user_input = None
    else:
        user_input = None

    # Chat-Eingabe (überschreibt user_input wenn etwas eingegeben wurde)
    chat_input = st.chat_input("Ihre Nachricht für die Meeting-Vorbereitung...")

    if chat_input:
        user_input = chat_input
        needs_processing = False  # Neue Nachricht, wird unten hinzugefügt

    if user_input and not needs_processing:
        # Füge Projekt-Kontext zur Nachricht hinzu, wenn ausgewählt
        enhanced_input = user_input
        if selected_project_gid and selected_project_name:
            # Prüfe ob der Nutzer sich auf "dieses Projekt" oder "dieses Board" bezieht
            if any(keyword in user_input.lower() for keyword in ['dieses projekt', 'dieses board', 'diesem projekt', 'diesem board', 'das projekt', 'dem board']):
                enhanced_input = f"{user_input}\n\n[KONTEXT: Der Nutzer bezieht sich auf das aktuell ausgewählte Asana-Projekt '{selected_project_name}' (GID: {selected_project_gid}). Verwende get_asana_project_tasks('{selected_project_name}') um die Aufgaben abzurufen.]"

        # Nutzer-Nachricht hinzufügen
        st.session_state['preparation_messages'].append({
            'role': 'user',
            'content': enhanced_input
        })

    # Verarbeite Nachricht (sowohl neue als auch unverarbeitete vom Button)
    if user_input:
        # Orchestrator für Antwort nutzen
        with st.status("🤖 Bereite Antwort vor...", expanded=True) as status:
            try:
                orch = st.session_state.orchestrator
                research_agent = orch.research_agent
                outlook_tool = orch.outlook_tool

                # Definiere Tool für Dokument-Erstellung
                from langchain_core.tools import tool
                from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage

                @tool
                def create_and_attach_document(title: str, content: str) -> str:
                    """Erstellt ein Dokument und hängt es an den Outlook-Termin an.

                    Args:
                        title: Titel des Dokuments (z.B. 'Agenda', 'Recherche-Ergebnisse')
                        content: Inhalt des Dokuments in Markdown-Format

                    Returns:
                        Erfolgsmeldung oder Fehlermeldung
                    """
                    try:
                        # Speichere Dokument
                        prep_dir = _get_user_ctx().meeting_prep
                        prep_dir.mkdir(parents=True, exist_ok=True)

                        safe_title = title.replace(' ', '_').replace('/', '-')[:50]
                        date_str = datetime.now().strftime("%Y-%m-%d_%H%M")
                        filename = f"{date_str}_{safe_title}.md"
                        filepath = prep_dir / filename

                        with open(filepath, 'w', encoding='utf-8') as f:
                            f.write(content)

                        # Konvertiere zu PDF
                        pdf_filename = filename.replace('.md', '.pdf')
                        pdf_path = prep_dir / pdf_filename

                        if not convert_markdown_to_pdf(filepath, pdf_path):
                            return f"❌ Fehler: PDF-Konvertierung fehlgeschlagen"

                        # Wenn es eine Agenda ist, speichere zusätzlich in data/agendas/
                        is_agenda = 'agenda' in title.lower()
                        if is_agenda:
                            # Erstelle Agenda-Ordner falls nicht vorhanden
                            agenda_dir = _get_user_ctx().data_dir / "agendas"
                            agenda_dir.mkdir(parents=True, exist_ok=True)

                            # Erstelle standardisierten Dateinamen
                            event = st.session_state.get('preparing_event')
                            event_title = event.get('title', 'Meeting') if event else 'Meeting'

                            # Sanitize Title
                            sanitized_title = sanitize_filename(event_title, max_length=50)

                            # Datum aus Termin extrahieren
                            event_date_str = datetime.now().strftime("%Y-%m-%d")
                            if event:
                                event_start = event.get('start')
                                if event_start:
                                    try:
                                        if isinstance(event_start, str):
                                            event_start_dt = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
                                        else:
                                            event_start_dt = event_start
                                        event_date_str = event_start_dt.strftime("%Y-%m-%d")
                                    except:
                                        pass

                            # Erstelle standardisierten Agenda-Dateinamen
                            agenda_filename = f"Agenda_{event_date_str}_{sanitized_title}.pdf"
                            agenda_path = agenda_dir / agenda_filename

                            # Kopiere PDF
                            import shutil
                            shutil.copy2(pdf_path, agenda_path)

                        # Hänge an Termin an
                        event = st.session_state.get('preparing_event')
                        event_id = event.get('id') if event else None

                        if not event_id:
                            return f"❌ Fehler: Termin-ID nicht verfügbar"

                        if not outlook_tool.is_authenticated():
                            return f"❌ Fehler: Outlook nicht authentifiziert"

                        result = outlook_tool.add_attachment_to_event(
                            event_id=event_id,
                            file_path=str(pdf_path),
                            file_name=pdf_filename
                        )

                        if result.get('success'):
                            msg = f"✅ Dokument '{title}' erfolgreich als '{pdf_filename}' an den Termin angehängt!"
                            if is_agenda:
                                msg += f"\n📁 Agenda zusätzlich gespeichert in: data/agendas/{agenda_filename}"
                            return msg
                        else:
                            return f"❌ Fehler beim Anhängen: {result.get('error')}"

                    except Exception as e:
                        return f"❌ Fehler: {str(e)}"

                @tool
                def list_asana_projects() -> str:
                    """Liste alle Asana-Projekte im Workspace auf.

                    Returns:
                        Formatierte Liste der Projekte
                    """
                    try:
                        projects = asana_agent.list_projects()
                        if not projects:
                            return "Keine Projekte gefunden."

                        result = "📁 **Asana-Projekte:**\n\n"
                        for proj in projects:
                            result += f"- {proj['name']}\n"
                        return result
                    except Exception as e:
                        return f"Fehler: {str(e)}"

                @tool
                def get_asana_project_tasks(project_name: str) -> str:
                    """Ruft alle Aufgaben eines bestimmten Asana-Projekts ab.

                    Args:
                        project_name: Name des Projekts (oder Teil davon)

                    Returns:
                        Formatierte Liste der Aufgaben im Projekt
                    """
                    try:
                        # Finde Projekt-GID
                        projects = asana_agent.list_projects()
                        project_gid = None
                        matched_project_name = None
                        for proj in projects:
                            if project_name.lower() in proj['name'].lower():
                                project_gid = proj['gid']
                                matched_project_name = proj['name']
                                break

                        if not project_gid:
                            return f"Projekt '{project_name}' nicht gefunden. Verfügbare Projekte mit list_asana_projects() abfragen."

                        tasks = asana_agent.get_project_tasks(project_gid, limit=100)
                        if not tasks:
                            return f"Keine Aufgaben im Projekt '{matched_project_name}' gefunden."

                        # Gruppiere nach Status
                        open_tasks = [t for t in tasks if not t.get('completed')]
                        completed_tasks = [t for t in tasks if t.get('completed')]

                        result = f"📋 **Aufgaben im Projekt '{matched_project_name}':**\n\n"
                        result += f"**Offene Aufgaben ({len(open_tasks)}):**\n"
                        for task in open_tasks[:20]:
                            name = task.get('name', 'Unbenannt')
                            due_on = task.get('due_on', 'Kein Datum')
                            result += f"- {name} (Fällig: {due_on})\n"

                        if len(open_tasks) > 20:
                            result += f"... und {len(open_tasks) - 20} weitere\n"

                        return result
                    except Exception as e:
                        return f"Fehler: {str(e)}"

                @tool
                def generate_asana_agenda(project_name: str, meeting_description: str = "") -> str:
                    """Generiert eine Agenda mit Kontext aus dem Asana-Projekt.

                    Inkludiert:
                    - Rückblick auf offene Punkte aus vorherigen Protokollen
                    - Tagesordnungspunkte aus dem "Agenda"-Section des Projekts
                    - Standardabschnitte für Diskussion, neue Aufgaben, etc.

                    Args:
                        project_name: Name des Asana-Projekts
                        meeting_description: Optionale zusätzliche Beschreibung/Kontext

                    Returns:
                        Vollständige Agenda als Markdown-String zum Anhängen mit create_and_attach_document
                    """
                    try:
                        # Finde Projekt-GID
                        projects = asana_agent.list_projects()
                        project_gid = None
                        matched_project_name = None

                        for proj in projects:
                            if project_name.lower() in proj['name'].lower():
                                project_gid = proj['gid']
                                matched_project_name = proj['name']
                                break

                        if not project_gid:
                            return f"❌ Projekt '{project_name}' nicht gefunden. Bitte verwende list_asana_projects() um verfügbare Projekte zu sehen."

                        # Hole Event-Titel aus Session State
                        event = st.session_state.get('preparing_event')
                        meeting_title = event.get('title', 'Meeting') if event else 'Meeting'

                        # Generiere Agenda mit Asana-Kontext
                        agenda_content = generate_agenda_with_asana_context(
                            asana_agent=asana_agent,
                            project_gid=project_gid,
                            meeting_title=meeting_title,
                            meeting_description=meeting_description
                        )

                        return f"""✅ Agenda generiert für Projekt '{matched_project_name}'!

**Nächster Schritt:** Verwende jetzt create_and_attach_document() um die Agenda an den Termin anzuhängen:

```
create_and_attach_document(
    title="Agenda: {meeting_title}",
    content='''
{agenda_content}
'''
)
```

Oder ich kann das direkt für dich tun - sag einfach "Bitte hänge die Agenda an"."""

                    except Exception as e:
                        return f"❌ Fehler beim Generieren der Agenda: {str(e)}"

                @tool
                def get_my_asana_tasks() -> str:
                    """Ruft die eigenen Asana-Aufgaben ab und gruppiert sie nach Status.

                    Returns:
                        Formatierte Liste der eigenen Aufgaben (überfällig, heute, kommend)
                    """
                    try:
                        tasks = asana_agent.get_my_tasks(limit=100)
                        if not tasks:
                            return "Keine eigenen Aufgaben gefunden."

                        # Gruppiere nach Status
                        overdue = []
                        today = []
                        upcoming = []

                        today_date = datetime.now().date()

                        for task in tasks:
                            if task.get('completed'):
                                continue

                            due_on = task.get('due_on')
                            if due_on:
                                try:
                                    due_date = datetime.strptime(due_on, '%Y-%m-%d').date()
                                    if due_date < today_date:
                                        overdue.append(task)
                                    elif due_date == today_date:
                                        today.append(task)
                                    else:
                                        upcoming.append(task)
                                except:
                                    pass

                        result = "✅ **Meine Asana-Aufgaben:**\n\n"

                        if overdue:
                            result += f"⚠️ **Überfällig ({len(overdue)}):**\n"
                            for task in overdue:
                                result += f"- {task['name']} (Fällig: {task['due_on']})\n"
                            result += "\n"

                        if today:
                            result += f"🔴 **Heute fällig ({len(today)}):**\n"
                            for task in today:
                                result += f"- {task['name']}\n"
                            result += "\n"

                        if upcoming:
                            result += f"📅 **Kommende Aufgaben ({len(upcoming)}):**\n"
                            for task in upcoming[:10]:
                                result += f"- {task['name']} (Fällig: {task['due_on']})\n"

                        return result
                    except Exception as e:
                        return f"Fehler: {str(e)}"

                # Binde Tools an LLM (inkl. Asana-Tools)
                tools = [
                    create_and_attach_document,
                    list_asana_projects,
                    get_asana_project_tasks,
                    get_my_asana_tasks,
                    generate_asana_agenda
                ]
                llm_with_tools = research_agent.llm.bind_tools(tools)

                # Konvertiere Nachrichten zu LangChain Format
                lc_messages = []
                for msg in st.session_state['preparation_messages']:
                    if msg['role'] == 'system':
                        lc_messages.append(SystemMessage(content=msg['content']))
                    elif msg['role'] == 'user':
                        lc_messages.append(HumanMessage(content=msg['content']))
                    elif msg['role'] == 'assistant':
                        # Prüfe ob Tool-Calls vorhanden sind
                        if 'tool_calls' in msg and msg['tool_calls']:
                            lc_messages.append(AIMessage(
                                content=msg['content'] if msg['content'] else "",
                                tool_calls=msg['tool_calls']
                            ))
                        else:
                            lc_messages.append(AIMessage(content=msg['content']))
                    elif msg['role'] == 'tool':
                        # Tool-Ergebnis als ToolMessage
                        lc_messages.append(ToolMessage(
                            content=msg['content'],
                            tool_call_id=msg.get('tool_call_id', '')
                        ))

                # Generiere Antwort (mit Tool-Calling)
                # Tool-Call-Schleife: Verarbeite alle Tool-Calls bis keine mehr kommen
                st.write("📤 Sende initiale Anfrage an LLM...")

                max_iterations = 10  # Sicherheitslimit
                iteration = 0
                response = llm_with_tools.invoke(lc_messages)

                st.write("✅ Initiale Antwort erhalten")

                while iteration < max_iterations:
                    iteration += 1
                    st.write(f"🔄 **Iteration {iteration}/{max_iterations}**")

                    # Prüfe auf Tool-Calls
                    if hasattr(response, 'tool_calls') and response.tool_calls:
                        st.write(f"   └─ {len(response.tool_calls)} Tool-Call(s) erkannt")

                        # Speichere AI-Nachricht mit Tool-Call
                        st.session_state['preparation_messages'].append({
                            'role': 'assistant',
                            'content': response.content if response.content else "",
                            'tool_calls': response.tool_calls
                        })

                        # Führe Tool-Calls aus und sammle Ergebnisse
                        tool_messages = []
                        for tool_call in response.tool_calls:
                            tool_name = tool_call['name']
                            tool_args = tool_call['args']

                            # Finde das richtige Tool
                            tool_func = None
                            for t in tools:
                                if t.name == tool_name:
                                    tool_func = t
                                    break

                            if tool_func:
                                # Zeige Tool-Ausführung mit Status-Update
                                if tool_name == 'create_and_attach_document':
                                    status_text = f"📄 Erstelle Dokument: {tool_args.get('title', 'Unbenannt')}"
                                elif tool_name.startswith('get_asana') or tool_name.startswith('list_asana'):
                                    status_text = "📋 Rufe Asana-Daten ab"
                                else:
                                    status_text = f"🔧 Führe aus: {tool_name}"

                                st.write(f"   └─ {status_text}...")
                                tool_result = tool_func.invoke(tool_args)
                                st.write(f"      ✓ Fertig")

                                # Speichere Tool-Ergebnis
                                st.session_state['preparation_messages'].append({
                                    'role': 'tool',
                                    'content': tool_result,
                                    'tool_call_id': tool_call.get('id')
                                })

                                # Sammle für Follow-up
                                tool_messages.append(ToolMessage(
                                    content=tool_result,
                                    tool_call_id=tool_call.get('id')
                                ))

                        # Füge Nachrichten hinzu und hole nächste Antwort
                        lc_messages.append(response)
                        lc_messages.extend(tool_messages)

                        # Nächste Antwort vom Agent (könnte weitere Tool-Calls enthalten)
                        st.write("   └─ 📤 Sende Follow-up an LLM...")
                        response = llm_with_tools.invoke(lc_messages)
                        st.write("   └─ ✅ Follow-up erhalten")
                    else:
                        # Keine weiteren Tool-Calls - finale Antwort
                        st.write("✅ Finale Antwort generiert")
                        st.session_state['preparation_messages'].append({
                            'role': 'assistant',
                            'content': response.content
                        })
                        break

                # Falls max_iterations erreicht wurde
                if iteration >= max_iterations:
                    st.warning("⚠️ Maximale Anzahl an Tool-Aufrufen erreicht. Antwort wird möglicherweise abgeschnitten.")

                status.update(label="✅ Antwort bereit!", state="complete")

                st.rerun()

            except Exception as e:
                status.update(label="❌ Fehler aufgetreten", state="error")
                st.error(f"Fehler bei der Antwort: {e}")
                import traceback
                with st.expander("Debug Info"):
                    st.code(traceback.format_exc())

    # Aktionen und Beispiel-Prompts unter dem Chat
    st.markdown("---")

    # Beispiel-Prompts für schnellen Start
    st.caption("💡 **Beispiele:**")
    col_ex1, col_ex2, col_ex3, col_ex4 = st.columns(4)

    with col_ex1:
        if st.button("📋 Agenda erstellen", use_container_width=True):
            # Wenn Asana-Projekt ausgewählt, nutze intelligente Agenda mit Kontext
            if st.session_state.get('prep_selected_project_name'):
                project_name = st.session_state['prep_selected_project_name']
                example = f"Erstelle eine Agenda mit generate_asana_agenda('{project_name}') und hänge sie dann mit create_and_attach_document an den Termin an."
            else:
                example = "Erstelle bitte eine strukturierte Agenda für dieses Meeting und hänge sie als Dokument an den Termin an."
            st.session_state['preparation_messages'].append({'role': 'user', 'content': example})
            st.rerun()

    with col_ex2:
        if st.button("✅ Asana-Tasks", use_container_width=True):
            if st.session_state.get('prep_selected_project_name'):
                example = f"Zeige mir alle offenen Asana-Aufgaben im Projekt {st.session_state['prep_selected_project_name']}"
            else:
                example = "Welche Asana-Aufgaben sind für dieses Meeting relevant?"
            st.session_state['preparation_messages'].append({'role': 'user', 'content': example})
            st.rerun()

    with col_ex3:
        if st.button("🔍 Recherche", use_container_width=True):
            example = "Recherchiere wichtige Themen für dieses Meeting und fasse sie in einem Dokument zusammen."
            st.session_state['preparation_messages'].append({'role': 'user', 'content': example})
            st.rerun()

    with col_ex4:
        if st.button("📊 Übersicht", use_container_width=True):
            example = "Erstelle eine Übersicht mit Meeting-Infos, Teilnehmern und relevanten Asana-Aufgaben, und hänge sie an."
            st.session_state['preparation_messages'].append({'role': 'user', 'content': example})
            st.rerun()

    st.markdown("---")

    # Aktions-Buttons
    col1, col2 = st.columns([1, 1])

    with col1:
        if st.button("🗑️ Chat zurücksetzen", use_container_width=True):
            st.session_state['preparation_messages'] = []
            st.rerun()

    with col2:
        if st.button("← Zurück zur Übersicht", use_container_width=True):
            if 'preparing_event' in st.session_state:
                del st.session_state['preparing_event']
            if 'preparation_messages' in st.session_state:
                del st.session_state['preparation_messages']
            st.rerun()


def render_asana_tasks_section():
    """Rendert die Asana-Aufgaben Sektion (rechts) mit Live-Daten"""
    st.subheader("✅ Ihre Prioritäten in Asana")

    asana_agent = st.session_state.orchestrator.asana_agent

    # Prüfe ob Asana verbunden ist
    if not asana_agent.is_connected():
        st.error("❌ **Asana-Verbindung fehlgeschlagen**")
        st.markdown("""
        **Mögliche Ursachen:**
        - ASANA_ACCESS_TOKEN fehlt oder ist ungültig
        - Keine Netzwerkverbindung zu Asana
        - Token abgelaufen oder widerrufen

        **Lösung:**
        1. Prüfen Sie die `.env` Datei
        2. Erstellen Sie einen neuen Token: https://app.asana.com/0/my-apps
        3. Fügen Sie ein: `ASANA_ACCESS_TOKEN=ihr_token`
        4. Starten Sie die App neu
        """)
        return

    # WICHTIG: Nur laden wenn User explizit klickt!
    if 'show_asana_tasks_dashboard' not in st.session_state:
        st.session_state.show_asana_tasks_dashboard = False

    if st.button("🔄 Asana-Aufgaben laden", key="load_asana_dashboard", use_container_width=True):
        st.session_state.show_asana_tasks_dashboard = True
        # Invalidiere Cache
        cached_get_asana_projects.clear()
        cached_get_asana_tasks.clear()
        st.rerun()

    if not st.session_state.show_asana_tasks_dashboard:
        st.info("💡 Klicke auf 'Asana-Aufgaben laden' um deine Tasks zu sehen")
        return

    # Lade echte Projekte aus Asana
    st.markdown("**📁 Projekt auswählen:**")

    try:
        # Hole echte Projekte (cached)
        projects = cached_get_asana_projects(asana_agent)

        if not projects:
            st.error("❌ Keine Projekte gefunden")
            st.caption("**Mögliche Ursachen:**")
            st.caption("- Asana-Token ungültig oder abgelaufen")
            st.caption("- Keine Projekte in Ihrem Workspace")
            st.caption("- Fehlende Berechtigungen")
            st.caption("**Lösung:** Prüfen Sie die .env Datei und erstellen Sie Projekte in Asana")
            return

        # Dropdown mit echten Projekten
        project_names = [p['name'] for p in projects]
        project_dict = {p['name']: p['gid'] for p in projects}

        # Füge "Alle Projekte" Option hinzu
        project_names.insert(0, "📋 Alle Projekte")

        selected_project_name = st.selectbox(
            "Projekt",
            project_names,
            key="dashboard_project_select",
            label_visibility="collapsed"
        )

        st.markdown(f"**Aktives Filter:** {selected_project_name}")
        st.markdown("---")

        # Lade Aufgaben (cached)
        if selected_project_name == "📋 Alle Projekte":
            # Lade alle eigenen Aufgaben
            tasks = cached_get_asana_tasks(asana_agent, days=7)
        else:
            # Lade Aufgaben des ausgewählten Projekts
            selected_project_gid = project_dict[selected_project_name]
            tasks = cached_get_asana_tasks(asana_agent, project_gid=selected_project_gid, days=7)

        if not tasks:
            st.info("📭 Keine anstehenden Aufgaben gefunden")
            st.caption("Alle Aufgaben sind erledigt oder es gibt keine Aufgaben im gewählten Projekt.")
            return

        # Gruppiere nach Dringlichkeit
        today_tasks = []
        upcoming_tasks = []

        today = datetime.now().date()

        for task in tasks:
            due_on = task.get('due_on')
            if due_on:
                try:
                    due_date = datetime.strptime(due_on, '%Y-%m-%d').date()
                    if due_date <= today:
                        today_tasks.append(task)
                    else:
                        upcoming_tasks.append(task)
                except:
                    upcoming_tasks.append(task)
            else:
                upcoming_tasks.append(task)

        # Heute fällige Aufgaben
        if today_tasks:
            st.markdown("### 🔴 Heute fällig")
            st.caption(f"{len(today_tasks)} Aufgabe(n)")
            for task in today_tasks:
                render_task_card(task, asana_agent)

        # Kommende Aufgaben
        if upcoming_tasks:
            st.markdown("### 📅 Diese Woche")
            st.caption(f"{len(upcoming_tasks)} Aufgabe(n)")
            for task in upcoming_tasks[:10]:  # Zeige max. 10
                render_task_card(task, asana_agent)

        # Asana-Analyse-Chat
        st.markdown("---")
        with st.expander("💬 Asana-Assistent (Fragen & Analysen)", expanded=False):
            render_asana_chat_assistant(asana_agent)

    except Exception as e:
        # Detailliertes Error-Logging
        print(f"[Dashboard] ❌ FEHLER beim Laden der Asana-Aufgaben:")
        print(f"[Dashboard]   Exception Type: {type(e).__name__}")
        print(f"[Dashboard]   Exception Message: {str(e)}")
        import traceback
        print(f"[Dashboard] Full Traceback:")
        traceback.print_exc()

        st.error(f"❌ **Fehler beim Laden der Aufgaben**")
        st.caption(f"**Fehlertyp:** {type(e).__name__}")
        st.caption(f"**Details:** {str(e)}")
        st.markdown("""
        **Bitte prüfen Sie:**
        - Ist Ihr Asana-Token noch gültig?
        - Haben Sie Zugriff auf die Projekte?
        - Prüfen Sie die Terminal-Logs für vollständige Details
        """)


def render_asana_chat_assistant(asana_agent):
    """Rendert einen Chat-Assistenten für Asana-Analysen und Abfragen"""

    st.caption("Stellen Sie Fragen zu Ihren Asana-Projekten und Aufgaben.")

    # Projekt-Auswahl
    selected_project_gid = None
    selected_project_name = None

    try:
        projects = asana_agent.list_projects()
        if projects:
            project_options = ["[Alle Projekte]"] + [p['name'] for p in projects]
            selected_project_name = st.selectbox(
                "Filter nach Projekt",
                project_options,
                key="asana_chat_project_filter",
                help="Fokussiere die Abfragen auf ein bestimmtes Projekt"
            )

            if selected_project_name != "[Alle Projekte]":
                for p in projects:
                    if p['name'] == selected_project_name:
                        selected_project_gid = p['gid']
                        st.caption(f"📁 Fokus auf Projekt: **{selected_project_name}**")
                        break
    except:
        pass

    st.markdown("---")

    # Initialisiere Chat-Historie
    if 'asana_chat_messages' not in st.session_state:
        st.session_state['asana_chat_messages'] = []

        # System-Prompt
        project_context = ""
        if selected_project_gid:
            project_context = f"\n\nDer Nutzer hat das Projekt '{selected_project_name}' ausgewählt. Fokussiere deine Antworten auf dieses Projekt, wenn relevant."

        system_context = f"""Du bist ein Asana-Analyse-Assistent. Du hast Zugriff auf die Asana-Daten des Nutzers und kannst:

- Projekte auflisten
- Aufgaben in Projekten abrufen
- Aufgaben nach verschiedenen Kriterien filtern
- Statistiken und Auswertungen erstellen
- Überfällige Tasks identifizieren
- Aufgaben nach Personen gruppieren{project_context}

Nutze die verfügbaren Tools, um die Fragen des Nutzers zu beantworten. Sei präzise und hilfreich."""

        st.session_state['asana_chat_messages'].append({
            'role': 'system',
            'content': system_context
        })

    # Speichere ausgewähltes Projekt
    if selected_project_gid:
        st.session_state['asana_chat_selected_project'] = selected_project_gid

    # Zeige Chat-Historie
    for msg in st.session_state['asana_chat_messages']:
        if msg['role'] == 'system':
            continue
        elif msg['role'] == 'user':
            with st.chat_message("user"):
                st.write(msg['content'])
        elif msg['role'] == 'assistant':
            with st.chat_message("assistant"):
                st.write(msg['content'])
        elif msg['role'] == 'tool':
            with st.chat_message("assistant"):
                st.info(msg['content'])

    # Chat-Eingabe
    user_input = st.chat_input("Ihre Frage zu Asana...", key="asana_chat_input")

    if user_input:
        # Nutzer-Nachricht hinzufügen
        st.session_state['asana_chat_messages'].append({
            'role': 'user',
            'content': user_input
        })

        with st.status("📊 Analysiere Asana-Daten...", expanded=True) as status:
            try:
                orch = st.session_state.orchestrator
                research_agent = orch.research_agent

                # Definiere Asana-Tools
                from langchain_core.tools import tool
                from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage

                @tool
                def list_asana_projects() -> str:
                    """Liste alle Asana-Projekte im Workspace auf.

                    Returns:
                        Formatierte Liste der Projekte mit Namen und GIDs
                    """
                    try:
                        projects = asana_agent.list_projects()
                        if not projects:
                            return "Keine Projekte gefunden."

                        result = "📁 **Asana-Projekte:**\n\n"
                        for proj in projects:
                            result += f"- {proj['name']} (GID: {proj['gid']})\n"
                        return result
                    except Exception as e:
                        return f"Fehler beim Abrufen der Projekte: {str(e)}"

                @tool
                def get_project_tasks(project_name: str) -> str:
                    """Ruft alle Aufgaben eines bestimmten Projekts ab.

                    Args:
                        project_name: Name des Projekts

                    Returns:
                        Formatierte Liste der Aufgaben im Projekt
                    """
                    try:
                        # Finde Projekt-GID
                        projects = asana_agent.list_projects()
                        project_gid = None
                        for proj in projects:
                            if project_name.lower() in proj['name'].lower():
                                project_gid = proj['gid']
                                break

                        if not project_gid:
                            return f"Projekt '{project_name}' nicht gefunden."

                        tasks = asana_agent.get_project_tasks(project_gid, limit=100)
                        if not tasks:
                            return f"Keine Aufgaben im Projekt '{project_name}' gefunden."

                        result = f"📋 **Aufgaben im Projekt '{project_name}':**\n\n"
                        for task in tasks:
                            name = task.get('name', 'Unbenannt')
                            due_on = task.get('due_on', 'Kein Datum')
                            completed = task.get('completed', False)
                            status = "✅" if completed else "⭕"
                            result += f"{status} {name} (Fällig: {due_on})\n"
                        return result
                    except Exception as e:
                        return f"Fehler beim Abrufen der Aufgaben: {str(e)}"

                @tool
                def get_my_tasks(days: int = 30) -> str:
                    """Ruft die eigenen Aufgaben ab.

                    Args:
                        days: Wie viele Tage in die Zukunft schauen (Standard: 30)

                    Returns:
                        Formatierte Liste der eigenen Aufgaben
                    """
                    try:
                        tasks = asana_agent.get_my_tasks(limit=100)
                        if not tasks:
                            return "Keine eigenen Aufgaben gefunden."

                        # Gruppiere nach Status
                        overdue = []
                        today = []
                        upcoming = []
                        no_date = []

                        today_date = datetime.now().date()

                        for task in tasks:
                            if task.get('completed'):
                                continue

                            due_on = task.get('due_on')
                            if due_on:
                                try:
                                    due_date = datetime.strptime(due_on, '%Y-%m-%d').date()
                                    if due_date < today_date:
                                        overdue.append(task)
                                    elif due_date == today_date:
                                        today.append(task)
                                    else:
                                        upcoming.append(task)
                                except:
                                    no_date.append(task)
                            else:
                                no_date.append(task)

                        result = "✅ **Meine Aufgaben:**\n\n"

                        if overdue:
                            result += f"⚠️ **Überfällig ({len(overdue)}):**\n"
                            for task in overdue:
                                result += f"- {task['name']} (Fällig: {task['due_on']})\n"
                            result += "\n"

                        if today:
                            result += f"🔴 **Heute fällig ({len(today)}):**\n"
                            for task in today:
                                result += f"- {task['name']}\n"
                            result += "\n"

                        if upcoming:
                            result += f"📅 **Kommende Aufgaben ({len(upcoming)}):**\n"
                            for task in upcoming[:10]:
                                result += f"- {task['name']} (Fällig: {task['due_on']})\n"
                            result += "\n"

                        if no_date:
                            result += f"📋 **Ohne Fälligkeitsdatum ({len(no_date)}):**\n"
                            for task in no_date[:5]:
                                result += f"- {task['name']}\n"

                        return result
                    except Exception as e:
                        return f"Fehler beim Abrufen der Aufgaben: {str(e)}"

                # Binde Tools an LLM
                tools = [list_asana_projects, get_project_tasks, get_my_tasks]
                llm_with_tools = research_agent.llm.bind_tools(tools)

                # Konvertiere Nachrichten
                lc_messages = []
                for msg in st.session_state['asana_chat_messages']:
                    if msg['role'] == 'system':
                        lc_messages.append(SystemMessage(content=msg['content']))
                    elif msg['role'] == 'user':
                        lc_messages.append(HumanMessage(content=msg['content']))
                    elif msg['role'] == 'assistant':
                        if 'tool_calls' in msg and msg['tool_calls']:
                            lc_messages.append(AIMessage(
                                content=msg['content'] if msg['content'] else "",
                                tool_calls=msg['tool_calls']
                            ))
                        else:
                            lc_messages.append(AIMessage(content=msg['content']))
                    elif msg['role'] == 'tool':
                        lc_messages.append(ToolMessage(
                            content=msg['content'],
                            tool_call_id=msg.get('tool_call_id', '')
                        ))

                # Generiere Antwort
                st.write("📤 Sende Anfrage an LLM...")
                response = llm_with_tools.invoke(lc_messages)
                st.write("✅ Antwort erhalten")

                # Prüfe auf Tool-Calls
                if hasattr(response, 'tool_calls') and response.tool_calls:
                    st.write(f"🔧 {len(response.tool_calls)} Tool-Call(s) erkannt")
                    # Speichere AI-Nachricht
                    st.session_state['asana_chat_messages'].append({
                        'role': 'assistant',
                        'content': response.content if response.content else "",
                        'tool_calls': response.tool_calls
                    })

                    # Führe Tools aus
                    tool_messages = []
                    for tool_call in response.tool_calls:
                        tool_name = tool_call['name']
                        tool_args = tool_call['args']

                        # Finde das richtige Tool
                        tool_func = None
                        for t in tools:
                            if t.name == tool_name:
                                tool_func = t
                                break

                        if tool_func:
                            st.write(f"   └─ 🔧 Führe aus: `{tool_name}`")
                            tool_result = tool_func.invoke(tool_args)
                            st.write(f"      ✓ Fertig")

                            st.session_state['asana_chat_messages'].append({
                                'role': 'tool',
                                'content': tool_result,
                                'tool_call_id': tool_call.get('id')
                            })

                            tool_messages.append(ToolMessage(
                                content=tool_result,
                                tool_call_id=tool_call.get('id')
                            ))

                    # Follow-up Antwort
                    lc_messages.append(response)
                    lc_messages.extend(tool_messages)

                    st.write("📤 Sende Follow-up an LLM...")
                    follow_up_response = llm_with_tools.invoke(lc_messages)
                    st.write("✅ Follow-up erhalten")

                    st.session_state['asana_chat_messages'].append({
                        'role': 'assistant',
                        'content': follow_up_response.content
                    })
                else:
                    # Normale Antwort
                    st.session_state['asana_chat_messages'].append({
                        'role': 'assistant',
                        'content': response.content
                    })

                status.update(label="✅ Analyse abgeschlossen!", state="complete")

                st.rerun()

            except Exception as e:
                status.update(label="❌ Fehler aufgetreten", state="error")
                st.error(f"Fehler: {e}")
                import traceback
                with st.expander("Debug Info"):
                    st.code(traceback.format_exc())

    # Reset-Button
    if st.button("🗑️ Chat zurücksetzen", key="reset_asana_chat"):
        st.session_state['asana_chat_messages'] = []
        st.rerun()


def render_task_card(task: dict, asana_agent):
    """Rendert eine einzelne Aufgaben-Karte mit optimiertem Layout für lange Texte"""
    name = task.get('name', 'Unbenannt')
    due_on = task.get('due_on', 'Kein Datum')
    notes = task.get('notes', '')
    projects = task.get('projects', [])
    task_gid = task.get('gid')

    # Vollständiger Titel im Expander (keine Kürzung)
    with st.expander(f"📌 {name}", expanded=False):
        # Fälligkeitsdatum mit Badge-Style
        if due_on != 'Kein Datum':
            try:
                due_date = datetime.strptime(due_on, '%Y-%m-%d')
                days_until = (due_date - datetime.now()).days

                if days_until < 0:
                    st.error(f"⚠️ **Überfällig seit {abs(days_until)} Tag(en)!**")
                elif days_until == 0:
                    st.warning("🔴 **Heute fällig!**")
                elif days_until == 1:
                    st.info("🟡 Morgen fällig")
                else:
                    st.caption(f"📅 Fällig in {days_until} Tag(en) ({due_on})")
            except:
                st.caption(f"📅 Fällig: {due_on}")

        # Projekt
        if projects:
            st.caption(f"📁 Projekt: {', '.join(projects)}")

        st.markdown("---")

        # Vollständige Beschreibung in scrollbarem Container
        if notes:
            st.markdown("### 📝 Beschreibung")

            # Wenn Text sehr lang ist (>500 Zeichen), verwende scrollbaren Container
            if len(notes) > 500:
                # Nutze st.text_area für scrollbare Anzeige
                st.text_area(
                    "Aufgabenbeschreibung",
                    notes,
                    height=200,
                    key=f"notes_{task_gid}",
                    label_visibility="collapsed",
                    disabled=True
                )
            else:
                # Für kürzere Texte: normale Anzeige mit Markdown
                st.markdown(notes)
        else:
            st.caption("_Keine Beschreibung vorhanden_")

        st.markdown("---")

        # Kommentare aus Asana laden mit verbessertem Layout
        st.markdown("### 💬 Kommentare")
        if task_gid:
            try:
                comments = asana_agent.get_task_stories(task_gid, limit=5)
                if comments:
                    # Container für Kommentare mit eigenem Styling
                    for i, comment in enumerate(comments):
                        author = comment.get('author', 'Unbekannt')
                        text = comment.get('text', '')
                        created_at = comment.get('created_at', '')

                        # Formatiere Datum wenn vorhanden
                        time_str = ""
                        if created_at:
                            try:
                                # ISO Format parsen: 2024-01-25T10:30:00.000Z
                                dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                                time_str = dt.strftime('%d.%m.%Y %H:%M')
                            except:
                                time_str = created_at[:10]  # Fallback: nur Datum

                        # Kommentar-Box mit besserer Formatierung
                        st.markdown(
                            f"""
                            <div style="
                                background-color: rgba(240, 242, 246, 0.5);
                                padding: 10px;
                                border-radius: 5px;
                                margin-bottom: 10px;
                                border-left: 3px solid #4A90E2;
                            ">
                                <strong>{author}</strong> <em style="color: #666; font-size: 0.9em;">{time_str}</em>
                                <p style="margin-top: 5px; margin-bottom: 0;">{text}</p>
                            </div>
                            """,
                            unsafe_allow_html=True
                        )
                else:
                    st.caption("_Keine Kommentare vorhanden_")
            except Exception as e:
                st.caption(f"_Fehler beim Laden der Kommentare: {e}_")
        else:
            st.caption("_Kommentare nicht verfügbar_")

        st.markdown("---")

        # Aktions-Buttons mit verbessertem Spacing
        col1, col2 = st.columns(2)
        with col1:
            if st.button("✓ Erledigt", key=f"complete_{task_gid}", use_container_width=True):
                try:
                    result = asana_agent.complete_task(task_gid)
                    if result.get('success'):
                        st.success("✅ Aufgabe als erledigt markiert!")
                        st.rerun()
                    else:
                        st.error(f"❌ Fehler: {result.get('error')}")
                except Exception as e:
                    st.error(f"❌ Fehler: {e}")

        with col2:
            if st.button("🔗 In Asana öffnen", key=f"open_{task_gid}", use_container_width=True):
                asana_url = f"https://app.asana.com/0/0/{task_gid}/f"
                st.markdown(f"[Aufgabe in Asana öffnen]({asana_url})")

        # Entferne die letzte Trennlinie (ist bereits durch Expander-Ende gegeben)


# ============================================================================
# MEETING MANAGER / TRANSKRIPTE TAB
# ============================================================================

def get_meeting_manager_pid():
    """
    Sucht nach laufendem Meeting Manager Prozess und gibt PID zurück.

    Returns:
        int or None: PID des Prozesses oder None wenn nicht läuft
    """
    try:
        result = subprocess.run(
            ["pgrep", "-f", "python.*meeting_manager.py"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().split('\n')[0])
    except Exception:
        pass
    return None


def is_meeting_manager_running():
    """Prüft ob Meeting Manager läuft"""
    return get_meeting_manager_pid() is not None


def start_meeting_manager():
    """Startet den Meeting Manager als Background-Prozess"""
    try:
        # Prüfe ob schon läuft
        if is_meeting_manager_running():
            return {"success": False, "error": "Meeting Manager läuft bereits"}

        # Starte Prozess im Hintergrund
        venv_python = Path("venv/bin/python")
        if not venv_python.exists():
            return {"success": False, "error": "Virtual Environment nicht gefunden"}

        log_file = Path("meeting_manager.log")

        # Starte Prozess
        with open(log_file, "w") as f:
            process = subprocess.Popen(
                [str(venv_python), "meeting_manager.py"],
                stdout=f,
                stderr=subprocess.STDOUT,
                start_new_session=True
            )

        # Warte kurz und prüfe ob Prozess noch läuft
        time.sleep(2)
        if process.poll() is None:
            return {"success": True, "pid": process.pid}
        else:
            return {"success": False, "error": "Prozess wurde sofort beendet"}

    except Exception as e:
        return {"success": False, "error": str(e)}


def stop_meeting_manager():
    """Stoppt den Meeting Manager"""
    try:
        pid = get_meeting_manager_pid()
        if pid is None:
            return {"success": False, "error": "Meeting Manager läuft nicht"}

        # Sende SIGTERM für graceful shutdown
        os.kill(pid, signal.SIGTERM)

        # Warte bis zu 5 Sekunden
        for _ in range(10):
            time.sleep(0.5)
            if not is_meeting_manager_running():
                return {"success": True}

        # Falls immer noch läuft, force kill
        if is_meeting_manager_running():
            os.kill(pid, signal.SIGKILL)
            time.sleep(0.5)

        return {"success": True}

    except Exception as e:
        return {"success": False, "error": str(e)}


def extract_protocol_from_transcript_streaming(transcript_text: str, meeting_title: str, llm, attendees: Optional[List[str]] = None, meeting_date: Optional[str] = None, agenda_text: Optional[str] = None):
    """
    Erstellt ein strukturiertes Protokoll aus einem Transkript mittels LLM mit STREAMING.

    Generator-Funktion, die das Protokoll Token-für-Token zurückgibt für Live-Feedback.

    Args:
        transcript_text: Text des Transkripts
        meeting_title: Titel des Meetings
        llm: LLM-Instanz
        attendees: Optional Liste von Teilnehmer-Namen aus Outlook
        meeting_date: Optional Datum des Meetings
        agenda_text: Optional Text der Agenda (aus PDF extrahiert)

    Yields:
        Teile des Protokolls als sie generiert werden
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    # Erstelle Teilnehmer-String
    if attendees:
        attendees_str = ", ".join(attendees)
    else:
        attendees_str = "[?]"

    # Erstelle Datums-String
    date_str = meeting_date if meeting_date else "[?]"

    # Erstelle Agenda-Kontext falls vorhanden
    agenda_context = ""
    if agenda_text:
        agenda_context = f"""

**WICHTIG: Es liegt eine Meeting-Agenda vor (Soll-Zustand)!**

Agenda-Inhalt (vollständig):
```
{agenda_text}
```

**AUFGABE:**
1. Strukturiere das Protokoll STRENG nach der vorliegenden Agenda
2. Gleiche die Diskussionspunkte des Transkripts gegen die Agenda ab
3. Markiere EXPLIZIT, welche Agenda-Punkte NICHT besprochen wurden oder wo vom Thema abgewichen wurde
4. Nutze die Agenda-Struktur als Grundlage für die Themenabschnitte im Protokoll
5. Ergänze bei jedem Thema einen Hinweis auf den Agenda-Status:
   - ✅ "Gemäß Agenda besprochen"
   - ⚠️ "Teilweise besprochen - [Hinweis]"
   - ❌ "Nicht besprochen"
   - ℹ️ "Abweichung von Agenda - [Beschreibung]"
"""

    system_prompt = f"""Du bist ein Assistent, der professionelle Meeting-Protokolle erstellt.{agenda_context}

Analysiere das Transkript und erstelle ein strukturiertes Protokoll in folgendem Format:

# Meeting-Protokoll: [Meeting-Titel]

**Datum:** {date_str}
**Teilnehmer:** {attendees_str}

---

## Thema 1: [Themenbezeichnung]

### Diskussion/Kontext
[Zusammenfassung der Diskussion zu diesem Thema]

### Entscheidungen
- [Getroffene Entscheidung 1]
- [Getroffene Entscheidung 2 oder [?] falls keine Entscheidung getroffen]

### Weitere Schritte
- **[?]**: [Aufgabe/Action Item 1] - Fällig: [Datum oder [?]]
- **[Name]**: [Aufgabe/Action Item 2] - Fällig: [Datum oder [?]]

---

## Thema 2: [Nächstes Thema]
[... gleiche Struktur ...]

---

## Zusammenfassung

[Kurze Zusammenfassung der wichtigsten Punkte]

WICHTIGE REGELN:
- Datum und Teilnehmer sind bereits vorgegeben - übernimm sie EXAKT wie oben angegeben
- IMMER [?] verwenden wenn Namen, Daten oder Entscheidungen unklar sind
- Jeder Themenblock MUSS alle drei Unterpunkte enthalten (Diskussion, Entscheidungen, Weitere Schritte)
- Bei 'Weitere Schritte': Format ist immer '**Zuständig**: Aufgabe - Fällig: Datum'
- Wenn kein Zuständiger erkennbar: **[?]**: verwenden
- Wenn kein Fälligkeitsdatum genannt: 'Fällig: [?]' verwenden
- Markdown-Formatierung verwenden
- Professioneller, sachlicher Ton"""

    # Berechne Transkript-Länge für Logging
    transcript_length = len(transcript_text)
    transcript_words = len(transcript_text.split())

    user_prompt = f"""Meeting-Titel: {meeting_title}

Transkript ({transcript_words} Wörter, {transcript_length} Zeichen):

{transcript_text}

---
Erstelle ein strukturiertes Protokoll nach dem vorgegebenen Format."""

    try:
        # STREAMING: Nutze stream() statt invoke()
        for chunk in llm.stream([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ]):
            if hasattr(chunk, 'content') and chunk.content:
                yield chunk.content

    except Exception as e:
        yield f"\n\n❌ **Fehler bei Protokoll-Erstellung:** {str(e)}"


def extract_protocol_from_transcript(transcript_text: str, meeting_title: str, llm, attendees: Optional[List[str]] = None, meeting_date: Optional[str] = None, agenda_text: Optional[str] = None) -> str:
    """
    Erstellt ein strukturiertes Protokoll aus einem Transkript mittels LLM.

    HINWEIS: Für Live-Feedback nutze extract_protocol_from_transcript_streaming()

    Das Protokoll wird in Themenabschnitte gegliedert, wobei jeder Abschnitt
    'Diskussion/Kontext', 'Entscheidungen' und 'Weitere Schritte' enthält.

    Args:
        transcript_text: Text des Transkripts
        meeting_title: Titel des Meetings
        llm: LLM-Instanz
        attendees: Optional Liste von Teilnehmer-Namen aus Outlook
        meeting_date: Optional Datum des Meetings
        agenda_text: Optional Text der Agenda (aus PDF extrahiert)

    Returns:
        Strukturiertes Protokoll als Markdown-String
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    # Erstelle Teilnehmer-String
    if attendees:
        attendees_str = ", ".join(attendees)
    else:
        attendees_str = "[?]"

    # Erstelle Datums-String
    date_str = meeting_date if meeting_date else "[?]"

    # Erstelle Agenda-Kontext falls vorhanden
    agenda_context = ""
    if agenda_text:
        agenda_context = f"""

**WICHTIG: Es liegt eine Meeting-Agenda vor (Soll-Zustand)!**

Agenda-Inhalt (vollständig):
```
{agenda_text}
```

**AUFGABE:**
1. Strukturiere das Protokoll STRENG nach der vorliegenden Agenda
2. Gleiche die Diskussionspunkte des Transkripts gegen die Agenda ab
3. Markiere EXPLIZIT, welche Agenda-Punkte NICHT besprochen wurden oder wo vom Thema abgewichen wurde
4. Nutze die Agenda-Struktur als Grundlage für die Themenabschnitte im Protokoll
5. Ergänze bei jedem Thema einen Hinweis auf den Agenda-Status:
   - ✅ "Gemäß Agenda besprochen"
   - ⚠️ "Teilweise besprochen - [Hinweis]"
   - ❌ "Nicht besprochen"
   - ℹ️ "Abweichung von Agenda - [Beschreibung]"
"""

    system_prompt = f"""Du bist ein Assistent, der professionelle Meeting-Protokolle erstellt.{agenda_context}

Analysiere das Transkript und erstelle ein strukturiertes Protokoll in folgendem Format:

# Meeting-Protokoll: [Meeting-Titel]

**Datum:** {date_str}
**Teilnehmer:** {attendees_str}

---

## Thema 1: [Themenbezeichnung]

### Diskussion/Kontext
[Zusammenfassung der Diskussion zu diesem Thema]

### Entscheidungen
- [Getroffene Entscheidung 1]
- [Getroffene Entscheidung 2 oder [?] falls keine Entscheidung getroffen]

### Weitere Schritte
- **[?]**: [Aufgabe/Action Item 1] - Fällig: [Datum oder [?]]
- **[Name]**: [Aufgabe/Action Item 2] - Fällig: [Datum oder [?]]

---

## Thema 2: [Nächstes Thema]
[... gleiche Struktur ...]

---

## Zusammenfassung

[Kurze Zusammenfassung der wichtigsten Punkte]

WICHTIGE REGELN:
- Datum und Teilnehmer sind bereits vorgegeben - übernimm sie EXAKT wie oben angegeben
- IMMER [?] verwenden wenn Namen, Daten oder Entscheidungen unklar sind
- Jeder Themenblock MUSS alle drei Unterpunkte enthalten (Diskussion, Entscheidungen, Weitere Schritte)
- Bei 'Weitere Schritte': Format ist immer '**Zuständig**: Aufgabe - Fällig: Datum'
- Wenn kein Zuständiger erkennbar: **[?]**: verwenden
- Wenn kein Fälligkeitsdatum genannt: 'Fällig: [?]' verwenden
- Markdown-Formatierung verwenden
- Professioneller, sachlicher Ton"""

    # Berechne Transkript-Länge für Logging
    transcript_length = len(transcript_text)
    transcript_words = len(transcript_text.split())

    user_prompt = f"""Meeting-Titel: {meeting_title}

Transkript ({transcript_words} Wörter, {transcript_length} Zeichen):

{transcript_text}

---
Erstelle ein strukturiertes Protokoll nach dem vorgegebenen Format."""

    try:
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ])

        protocol_text = response.content.strip()
        return protocol_text

    except Exception as e:
        st.error(f"Fehler bei Protokoll-Erstellung: {e}")
        return f"# Fehler bei Protokoll-Erstellung\n\n{str(e)}"


def extract_tasks_from_transcript(transcript_text: str, llm) -> List[Dict[str, str]]:
    """
    Extrahiert Aufgaben aus einem Transkript oder Protokoll mittels LLM.

    Args:
        transcript_text: Text des Transkripts oder Protokolls
        llm: LLM-Instanz

    Returns:
        Liste von Dicts mit Aufgaben (title, description, due_date, assignee)
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    # Berechne Text-Länge für Logging
    text_length = len(transcript_text)
    text_words = len(transcript_text.split())

    system_prompt = """Du bist ein Assistent, der Meeting-Protokolle und Transkripte analysiert und konkrete Aufgaben extrahiert.

Analysiere den Text und identifiziere ALLE Aufgaben, Todos und Aktionspunkte.

WICHTIG: Suche nach Aufgaben im GESAMTEN Dokument, nicht nur am Anfang!
- In strukturierten Protokollen stehen Aufgaben oft in "Weitere Schritte" oder "Action Items" Abschnitten
- Im Transkript können Aufgaben überall erwähnt werden (z.B. "Peter übernimmt...", "Bis nächste Woche sollten wir...")

Gib die Aufgaben im folgenden JSON-Format zurück (ein Array von Objekten):

[
  {
    "title": "Kurzer Aufgabentitel (max 80 Zeichen)",
    "assignee": "Name der zuständigen Person oder [?] falls unklar",
    "description": "Detaillierte Beschreibung mit Kontext aus dem Meeting",
    "due_date": "YYYY-MM-DD oder null falls kein Datum genannt",
    "top": "Name des Tagesordnungspunkts oder Abschnitts, unter dem diese Aufgabe steht (z.B. 'TOP 3: Personalplanung' oder 'Weitere Schritte')"
  }
]

Regeln:
- Extrahiere ALLE Aufgaben aus dem GESAMTEN Text, nicht nur vom Anfang
- Nur konkrete Aufgaben, keine Diskussionspunkte
- Titel sollten actionable sein (mit Verb starten)
- Assignee: Extrahiere den Namen der zuständigen Person (Format: "**Name**:" oder "Name übernimmt" oder ähnlich)
- Beschreibung sollte genug Kontext für Asana enthalten
- Falls ein Datum oder Frist erwähnt wird, berechne das due_date
- Falls kein Datum erwähnt wird, setze due_date auf null
- top: Der Tagesordnungspunkt oder Abschnitt des Protokolls, unter dem die Aufgabe steht. Falls nicht eindeutig, setze auf null"""

    user_prompt = f"""Text ({text_words} Wörter, {text_length} Zeichen):

{transcript_text}

---
Extrahiere ALLE Aufgaben aus diesem GESAMTEN Text im JSON-Format.
Achte besonders auf "Weitere Schritte" oder "Action Items" Abschnitte."""

    try:
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ])

        import json
        import re

        # Extrahiere JSON aus der Antwort
        content = response.content

        # Suche nach JSON-Array mit Klammer-Balancierung.
        # WICHTIG: Kein greedy-Regex (\[[\s\S]*\]) – der würde bei [?]-Platzhaltern
        # im LLM-Text vom ersten [ bis zum letzten ] matchen und ungültiges JSON liefern.
        tasks_json = None
        start_match = re.search(r'\[\s*[\{\]]', content)  # Findet [ gefolgt von { oder ] (leeres Array)
        if start_match:
            start = start_match.start()
            depth = 0
            in_string = False
            escape_next = False
            for i in range(start, len(content)):
                c = content[i]
                if escape_next:
                    escape_next = False
                    continue
                if c == '\\' and in_string:
                    escape_next = True
                    continue
                if c == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if c == '[':
                    depth += 1
                elif c == ']':
                    depth -= 1
                    if depth == 0:
                        tasks_json = content[start:i + 1]
                        break

        if tasks_json:
            tasks = json.loads(tasks_json)
            return tasks
        else:
            return []

    except Exception as e:
        # Exception propagieren, damit der Aufrufer sie dauerhaft anzeigen kann
        raise RuntimeError(f"Fehler bei LLM-Analyse: {e}") from e


def count_placeholders_in_protocol(protocol_text: str) -> int:
    """
    Zählt die Anzahl der [?] Platzhalter im Protokoll.

    Args:
        protocol_text: Protokoll-Text

    Returns:
        Anzahl der Platzhalter
    """
    import re
    return len(re.findall(r'\[\?\]', protocol_text))


def extract_person_names_from_protocol_markdown(protocol_text: str) -> List[str]:
    """
    Extrahiert Personen-Namen aus dem Protokoll-Text im Markdown-Format.

    Sucht nach dem Muster: - **Name**: Aufgabenbeschreibung

    Args:
        protocol_text: Protokoll-Text im Markdown-Format

    Returns:
        Liste eindeutiger Namen
    """
    import re

    names = set()

    # Muster: - **Name**: Aufgabe (mit oder ohne Bindestriche vor **)
    # Beispiel: - **Philipp Scheidlock**: Übergabelösung entwickeln
    pattern = r'-\s*\*\*([^*]+)\*\*\s*:'

    matches = re.findall(pattern, protocol_text)

    for match in matches:
        name = match.strip()
        # Filtere unerwünschte Matches
        if name and name not in ['?', 'TBD', 'TODO', 'Alle', 'Team', 'Datum', 'Ort', 'Zeit']:
            names.add(name)

    return sorted(list(names))


def extract_person_names_from_tasks(tasks: List[Dict[str, Any]]) -> List[str]:
    """
    Extrahiert alle eindeutigen Personen-Namen aus den Aufgaben.

    Args:
        tasks: Liste von Task-Dictionaries mit 'assignee' Feld

    Returns:
        Liste eindeutiger Namen (ohne [?], TBD, etc.)
    """
    names = set()

    for task in tasks:
        assignee = task.get('assignee', '')
        if assignee and isinstance(assignee, str):
            # Filtere Platzhalter und unerwünschte Werte
            if assignee not in ['[?]', '?', 'TBD', 'TODO', '', 'Alle', 'Team', 'null']:
                names.add(assignee.strip())

    return sorted(list(names))


def extract_all_person_names(protocol_text: str, tasks: List[Dict[str, Any]]) -> List[str]:
    """
    Kombiniert Namen aus Protokoll-Text und Tasks.

    Args:
        protocol_text: Protokoll-Text
        tasks: Liste von Tasks

    Returns:
        Kombinierte, eindeutige Liste von Namen
    """
    names_from_protocol = extract_person_names_from_protocol_markdown(protocol_text)
    names_from_tasks = extract_person_names_from_tasks(tasks)

    # Kombiniere und dedupliziere
    all_names = set(names_from_protocol + names_from_tasks)

    return sorted(list(all_names))


def extract_tasks_from_protocol_text(protocol_text: str, llm) -> List[Dict[str, Any]]:
    """
    Extrahiert Aufgaben aus dem "Weitere Schritte" Abschnitt des Protokolls.

    Dies ermöglicht Single Source of Truth: Nutzer bearbeitet Aufgaben im Protokoll,
    und diese werden beim Finalisieren automatisch extrahiert.

    Args:
        protocol_text: Vollständiger Protokoll-Text (editiert vom Nutzer)
        llm: LLM-Instanz für Parsing

    Returns:
        Liste von Dicts mit Aufgaben (title, description, due_date, assignee)
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    import json
    import re

    system_prompt = """Du bist ein Assistent, der aus Meeting-Protokollen Aufgaben extrahiert.

Analysiere den Protokoll-Text und extrahiere ALLE Aufgaben aus den "Weitere Schritte" Abschnitten.

Format im Protokoll ist typischerweise:
- **[Name oder [?]]**: [Aufgabenbeschreibung] - Fällig: [Datum oder [?]]

Gib die Aufgaben im folgenden JSON-Format zurück:

[
  {
    "title": "Kurzer Aufgabentitel (max 80 Zeichen)",
    "description": "Detaillierte Beschreibung",
    "due_date": "YYYY-MM-DD oder null",
    "assignee": "Name der zuständigen Person oder null"
  }
]

Regeln:
- Ignoriere [?] Platzhalter - extrahiere nur konkrete Aufgaben
- Titel sollten actionable sein
- Falls Datum [?] ist, setze due_date auf null
- Falls Zuständiger [?] ist, setze assignee auf null"""

    user_prompt = f"""Protokoll-Text:

{protocol_text}

---
Extrahiere alle Aufgaben aus den "Weitere Schritte" Abschnitten im JSON-Format."""

    try:
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ])

        # Extrahiere JSON aus der Antwort
        content = response.content

        # Suche nach JSON-Array mit Klammer-Balancierung (kein greedy-Regex).
        tasks_json = None
        start_match = re.search(r'\[\s*[\{\]]', content)
        if start_match:
            start = start_match.start()
            depth = 0
            in_string = False
            escape_next = False
            for i in range(start, len(content)):
                c = content[i]
                if escape_next:
                    escape_next = False
                    continue
                if c == '\\' and in_string:
                    escape_next = True
                    continue
                if c == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if c == '[':
                    depth += 1
                elif c == ']':
                    depth -= 1
                    if depth == 0:
                        tasks_json = content[start:i + 1]
                        break

        if tasks_json:
            tasks = json.loads(tasks_json)
            return tasks
        else:
            return []

    except Exception as e:
        raise RuntimeError(f"Fehler beim Extrahieren der Tasks aus Protokoll: {e}") from e


# ============================================================================
# AGENDA TEMPLATE MANAGEMENT
# ============================================================================

def load_agenda_templates() -> List[Dict[str, Any]]:
    """
    Lädt Agenda-Vorlagen aus JSON-Datei.

    Returns:
        Liste von Template-Dictionaries
    """
    template_file = _get_user_ctx().data_dir / "agenda_templates.json"

    if not template_file.exists():
        # Erstelle leere Template-Datei
        template_file.parent.mkdir(parents=True, exist_ok=True)
        with open(template_file, 'w', encoding='utf-8') as f:
            import json
            json.dump({"templates": []}, f, ensure_ascii=False, indent=2)
        return []

    try:
        with open(template_file, 'r', encoding='utf-8') as f:
            import json
            data = json.load(f)
            return data.get('templates', [])
    except Exception as e:
        print(f"[load_agenda_templates] Fehler beim Laden: {e}")
        return []


def save_agenda_templates(templates: List[Dict[str, Any]]) -> bool:
    """
    Speichert Agenda-Vorlagen in JSON-Datei.

    Args:
        templates: Liste von Template-Dictionaries

    Returns:
        True bei Erfolg, False bei Fehler
    """
    template_file = _get_user_ctx().data_dir / "agenda_templates.json"
    template_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Backup erstellen falls Datei existiert
        if template_file.exists():
            backup_file = template_file.with_suffix('.json.backup')
            shutil.copy2(template_file, backup_file)

        with open(template_file, 'w', encoding='utf-8') as f:
            import json
            json.dump({"templates": templates}, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[save_agenda_templates] Fehler beim Speichern: {e}")
        return False


def create_agenda_from_template(
    template: Dict[str, Any],
    meeting_title: str,
    asana_data: Optional[Dict[str, Any]] = None,
    combine_with_asana: bool = False
) -> str:
    """
    Erstellt eine Agenda aus einer Vorlage.

    Args:
        template: Template-Dictionary mit Sections
        meeting_title: Titel des Meetings
        asana_data: Optional - Daten aus Asana (open_protocols, agenda_items)
        combine_with_asana: Ob Asana-Daten integriert werden sollen

    Returns:
        Agenda als Markdown-String
    """
    from datetime import datetime
    date_str = datetime.now().strftime("%d.%m.%Y")

    agenda_content = f"""# Agenda: {meeting_title}
**Datum:** {date_str}

⚠️ **Keine Besprechung ohne Protokoll - Aufzeichnung aktivieren!**

---

"""

    top_number = 1

    # Asana-Rückblick (falls vorhanden und kombiniert)
    if combine_with_asana and asana_data and asana_data.get('open_protocols'):
        agenda_content += f"""## TOP {top_number}: Rückblick - Offene Punkte aus vorherigen Besprechungen

"""
        for protocol in asana_data['open_protocols']:
            protocol_name_short = protocol['protocol_name'].replace('📄 Protokoll ', '')
            for item in protocol['open_items']:
                assignee_str = f" - Zuständig: {item['assignee']}" if item['assignee'] else ""
                due_str = f" - Fällig: {item['due_on']}" if item['due_on'] else ""
                agenda_content += f"""- [ ] {item['name']}{assignee_str}{due_str} *(aus {protocol_name_short})*
"""

        agenda_content += "\n---\n\n"
        top_number += 1

    # Template-Sections
    if template.get('sections'):
        agenda_content += """## 📝 Tagesordnungspunkte

"""
        for section in template['sections']:
            agenda_content += f"""### TOP {top_number}: {section['title']}
"""
            if section.get('content'):
                agenda_content += f"""{section['content']}

"""
            agenda_content += "\n"
            top_number += 1

        agenda_content += "---\n\n"

    # Asana Agenda-Items (falls vorhanden und kombiniert)
    if combine_with_asana and asana_data and asana_data.get('agenda_items'):
        for item in asana_data['agenda_items']:
            assignee_str = f" (Themenverantwortlich: {item['assignee']})" if item['assignee'] else ""
            agenda_content += f"""### TOP {top_number}: {item['name']}{assignee_str}
"""
            if item['notes']:
                agenda_content += f"""{item['notes']}

"""
            agenda_content += "\n"
            top_number += 1

        agenda_content += "---\n\n"

    # Standard-Abschnitte
    agenda_content += """## 💬 Diskussion & Entscheidungen

*Notizen während des Meetings:*
-

---

## ✅ Weitere Schritte & Aufgaben

*Neue Aufgaben aus diesem Meeting:*
-

---

## 📅 Nächstes Meeting

**Termin:**
**Themen:**

"""

    return agenda_content


def sanitize_filename(name: str, max_length: int = 100) -> str:
    """
    Bereinigt einen String für die Verwendung als Dateinamen.

    Args:
        name: Zu bereinigender String
        max_length: Maximale Länge des Dateinamens

    Returns:
        Bereinigter String
    """
    import re

    # Entferne Anführungszeichen und problematische Zeichen
    invalid_chars = '<>:"/\\|?*\n\r\t'
    for char in invalid_chars:
        name = name.replace(char, '')

    # Ersetze Leerzeichen durch Unterstriche
    name = name.replace(' ', '_')

    # Entferne mehrfache Unterstriche
    while '__' in name:
        name = name.replace('__', '_')

    # Entferne führende/trailing Unterstriche
    name = name.strip('_')

    # Trimme auf maximale Länge
    if len(name) > max_length:
        name = name[:max_length].strip('_')

    return name


def convert_markdown_to_pdf(markdown_file: Path, output_pdf: Path) -> bool:
    """
    Konvertiert eine Markdown-Datei zu PDF.

    Args:
        markdown_file: Pfad zur .md Datei
        output_pdf: Pfad für die Ausgabe-PDF

    Returns:
        True bei Erfolg, False bei Fehler
    """
    try:
        import markdown
        from weasyprint import HTML, CSS
        from io import BytesIO

        # Lese Markdown
        with open(markdown_file, 'r', encoding='utf-8') as f:
            md_content = f.read()

        # Konvertiere zu HTML
        html_content = markdown.markdown(md_content, extensions=['extra', 'nl2br'])

        # Füge CSS für besseres Layout hinzu
        full_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    line-height: 1.6;
                    max-width: 800px;
                    margin: 40px auto;
                    padding: 20px;
                    color: #333;
                }}
                h1 {{
                    color: #2c3e50;
                    border-bottom: 2px solid #3498db;
                    padding-bottom: 10px;
                }}
                h2 {{
                    color: #34495e;
                    margin-top: 30px;
                }}
                h3 {{
                    color: #7f8c8d;
                }}
                strong {{
                    color: #2c3e50;
                }}
                ul, ol {{
                    margin-left: 20px;
                }}
                hr {{
                    border: none;
                    border-top: 1px solid #ddd;
                    margin: 20px 0;
                }}
            </style>
        </head>
        <body>
            {html_content}
        </body>
        </html>
        """

        # Konvertiere HTML zu PDF
        HTML(string=full_html).write_pdf(output_pdf)

        return True

    except Exception as e:
        st.error(f"Fehler bei PDF-Konvertierung: {e}")
        return False


def generate_agenda_with_asana_context(
    asana_agent,
    project_gid: str,
    meeting_title: str,
    meeting_description: str = ""
) -> str:
    """
    Generiert eine Agenda mit Kontext aus Asana (Agenda-Items und offene Protokollpunkte).

    Args:
        asana_agent: AsanaAgent-Instanz
        project_gid: Asana-Projekt-GID
        meeting_title: Titel des Meetings
        meeting_description: Optionale Meeting-Beschreibung

    Returns:
        Formatierte Agenda als Markdown-String
    """
    from datetime import datetime

    date_str = datetime.now().strftime("%d.%m.%Y")

    # Stelle sicher, dass die notwendigen Sections existieren
    agenda_section_gid = asana_agent.ensure_section_exists(project_gid, "Agenda")
    protocol_section_gid = asana_agent.ensure_section_exists(project_gid, "Protokolle")

    agenda_content = f"""# Agenda: {meeting_title}
**Datum:** {date_str}

⚠️ **Keine Besprechung ohne Protokoll - Aufzeichnung aktivieren!**

---

"""

    # Füge Meeting-Beschreibung hinzu, falls vorhanden
    if meeting_description:
        agenda_content += f"""## Kontext
{meeting_description}

---

"""

    # TOP-Counter für Nummerierung
    top_number = 1

    # 1. Rückblick auf offene Punkte aus vorherigen Protokollen - als TOP 1
    if protocol_section_gid:
        open_protocols = asana_agent.find_protocol_tasks_with_open_items(
            project_gid=project_gid,
            protocol_section_name="Protokolle"
        )

        if open_protocols:
            agenda_content += f"""## TOP {top_number}: Rückblick - Offene Punkte aus vorherigen Besprechungen

"""
            # Sammle alle offenen Punkte in einem einzigen Tagesordnungspunkt
            for protocol in open_protocols:
                protocol_name_short = protocol['protocol_name'].replace('📄 Protokoll ', '')
                for item in protocol['open_items']:
                    assignee_str = f" - Zuständig: {item['assignee']}" if item['assignee'] else ""
                    due_str = f" - Fällig: {item['due_on']}" if item['due_on'] else ""
                    agenda_content += f"""- [ ] {item['name']}{assignee_str}{due_str} *(aus {protocol_name_short})*
"""

            agenda_content += "\n---\n\n"
            top_number += 1

    # 2. Tagesordnungspunkte aus dem "Agenda"-Section - mit TOP-Nummerierung
    if agenda_section_gid:
        agenda_items = asana_agent.get_tasks_from_section(
            section_gid=agenda_section_gid,
            limit=50,
            include_completed=False
        )

        if agenda_items:
            agenda_content += """## 📝 Tagesordnungspunkte

"""
            for item in agenda_items:
                assignee_str = f" (Themenverantwortlich: {item['assignee']})" if item['assignee'] else ""
                agenda_content += f"""### TOP {top_number}: {item['name']}{assignee_str}
"""
                if item['notes']:
                    agenda_content += f"""{item['notes']}

"""
                agenda_content += "\n"
                top_number += 1

            agenda_content += "---\n\n"
        else:
            agenda_content += """## 📝 Tagesordnungspunkte

*Noch keine Tagesordnungspunkte im Asana-Board hinterlegt.*

---

"""

    # 3. Standardabschnitte
    agenda_content += """## 💬 Diskussion & Entscheidungen

*Notizen während des Meetings:*
-

---

## ✅ Weitere Schritte & Aufgaben

*Neue Aufgaben aus diesem Meeting:*
-

---

## 📅 Nächstes Meeting

**Termin:**
**Themen:**

"""

    return agenda_content


def create_protocol_task_in_asana(
    asana_agent,
    project_gid: str,
    meeting_title: str,
    protocol_text: str,
    protocol_file_path: Optional[Path] = None,
    pdf_file_path: Optional[Path] = None,
    outlook_event_id: Optional[str] = None,
    outlook_tool = None
) -> Dict[str, Any]:
    """
    Erstellt eine zentrale Protokoll-Aufgabe in Asana mit optionalem PDF-Anhang
    und verschiebt sie automatisch in den "Protokolle"-Section.
    Setzt optional die Kategorie "Protokoll" im Outlook-Termin.

    Args:
        asana_agent: AsanaAgent-Instanz
        project_gid: Asana-Projekt-GID
        meeting_title: Titel des Meetings
        protocol_text: Vollständiger Protokoll-Text
        protocol_file_path: Optional - Pfad zur .md Datei
        pdf_file_path: Optional - Pfad zur PDF-Datei zum Anhängen
        outlook_event_id: Optional - Outlook Event-ID für Kategorie-Zuweisung
        outlook_tool: Optional - OutlookGraphTool-Instanz

    Returns:
        Result-Dict mit success, task_gid, permalink_url und ggf. error
    """
    try:
        from datetime import datetime

        date_str = datetime.now().strftime("%Y-%m-%d")
        task_title = f"📄 Protokoll {date_str} - {meeting_title}"

        # Stelle sicher, dass der "Protokolle"-Section existiert
        protocol_section_gid = asana_agent.ensure_section_exists(project_gid, "Protokolle")

        # Erstelle Hauptaufgabe
        result = asana_agent.create_task(
            name=task_title,
            notes=protocol_text,
            project_gid=project_gid
        )

        if not result.get('success'):
            return result

        task_gid = result.get('task_gid')

        # Verschiebe in "Protokolle"-Section
        if protocol_section_gid:
            section_result = asana_agent.add_task_to_section(
                task_gid=task_gid,
                section_gid=protocol_section_gid
            )

            if section_result.get('success'):
                print(f"[create_protocol_task_in_asana] ✓ Aufgabe in 'Protokolle'-Section verschoben")
            else:
                print(f"[create_protocol_task_in_asana] ⚠️ Konnte Aufgabe nicht in Section verschieben: {section_result.get('error')}")

        # Hänge PDF an, falls vorhanden
        if pdf_file_path and pdf_file_path.exists():
            attachment_result = asana_agent.attach_file_to_task(
                task_gid=task_gid,
                file_path=str(pdf_file_path)
            )

            if not attachment_result.get('success'):
                print(f"[create_protocol_task_in_asana] ⚠️ PDF-Anhang fehlgeschlagen: {attachment_result.get('error')}")

        # Füge "Protokoll"-Kategorie + Betreff-Prefix zum Outlook-Termin hinzu (falls angegeben)
        if outlook_event_id and outlook_tool:
            try:
                category_result = outlook_tool.add_category_to_event(
                    event_id=outlook_event_id,
                    category="Protokoll"
                )
                if category_result.get('success'):
                    print(f"[create_protocol_task_in_asana] ✓ Kategorie 'Protokoll' zum Termin hinzugefügt")
                else:
                    print(f"[create_protocol_task_in_asana] ⚠️ Kategorie-Zuweisung fehlgeschlagen: {category_result.get('error')}")
            except Exception as e:
                print(f"[create_protocol_task_in_asana] ⚠️ Fehler beim Setzen der Kategorie: {e}")

            try:
                prefix_result = outlook_tool.add_protocol_subject_prefix(event_id=outlook_event_id)
                if prefix_result.get('success'):
                    print(f"[create_protocol_task_in_asana] ✓ Betreff-Prefix '📄 ' gesetzt")
                else:
                    print(f"[create_protocol_task_in_asana] ⚠️ Betreff-Prefix fehlgeschlagen: {prefix_result.get('error')}")
            except Exception as e:
                print(f"[create_protocol_task_in_asana] ⚠️ Fehler beim Setzen des Betreff-Prefix: {e}")

        return {
            'success': True,
            'task_gid': task_gid,
            'task_name': result.get('task_name'),
            'permalink_url': result.get('permalink_url', ''),
            'message': f'Protokoll-Aufgabe erstellt: {task_title}'
        }

    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


@st.cache_data(ttl=600, show_spinner=False)
def cached_get_asana_projects(_asana_agent):
    """Cache für Asana-Projekte (10 Min TTL)"""
    return _asana_agent.list_projects()


@st.cache_data(ttl=10, show_spinner=False)
def load_emails_from_database(db_path: str = "data/email_cache.db"):
    """
    Lädt Emails aus Datenbank (mit 10s Cache)

    Returns:
        Liste von Email-Dicts mit allen Analyse-Daten
    """
    from utils.database import EmailDatabase

    db = EmailDatabase(db_path)

    # Hole Emails mit Status: analyzed und alle pending-Stati
    emails = db.get_emails_by_status(
        ['analyzed', 'pending_asana', 'pending_forward', 'pending_archive', 'pending_reply'],
        limit=50
    )

    return emails


def render_email_action_chat():
    """
    Vereinfachter Dialog für Forward/Reply - OHNE LLM
    Nutzt vor-generierte Drafts aus DB
    """
    if not st.session_state.email_chat_active or not st.session_state.email_chat_data:
        return

    email_data = st.session_state.email_chat_data
    action_type = email_data.get('action_type')  # 'forward' oder 'reply'
    email_id = email_data.get('email_id')
    email_db_id = email_data.get('email_db_id')
    subject = email_data.get('subject', '')
    sender = email_data.get('sender', '')
    body = email_data.get('body', '')
    forwarding_rule = email_data.get('forwarding_rule')

    # Header
    if action_type == 'forward':
        st.subheader("↗️ Email weiterleiten")
    else:
        st.subheader("✉️ Email beantworten")

    # Zeige Forwarding-Rule Vorschlag (falls vorhanden)
    if action_type == 'forward' and forwarding_rule:
        forward_to = forwarding_rule.get('forward_to', '')
        template = forwarding_rule.get('template', '')
        st.info(f"💡 **Regel-Vorschlag:** An {forward_to} weiterleiten\n\nVorgeschlagener Text: {template}")

    # Email-Kontext anzeigen
    with st.expander("📧 Email-Details", expanded=False):
        st.markdown(f"**Betreff:** {subject}")
        st.markdown(f"**Von:** {sender}")
        st.markdown(f"**Inhalt:**")
        st.text_area("Email-Text", body, height=200, disabled=True, key=f"email_body_{email_id}")

    st.markdown("---")

    # NEU: Hole Draft aus DB (KEIN LLM-Call!)
    from utils.database import EmailDatabase
    db = EmailDatabase()
    email = db.get_email_by_id(email_db_id)
    draft_reply = email.get('draft_reply', '') if email else ''

    # Zeige Draft als Suggestion
    if draft_reply:
        st.info("💡 **Vorgeschlagene Antwort:**")
        st.text_area("Draft", draft_reply, height=150, disabled=True, key="draft_preview")

    st.markdown("### Ihre Nachricht")

    # Reply-All Option nur bei Antworten
    if action_type == 'reply':
        reply_all = st.checkbox(
            "An alle antworten (Reply All)",
            value=False,
            key=f'reply_all_{email_id}'
        )

    # Input für Empfänger (nur bei Forward)
    if action_type == 'forward':
        recipients_input = st.text_input(
            "An (Komma-getrennt)",
            value=forwarding_rule.get('forward_to', '') if forwarding_rule else '',
            placeholder="max@firma.de, anna@firma.de",
            key=f"recipients_{email_id}"
        )

    # Bestimme initial value für message_input
    initial_message = ""
    if action_type == 'reply' and draft_reply:
        initial_message = draft_reply
    elif action_type == 'forward' and forwarding_rule:
        initial_message = forwarding_rule.get('template', '')

    # Input für finale Message (pre-filled mit Draft)
    message_input = st.text_area(
        "Nachricht bearbeiten",
        value=initial_message,
        height=250,
        key=f"message_{email_id}"
    )

    # Buttons
    col1, col2 = st.columns(2)
    with col1:
        if st.button("✅ Senden", type="primary", key=f"send_{email_id}"):
            # Validierung
            if action_type == 'forward' and not recipients_input:
                st.error("❌ Bitte Empfänger angeben!")
            elif not message_input:
                st.error("❌ Bitte Nachricht eingeben!")
            else:
                # Schreibe in Action Queue (non-blocking!)
                action_data = {'comment': message_input}

                if action_type == 'forward':
                    recipients = [r.strip() for r in recipients_input.split(',') if r.strip()]
                    action_data['to_recipients'] = recipients
                    db.create_action(email_db_id, 'forward', action_data)
                    db.update_email_status(email_db_id, 'pending_forward')
                    success_msg = f"✅ Wird an {', '.join(recipients)} weitergeleitet..."
                else:  # reply
                    reply_all = st.session_state.get(f'reply_all_{email_id}', False)
                    action_data['reply_all'] = reply_all
                    action_data['archive_after_reply'] = True
                    db.create_action(email_db_id, 'reply', action_data)
                    db.update_email_status(email_db_id, 'pending_reply')
                    success_msg = "✅ Antwort wird gesendet..."

                # Cache invalidieren
                load_emails_from_database.clear()

                st.success(success_msg)

                # Chat schließen
                st.session_state.email_chat_active = False
                st.session_state.email_chat_data = None
                st.session_state.email_chat_history = []

                time.sleep(0.5)
                st.rerun()

    with col2:
        if st.button("❌ Abbrechen", key=f"cancel_{email_id}"):
            st.session_state.email_chat_active = False
            st.session_state.email_chat_data = None
            st.session_state.email_chat_history = []
            st.rerun()


def render_inbox_tab():
    """
    Radikale Vereinfachung: Nur DB lesen/schreiben, KEINE API-Calls!
    """
    import time
    start_time = time.time()
    print(f"[DEBUG] render_inbox_tab START @ {start_time}")

    # Prüfe ob Email-Chat aktiv ist
    if 'email_chat_active' in st.session_state and st.session_state.email_chat_active:
        from render_email_chat import render_email_chat
        render_email_chat()
        return

    st.markdown("## 📬 Posteingang")
    st.caption("💡 **Neue asynchrone Architektur** - UI reagiert sofort, Worker verarbeitet im Hintergrund")
    st.divider()

    # Initialisiere neue EmailDB
    from database.email_db import EmailDB
    db_start = time.time()
    db = EmailDB()
    print(f"[DEBUG] EmailDB init took {(time.time()-db_start)*1000:.2f}ms")

    # Stats aus DB
    stats = db.get_stats()
    total_unread = stats.get('unread', 0)
    processing = stats.get('processing', 0)
    done = stats.get('done', 0)
    errors = stats.get('error', 0)

    # Metriken
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("📧 Ungelesen", total_unread)
    with col2:
        st.metric("⚙️ In Bearbeitung", processing)
    with col3:
        st.metric("✅ Erledigt", done)
    with col4:
        st.metric("❌ Fehler", errors)

    st.divider()

    # Lade ungelesene Emails
    emails = db.get_unread_emails(limit=50)

    if not emails:
        st.info("✅ Keine ungelesenen E-Mails!")
        st.markdown("💡 Der Background Worker analysiert neue E-Mails automatisch.")
        return

    st.markdown(f"**{len(emails)} ungelesene E-Mails**")
    st.divider()

    # Email-Karten
    cards_start = time.time()
    for idx, email in enumerate(emails):
        render_simple_email_card(email, idx, db)
    print(f"[DEBUG] Rendering cards took {(time.time()-cards_start)*1000:.2f}ms")
    print(f"[DEBUG] render_inbox_tab TOTAL took {(time.time()-start_time)*1000:.2f}ms")


def render_simple_email_card(email: Dict[str, Any], idx: int, db: 'EmailDB'):
    """
    Einfache Email-Karte - nur anzeigen und DB-Instruktionen setzen
    """
    # Prioritäts-Badge
    priority = email.get('priority', 3)
    priority_badges = {
        5: "🔴 Kritisch",
        4: "🟠 Dringend",
        3: "🟡 Normal",
        2: "🟢 Niedrig",
        1: "⚪ Sehr niedrig"
    }
    priority_badge = priority_badges.get(priority, "🟡 Normal")

    # Sentiment
    sentiment = email.get('sentiment', 'neutral')
    sentiment_emojis = {
        'positiv': '😊',
        'neutral': '😐',
        'negativ': '😟',
        'dringend': '⚡'
    }
    sentiment_emoji = sentiment_emojis.get(sentiment, '😐')

    # Card Container
    priority_colors = {
        5: "#ffebee",
        4: "#fff3e0",
        3: "#f5f5f5",
        2: "#e8f5e9",
        1: "#fafafa"
    }
    bg_color = priority_colors.get(priority, "#f5f5f5")

    with st.container():
        st.markdown(
            f"""<div style="padding: 1rem; background-color: {bg_color}; border-radius: 0.5rem;
                        border-left: 4px solid {'#d32f2f' if priority >= 4 else '#757575'};
                        margin-bottom: 1rem;"></div>""",
            unsafe_allow_html=True
        )

        # Header
        col1, col2, col3 = st.columns([3, 1, 1])
        with col1:
            st.markdown(f"### {email.get('subject', 'Kein Betreff')}")
        with col2:
            st.markdown(f"**{priority_badge}**")
        with col3:
            st.markdown(f"**{sentiment_emoji} {sentiment.title()}**")

        # Meta
        col1, col2 = st.columns([2, 1])
        with col1:
            st.markdown(f"**Von:** {email.get('sender_name', 'Unbekannt')} `<{email.get('sender_email', '')}>`")
        with col2:
            st.markdown(f"**{email.get('received_dt', '')[:16]}**")

        # Kategorie
        st.markdown(f"**Kategorie:** {email.get('category', 'Sonstiges')}")

        # Expander: Details
        with st.expander("📝 Zusammenfassung & Details"):
            st.markdown(f"**Zusammenfassung:** {email.get('summary', 'Keine Zusammenfassung')}")

            action_items = email.get('action_items', [])
            if action_items:
                st.markdown("**Handlungspunkte:**")
                for item in action_items:
                    st.markdown(f"- {item}")

            if email.get('deadline'):
                st.markdown(f"**Deadline:** {email.get('deadline')}")

            st.markdown("**E-Mail-Vorschau:**")
            preview = email.get('body_preview', '')
            st.text(preview[:300] + '...' if len(preview) > 300 else preview)

        # Action Buttons
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            if st.button("💬 Bearbeiten", key=f"edit_{idx}", use_container_width=True):
                # Öffne Chat für diese Email
                st.session_state.email_chat_active = True
                st.session_state.email_chat_data = email
                st.session_state.email_chat_history = []
                st.rerun()

        with col2:
            if st.button("📤 Asana", key=f"asana_{idx}", use_container_width=True):
                # Setze instruction in DB
                db.set_instruction(email['id'], 'asana', {'project_gid': 'default'})
                db.hide_email(email['id'])
                st.success("✅ Wird an Asana gesendet...")
                st.rerun()

        with col3:
            if st.button("🗄️ Archiv", key=f"arch_{idx}", use_container_width=True):
                import time
                btn_start = time.time()
                print(f"[DEBUG] BUTTON CLICKED @ {btn_start}")

                # Setze instruction in DB
                db.set_instruction(email['id'], 'archive')
                db.hide_email(email['id'])

                print(f"[DEBUG] DB operations took {(time.time()-btn_start)*1000:.2f}ms")
                print(f"[DEBUG] About to st.rerun()...")

                st.success("✅ Wird archiviert...")
                st.rerun()

        with col4:
            if st.button("🗑️", key=f"del_{idx}", use_container_width=True):
                # Lösche direkt aus DB
                db.delete_email(email['id'])
                st.success("✅ Gelöscht!")
                st.rerun()

        st.markdown("---")


def render_transcripts_tab_OLD():
    """
    Rendert den Meeting Manager Tab - nur Nachbereitung (Vorbereitung ist jetzt in "Mein Tag")
    """
    st.header("🎙️ Meeting Manager - Nachbereitung")

    st.markdown("""
    Der Meeting Manager hilft bei der **Nachbereitung** von Meetings durch
    automatische Protokoll-Erstellung und Task-Extraktion aus Transkripten.

    💡 **Hinweis:** Die Meeting-Vorbereitung findest du jetzt im Tab **"Mein Tag"**.
    """)

    st.markdown("---")

    # Transkript-Analyse Bereich
    st.markdown("### 🔍 Transkript analysieren & Tasks extrahieren")

    # BATCH-UPLOAD: Mehrere Dateien auf einmal!
    uploaded_files = st.file_uploader(
        "📤 Transkript(e) hochladen",
        type=['txt', 'md', 'pdf'],
        accept_multiple_files=True,  # ← NEU: Mehrere Dateien!
        help="Lade ein oder mehrere Meeting-Transkripte hoch (TXT, MD oder PDF)"
    )

    processed_dir = _get_user_ctx().transcripts_processed
    processed_dir.mkdir(parents=True, exist_ok=True)

    # Initialisiere Transcript Queue im Session State
    if 'transcript_queue' not in st.session_state:
        st.session_state['transcript_queue'] = []

    # Verarbeite hochgeladene Files
    if uploaded_files:
        newly_uploaded = []
        for uploaded_file in uploaded_files:
            # Speichere Datei temporär mit Original-Namen
            file_path = processed_dir / uploaded_file.name

            # Prüfe ob Datei bereits in Queue
            existing_files = [item['filename'] for item in st.session_state['transcript_queue']]
            if uploaded_file.name not in existing_files:
                with open(file_path, 'wb') as f:
                    f.write(uploaded_file.getbuffer())

                # Füge zur Queue hinzu
                st.session_state['transcript_queue'].append({
                    'filename': uploaded_file.name,
                    'path': str(file_path),
                    'status': 'pending',  # pending, processing, completed, error
                    'protocol': None,
                    'tasks': None,
                    'error': None,
                    'uploaded_at': datetime.now().isoformat()
                })
                newly_uploaded.append(uploaded_file.name)

        if newly_uploaded:
            st.success(f"✅ {len(newly_uploaded)} Transkript(e) hochgeladen: {', '.join(newly_uploaded[:3])}{' ...' if len(newly_uploaded) > 3 else ''}")
            st.info("💡 Wähle unten einen Workflow: Einzeln oder alle auf einmal verarbeiten")

    # ========================================================================
    # QUEUE-DASHBOARD: Zeige alle Transkripte in der Warteschlange
    # ========================================================================
    if st.session_state['transcript_queue']:
        st.markdown("---")
        st.markdown("### 📊 Transkript-Warteschlange")

        queue = st.session_state['transcript_queue']

        # Statistiken
        total = len(queue)
        pending = len([item for item in queue if item['status'] == 'pending'])
        processing = len([item for item in queue if item['status'] == 'processing'])
        completed = len([item for item in queue if item['status'] == 'completed'])
        errors = len([item for item in queue if item['status'] == 'error'])

        # Status-Übersicht
        col1, col2, col3, col4, col5 = st.columns(5)
        with col1:
            st.metric("📝 Gesamt", total)
        with col2:
            st.metric("⏳ Wartend", pending)
        with col3:
            st.metric("🔄 In Bearbeitung", processing)
        with col4:
            st.metric("✅ Fertig", completed)
        with col5:
            st.metric("❌ Fehler", errors)

        # Batch-Verarbeitung Buttons
        st.markdown("#### 🎯 Aktionen")
        col_batch1, col_batch2, col_batch3 = st.columns(3)

        with col_batch1:
            if st.button("🚀 Alle verarbeiten", disabled=(pending == 0), use_container_width=True, type="primary"):
                st.session_state['batch_process_all'] = True
                st.rerun()

        with col_batch2:
            if st.button("🧹 Queue leeren", disabled=(total == 0), use_container_width=True):
                st.session_state['transcript_queue'] = []
                st.success("✅ Queue geleert!")
                st.rerun()

        with col_batch3:
            if st.button("🔄 Fertige entfernen", disabled=(completed == 0), use_container_width=True):
                st.session_state['transcript_queue'] = [
                    item for item in queue if item['status'] != 'completed'
                ]
                st.success(f"✅ {completed} fertige Transkripte entfernt!")
                st.rerun()

        # Detaillierte Liste
        st.markdown("#### 📋 Transkript-Liste")
        for idx, item in enumerate(queue):
            status = item['status']

            # Status-Icons
            status_icons = {
                'pending': '⏳',
                'processing': '🔄',
                'completed': '✅',
                'error': '❌'
            }
            status_colors = {
                'pending': 'blue',
                'processing': 'orange',
                'completed': 'green',
                'error': 'red'
            }

            icon = status_icons.get(status, '❓')
            color = status_colors.get(status, 'gray')

            with st.expander(f"{icon} {item['filename']} - Status: {status.upper()}", expanded=(status == 'processing')):
                col_info1, col_info2 = st.columns([3, 1])

                with col_info1:
                    st.caption(f"📁 Datei: `{item['filename']}`")
                    st.caption(f"📅 Hochgeladen: {datetime.fromisoformat(item['uploaded_at']).strftime('%d.%m.%Y %H:%M')}")

                    if status == 'error' and item.get('error'):
                        st.error(f"Fehler: {item['error']}")

                    if status == 'completed':
                        st.success("✅ Protokoll erstellt!")
                        if item.get('protocol'):
                            st.caption(f"📄 Protokoll: {len(item['protocol'])} Zeichen")
                        if item.get('tasks'):
                            st.caption(f"📋 Tasks: {len(item['tasks'])} gefunden")

                with col_info2:
                    # Einzelne Verarbeitung
                    if status == 'pending':
                        if st.button("▶️ Jetzt verarbeiten", key=f"process_{idx}"):
                            st.session_state['process_single_idx'] = idx
                            st.rerun()

                    # Details anzeigen
                    if status == 'completed' and item.get('protocol'):
                        if st.button("👁️ Ansehen", key=f"view_{idx}"):
                            st.session_state['view_protocol_idx'] = idx
                            st.rerun()

        st.markdown("---")

    # Finde neueste Transkript-Datei
    processed_files = sorted(
        list(processed_dir.glob("*.txt")) + list(processed_dir.glob("*.md")) + list(processed_dir.glob("*.pdf")),
        key=lambda f: f.stat().st_mtime,
        reverse=True
    )

    if not processed_files:
        st.info("Keine verarbeiteten Transkripte gefunden. Lade ein Transkript hoch.")
    else:
        # Zeige neueste Datei
        latest_file = processed_files[0]
        st.info(f"📄 Neustes Transkript: **{latest_file.name}**")

        # TAGESNAVIGATION FÜR TERMIN-AUSWAHL
        st.markdown("---")
        st.markdown("### 📅 Termin auswählen")

        # Initialisiere Datum im Session State
        if 'meeting_date_selection' not in st.session_state:
            # Versuche Datum aus Transkript-Dateiname zu extrahieren
            extracted_date = None
            try:
                # Versuche Datum aus Dateinamen zu extrahieren (Format: YYYY-MM-DD_...)
                filename = latest_file.stem
                import re
                date_match = re.search(r'(\d{4}-\d{2}-\d{2})', filename)
                if date_match:
                    date_str = date_match.group(1)
                    extracted_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    st.info(f"📅 Datum aus Transkript erkannt: {extracted_date.strftime('%d.%m.%Y')}")
            except Exception as e:
                st.warning(f"⚠️ Konnte Datum nicht aus Dateinamen extrahieren: {e}")

            st.session_state['meeting_date_selection'] = extracted_date if extracted_date else datetime.now().date()

        # Tagesnavigation
        col_prev, col_date, col_next = st.columns([1, 3, 1])

        with col_prev:
            if st.button("◀ Vorheriger Tag", key="transcript_prev_day"):
                st.session_state['meeting_date_selection'] -= timedelta(days=1)
                # Reset Termine-Trigger damit neu geladen werden kann
                st.session_state['load_meeting_events_trigger'] = False
                st.rerun()

        with col_date:
            selected_date = st.date_input(
                "Datum des Meetings:",
                value=st.session_state['meeting_date_selection'],
                key="meeting_date_picker"
            )
            if selected_date != st.session_state['meeting_date_selection']:
                st.session_state['meeting_date_selection'] = selected_date
                # Reset Termine-Trigger damit neu geladen werden kann
                st.session_state['load_meeting_events_trigger'] = False
                st.rerun()

        with col_next:
            if st.button("Nächster Tag ▶", key="transcript_next_day"):
                st.session_state['meeting_date_selection'] += timedelta(days=1)
                # Reset Termine-Trigger damit neu geladen werden kann
                st.session_state['load_meeting_events_trigger'] = False
                st.rerun()

        # Lade Termine für gewählten Tag
        orch = st.session_state.get('orchestrator')
        if not orch:
            st.warning("Orchestrator nicht initialisiert. Bitte App neu laden.")
            return

        outlook_tool = orch.outlook_tool
        day_events = []

        # Button zum Laden der Termine
        if st.button("🔄 Termine aktualisieren", key="load_meeting_events", use_container_width=True):
            st.session_state['load_meeting_events_trigger'] = True
            st.rerun()

        # Prüfe ob Termine geladen werden sollen
        if st.session_state.get('load_meeting_events_trigger', False):
            if outlook_tool.is_authenticated():
                with st.spinner("📅 Lade Termine..."):
                    try:
                        # Konvertiere date zu datetime
                        start_of_day = datetime.combine(st.session_state['meeting_date_selection'], datetime.min.time())
                        end_of_day = datetime.combine(st.session_state['meeting_date_selection'], datetime.max.time())

                        day_events = outlook_tool.get_events_for_date_range(start_of_day, end_of_day)

                        if day_events:
                            st.success(f"✓ {len(day_events)} Termin(e) am {st.session_state['meeting_date_selection'].strftime('%d.%m.%Y')} gefunden")
                        else:
                            st.info(f"📭 Keine Termine am {st.session_state['meeting_date_selection'].strftime('%d.%m.%Y')} gefunden")
                    except Exception as e:
                        st.error(f"❌ Fehler beim Laden der Termine: {e}")
                        st.caption(f"Details: {str(e)}")
            else:
                st.warning("⚠️ Outlook nicht authentifiziert. Bitte authentifizieren Sie sich in der Sidebar unter 'Microsoft Graph API'.")
        else:
            st.info("💡 Klicken Sie auf 'Termine aktualisieren' um die Termine für den ausgewählten Tag zu laden.")

        # Termin-Auswahl
        if day_events:
            event_options = ["Kein Termin zuordnen"]
            event_dict = {}

            for event in day_events:
                event_start = event.get('start')
                event_title = event.get('title', 'Ohne Titel')

                # Parse Zeit
                if isinstance(event_start, str):
                    try:
                        event_start_dt = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
                        # Konvertiere zu Berlin-Zeit
                        event_start_dt = convert_to_berlin_time(event_start_dt)
                        time_str = event_start_dt.strftime('%H:%M')
                    except:
                        time_str = event_start[:5] if len(event_start) >= 5 else "??:??"
                else:
                    event_start_converted = convert_to_berlin_time(event_start) if hasattr(event_start, 'tzinfo') else event_start
                    time_str = event_start_converted.strftime('%H:%M') if hasattr(event_start_converted, 'strftime') else "??:??"

                option_label = f"{time_str} - {event_title}"
                event_options.append(option_label)
                event_dict[option_label] = event

            # Selectbox für Meeting-Auswahl
            selected_option = st.selectbox(
                "Wähle den passenden Termin:",
                options=event_options,
                help="Wähle den Termin, zu dem dieses Transkript gehört."
            )

            # Speichere Auswahl
            if selected_option == "Kein Termin zuordnen":
                st.session_state['selected_event_for_transcript'] = None
            else:
                st.session_state['selected_event_for_transcript'] = event_dict[selected_option]

            # Zeige Details des ausgewählten Termins
            if selected_option != "Kein Termin zuordnen":
                event = event_dict[selected_option]
                with st.expander("📋 Termin-Details", expanded=False):
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        event_start = event.get('start')
                        if isinstance(event_start, str):
                            event_start_dt = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
                            # Konvertiere zu Berlin-Zeit
                            event_start_dt = convert_to_berlin_time(event_start_dt)
                            st.caption(f"🕐 Start: {event_start_dt.strftime('%H:%M')}")
                        else:
                            event_start_converted = convert_to_berlin_time(event_start) if hasattr(event_start, 'tzinfo') else event_start
                            st.caption(f"🕐 Start: {event_start_converted.strftime('%H:%M') if hasattr(event_start_converted, 'strftime') else '??:??'}")
                    with col2:
                        if event.get('location'):
                            st.caption(f"📍 Ort: {event['location']}")
                    with col3:
                        if event.get('attendees'):
                            st.caption(f"👥 {len(event['attendees'])} Teilnehmer")

                # AUTO-UMBENENNUNG: Biete an, die Datei nach Meeting-Titel umzubenennen
                if latest_file.exists():
                    # Prüfe ob Datei bereits umbenannt wurde
                    import re
                    date_match = re.search(r'^\d{4}-\d{2}-\d{2}_', latest_file.name)

                    # Prüfe ob Datei gerade hochgeladen wurde (in uploaded_files Liste)
                    is_newly_uploaded = False
                    if uploaded_files:
                        is_newly_uploaded = any(f.name == latest_file.name for f in uploaded_files)

                    if not date_match or is_newly_uploaded:
                        # Datei ist noch nicht umbenannt oder wurde gerade hochgeladen
                        st.markdown("---")
                        st.markdown("#### 📝 Transkript umbenennen")

                        # Erstelle neuen Dateinamen
                        def sanitize_filename(name: str, max_length: int = 100) -> str:
                            """Bereinigt String für Dateinamen"""
                            invalid_chars = '<>:"/\\|?*\n\r\t'
                            for char in invalid_chars:
                                name = name.replace(char, '')
                            name = name.replace(' ', '_')
                            while '__' in name:
                                name = name.replace('__', '_')
                            return name[:max_length].strip('_')

                        meeting_title = event.get('title', 'Ohne_Titel')
                        sanitized_title = sanitize_filename(meeting_title, max_length=100)
                        date_str = st.session_state['meeting_date_selection'].strftime("%Y-%m-%d")
                        file_extension = latest_file.suffix
                        new_filename = f"{date_str}_Protokoll_{sanitized_title}{file_extension}"

                        st.info(f"**Neuer Dateiname:** `{new_filename}`")

                        if st.button("✅ Datei umbenennen", key="rename_transcript", use_container_width=True):
                            try:
                                new_file_path = processed_dir / new_filename

                                # Falls Datei bereits existiert, füge Nummer hinzu
                                counter = 1
                                while new_file_path.exists():
                                    new_filename = f"{date_str}_Protokoll_{sanitized_title}_{counter}{file_extension}"
                                    new_file_path = processed_dir / new_filename
                                    counter += 1

                                # Benenne Datei um
                                import shutil
                                import time
                                shutil.move(str(latest_file), str(new_file_path))

                                st.success(f"✅ Datei erfolgreich umbenannt zu: **{new_filename}**")
                                st.balloons()
                                time.sleep(1)
                                st.rerun()

                            except Exception as e:
                                st.error(f"❌ Fehler beim Umbenennen: {e}")
                                import traceback
                                st.caption(traceback.format_exc())

        st.markdown("---")

        # AGENDA-AUSWAHL UND AUTO-FINDER
        st.markdown("### 📋 Agenda zuordnen (optional)")
        st.caption("Wenn eine Agenda vorhanden ist, wird das Protokoll streng nach der Agenda-Struktur erstellt.")

        # Suche nach verfügbaren Agendas
        agenda_dir = _get_user_ctx().data_dir / "agendas"
        agenda_files = []
        if agenda_dir.exists():
            agenda_files = sorted(
                list(agenda_dir.glob("*.pdf")),
                key=lambda f: f.stat().st_mtime,
                reverse=True
            )

        # Auto-Finder: Versuche Agenda automatisch zu finden
        auto_matched_agenda = None
        selected_event = st.session_state.get('selected_event_for_transcript')

        if agenda_files and selected_event:
            from difflib import SequenceMatcher

            def fuzzy_match_score(text1: str, text2: str) -> float:
                """Berechnet Ähnlichkeits-Score zwischen zwei Strings (0.0 - 1.0)"""
                return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()

            event_title = selected_event.get('title', '')
            event_date = st.session_state.get('meeting_date_selection')

            best_match = None
            best_score = 0.0

            for agenda_file in agenda_files:
                # Extrahiere Datum und Titel aus Dateinamen (Format: Agenda_YYYY-MM-DD_Title.pdf)
                filename = agenda_file.stem  # Ohne .pdf
                parts = filename.split('_', 2)  # Split in maximal 3 Teile

                if len(parts) >= 3 and parts[0] == "Agenda":
                    try:
                        # Parse Datum
                        agenda_date_str = parts[1]  # YYYY-MM-DD
                        agenda_date = datetime.strptime(agenda_date_str, "%Y-%m-%d").date()

                        # Parse Titel
                        agenda_title = parts[2].replace('_', ' ')

                        # Datum-Match (exakt)
                        date_match = (agenda_date == event_date)

                        # Titel-Match (Fuzzy)
                        title_score = fuzzy_match_score(event_title, agenda_title)

                        # Kombinierter Score (Datum hat Priorität)
                        if date_match and title_score > 0.5:
                            combined_score = title_score
                            if combined_score > best_score:
                                best_score = combined_score
                                best_match = agenda_file
                    except:
                        pass

            if best_match and best_score >= 0.5:
                auto_matched_agenda = best_match
                st.success(f"✅ Agenda automatisch zugeordnet: **{best_match.name}** (Match: {best_score:.0%})")

        # Manuelle Agenda-Auswahl
        if agenda_files:
            agenda_options = ["Keine Agenda verwenden"] + [f.name for f in agenda_files]

            # Default-Index basierend auf Auto-Match
            default_idx = 0
            if auto_matched_agenda:
                try:
                    default_idx = agenda_options.index(auto_matched_agenda.name)
                except ValueError:
                    default_idx = 0

            selected_agenda_name = st.selectbox(
                "Wähle eine Agenda:",
                options=agenda_options,
                index=default_idx,
                help="Wähle die passende Agenda für dieses Meeting. Das Protokoll wird dann nach der Agenda-Struktur erstellt."
            )

            # Speichere Auswahl
            if selected_agenda_name == "Keine Agenda verwenden":
                st.session_state['selected_agenda_for_protocol'] = None
            else:
                # Finde Agenda-Datei
                for f in agenda_files:
                    if f.name == selected_agenda_name:
                        st.session_state['selected_agenda_for_protocol'] = f
                        break

            # Zeige Agenda-Vorschau
            if st.session_state.get('selected_agenda_for_protocol'):
                agenda_file = st.session_state['selected_agenda_for_protocol']
                with st.expander("📄 Agenda-Vorschau", expanded=False):
                    try:
                        # Extrahiere Text aus PDF
                        try:
                            from pypdf import PdfReader
                        except ImportError:
                            from PyPDF2 import PdfReader

                        reader = PdfReader(str(agenda_file))
                        agenda_preview_text = ""
                        for page in reader.pages[:3]:  # Max. 3 Seiten Vorschau
                            agenda_preview_text += page.extract_text()

                        st.text_area(
                            "Agenda-Inhalt:",
                            agenda_preview_text[:2000],  # Max. 2000 Zeichen
                            height=200,
                            disabled=True
                        )
                    except Exception as e:
                        st.warning(f"⚠️ Vorschau nicht verfügbar: {e}")
        else:
            st.info("📭 Keine Agendas gefunden. Erstellen Sie Agendas im Tab 'Mein Tag' bei der Meeting-Vorbereitung.")
            st.session_state['selected_agenda_for_protocol'] = None

        st.markdown("---")

        # ========================================================================
        # BATCH-VERARBEITUNG: Verarbeite alle Transkripte in der Queue
        # ========================================================================
        if st.session_state.get('batch_process_all', False):
            st.markdown("### 🚀 Batch-Verarbeitung")

            # Hole LLM
            if 'orchestrator' not in st.session_state:
                st.error("Orchestrator nicht verfügbar")
                st.session_state['batch_process_all'] = False
                st.stop()

            orch = st.session_state.orchestrator
            llm = orch.research_agent.llm

            # Filtere pending Transkripte
            queue = st.session_state['transcript_queue']
            pending_items = [(idx, item) for idx, item in enumerate(queue) if item['status'] == 'pending']

            if not pending_items:
                st.info("✅ Alle Transkripte bereits verarbeitet!")
                st.session_state['batch_process_all'] = False
                st.stop()

            st.info(f"🔄 Verarbeite {len(pending_items)} Transkript(e)...")

            # Gesamtfortschritt
            overall_progress = st.progress(0, text="🚀 Starte Batch-Verarbeitung...")

            # Verarbeite jedes Transkript
            for progress_idx, (queue_idx, item) in enumerate(pending_items, 1):
                # Update Status
                st.session_state['transcript_queue'][queue_idx]['status'] = 'processing'

                # Zeige aktuelles Transkript
                st.markdown(f"#### 📄 {progress_idx}/{len(pending_items)}: {item['filename']}")

                try:
                    # Lade Transkript
                    file_path = Path(item['path'])
                    if file_path.suffix.lower() == '.pdf':
                        from langchain_community.document_loaders import PyPDFLoader
                        loader = PyPDFLoader(str(file_path))
                        pages = loader.load()
                        transcript_text = "\n\n".join([page.page_content for page in pages])
                    else:
                        transcript_text = file_path.read_text(encoding='utf-8')

                    # Meeting-Titel
                    meeting_title = file_path.stem.split('_', 1)[-1] if '_' in file_path.stem else file_path.stem

                    # Erstelle Protokoll mit Streaming
                    st.markdown("##### 🎬 Generiere Protokoll...")
                    protocol_preview = st.empty()
                    protocol_parts = []

                    for chunk in extract_protocol_from_transcript_streaming(
                        transcript_text,
                        meeting_title,
                        llm,
                        attendees=None,
                        meeting_date=None,
                        agenda_text=None
                    ):
                        protocol_parts.append(chunk)

                        # Live-Vorschau (alle 10 Chunks)
                        if len(protocol_parts) % 10 == 0:
                            protocol_preview.markdown(''.join(protocol_parts)[:500] + "...")

                    protocol_text = ''.join(protocol_parts)
                    protocol_preview.empty()

                    # Speichere Ergebnis
                    st.session_state['transcript_queue'][queue_idx]['protocol'] = protocol_text
                    st.session_state['transcript_queue'][queue_idx]['status'] = 'completed'

                    st.success(f"✅ Protokoll erstellt: {len(protocol_text)} Zeichen")

                except Exception as e:
                    st.error(f"❌ Fehler: {e}")
                    st.session_state['transcript_queue'][queue_idx]['status'] = 'error'
                    st.session_state['transcript_queue'][queue_idx]['error'] = str(e)

                # Update Gesamtfortschritt
                overall_progress.progress(
                    int((progress_idx / len(pending_items)) * 100),
                    text=f"🔄 {progress_idx}/{len(pending_items)} verarbeitet"
                )

                st.markdown("---")

            # Fertig!
            overall_progress.progress(100, text="🎉 Batch-Verarbeitung abgeschlossen!")
            st.balloons()

            # Cleanup
            st.session_state['batch_process_all'] = False

            # Zusammenfassung
            completed_count = len([item for item in st.session_state['transcript_queue'] if item['status'] == 'completed'])
            error_count = len([item for item in st.session_state['transcript_queue'] if item['status'] == 'error'])

            st.success(f"✅ Fertig! {completed_count} erfolgreich, {error_count} Fehler")

            if st.button("🔙 Zurück zur Übersicht"):
                st.rerun()

            st.stop()

        # Button zum Laden und Analysieren (EINZELN)
        if st.button("🔍 Protokoll & Tasks erstellen", type="primary"):
            with st.spinner("Analysiere Transkript und erstelle Protokoll..."):
                try:
                    # Lade Transkript
                    if latest_file.suffix.lower() == '.pdf':
                        from langchain_community.document_loaders import PyPDFLoader
                        loader = PyPDFLoader(str(latest_file))
                        pages = loader.load()
                        transcript_text = "\n\n".join([page.page_content for page in pages])
                    else:
                        transcript_text = latest_file.read_text(encoding='utf-8')

                    # LLM initialisieren
                    if 'orchestrator' in st.session_state:
                        orch = st.session_state.orchestrator

                        # Nutze Research Agent's LLM
                        research_agent = orch.research_agent
                        llm = research_agent.llm

                        # Meeting-Titel aus Dateiname extrahieren
                        meeting_title = latest_file.stem.split('_', 1)[-1] if '_' in latest_file.stem else latest_file.stem

                        # Hole gewählten Termin
                        selected_event = st.session_state.get('selected_event_for_transcript')
                        attendees = None
                        meeting_date = None

                        # Extrahiere Teilnehmer aus Outlook-Termin
                        if selected_event:
                            if selected_event.get('attendees'):
                                attendees = []
                                for att in selected_event['attendees']:
                                    if isinstance(att, dict):
                                        attendees.append(att.get('name', att.get('email', '')))
                                    elif isinstance(att, str):
                                        attendees.append(att)
                                    else:
                                        attendees.append(str(att))

                            # Extrahiere Datum
                            if selected_event.get('start'):
                                event_start = selected_event['start']
                                if isinstance(event_start, str):
                                    event_start_dt = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
                                    # Konvertiere zu Berlin-Zeit
                                    event_start_dt = convert_to_berlin_time(event_start_dt)
                                    meeting_date = event_start_dt.strftime('%d.%m.%Y %H:%M')
                                else:
                                    event_start_converted = convert_to_berlin_time(event_start) if hasattr(event_start, 'tzinfo') else event_start
                                    meeting_date = event_start_converted.strftime('%d.%m.%Y %H:%M')

                        # Lade Agenda-Text falls vorhanden
                        agenda_text = None
                        selected_agenda = st.session_state.get('selected_agenda_for_protocol')
                        if selected_agenda:
                            try:
                                # Extrahiere Text aus Agenda-PDF
                                try:
                                    from pypdf import PdfReader
                                except ImportError:
                                    from PyPDF2 import PdfReader

                                reader = PdfReader(str(selected_agenda))
                                agenda_text = ""
                                for page in reader.pages:
                                    agenda_text += page.extract_text()

                                st.info(f"📋 Agenda '{selected_agenda.name}' wird in die Protokoll-Erstellung einbezogen")
                            except Exception as e:
                                st.warning(f"⚠️ Fehler beim Laden der Agenda: {e}")
                                agenda_text = None

                        # Extrahiere Protokoll (mit Outlook-Teilnehmern und Agenda)
                        # MIT STREAMING & LIVE-FEEDBACK!

                        st.markdown("---")
                        st.markdown("### 🎬 Live-Protokoll-Erstellung")

                        # Fortschrittsanzeige
                        progress_bar = st.progress(0, text="⏳ Vorbereitung...")
                        status_text = st.empty()
                        protocol_preview = st.empty()

                        # Status-Updates
                        import time

                        # Schritt 1: Vorbereitung (10%)
                        progress_bar.progress(10, text="📋 Analysiere Transkript...")
                        status_text.info(f"📊 Transkript: {len(transcript_text.split())} Wörter | {len(transcript_text)} Zeichen")
                        time.sleep(0.3)

                        # Schritt 2: LLM-Start (20%)
                        progress_bar.progress(20, text="🤖 Starte KI-Verarbeitung...")
                        status_text.info("🔄 Verbinde mit Claude Sonnet 4.5...")
                        time.sleep(0.3)

                        # Schritt 3: STREAMING (20-90%)
                        progress_bar.progress(30, text="✨ Generiere Protokoll live...")
                        status_text.success("🎯 Protokoll wird erstellt - Du siehst es in Echtzeit!")

                        protocol_parts = []
                        chunk_count = 0

                        try:
                            # STREAMING: Generator-Funktion
                            for chunk in extract_protocol_from_transcript_streaming(
                                transcript_text,
                                meeting_title,
                                llm,
                                attendees=attendees,
                                meeting_date=meeting_date,
                                agenda_text=agenda_text
                            ):
                                protocol_parts.append(chunk)
                                chunk_count += 1

                                # Update Fortschritt (30-90%)
                                # Schätze basierend auf Chunk-Count (erste 100 Chunks = 30-90%)
                                estimated_progress = min(90, 30 + (chunk_count * 0.6))
                                progress_bar.progress(int(estimated_progress), text=f"✨ Generiere... ({chunk_count} Tokens)")

                                # Live-Vorschau aktualisieren (alle 5 Chunks für Performance)
                                if chunk_count % 5 == 0:
                                    current_protocol = ''.join(protocol_parts)
                                    protocol_preview.markdown(current_protocol)

                            # Finales Protokoll
                            protocol_text = ''.join(protocol_parts)

                            # Schritt 4: Finalisierung (95%)
                            progress_bar.progress(95, text="✅ Finalisiere Protokoll...")
                            protocol_preview.markdown(protocol_text)
                            time.sleep(0.3)

                            # Schritt 5: Fertig! (100%)
                            progress_bar.progress(100, text="🎉 Protokoll erstellt!")
                            status_text.success(f"✅ Fertig! {chunk_count} Tokens generiert | {len(protocol_text)} Zeichen")
                            time.sleep(1)

                            # Cleanup
                            progress_bar.empty()
                            status_text.empty()
                            protocol_preview.empty()

                        except Exception as e:
                            progress_bar.empty()
                            status_text.error(f"❌ Fehler: {e}")
                            protocol_text = f"# Fehler bei Protokoll-Erstellung\n\n{str(e)}"

                        # OPTIMIERUNG: Task-Extraktion wird NICHT sofort durchgeführt!
                        # Grund: User will Protokoll erst bearbeiten, dann Tasks extrahieren
                        # → Spart 30-60 Sekunden Wartezeit
                        # Tasks können später mit "🔄 Aufgaben neu extrahieren" Button erstellt werden

                        # Speichere in Session State
                        st.session_state['extracted_protocol'] = protocol_text
                        st.session_state['extracted_tasks'] = []  # Leer - wird später extrahiert
                        st.session_state['transcript_source'] = latest_file.name
                        st.session_state['meeting_title'] = meeting_title
                        st.session_state['selected_event'] = selected_event

                        # Zähle Platzhalter
                        placeholder_count = count_placeholders_in_protocol(protocol_text)

                        st.success(f"✓ Protokoll erstellt!")
                        if placeholder_count > 0:
                            st.warning(f"⚠️ {placeholder_count} Platzhalter [?] gefunden - bitte prüfen und ergänzen!")
                        st.info(f"💡 Tipp: Bearbeiten Sie das Protokoll und klicken Sie dann auf '🔄 Aufgaben neu extrahieren'")
                        st.rerun()
                    else:
                        st.error("Orchestrator nicht verfügbar")

                except Exception as e:
                    st.error(f"Fehler bei der Analyse: {e}")
                    import traceback
                    st.code(traceback.format_exc())

    # Wenn Protokoll und Tasks extrahiert wurden, zeige Editor
    if 'extracted_protocol' in st.session_state:
        st.markdown("---")
        st.markdown("### ✏️ Protokoll & Aufgaben bearbeiten")

        st.caption(f"Quelle: {st.session_state.get('transcript_source', 'Unbekannt')}")

        st.markdown("---")

        # PROTOKOLL BEARBEITEN (VOLLE BREITE)
        st.markdown("#### 📄 Protokoll")

        # Platzhalter-Warnung
        protocol_text = st.session_state.get('extracted_protocol', '')
        placeholder_count = count_placeholders_in_protocol(protocol_text)

        if placeholder_count > 0:
            st.warning(f"⚠️ **{placeholder_count} Platzhalter [?] gefunden** - bitte vor dem Finalisieren ergänzen!")

        # Zeige Mapping-Status (aus Protokoll und Tasks extrahiert)
        current_tasks = st.session_state.get('extracted_tasks', [])
        task_names = extract_all_person_names(protocol_text, current_tasks)
        if task_names:
            try:
                import json
                config_path = Path('config/mapping_config.json')
                if config_path.exists():
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config = json.load(f)
                        current_mappings = config.get('user_mappings', {})
                else:
                    current_mappings = {}

                # Zähle gemappte vs. ungemappte Namen
                mapped_count = sum(1 for name in task_names if name in current_mappings)
                unmapped_count = len(task_names) - mapped_count

                if unmapped_count > 0:
                    st.info(f"👥 {len(task_names)} Personen im Protokoll: {mapped_count} 🟢 zugeordnet, {unmapped_count} 🔴 noch ohne Zuordnung")
                else:
                    st.success(f"✓ Alle {len(task_names)} Personen sind Asana-Benutzern zugeordnet")
            except:
                pass

        # Editor für Protokoll
        edited_protocol = st.text_area(
            "Protokoll bearbeiten:",
            value=protocol_text,
            height=400,
            key="protocol_editor",
            help="Bearbeite das Protokoll und ersetze Platzhalter [?] durch korrekte Werte"
        )

        # Speichere editiertes Protokoll
        st.session_state['extracted_protocol'] = edited_protocol

        # Button zum Extrahieren der Aufgaben aus dem Protokoll
        current_tasks = st.session_state.get('extracted_tasks', [])

        col_re1, col_re2 = st.columns([1, 3])
        with col_re1:
            # Dynamischer Button-Text je nachdem ob Tasks schon existieren
            if len(current_tasks) == 0:
                button_text = "⚡ Aufgaben jetzt extrahieren"
                button_help = "Extrahiert alle Aufgaben aus dem Protokoll (empfohlen: erst Protokoll bearbeiten)"
            else:
                button_text = "🔄 Aufgaben neu extrahieren"
                button_help = f"Extrahiert Aufgaben erneut aus dem bearbeiteten Protokoll (aktuell: {len(current_tasks)} Aufgaben)"

            if st.button(button_text, help=button_help, type="primary" if len(current_tasks) == 0 else "secondary"):
                with st.spinner("Extrahiere Aufgaben aus Protokoll..."):
                    try:
                        orch = st.session_state.orchestrator
                        research_agent = orch.research_agent
                        llm = research_agent.llm

                        new_tasks = extract_tasks_from_transcript(edited_protocol, llm)
                        st.session_state['extracted_tasks'] = new_tasks if new_tasks else []
                        st.success(f"✓ {len(st.session_state['extracted_tasks'])} Aufgaben extrahiert!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Fehler: {e}")

        st.markdown("---")

        # NAMEN-MAPPING GUI
        st.markdown("#### 👥 Personen-Zuordnung (Protokoll & Aufgaben → Asana)")

        with st.expander("🔧 Namen-Mapping konfigurieren", expanded=False):
            st.caption("Ordne Personen-Namen aus dem Protokoll und den Aufgaben den Asana-Benutzern zu. Diese Zuordnung wird gespeichert und bei zukünftigen Protokollen automatisch verwendet.")

            # Extrahiere Namen aus Protokoll UND Tasks
            task_names = extract_all_person_names(edited_protocol, current_tasks)

            if not task_names:
                st.info("Keine Personen-Namen im Protokoll oder in den Aufgaben gefunden.")
            else:
                # Lade aktuelle Mappings
                try:
                    import json
                    config_path = Path('config/mapping_config.json')
                    config_path.parent.mkdir(parents=True, exist_ok=True)

                    if config_path.exists():
                        with open(config_path, 'r', encoding='utf-8') as f:
                            config = json.load(f)
                            current_mappings = config.get('user_mappings', {})
                    else:
                        current_mappings = {}
                except:
                    current_mappings = {}

                # Lade Asana-User
                orch = st.session_state.orchestrator
                asana_agent = orch.asana_agent

                if asana_agent.is_connected():
                    asana_users = asana_agent.get_workspace_users()
                    asana_user_names = ["[Keine Zuordnung]"] + [user['name'] for user in asana_users]

                    st.caption(f"**{len(task_names)} Namen im Protokoll gefunden:**")

                    # Erstelle Mapping-GUI
                    new_mappings = {}

                    for protocol_name in task_names:
                        # Prüfe ob bereits Mapping existiert
                        current_mapping = current_mappings.get(protocol_name, "[Keine Zuordnung]")

                        # Finde Index für default
                        default_idx = 0
                        if current_mapping in asana_user_names:
                            default_idx = asana_user_names.index(current_mapping)

                        # Zeige Status-Icon
                        is_mapped = current_mapping != "[Keine Zuordnung]"
                        status_icon = "🟢" if is_mapped else "🔴"

                        # Selectbox für Mapping
                        col1, col2 = st.columns([1, 2])
                        with col1:
                            st.markdown(f"{status_icon} **{protocol_name}**")
                        with col2:
                            selected_asana_user = st.selectbox(
                                "Asana-Benutzer:",
                                options=asana_user_names,
                                index=default_idx,
                                key=f"mapping_{protocol_name}",
                                label_visibility="collapsed"
                            )

                            if selected_asana_user != "[Keine Zuordnung]":
                                new_mappings[protocol_name] = selected_asana_user

                    # Speichern-Button
                    col_save1, col_save2 = st.columns([1, 3])
                    with col_save1:
                        if st.button("💾 Mappings speichern"):
                            try:
                                # Merge mit existierenden Mappings
                                current_mappings.update(new_mappings)

                                # Speichere in config
                                config = {'user_mappings': current_mappings}

                                with open(config_path, 'w', encoding='utf-8') as f:
                                    json.dump(config, f, indent=2, ensure_ascii=False)

                                st.success(f"✓ {len(new_mappings)} Mappings gespeichert!")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Fehler beim Speichern: {e}")

                    # Zeige Legende
                    st.markdown("---")
                    st.caption("**Legende:** 🟢 = Zugeordnet | 🔴 = Noch keine Zuordnung")

                else:
                    st.warning("⚠️ Asana nicht verbunden. Mapping nicht möglich.")

        st.markdown("---")

        # TASKS BEARBEITEN (MIT DATA_EDITOR)
        st.markdown("#### ✅ Aufgaben")

        # Info-Panel mit Tipps
        with st.expander("💡 Tipps & Best Practices", expanded=False):
            tips_col1, tips_col2 = st.columns(2)

            with tips_col1:
                st.markdown("""
                **⚡ Schnelle Bearbeitung:**
                - Nutze Batch-Aktionen für mehrere Tasks
                - Tab-Taste zum Navigieren in der Tabelle
                - Doppelklick zum Bearbeiten einer Zelle
                - Enter zum Bestätigen von Änderungen
                """)

            with tips_col2:
                st.markdown("""
                **✅ Qualitäts-Checks:**
                - Prüfe Validierung vor dem Erstellen
                - Stelle sicher, dass Personen zugeordnet sind
                - Setze realistische Fälligkeitsdaten
                - Nutze aussagekräftige Titel (min. 3 Zeichen)
                """)

            st.info("🚀 **Pro-Tipp:** Tasks werden jetzt ~55% schneller erstellt dank User-Caching und optimiertem API-Zugriff!")

        tasks = st.session_state.get('extracted_tasks', [])

        if not tasks:
            st.info("💡 **Noch keine Aufgaben extrahiert.** Klicken Sie oben auf '⚡ Aufgaben jetzt extrahieren' um alle Aufgaben aus dem Protokoll zu extrahieren.")
        else:
            # Asana-Integration
            orch = st.session_state.orchestrator
            asana_agent = orch.asana_agent

            if asana_agent.is_connected():
                # Lade Asana-User für Zuweisungen
                try:
                    asana_users = asana_agent.get_workspace_users()
                    user_names = ["[Nicht zugewiesen]", "[Ich selbst]"] + [user['name'] for user in asana_users]

                    # Lade User-Mappings aus Config für besseres Matching
                    try:
                        import json
                        with open('config/mapping_config.json', 'r', encoding='utf-8') as f:
                            config = json.load(f)
                            user_mappings = config.get('user_mappings', {})
                    except:
                        user_mappings = {}

                    # Konvertiere Tasks zu DataFrame
                    import pandas as pd

                    # Info: Zeige aktive Mappings
                    if user_mappings:
                        with st.expander("ℹ️ Aktive Namen-Mappings", expanded=False):
                            st.caption("Diese Zuordnungen werden automatisch angewendet:")
                            for orig_name, mapped_name in user_mappings.items():
                                st.text(f"• {orig_name} → {mapped_name}")

                    # Bereite Tasks vor
                    task_list = []
                    mapping_applied_count = 0
                    mapping_failed = []

                    for task in tasks:
                        assignee_name = task.get('assignee', '')

                        # Mappe Assignee-Namen
                        original_assignee = assignee_name  # Für Tracking
                        if assignee_name and assignee_name != '[?]':
                            # Hole gemappten Namen aus Config
                            mapped_name = user_mappings.get(assignee_name, assignee_name)

                            # Suche in Asana-Usern (mit besserer Matching-Logik)
                            found = False

                            # 1. Versuch: Exakte Übereinstimmung
                            for user in asana_users:
                                if mapped_name.lower().strip() == user['name'].lower().strip():
                                    assignee_name = user['name']
                                    found = True
                                    if original_assignee in user_mappings:
                                        mapping_applied_count += 1
                                    break

                            # 2. Versuch: Partielle Übereinstimmung (nur wenn nicht gefunden)
                            if not found:
                                for user in asana_users:
                                    user_name_lower = user['name'].lower()
                                    mapped_name_lower = mapped_name.lower()

                                    # Prüfe ob alle Wörter des gemappten Namens im User-Namen vorkommen
                                    mapped_words = mapped_name_lower.split()
                                    if all(word in user_name_lower for word in mapped_words):
                                        assignee_name = user['name']
                                        found = True
                                        if original_assignee in user_mappings:
                                            mapping_applied_count += 1
                                        break

                            # Wenn immer noch nicht gefunden, setze auf "[Nicht zugewiesen]"
                            if not found:
                                if original_assignee in user_mappings:
                                    mapping_failed.append(f"{original_assignee} → {mapped_name}")
                                assignee_name = "[Nicht zugewiesen]"
                        else:
                            assignee_name = "[Nicht zugewiesen]"

                        task_list.append({
                            'create': True,
                            'title': task.get('title', ''),
                            'assignee': assignee_name,
                            'due_date': task.get('due_date'),
                            'description': task.get('description', '')
                        })

                    df = pd.DataFrame(task_list)

                    # Konvertiere due_date zu datetime.date
                    if 'due_date' in df.columns:
                        from datetime import date
                        def parse_date(d):
                            if d is None or (isinstance(d, str) and d.lower() == 'null'):
                                return None
                            if isinstance(d, date):
                                return d
                            if isinstance(d, str):
                                try:
                                    from datetime import datetime as dt
                                    parsed = dt.strptime(d, '%Y-%m-%d')
                                    return parsed.date()
                                except:
                                    return None
                            return None

                        df['due_date'] = df['due_date'].apply(parse_date)

                    # Zeige Mapping-Statistik
                    if mapping_applied_count > 0:
                        st.success(f"✅ {mapping_applied_count} Mapping(s) erfolgreich angewendet")
                    if mapping_failed:
                        st.warning(f"⚠️ {len(mapping_failed)} Mapping(s) fehlgeschlagen:")
                        for failed in mapping_failed:
                            st.caption(f"  • {failed}")

                    st.caption(f"**{len(df)} Aufgaben** - bearbeite sie vor dem Finalisieren:")

                    # ========================================
                    # BATCH-ACTIONS (Massenbearbeitung)
                    # ========================================
                    with st.expander("⚡ Batch-Aktionen (Massenbearbeitung)", expanded=False):
                        st.caption("Bearbeite mehrere Tasks gleichzeitig")

                        batch_col1, batch_col2, batch_col3 = st.columns(3)

                        with batch_col1:
                            st.markdown("**👤 Zuweisung**")
                            batch_assignee = st.selectbox(
                                "Person für alle auswählen:",
                                user_names,
                                key="batch_assignee",
                                label_visibility="collapsed"
                            )
                            if st.button("→ Allen zuweisen", use_container_width=True):
                                for i in range(len(df)):
                                    df.at[i, 'assignee'] = batch_assignee
                                st.success(f"✅ Alle {len(df)} Tasks an '{batch_assignee}' zugewiesen")
                                st.rerun()

                        with batch_col2:
                            st.markdown("**📅 Fälligkeit**")
                            from datetime import date, timedelta
                            batch_due_date = st.date_input(
                                "Datum für alle auswählen:",
                                value=date.today() + timedelta(days=7),
                                key="batch_due_date",
                                label_visibility="collapsed"
                            )
                            if st.button("→ Für alle setzen", use_container_width=True):
                                for i in range(len(df)):
                                    df.at[i, 'due_date'] = batch_due_date
                                st.success(f"✅ Datum für alle {len(df)} Tasks gesetzt")
                                st.rerun()

                        with batch_col3:
                            st.markdown("**✓ Auswahl**")
                            col_all, col_none = st.columns(2)
                            with col_all:
                                if st.button("✓ Alle", use_container_width=True):
                                    for i in range(len(df)):
                                        df.at[i, 'create'] = True
                                    st.success(f"✅ Alle {len(df)} Tasks ausgewählt")
                                    st.rerun()
                            with col_none:
                                if st.button("✗ Keine", use_container_width=True):
                                    for i in range(len(df)):
                                        df.at[i, 'create'] = False
                                    st.success(f"❌ Alle Tasks abgewählt")
                                    st.rerun()

                    st.markdown("---")

                    # Editierbare Tabelle
                    edited_df = st.data_editor(
                        df,
                        use_container_width=True,
                        num_rows="dynamic",
                        height=400,
                        column_config={
                            "create": st.column_config.CheckboxColumn(
                                "✓",
                                help="Task in Asana erstellen",
                                default=True,
                                width="small"
                            ),
                            "title": st.column_config.TextColumn(
                                "Titel",
                                help="Aufgaben-Titel",
                                max_chars=200,
                                required=True,
                                width="medium"
                            ),
                            "assignee": st.column_config.SelectboxColumn(
                                "Verantwortlich",
                                help="Zuständige Person in Asana",
                                options=user_names,
                                width="small"
                            ),
                            "due_date": st.column_config.DateColumn(
                                "Fälligkeit",
                                help="Fälligkeitsdatum",
                                width="small"
                            ),
                            "description": st.column_config.TextColumn(
                                "Beschreibung",
                                help="Detailbeschreibung der Aufgabe",
                                width="large"
                            )
                        }
                    )

                    # Speichere editierte Tasks
                    st.session_state['extracted_tasks'] = edited_df.to_dict('records')

                    # ========================================
                    # VALIDIERUNG & STATISTIK
                    # ========================================
                    st.markdown("---")

                    # Validiere Tasks
                    validation_errors = []
                    validation_warnings = []
                    tasks_to_create_count = 0

                    for idx, row in edited_df.iterrows():
                        if not row.get('create', False):
                            continue

                        tasks_to_create_count += 1
                        task_num = idx + 1

                        # Prüfe Titel
                        title = row.get('title', '').strip()
                        if not title:
                            validation_errors.append(f"Task #{task_num}: Kein Titel angegeben")
                        elif len(title) < 3:
                            validation_warnings.append(f"Task #{task_num}: Titel sehr kurz ('{title}')")

                        # Prüfe Assignee
                        assignee = row.get('assignee', '')
                        if not assignee or assignee == "[Nicht zugewiesen]":
                            validation_warnings.append(f"Task #{task_num}: Keine Person zugewiesen")

                        # Prüfe Datum
                        due_date = row.get('due_date')
                        if due_date:
                            try:
                                from datetime import date
                                if isinstance(due_date, str):
                                    due_date = date.fromisoformat(due_date)
                                if due_date < date.today():
                                    validation_warnings.append(f"Task #{task_num}: Datum liegt in der Vergangenheit")
                            except:
                                pass

                    # Zeige Validierungs-Ergebnisse
                    col_val1, col_val2, col_val3 = st.columns(3)

                    with col_val1:
                        if tasks_to_create_count > 0:
                            st.metric("✅ Zu erstellen", tasks_to_create_count)
                        else:
                            st.metric("⚠️ Zu erstellen", "0", help="Keine Tasks ausgewählt!")

                    with col_val2:
                        if validation_errors:
                            st.metric("❌ Fehler", len(validation_errors))
                        else:
                            st.metric("✓ Fehler", "0")

                    with col_val3:
                        if validation_warnings:
                            st.metric("⚠️ Warnungen", len(validation_warnings))
                        else:
                            st.metric("✓ Warnungen", "0")

                    # Zeige Details bei Problemen
                    if validation_errors:
                        with st.expander("❌ Fehler beheben (Pflicht)", expanded=True):
                            for error in validation_errors:
                                st.error(f"• {error}")

                    if validation_warnings:
                        with st.expander("⚠️ Warnungen prüfen (Optional)", expanded=False):
                            for warning in validation_warnings:
                                st.warning(f"• {warning}")

                except Exception as e:
                    st.error(f"Fehler bei Asana-Integration: {e}")
                    # Fallback: Zeige Tasks als Liste
                    for i, task in enumerate(tasks):
                        with st.expander(f"**{i+1}. {task.get('title', 'Unbenannt')}**"):
                            st.write(f"**Zuständig:** {task.get('assignee', '[?]')}")
                            st.write(f"**Fällig:** {task.get('due_date', 'Kein Datum')}")
                            st.write(f"**Beschreibung:** {task.get('description', '')}")
            else:
                st.warning("⚠️ Asana nicht verbunden. Tasks werden nur angezeigt.")
                for i, task in enumerate(tasks):
                    with st.expander(f"**{i+1}. {task.get('title', 'Unbenannt')}**"):
                        st.write(f"**Zuständig:** {task.get('assignee', '[?]')}")
                        st.write(f"**Fällig:** {task.get('due_date', 'Kein Datum')}")
                        st.write(f"**Beschreibung:** {task.get('description', '')}")

        st.markdown("---")

        # FINALISIERUNG
        st.markdown("### 🎯 Finalisieren")

        col_asana, col_outlook = st.columns(2)

        with col_asana:
            st.markdown("#### 📋 Asana-Projekt")

            # Asana-Projekt-Auswahl
            if asana_agent.is_connected():
                projects = asana_agent.list_projects()
                if projects:
                    project_options = ["[Kein Projekt]"] + [p['name'] for p in projects]

                    # Versuche Auto-Match mit Meeting-Titel
                    meeting_title = st.session_state.get('meeting_title', '')
                    default_project_idx = 0

                    if meeting_title:
                        for i, p in enumerate(projects, 1):
                            if meeting_title.lower() in p['name'].lower() or p['name'].lower() in meeting_title.lower():
                                default_project_idx = i
                                break

                    selected_project_name = st.selectbox(
                        "Ziel-Projekt für Tasks:",
                        project_options,
                        index=default_project_idx,
                        key="final_asana_project"
                    )

                    selected_project_gid = None
                    if selected_project_name != "[Kein Projekt]":
                        for p in projects:
                            if p['name'] == selected_project_name:
                                selected_project_gid = p['gid']
                                break

                    # SCHRITT 1: Protokoll-Aufgabe erstellen
                    protocol_task_gid = st.session_state.get('protocol_task_gid')

                    if not protocol_task_gid:
                        if st.button("📄 Protokoll-Aufgabe in Asana erstellen", type="primary", use_container_width=True):
                            try:
                                protocol_text = st.session_state.get('extracted_protocol', '')
                                meeting_title = st.session_state.get('meeting_title', 'Meeting')
                                date_str = datetime.now().strftime("%Y-%m-%d")

                                protocol_task_title = f"📄 Protokoll {date_str} - {meeting_title}"

                                with st.spinner("Erstelle Protokoll-Aufgabe..."):
                                    # Stelle sicher, dass der "Protokolle"-Section existiert
                                    protocol_section_gid = asana_agent.ensure_section_exists(selected_project_gid, "Protokolle")

                                    protocol_task_result = asana_agent.create_task(
                                        name=protocol_task_title,
                                        notes=protocol_text,
                                        project_gid=selected_project_gid,
                                        assignee_gid=None
                                    )

                                    if not protocol_task_result.get('success'):
                                        st.error(f"❌ Fehler: {protocol_task_result.get('error')}")
                                        raise Exception("Protokoll-Aufgabe konnte nicht erstellt werden")

                                    protocol_task_gid = protocol_task_result.get('task_gid')

                                    # Verschiebe in "Protokolle"-Section
                                    if protocol_section_gid:
                                        section_result = asana_agent.add_task_to_section(
                                            task_gid=protocol_task_gid,
                                            section_gid=protocol_section_gid
                                        )

                                        if section_result.get('success'):
                                            st.success(f"✅ Protokoll-Aufgabe erstellt und in 'Protokolle' abgelegt")
                                        else:
                                            st.success(f"✅ Protokoll-Aufgabe erstellt")
                                    else:
                                        st.success(f"✅ Protokoll-Aufgabe erstellt")

                                with st.spinner("Erstelle und hänge PDF an..."):
                                    protocol_dir = _get_user_ctx().protocols_dir
                                    protocol_dir.mkdir(parents=True, exist_ok=True)

                                    md_filename = f"{date_str}_Protokoll_{meeting_title}.md"
                                    md_path = protocol_dir / md_filename

                                    with open(md_path, 'w', encoding='utf-8') as f:
                                        f.write(protocol_text)

                                    pdf_filename = md_filename.replace('.md', '.pdf')
                                    pdf_path = protocol_dir / pdf_filename

                                    if convert_markdown_to_pdf(md_path, pdf_path):
                                        attachment_result = asana_agent.attach_file_to_task(
                                            task_gid=protocol_task_gid,
                                            file_path=str(pdf_path),
                                            file_name=pdf_filename
                                        )

                                        if attachment_result.get('success'):
                                            st.success(f"✅ PDF '{pdf_filename}' angehängt")
                                        else:
                                            st.warning(f"⚠️ PDF-Anhang fehlgeschlagen: {attachment_result.get('error')}")
                                    else:
                                        st.warning("⚠️ PDF-Konvertierung fehlgeschlagen")

                                # Speichere Task-GID im Session State
                                st.session_state['protocol_task_gid'] = protocol_task_gid
                                st.session_state['protocol_permalink'] = protocol_task_result.get('permalink_url', '')

                                st.info("💡 Protokoll erstellt! Sie können es jetzt bearbeiten und danach die Tasks hinzufügen.")
                                st.rerun()

                            except Exception as e:
                                st.error(f"❌ Fehler: {str(e)}")
                                import traceback
                                st.text(traceback.format_exc())
                    else:
                        # Protokoll-Aufgabe wurde bereits erstellt
                        st.success("✅ Protokoll-Aufgabe bereits in Asana erstellt")

                        if st.session_state.get('protocol_permalink'):
                            st.markdown(f"[🔗 Protokoll in Asana öffnen]({st.session_state['protocol_permalink']})")

                        st.markdown("---")

                        # SCHRITT 2: Tasks als Unteraufgaben hinzufügen
                        st.markdown("##### ➕ Tasks hinzufügen")

                        tasks_to_create = [t for t in st.session_state.get('extracted_tasks', []) if t.get('create')]

                        # Validierung (nochmal prüfen vor dem Erstellen)
                        has_validation_errors = False
                        for task in tasks_to_create:
                            if not task.get('title', '').strip():
                                has_validation_errors = True
                                break

                        if not tasks_to_create:
                            st.info("ℹ️ Keine Tasks zum Hinzufügen (alle deaktiviert oder keine vorhanden)")
                        elif has_validation_errors:
                            st.error("❌ Kann nicht fortfahren: Bitte behebe zuerst alle Validierungsfehler oben")
                        else:
                            st.caption(f"✅ {len(tasks_to_create)} Task(s) bereit zum Hinzufügen als Unteraufgaben")

                        col_add, col_reset = st.columns(2)

                        with col_add:
                            # Button ist deaktiviert wenn keine Tasks oder Validierungsfehler
                            button_disabled = not tasks_to_create or has_validation_errors
                            if st.button("✅ Tasks als Unteraufgaben hinzufügen", type="primary", disabled=button_disabled):
                                try:
                                    # ========================================
                                    # PERFORMANCE-OPTIMIERUNG: User-Lookup Cache
                                    # ========================================
                                    # Erstelle Cache für schnelleren User-Lookup (verhindert wiederholte API-Calls)
                                    user_cache = {}  # name -> gid
                                    try:
                                        cached_users = asana_agent.get_workspace_users()
                                        for user in cached_users:
                                            user_name_lower = user['name'].lower().strip()
                                            user_cache[user_name_lower] = user['gid']
                                    except Exception as e:
                                        st.warning(f"⚠️ User-Cache konnte nicht erstellt werden: {e}")

                                    # ========================================
                                    # ERWEITERTE FORTSCHRITTSANZEIGE
                                    # ========================================
                                    progress_bar = st.progress(0)
                                    status_text = st.empty()
                                    stats_container = st.container()

                                    success_count = 0
                                    errors = []
                                    total_tasks = len(tasks_to_create)

                                    import time
                                    start_time = time.time()

                                    for idx, task in enumerate(tasks_to_create, start=1):
                                        try:
                                            title = task.get('title', '')
                                            description = task.get('description', '')
                                            due_date_obj = task.get('due_date')
                                            assignee_name = task.get('assignee', '')

                                            # Berechne Statistiken
                                            elapsed_time = time.time() - start_time
                                            avg_time_per_task = elapsed_time / idx if idx > 0 else 0
                                            remaining_tasks = total_tasks - idx
                                            estimated_remaining = avg_time_per_task * remaining_tasks

                                            # Update Fortschrittsanzeige mit erweiterten Infos
                                            status_msg = f"🔄 Erstelle Task {idx}/{total_tasks}: {title[:40]}..."
                                            if estimated_remaining > 0:
                                                status_msg += f" (~{int(estimated_remaining)}s verbleibend)"
                                            status_text.text(status_msg)

                                            # Zeige Live-Statistik in Container
                                            with stats_container:
                                                stat_col1, stat_col2, stat_col3, stat_col4 = st.columns(4)
                                                with stat_col1:
                                                    st.metric("Fortschritt", f"{idx}/{total_tasks}")
                                                with stat_col2:
                                                    st.metric("✅ Erfolg", success_count)
                                                with stat_col3:
                                                    st.metric("❌ Fehler", len(errors))
                                                with stat_col4:
                                                    st.metric("⏱️ Zeit", f"{int(elapsed_time)}s")

                                            # Konvertiere due_date
                                            due_date_str = None
                                            if due_date_obj:
                                                if isinstance(due_date_obj, str):
                                                    due_date_str = due_date_obj if due_date_obj != 'null' else None
                                                else:
                                                    due_date_str = due_date_obj.strftime('%Y-%m-%d')

                                            # ========================================
                                            # OPTIMIERTER USER-LOOKUP (mit Cache)
                                            # ========================================
                                            assignee_gid = None
                                            if assignee_name == "[Ich selbst]":
                                                assignee_gid = "me"
                                            elif assignee_name == "[Nicht zugewiesen]" or not assignee_name:
                                                assignee_gid = None
                                            else:
                                                # Mappe Namen falls Mapping existiert
                                                mapped_name = user_mappings.get(assignee_name, assignee_name)

                                                # Suche im Cache (schnell!)
                                                cache_key = mapped_name.lower().strip()
                                                if cache_key in user_cache:
                                                    assignee_gid = user_cache[cache_key]
                                                else:
                                                    # Fallback: Fuzzy-Search im Cache
                                                    found = False
                                                    mapped_words = cache_key.split()
                                                    for cached_name, gid in user_cache.items():
                                                        if all(word in cached_name for word in mapped_words):
                                                            assignee_gid = gid
                                                            found = True
                                                            break

                                                    if not found:
                                                        # Letzter Fallback: API-Call (Streamlit-Cache, 10 Min)
                                                        user_info = cached_find_asana_user(id(asana_agent), mapped_name)
                                                        if user_info:
                                                            assignee_gid = user_info['gid']
                                                            # Cache aktualisieren für nächstes Mal
                                                            user_cache[cache_key] = assignee_gid
                                                        else:
                                                            description = f"⚠️ Zuständig: {assignee_name}\n\n{description}"
                                                            assignee_gid = None

                                            # Erstelle Subtask (Unteraufgabe der Protokoll-Aufgabe)
                                            result = asana_agent.create_subtask(
                                                parent_task_gid=protocol_task_gid,
                                                name=title,
                                                notes=description,
                                                due_on=due_date_str,
                                                assignee_gid=assignee_gid
                                            )

                                            if result.get('success'):
                                                success_count += 1
                                            else:
                                                errors.append(f"{title}: {result.get('error')}")

                                        except Exception as e:
                                            errors.append(f"{task.get('title', 'Unbekannt')}: {str(e)}")

                                        # Update Progress Bar
                                        progress_bar.progress(idx / total_tasks)

                                    # ========================================
                                    # ABSCHLUSS-MELDUNGEN
                                    # ========================================
                                    progress_bar.empty()
                                    status_text.empty()
                                    stats_container.empty()

                                    # Finale Statistik
                                    total_time = time.time() - start_time

                                    if success_count > 0:
                                        st.success(f"✅ {success_count} Unteraufgabe(n) erfolgreich erstellt in {int(total_time)}s!")

                                        # Zeige finale Performance-Stats
                                        with st.expander("📊 Performance-Details", expanded=False):
                                            perf_col1, perf_col2, perf_col3 = st.columns(3)
                                            with perf_col1:
                                                st.metric("Gesamt-Zeit", f"{int(total_time)}s")
                                            with perf_col2:
                                                avg_time = total_time / total_tasks if total_tasks > 0 else 0
                                                st.metric("Ø pro Task", f"{avg_time:.1f}s")
                                            with perf_col3:
                                                success_rate = (success_count / total_tasks * 100) if total_tasks > 0 else 0
                                                st.metric("Erfolgsrate", f"{success_rate:.0f}%")

                                    if errors:
                                        st.error(f"❌ {len(errors)} Fehler:")
                                        for err in errors:
                                            st.caption(f"• {err}")

                                except Exception as e:
                                    st.error(f"❌ Fehler beim Erstellen der Unteraufgaben: {str(e)}")
                                    import traceback
                                    st.text(traceback.format_exc())

                        with col_reset:
                            if st.button("🔄 Neu beginnen", help="Protokoll-Aufgabe zurücksetzen um neu zu starten"):
                                # Reset der Session State Variablen
                                if 'protocol_task_gid' in st.session_state:
                                    del st.session_state['protocol_task_gid']
                                if 'protocol_permalink' in st.session_state:
                                    del st.session_state['protocol_permalink']
                                st.success("✅ Zurückgesetzt! Sie können jetzt eine neue Protokoll-Aufgabe erstellen.")
                                st.rerun()

                else:
                    st.warning("Keine Asana-Projekte gefunden.")
            else:
                st.warning("⚠️ Asana nicht verbunden")

        with col_outlook:
            st.markdown("#### 📧 Outlook-Termin")

            # Speichere Protokoll als PDF und hänge an Outlook an
            selected_event = st.session_state.get('selected_event')

            if selected_event:
                event_title = selected_event.get('title', 'Meeting')
                event_id = selected_event.get('id')

                if st.button("📎 Protokoll an Termin anhängen"):
                    try:
                        # Erstelle Protokoll-Datei
                        protocol_text = st.session_state['extracted_protocol']

                        # Speichere als Markdown
                        protocol_dir = _get_user_ctx().protocols_dir
                        protocol_dir.mkdir(parents=True, exist_ok=True)

                        meeting_title = st.session_state.get('meeting_title', 'Meeting')
                        date_str = datetime.now().strftime("%Y-%m-%d")
                        md_filename = f"{date_str}_Protokoll_{meeting_title}.md"
                        md_path = protocol_dir / md_filename

                        with open(md_path, 'w', encoding='utf-8') as f:
                            f.write(protocol_text)

                        # Konvertiere zu PDF
                        pdf_filename = md_filename.replace('.md', '.pdf')
                        pdf_path = protocol_dir / pdf_filename

                        if convert_markdown_to_pdf(md_path, pdf_path):
                            # Hänge an Outlook an
                            result = outlook_tool.add_attachment_to_event(
                                event_id=event_id,
                                file_path=str(pdf_path),
                                file_name=pdf_filename
                            )

                            if result.get('success'):
                                st.success(f"✅ Protokoll als '{pdf_filename}' an Termin angehängt!")
                            else:
                                st.error(f"❌ Fehler beim Anhängen: {result.get('error')}")
                        else:
                            st.error("❌ PDF-Konvertierung fehlgeschlagen")

                    except Exception as e:
                        st.error(f"❌ Fehler: {e}")
            else:
                st.info("ℹ️ Kein Termin ausgewählt")

        # Abschließen und Archivieren
        st.markdown("---")
        st.markdown("### 🎯 Protokoll abschließen")

        col_complete, col_reset = st.columns(2)

        with col_complete:
            if st.button("✅ Abschließen & Archivieren", type="primary", help="Protokoll als PDF archivieren und zur nächsten Nachbereitung wechseln"):
                try:
                    # Erstelle Archiv-Ordner
                    archive_dir = _get_user_ctx().transcripts_archive
                    archive_dir.mkdir(parents=True, exist_ok=True)

                    # Speichere Protokoll als Markdown
                    protocol_text = st.session_state['extracted_protocol']
                    meeting_title = st.session_state.get('meeting_title', 'Meeting')

                    # Sanitize filename
                    import re
                    safe_title = re.sub(r'[^\w\s-]', '', meeting_title)
                    safe_title = re.sub(r'[-\s]+', '-', safe_title)[:50]

                    date_str = datetime.now().strftime("%Y-%m-%d_%H%M")
                    md_filename = f"{date_str}_Protokoll_{safe_title}.md"
                    md_path = archive_dir / md_filename

                    with open(md_path, 'w', encoding='utf-8') as f:
                        f.write(protocol_text)

                    # Konvertiere zu PDF
                    pdf_filename = md_filename.replace('.md', '.pdf')
                    pdf_path = archive_dir / pdf_filename

                    if convert_markdown_to_pdf(md_path, pdf_path):
                        st.success(f"✅ Protokoll archiviert als: {pdf_filename}")

                        # Optional: Verschiebe auch das Transkript ins Archiv
                        transcript_source = st.session_state.get('transcript_source')
                        if transcript_source:
                            source_path = _get_user_ctx().transcripts_processed / transcript_source
                            if source_path.exists():
                                archive_transcript_path = archive_dir / f"{date_str}_Transkript_{transcript_source}"
                                import shutil
                                shutil.move(str(source_path), str(archive_transcript_path))
                                st.info(f"📄 Transkript archiviert als: {archive_transcript_path.name}")

                        # Lösche Session State
                        for key in ['extracted_protocol', 'extracted_tasks', 'transcript_source', 'meeting_title',
                                    'selected_event', 'meeting_date_selection', 'selected_event_for_transcript',
                                    'protocol_task_gid', 'protocol_permalink']:
                            if key in st.session_state:
                                del st.session_state[key]

                        st.success("✓ Nachbereitung abgeschlossen! Sie können nun das nächste Protokoll bearbeiten.")
                        st.balloons()
                        st.rerun()
                    else:
                        st.error("❌ PDF-Konvertierung fehlgeschlagen")

                except Exception as e:
                    st.error(f"❌ Fehler beim Archivieren: {e}")
                    import traceback
                    st.code(traceback.format_exc())

        with col_reset:
            if st.button("🗑️ Verwerfen", help="Verwirft die aktuell geladenen Daten ohne Archivierung"):
                # Lösche aus Session State
                for key in ['extracted_protocol', 'extracted_tasks', 'transcript_source', 'meeting_title',
                            'selected_event', 'meeting_date_selection', 'selected_event_for_transcript',
                            'protocol_task_gid', 'protocol_permalink']:
                    if key in st.session_state:
                        del st.session_state[key]
                st.success("✓ Daten verworfen")
                st.rerun()


@st.fragment
def render_settings_tab():
    """Einstellungen-Tab: Per-User Credentials für Outlook und Asana.

    Performance: @st.fragment isoliert Re-Renders auf diesen Tab.
    """
    st.header("⚙️ Einstellungen")
    user_ctx = st.session_state.get('user_ctx')
    if not user_ctx:
        st.warning("Kein Benutzerkontext verfügbar.")
        return

    st.subheader(f"Verbindungen für: **{st.session_state.get('name', user_ctx.username)}**")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### 📧 Microsoft Outlook")
        if user_ctx.has_outlook_token():
            st.success("✅ Outlook verbunden")
            st.caption(f"Token: `{user_ctx.outlook_token_file}`")
            if st.button("🔄 Outlook neu verbinden", key="reconnect_outlook"):
                # Token löschen, damit Device-Code-Flow beim nächsten Aufruf neu startet
                user_ctx.outlook_token_file.unlink(missing_ok=True)
                # Auch Orchestrator-Cache invalidieren
                if 'orchestrator' in st.session_state:
                    st.session_state.orchestrator._outlook_tool = None
                st.success("Token gelöscht. Bitte laden Sie die Seite neu, um sich erneut zu verbinden.")
                st.rerun()
        else:
            st.warning("❌ Outlook nicht verbunden")
            st.caption("Die Verbindung wird automatisch hergestellt, wenn Sie den Kalender oder E-Mails nutzen.")

    with col2:
        st.markdown("### 📋 Asana")
        current_token = user_ctx.get_asana_token()
        if current_token and user_ctx.asana_credentials_file.exists():
            st.success("✅ Asana verbunden (eigener Token)")
            masked = current_token[:6] + "..." + current_token[-4:] if len(current_token) > 10 else "***"
            st.caption(f"Token: `{masked}`")
        elif current_token:
            st.info("ℹ️ Asana verbunden (globaler Token aus .env)")
            st.caption("Sie können einen eigenen Token hinterlegen:")
        else:
            st.warning("❌ Asana nicht verbunden")

        new_asana_token = st.text_input(
            "Asana Personal Access Token",
            type="password",
            key="settings_asana_pat",
            help="Erstellen Sie einen Token unter: https://app.asana.com/0/my-apps"
        )
        if st.button("💾 Asana Token speichern", key="save_asana_token"):
            if new_asana_token and len(new_asana_token) > 10:
                user_ctx.save_asana_token(new_asana_token)
                st.success("✅ Asana Token gespeichert!")
                st.rerun()
            else:
                st.error("Bitte geben Sie einen gültigen Token ein.")

    st.markdown("---")

    # Hinweis: Passwort-Änderung passiert jetzt im Herbert-Portal
    st.subheader("🔑 Konto & Passwort")
    st.info(
        "Dein Passwort verwaltest du zentral im **Herbert-Portal**. "
        "Dort kannst du es auch zurücksetzen oder 2-Faktor-Authentifizierung einrichten."
    )
    st.link_button(
        "🌐 Zum Herbert-Portal",
        "https://auth.herbertgruppe.com/if/user/",
        use_container_width=False,
    )


@st.fragment
def render_admin_panel():
    """Admin-Bereich: Rollen verwalten (nur für Admins).

    Seit Phase 2.4 (SSO) werden User selbst in Authentik angelegt; diese
    Seite mapped nur noch Authentik-Emails auf App-Rollen (admin|user).
    Performance: @st.fragment isoliert Re-Renders auf diesen Tab.
    """
    config = _load_users_config()
    roles = config.setdefault('roles', {})
    default_role = config.get('default_role', 'user')

    st.subheader("👥 Rollen-Verwaltung")
    st.caption(
        "Benutzer-Accounts selbst werden im [Herbert-Portal](https://auth.herbertgruppe.com/if/admin/) "
        f"angelegt. Hier legst du nur fest, welche App-Rolle eine Email hat. "
        f"Nicht gelistete User bekommen die Default-Rolle **{default_role}**."
    )

    # Bestehende Rollen-Einträge anzeigen
    if not roles:
        st.info("Noch keine expliziten Rollen-Zuweisungen.")
    else:
        for email, role in sorted(roles.items()):
            col1, col2, col3 = st.columns([3, 1, 1])
            with col1:
                st.text(f"📧 {email}")
            with col2:
                st.text(f"🔑 {role}")
            with col3:
                if email != st.session_state.get('email'):
                    if st.button("🗑️", key=f"del_role_{email}", help=f"Rolle für {email} entfernen"):
                        del config['roles'][email]
                        _save_users_config(config)
                        st.success(f"Rollen-Zuweisung für {email} entfernt.")
                        st.rerun()

    st.markdown("---")

    # Neue Rollen-Zuweisung
    with st.expander("➕ Rolle zuweisen / ändern"):
        new_email = st.text_input("Email (wie in Authentik)", key="new_role_email")
        new_role = st.selectbox("Rolle", ["user", "admin"], key="new_role_val")
        if st.button("✅ Rolle speichern", key="save_role_btn", type="primary"):
            if not new_email or '@' not in new_email:
                st.error("Bitte eine gültige Email angeben.")
            else:
                config.setdefault('roles', {})[new_email.lower()] = new_role
                _save_users_config(config)
                st.success(f"✅ {new_email} → {new_role}")
                st.rerun()


def main():
    """Haupt-App-Logik"""
    import time
    main_start = time.time()
    print(f"\n[DEBUG] ========== MAIN START @ {main_start} ==========")

    # ---- Authentication Gate (Authentik OIDC via st.login) ----
    if not st.experimental_user.is_logged_in:
        # Portal-Login-Screen — Logo + ein Button
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.markdown(
                "<div style='text-align:center; padding-top: 3rem;'>"
                "<h1 style='margin-bottom:0.25rem;'>🤖 Mein Assistent</h1>"
                "<p style='color:#888; margin-top:0;'>Interner Zugang über das Herbert-Portal</p>"
                "</div>",
                unsafe_allow_html=True,
            )
            st.markdown("<div style='height: 1.5rem'></div>", unsafe_allow_html=True)
            if st.button("🔐 Mit Herbert-Portal anmelden", type="primary", use_container_width=True):
                st.login("authentik")
        st.stop()

    # ---- Authentifiziert: User-Kontext aufbauen ----
    email = (st.experimental_user.email or '').lower()
    display_name = st.experimental_user.name or (email.split('@')[0] if email else 'User')
    username = get_username(email)
    role = get_user_role(email)

    print(f"[AUTH] User eingeloggt: {username} ({email}, role={role})")

    st.session_state['username'] = username
    st.session_state['email'] = email
    st.session_state['name'] = display_name
    st.session_state['role'] = role

    # UserContext erstellen/laden
    if 'user_ctx' not in st.session_state or st.session_state['user_ctx'].username != username:
        user_ctx = UserContext(username)
        user_ctx.ensure_dirs()
        st.session_state['user_ctx'] = user_ctx
        print(f"[AUTH] UserContext erstellt für: {username} → {user_ctx.base}")

    # Initialisierung
    init_start = time.time()
    initialize_session_state()
    print(f"[DEBUG] initialize_session_state took {(time.time()-init_start)*1000:.2f}ms")

    # Prüfe ob .env geändert wurde und lösche Cache bei Bedarf
    cache_start = time.time()
    check_and_reset_cache_if_env_changed()
    print(f"[DEBUG] check_and_reset_cache took {(time.time()-cache_start)*1000:.2f}ms")

    # Erstelle notwendige Ordner (user-spezifisch)
    user_ctx = st.session_state['user_ctx']
    (user_ctx.data_dir / "agendas").mkdir(parents=True, exist_ok=True)

    # Kompaktheits-Overrides (Farb-/Layout-Tokens kommen aus dem globalen
    # CSS-Block oben, der das Herbert-Design-System umsetzt).
    st.markdown("""
    <style>
        /* Streamlit Header + Deko-Balken + Toolbar komplett verstecken */
        header[data-testid="stHeader"] { display: none !important; height: 0 !important; }
        div[data-testid="stDecoration"] { display: none !important; }
        div[data-testid="stToolbar"]    { display: none !important; }
        #MainMenu { display: none !important; visibility: hidden !important; }
        footer    { display: none !important; visibility: hidden !important; }

        /* Weniger Weissraum im Haupt-Content — praktisch kein Top-Padding */
        .main .block-container,
        section.main > div.block-container,
        div[data-testid="stMainBlockContainer"] {
            padding-top: 0.5rem !important;
            padding-bottom: 1rem !important;
            padding-left: 1.5rem !important;
            padding-right: 1.5rem !important;
            max-width: 100% !important;
        }
        [data-testid="stAppViewContainer"] { padding-top: 0 !important; }
        [data-testid="stAppViewContainer"] > .main { padding-top: 0 !important; }

        /* Erste H1/H2/H3 im Content ohne margin-top */
        .main .block-container > div:first-child h1,
        .main .block-container > div:first-child h2,
        .main .block-container > div:first-child h3 {
            margin-top: 0 !important;
            padding-top: 0 !important;
        }

        /* Links -> Brand-Blau */
        a, a:visited { color: var(--brand-500) !important; }
        a:hover      { color: var(--brand-700) !important; }

        /* Metrics -> Brand-Blau */
        [data-testid="stMetricValue"] { color: var(--brand-600); }

        /* Progress Bar -> Brand-Blau */
        .stProgress > div > div > div { background-color: var(--brand-600); }
    </style>
    """, unsafe_allow_html=True)

    # Sidebar rendern
    sidebar_start = time.time()
    render_sidebar()
    print(f"[DEBUG] render_sidebar took {(time.time()-sidebar_start)*1000:.2f}ms")

    # Hauptbereich mit Tabs - Admin-Tab nur für Admins
    tabs_start = time.time()
    is_admin = (st.session_state.get('role') == 'admin')

    tab_labels = ["📊 Mein Tag", "🎙️ Meeting Manager", "📁 Dokumente", "📚 Archiv", "⚙️ Einstellungen"]
    if is_admin:
        tab_labels.append("👥 Admin")

    tabs = st.tabs(tab_labels)

    with tabs[0]:
        tab_start = time.time()
        render_dashboard_tab()
        print(f"[DEBUG] render_dashboard_tab took {(time.time()-tab_start)*1000:.2f}ms")

    with tabs[1]:
        tab_start = time.time()
        render_transcripts_tab()
        print(f"[DEBUG] render_transcripts_tab took {(time.time()-tab_start)*1000:.2f}ms")

    with tabs[2]:
        tab_start = time.time()
        render_documents_tab()
        print(f"[DEBUG] render_documents_tab took {(time.time()-tab_start)*1000:.2f}ms")

    with tabs[3]:
        tab_start = time.time()
        render_archive_tab()
        print(f"[DEBUG] render_archive_tab took {(time.time()-tab_start)*1000:.2f}ms")

    with tabs[4]:
        render_settings_tab()

    if is_admin:
        with tabs[5]:
            render_admin_panel()

    print(f"[DEBUG] ========== MAIN TOTAL took {(time.time()-main_start)*1000:.2f}ms ==========")



def save_wip_item(item: Dict[str, Any], wip_dir: Path):
    """Speichert ein WIP-Item persistent"""
    try:
        import json
        item_id = item.get('id', 'unknown')
        wip_file = wip_dir / f"item_{item_id}.json"

        # Erstelle Kopie ohne große Daten
        item_copy = item.copy()

        # Speichere
        with open(wip_file, 'w', encoding='utf-8') as f:
            json.dump(item_copy, f, indent=2, ensure_ascii=False, default=str)

    except Exception as e:
        print(f"Fehler beim Speichern von WIP-Item: {e}")


def delete_wip_item(item: Dict[str, Any], wip_dir: Path):
    """Löscht ein WIP-Item von Disk"""
    try:
        item_id = item.get('id', 'unknown')
        wip_file = wip_dir / f"item_{item_id}.json"

        if wip_file.exists():
            wip_file.unlink()
            print(f"[delete_wip_item] ✓ WIP-Item gelöscht: {item_id}")
        else:
            print(f"[delete_wip_item] ⚠️ WIP-Datei nicht gefunden: {wip_file}")

    except Exception as e:
        print(f"[delete_wip_item] ✗ Fehler beim Löschen: {e}")




@st.fragment
def render_transcripts_tab():
    """
    Neue Meeting Manager Struktur mit Liste-basierter Navigation.

    Performance: @st.fragment isoliert Re-Renders auf diesen Tab.

    Workflow:
    1. Upload-Bereich (immer oben)
    2. Transkript-Liste (gruppiert nach Status)
    3. Detail-Ansicht (nur für ausgewähltes Transkript)
    """

    st.header("🎙️ Meeting Manager - Nachbereitung ⚡ NEUE VERSION")

    # VERSION MARKER - WENN DU DAS NICHT SIEHST, LADE NEU!
    st.success("✅ NEUE VERSION v2.0 - Upload-basierter Workflow")

    st.markdown("""
    Der Meeting Manager hilft bei der **Nachbereitung** von Meetings durch
    automatische Protokoll-Erstellung und Task-Extraktion aus Transkripten.

    💡 **Hinweis:** Die Meeting-Vorbereitung findest du jetzt im Tab **"Mein Tag"**.
    """)

    st.markdown("---")

    # ========================================================================
    # 1. UPLOAD-BEREICH (immer oben, immer sichtbar)
    # ========================================================================

    st.markdown("### 📤 Transkripte hochladen")

    uploaded_files = st.file_uploader(
        "Dateien auswählen",
        type=['txt', 'md', 'pdf'],
        accept_multiple_files=True,
        help="Lade ein oder mehrere Meeting-Transkripte hoch (TXT, MD oder PDF)",
        key="transcript_uploader"
    )

    processed_dir = _get_user_ctx().transcripts_processed
    processed_dir.mkdir(parents=True, exist_ok=True)

    # WIP-Verzeichnis erstellen
    wip_dir = _get_user_ctx().wip_dir
    wip_dir.mkdir(parents=True, exist_ok=True)

    # ========================================================================
    # PERSISTENTE SPEICHERUNG: Lade Queue aus WIP-Verzeichnis
    # ========================================================================
    if 'transcript_queue' not in st.session_state:
        st.session_state['transcript_queue'] = []

        # Lade alle WIP-Items aus Verzeichnis
        import json
        wip_files = list(wip_dir.glob("item_*.json"))

        if wip_files:
            for wip_file in wip_files:
                try:
                    with open(wip_file, 'r', encoding='utf-8') as f:
                        item = json.load(f)
                        st.session_state['transcript_queue'].append(item)
                except Exception as e:
                    st.error(f"Fehler beim Laden von {wip_file.name}: {e}")

            if len(wip_files) > 0:
                st.toast(f"📂 {len(wip_files)} gespeicherte Workflows wiederhergestellt!", icon="✅")

    # Initialisiere selected_transcript_idx
    if 'selected_transcript_idx' not in st.session_state:
        st.session_state['selected_transcript_idx'] = None

    # Initialisiere assign_termin_idx (Inline-Zuordnung)
    if 'assign_termin_idx' not in st.session_state:
        st.session_state['assign_termin_idx'] = None

    # Initialisiere show_archive
    if 'show_archive' not in st.session_state:
        st.session_state['show_archive'] = False

    # Verarbeite hochgeladene Files
    if uploaded_files:
        newly_uploaded = []
        updated_files = []

        for uploaded_file in uploaded_files:
            # Speichere Datei
            file_path = processed_dir / uploaded_file.name

            # Prüfe auf Duplikate (gleicher Dateiname)
            existing_idx = None
            for idx, item in enumerate(st.session_state['transcript_queue']):
                if item['filename'] == uploaded_file.name:
                    existing_idx = idx
                    break

            if existing_idx is not None:
                # Duplikat gefunden - aktualisiere bestehendes Item
                old_item = st.session_state['transcript_queue'][existing_idx]
                old_id = old_item.get('id')

                # Überschreibe Datei
                with open(file_path, 'wb') as f:
                    f.write(uploaded_file.getbuffer())

                # Erstelle aktualisiertes Item (behalte ID, setze zurück auf 'new')
                updated_item = {
                    'id': old_id,
                    'filename': uploaded_file.name,
                    'path': str(file_path),
                    'status': 'new',
                    'selected_event': None,
                    'protocol': None,
                    'tasks': None,
                    'error': None,
                    'uploaded_at': datetime.now().isoformat(),
                    'workflow_step': 0
                }

                # Ersetze altes Item
                st.session_state['transcript_queue'][existing_idx] = updated_item
                updated_files.append(uploaded_file.name)

                # Speichere persistent (überschreibt alte WIP-Datei)
                save_wip_item(updated_item, wip_dir)
            else:
                # Neu - erstelle neues Item
                with open(file_path, 'wb') as f:
                    f.write(uploaded_file.getbuffer())

                # Erstelle eindeutige ID
                import uuid
                item_id = str(uuid.uuid4())[:8]

                # Füge zur Queue hinzu
                new_item = {
                    'id': item_id,
                    'filename': uploaded_file.name,
                    'path': str(file_path),
                    'status': 'new',
                    'selected_event': None,
                    'protocol': None,
                    'tasks': None,
                    'error': None,
                    'uploaded_at': datetime.now().isoformat(),
                    'workflow_step': 0
                }
                st.session_state['transcript_queue'].append(new_item)
                newly_uploaded.append(uploaded_file.name)

                # Speichere persistent
                save_wip_item(new_item, wip_dir)

        if newly_uploaded:
            st.success(f"✅ {len(newly_uploaded)} neue Transkript(e) hochgeladen!")
            st.rerun()
        elif updated_files:
            st.info(f"🔄 {len(updated_files)} bestehende(s) Transkript(e) aktualisiert: {', '.join(updated_files)}")

    # ========================================================================
    # BACKGROUND-PROTOKOLL-GENERIERUNG (Thread-basiert, blockiert UI nicht)
    # ========================================================================

    # Fertige Jobs in Session State übertragen
    with _bg_jobs_lock:
        done_ids = [k for k, v in _bg_protocol_jobs.items() if v['status'] == 'done']
        error_ids = [k for k, v in _bg_protocol_jobs.items() if v['status'] == 'error']

    for item_id in done_ids:
        with _bg_jobs_lock:
            job = _bg_protocol_jobs.pop(item_id)
        for i, item in enumerate(st.session_state['transcript_queue']):
            if item['id'] == item_id:
                # Nur setzen wenn noch kein Protokoll vorhanden (kein manuell erstelltes überschreiben)
                if not st.session_state['transcript_queue'][i].get('protocol'):
                    st.session_state['transcript_queue'][i]['protocol'] = job['protocol']
                st.session_state['transcript_queue'][i]['status'] = 'processing'
                # workflow_step NICHT zurücksetzen – Benutzer soll an seiner aktuellen Stelle bleiben
                save_wip_item(st.session_state['transcript_queue'][i], wip_dir)
                break

    for item_id in error_ids:
        with _bg_jobs_lock:
            job = _bg_protocol_jobs.pop(item_id)
        for i, item in enumerate(st.session_state['transcript_queue']):
            if item['id'] == item_id:
                st.session_state['transcript_queue'][i]['error'] = job['error']
                st.session_state['transcript_queue'][i]['status'] = 'error'
                save_wip_item(st.session_state['transcript_queue'][i], wip_dir)
                break

    # 3. Background-Job Status Banner (KEIN Fragment/run_every – stört Button-Interaktionen)
    with _bg_jobs_lock:
        _running_jobs = [(k, v) for k, v in _bg_protocol_jobs.items() if v['status'] == 'running']

    if _running_jobs:
        for _, job in _running_jobs:
            st.info(f"⏳ Protokoll wird erstellt: **{job['filename'][:60]}** ({job['chunks']} Tokens) – du kannst währenddessen weiterarbeiten.")
        st.caption("💡 Die Ergebnisse werden beim nächsten Klick automatisch übernommen.")

    st.markdown("---")

    # ========================================================================
    # 2. TRANSKRIPT-LISTE (gruppiert nach Status)
    # ========================================================================

    queue = st.session_state['transcript_queue']

    if not queue:
        st.info("📭 Keine Transkripte vorhanden. Lade Dateien hoch um zu beginnen.")
        return

    st.markdown("### 📋 Meine Transkripte")

    # Gruppiere nach Status
    new_items = [(idx, item) for idx, item in enumerate(queue) if item['status'] == 'new']
    processing_items = [(idx, item) for idx, item in enumerate(queue) if item['status'] == 'processing']
    completed_items = [(idx, item) for idx, item in enumerate(queue) if item['status'] == 'completed']
    error_items = [(idx, item) for idx, item in enumerate(queue) if item['status'] == 'error']

    # Statistiken
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("🟡 Neu", len(new_items))
    with col2:
        st.metric("🟠 In Bearbeitung", len(processing_items))
    with col3:
        st.metric("🟢 Fertig", len(completed_items))
    with col4:
        st.metric("🔴 Fehler", len(error_items))

    # Batch-Aktionen
    col_batch1, col_batch2, col_batch3 = st.columns(3)
    with col_batch1:
        if st.button("🚀 Alle Neuen verarbeiten", disabled=(len(new_items) == 0), use_container_width=True):
            st.session_state['batch_process_all'] = True
            st.rerun()

    with col_batch2:
        if st.button("🧹 Queue leeren", disabled=(len(queue) == 0), use_container_width=True):
            if st.session_state.get('confirm_clear_queue', False):
                st.session_state['transcript_queue'] = []
                st.session_state['selected_transcript_idx'] = None
                st.session_state['confirm_clear_queue'] = False
                st.success("✅ Queue geleert!")
                st.rerun()
            else:
                st.session_state['confirm_clear_queue'] = True
                st.warning("⚠️ Nochmal klicken zum Bestätigen!")

    with col_batch3:
        # Toggle Archiv
        if st.button(
            "📁 Archiv verbergen" if st.session_state['show_archive'] else "📁 Archiv anzeigen",
            disabled=(len(completed_items) == 0),
            use_container_width=True
        ):
            st.session_state['show_archive'] = not st.session_state['show_archive']
            st.rerun()

    st.markdown("---")

    # NEUE TRANSKRIPTE
    if new_items:
        st.markdown("#### 🟡 Neue Transkripte")
        for idx, item in new_items:
            selected_event = item.get('selected_event')
            col_name, col_termin, col_assign, col_edit, col_delete = st.columns([3, 2, 0.8, 1.2, 0.5])
            with col_name:
                st.write(f"📄 **{item['filename']}**")
                st.caption(f"Hochgeladen: {datetime.fromisoformat(item['uploaded_at']).strftime('%d.%m.%Y %H:%M')}")
            with col_termin:
                if selected_event:
                    ev_title = selected_event.get('title', 'Termin')[:35]
                    st.write(f"✅ **{ev_title}**")
                else:
                    st.caption("❌ Kein Termin")
            with col_assign:
                assign_label = "📅" if not selected_event else "📅✏️"
                if st.button(assign_label, key=f"assign_{idx}", help="Termin zuordnen", use_container_width=True):
                    current = st.session_state.get('assign_termin_idx')
                    st.session_state['assign_termin_idx'] = None if current == idx else idx
                    st.rerun()
            with col_edit:
                if st.button("▶️ Bearbeiten", key=f"edit_new_{idx}", use_container_width=True):
                    st.session_state['selected_transcript_idx'] = idx
                    st.session_state['transcript_queue'][idx]['status'] = 'processing'
                    if st.session_state['transcript_queue'][idx].get('workflow_step', 0) < 1:
                        st.session_state['transcript_queue'][idx]['workflow_step'] = 1
                    save_wip_item(st.session_state['transcript_queue'][idx], _get_user_ctx().wip_dir)
                    st.rerun()
            with col_delete:
                if st.button("🗑️", key=f"delete_new_{idx}", use_container_width=True, help="Löschen"):
                    deleted_item = st.session_state['transcript_queue'].pop(idx)
                    delete_wip_item(deleted_item, _get_user_ctx().wip_dir)
                    if st.session_state.get('selected_transcript_idx') == idx:
                        st.session_state['selected_transcript_idx'] = None
                    st.success(f"✅ '{deleted_item['filename']}' gelöscht")
                    st.rerun()

            # Inline Termin-Zuordnung (aufklappbar)
            if st.session_state.get('assign_termin_idx') == idx:
                render_inline_termin_assignment(idx)

    # IN BEARBEITUNG
    if processing_items:
        st.markdown("#### 🟠 In Bearbeitung")
        for idx, item in processing_items:
            selected_event = item.get('selected_event')
            workflow_steps = ["Neu", "Umbenennen", "Protokoll erstellen", "Tasks extrahieren", "Finalisieren"]
            current_step = item.get('workflow_step', 0)
            step_label = workflow_steps[min(current_step, len(workflow_steps) - 1)]

            col_name, col_termin, col_assign, col_edit, col_delete = st.columns([3, 2, 0.8, 1.2, 0.5])
            with col_name:
                st.write(f"📄 **{item['filename']}**")
                st.caption(f"Schritt {current_step}/4: {step_label}")
            with col_termin:
                if selected_event:
                    ev_title = selected_event.get('title', 'Termin')[:35]
                    st.write(f"✅ **{ev_title}**")
                else:
                    st.caption("❌ Kein Termin")
            with col_assign:
                assign_label = "📅" if not selected_event else "📅✏️"
                if st.button(assign_label, key=f"assign_proc_{idx}", help="Termin zuordnen/ändern", use_container_width=True):
                    current = st.session_state.get('assign_termin_idx')
                    st.session_state['assign_termin_idx'] = None if current == idx else idx
                    st.rerun()
            with col_edit:
                if st.button("📝 Fortsetzen", key=f"edit_proc_{idx}", use_container_width=True):
                    st.session_state['selected_transcript_idx'] = idx
                    st.rerun()
            with col_delete:
                if st.button("🗑️", key=f"delete_proc_{idx}", use_container_width=True, help="Löschen"):
                    deleted_item = st.session_state['transcript_queue'].pop(idx)
                    delete_wip_item(deleted_item, _get_user_ctx().wip_dir)
                    if st.session_state.get('selected_transcript_idx') == idx:
                        st.session_state['selected_transcript_idx'] = None
                    st.success(f"✅ '{deleted_item['filename']}' gelöscht")
                    st.rerun()

            # Inline Termin-Zuordnung (aufklappbar)
            if st.session_state.get('assign_termin_idx') == idx:
                render_inline_termin_assignment(idx)

    # FERTIG (Archiv - optional ausblendbat)
    if completed_items and st.session_state['show_archive']:
        st.markdown("#### 🟢 Fertig (Archiv)")
        for idx, item in completed_items:
            with st.expander(f"✅ {item['filename']}", expanded=False):
                st.caption(f"Abgeschlossen: {datetime.fromisoformat(item['uploaded_at']).strftime('%d.%m.%Y %H:%M')}")

                col_view, col_reopen, col_delete = st.columns(3)
                with col_view:
                    if st.button("👁️ Ansehen", key=f"view_{idx}"):
                        st.session_state['selected_transcript_idx'] = idx
                        st.rerun()

                with col_reopen:
                    if st.button("🔄 Wieder öffnen", key=f"reopen_{idx}"):
                        st.session_state['transcript_queue'][idx]['status'] = 'processing'
                        st.session_state['selected_transcript_idx'] = idx
                        st.rerun()

                with col_delete:
                    if st.button("🗑️ Löschen", key=f"delete_completed_{idx}"):
                        # Lösche aus Queue
                        deleted_item = st.session_state['transcript_queue'].pop(idx)
                        # Lösche WIP-Datei
                        wip_dir = _get_user_ctx().wip_dir
                        delete_wip_item(deleted_item, wip_dir)
                        # Reset selected_idx falls nötig
                        if st.session_state.get('selected_transcript_idx') == idx:
                            st.session_state['selected_transcript_idx'] = None
                        st.success(f"✅ '{deleted_item['filename']}' gelöscht")
                        st.rerun()

    # FEHLER
    if error_items:
        st.markdown("#### 🔴 Fehler")
        for idx, item in error_items:
            with st.expander(f"❌ {item['filename']}", expanded=False):
                st.error(f"Fehler: {item.get('error', 'Unbekannter Fehler')}")

                col_retry, col_delete = st.columns(2)
                with col_retry:
                    if st.button("🔄 Erneut versuchen", key=f"retry_{idx}"):
                        st.session_state['transcript_queue'][idx]['status'] = 'new'
                        st.session_state['transcript_queue'][idx]['error'] = None
                        st.rerun()

                with col_delete:
                    if st.button("🗑️ Löschen", key=f"delete_error_{idx}"):
                        # Lösche aus Queue
                        deleted_item = st.session_state['transcript_queue'].pop(idx)
                        # Lösche WIP-Datei
                        wip_dir = _get_user_ctx().wip_dir
                        delete_wip_item(deleted_item, wip_dir)
                        # Reset selected_idx falls nötig
                        if st.session_state.get('selected_transcript_idx') == idx:
                            st.session_state['selected_transcript_idx'] = None
                        st.success(f"✅ '{deleted_item['filename']}' gelöscht")
                        st.rerun()

    st.markdown("---")

    # ========================================================================
    # 3. DETAIL-ANSICHT (nur wenn Transkript ausgewählt)
    # ========================================================================

    selected_idx = st.session_state.get('selected_transcript_idx')

    if selected_idx is not None and selected_idx < len(queue):
        render_transcript_detail_view(selected_idx)
    else:
        st.info("💡 Wähle ein Transkript aus der Liste oben um zu beginnen")


def render_transcript_detail_view(idx: int):
    """
    Rendert die Detail-Ansicht für ein ausgewähltes Transkript

    Workflow-Schritte (Termin-Zuordnung erfolgt in der Listenansicht):
    1. Umbenennen
    2. Protokoll erstellen
    3. Tasks extrahieren
    4. Finalisieren
    """

    item = st.session_state['transcript_queue'][idx]

    # Falls workflow_step noch 0 ist (altes Item), auf 1 setzen
    if item.get('workflow_step', 0) < 1:
        st.session_state['transcript_queue'][idx]['workflow_step'] = 1
        item['workflow_step'] = 1

    st.markdown("---")
    st.markdown(f"## 📄 {item['filename']}")

    # Termin-Info anzeigen
    selected_event = item.get('selected_event')
    if selected_event:
        ev_title = selected_event.get('title', 'Termin')
        st.caption(f"📅 Zugeordneter Termin: **{ev_title}**")
    else:
        st.caption("📅 Kein Termin zugeordnet (kann in der Liste nachgeholt werden)")

    # Zurück-Button
    col_back, col_status = st.columns([1, 3])
    with col_back:
        if st.button("← Zurück zur Liste"):
            st.session_state['selected_transcript_idx'] = None
            st.rerun()

    with col_status:
        current_step = item.get('workflow_step', 1)
        # Fortschritt: Step 1-4, angezeigt als 0-100%
        progress = max(0, (current_step - 1)) / 4
        st.progress(progress, text=f"Fortschritt: Schritt {current_step}/4")

    st.markdown("---")

    # Workflow-Schritte als Checkboxen
    st.markdown("### 📝 Workflow")

    steps = [
        ("1️⃣ Umbenennen", 1),
        ("2️⃣ Protokoll erstellen", 2),
        ("3️⃣ Tasks extrahieren", 3),
        ("4️⃣ Finalisieren", 4)
    ]

    for step_name, step_num in steps:
        if current_step > step_num:
            st.success(f"✅ {step_name}")
        elif current_step == step_num:
            st.info(f"⏳ {step_name} - **Aktueller Schritt**")
        else:
            st.caption(f"⬜ {step_name}")

    st.markdown("---")

    # Zeige aktuellen Schritt
    if current_step == 1:
        render_step_rename(idx)
    elif current_step == 2:
        render_step_create_protocol(idx)
    elif current_step == 3:
        render_step_extract_tasks(idx)
    elif current_step == 4:
        render_step_finalize(idx)
    else:
        st.success("🎉 Protokoll abgeschlossen!")


def render_inline_termin_assignment(idx: int):
    """Inline Termin-Zuordnung in der Listenansicht (aufklappbar)"""
    item = st.session_state['transcript_queue'][idx]
    file_path = Path(item.get('path', ''))
    wip_dir = _get_user_ctx().wip_dir

    with st.container():
        st.markdown("---")
        st.markdown("##### 📅 Termin zuordnen")

        # Datums-Extraktion aus Dateiname oder default heute
        import re
        date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', file_path.stem)
        if date_match:
            extracted_date = datetime.strptime(date_match.group(0), '%Y-%m-%d').date()
        else:
            extracted_date = datetime.now().date()

        # Datum-Picker
        meeting_date = st.date_input(
            "Meeting-Datum:",
            value=extracted_date,
            key=f"meeting_date_{idx}"
        )

        # Auth-Check
        orch = st.session_state.get('orchestrator')
        if not orch or not orch.outlook_tool.is_authenticated():
            st.warning("⚠️ Outlook nicht authentifiziert. Bitte in der Sidebar authentifizieren.")
            if st.button("✖️ Schließen", key=f"close_assign_noauth_{idx}"):
                st.session_state['assign_termin_idx'] = None
                st.rerun()
            st.markdown("---")
            return

        outlook_tool = orch.outlook_tool

        # Events laden und cachen (Schlüssel: Datum + idx)
        cache_key = f"cached_events_{meeting_date.isoformat()}_{idx}"

        col_load, col_close = st.columns([2, 1])
        with col_load:
            if st.button("🔄 Termine laden", key=f"load_ev_{idx}", use_container_width=True):
                try:
                    start_of_day = datetime.combine(meeting_date, datetime.min.time())
                    end_of_day = datetime.combine(meeting_date, datetime.max.time())
                    events = outlook_tool.get_events_for_date_range(start_of_day, end_of_day)
                    st.session_state[cache_key] = events or []
                except Exception as e:
                    st.error(f"❌ Fehler beim Laden: {e}")
                    st.session_state[cache_key] = []
                st.rerun()
        with col_close:
            if st.button("✖️ Schließen", key=f"close_assign_{idx}", use_container_width=True):
                st.session_state['assign_termin_idx'] = None
                st.rerun()

        # Zeige gecachte Events
        events = st.session_state.get(cache_key)
        if events is not None:
            if events:
                st.success(f"✓ {len(events)} Termin(e) gefunden")

                event_options = ["— Bitte wählen —"]
                event_dict = {}

                for event in events:
                    event_start = event.get('start')
                    event_title = event.get('title', 'Ohne Titel')

                    if isinstance(event_start, str):
                        try:
                            event_start_dt = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
                            event_start_dt = convert_to_berlin_time(event_start_dt)
                            time_str = event_start_dt.strftime('%H:%M')
                        except Exception:
                            time_str = event_start[:5] if len(event_start) >= 5 else "??:??"
                    else:
                        time_str = "??:??"

                    option_label = f"{time_str} - {event_title}"
                    event_options.append(option_label)
                    event_dict[option_label] = event

                # Event-Dict dauerhaft im Session State für Zugriff bei jedem Rerun
                st.session_state[f'_event_dict_{idx}'] = event_dict

                selected_option = st.selectbox(
                    "Termin:",
                    options=event_options,
                    key=f"event_select_{idx}"
                )

                if selected_option != "— Bitte wählen —" and selected_option in event_dict:
                    selected_event = event_dict[selected_option]

                    # Zeige Details
                    with st.expander("📋 Details", expanded=False):
                        st.write(f"**Titel:** {selected_event.get('title')}")
                        st.write(f"**Ort:** {selected_event.get('location', 'Kein Ort')}")
                        if selected_event.get('attendees'):
                            st.write(f"**Teilnehmer:** {len(selected_event['attendees'])}")

                    # Zuordnen-Button: speichert + startet Protokoll + schließt Panel
                    if st.button("✅ Zuordnen & Protokoll starten", type="primary", key=f"confirm_assign_{idx}", use_container_width=True):
                        # 1. EXPLIZIT speichern (wichtigster Schritt!)
                        st.session_state['transcript_queue'][idx]['selected_event'] = selected_event
                        save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)
                        print(f"[Termin-Zuordnung] ✅ Event '{selected_event.get('title')}' gespeichert für Item {item.get('id')} (idx={idx})")

                        # 2. Background-Protokoll starten
                        try:
                            _start_bg_protocol_for_item(idx, selected_event)
                        except Exception as e:
                            print(f"[BG-Protocol] Fehler beim Starten: {e}")

                        # 3. UI schließen
                        st.session_state['assign_termin_idx'] = None
                        st.rerun()

                    st.info(f"👆 Klicke **'Zuordnen & Protokoll starten'** um **{selected_event.get('title', 'den Termin')}** zuzuordnen.")
            else:
                st.info(f"📭 Keine Termine am {meeting_date.strftime('%d.%m.%Y')} gefunden")
        else:
            st.caption("💡 Klicke 'Termine laden' um Outlook-Termine abzurufen")

        st.markdown("---")


def _start_bg_protocol_for_item(idx: int, selected_event: dict):
    """Startet die Hintergrund-Protokoll-Generierung für ein Item mit zugeordnetem Termin"""
    item = st.session_state['transcript_queue'][idx]
    orch = st.session_state.get('orchestrator')

    if not orch or not orch.research_agent or item.get('protocol'):
        return

    with _bg_jobs_lock:
        already_running = item['id'] in _bg_protocol_jobs
    if already_running:
        return

    # Teilnehmer extrahieren
    bg_attendees = []
    for att in selected_event.get('attendees', []):
        if isinstance(att, dict):
            bg_attendees.append(att.get('name', att.get('email', '')))
        elif isinstance(att, str):
            bg_attendees.append(att)

    # Datum extrahieren
    bg_date = None
    ev_start = selected_event.get('start')
    if isinstance(ev_start, str):
        try:
            ev_dt = datetime.fromisoformat(ev_start.replace('Z', '+00:00'))
            ev_dt = convert_to_berlin_time(ev_dt)
            bg_date = ev_dt.strftime('%d.%m.%Y %H:%M')
        except Exception:
            pass

    # Agenda laden (falls vorhanden)
    bg_agenda = None
    agenda_dir = _get_user_ctx().data_dir / "agendas"
    if agenda_dir.exists() and bg_date:
        date_str_short = bg_date[:10]
        ev_title = selected_event.get('title', '')
        for af in agenda_dir.glob("Agenda_*.pdf"):
            if date_str_short in af.stem and any(
                w.lower() in af.stem.lower() for w in ev_title.split() if len(w) > 3
            ):
                try:
                    from langchain_community.document_loaders import PyPDFLoader
                    bg_agenda = "\n\n".join(
                        p.page_content for p in PyPDFLoader(str(af)).load()
                    )
                except Exception:
                    pass
                break

    fp = Path(item['path'])
    title = fp.stem.split('_', 2)[-1] if '_' in fp.stem else fp.stem
    start_bg_protocol_generation(
        item['id'], item['path'], title,
        orch.research_agent.llm, item['filename'],
        attendees=bg_attendees or None,
        meeting_date=bg_date,
        agenda_text=bg_agenda,
        protocol_cache_dir=str(_get_user_ctx().protocol_cache),
        wip_dir_str=str(_get_user_ctx().wip_dir)
    )
    st.toast("⏳ Protokoll wird im Hintergrund erstellt...", icon="🚀")


def render_step_rename(idx: int):
    """Schritt 1: Umbenennen"""
    st.markdown("### 1️⃣ Datei umbenennen")

    item = st.session_state['transcript_queue'][idx]
    file_path = Path(item['path'])
    selected_event = item.get('selected_event')

    # Prüfe ob Quelldatei noch existiert
    if not file_path.exists():
        st.warning(f"⚠️ Quelldatei nicht mehr vorhanden: `{file_path.name}`")
        st.info("Die Datei wurde möglicherweise bereits umbenannt oder gelöscht. Umbenennung wird übersprungen.")
        if st.button("Weiter →", type="primary", key=f"skip_rename_missing_{idx}"):
            st.session_state['transcript_queue'][idx]['workflow_step'] = 2
            wip_dir = _get_user_ctx().wip_dir
            save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)
            st.rerun()
        return

    if not selected_event:
        st.info("ℹ️ Kein Termin zugeordnet – Umbenennung übersprungen. Du kannst in der Liste noch einen Termin zuordnen.")
        if st.button("Weiter →", type="primary"):
            st.session_state['transcript_queue'][idx]['workflow_step'] = 2
            st.rerun()
        return

    # Erstelle neuen Dateinamen
    def sanitize_filename(name: str, max_length: int = 100) -> str:
        """Bereinigt String für Dateinamen"""
        invalid_chars = '<>:"/\\|?*\n\r\t'
        for char in invalid_chars:
            name = name.replace(char, '')
        name = name.replace(' ', '_')
        while '__' in name:
            name = name.replace('__', '_')
        return name[:max_length].strip('_')

    # Extrahiere Datum aus Event
    event_start = selected_event.get('start')
    if isinstance(event_start, str):
        try:
            event_start_dt = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
            date_str = event_start_dt.strftime("%Y-%m-%d")
        except:
            date_str = datetime.now().strftime("%Y-%m-%d")
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")

    meeting_title = selected_event.get('title', 'Ohne_Titel')
    sanitized_title = sanitize_filename(meeting_title, max_length=100)
    file_extension = file_path.suffix
    suggested_name = f"{date_str}_Protokoll_{sanitized_title}"

    # Zeige alten Namen
    st.markdown("#### 📝 Datei umbenennen")
    st.caption(f"Aktueller Name: `{file_path.name}`")

    # Editierbares Feld für neuen Namen (ohne Dateiendung)
    new_name_stem = st.text_input(
        "Neuer Dateiname (ohne Endung):",
        value=suggested_name,
        key=f"rename_input_{idx}",
        help=f"Dateiendung '{file_extension}' wird automatisch angehängt"
    )

    # Bereinige manuelle Eingabe
    new_name_stem = sanitize_filename(new_name_stem, max_length=150)
    new_filename = f"{new_name_stem}{file_extension}"

    # Vorschau des endgültigen Namens
    st.code(new_filename)

    # Umbenennen-Button
    col_rename, col_skip = st.columns(2)
    with col_rename:
        if st.button("✅ Umbenennen", type="primary", use_container_width=True):
            try:
                processed_dir = _get_user_ctx().transcripts_processed
                new_file_path = processed_dir / new_filename

                # Duplikat-Check
                counter = 1
                while new_file_path.exists():
                    new_filename = f"{new_name_stem}_{counter}{file_extension}"
                    new_file_path = processed_dir / new_filename
                    counter += 1

                # Benenne um
                import shutil
                shutil.move(str(file_path), str(new_file_path))

                # Update Queue
                st.session_state['transcript_queue'][idx]['path'] = str(new_file_path)
                st.session_state['transcript_queue'][idx]['filename'] = new_filename
                st.session_state['transcript_queue'][idx]['workflow_step'] = 2

                st.success(f"✅ Umbenannt zu: {new_filename}")
                st.balloons()
                import time
                time.sleep(1)
                st.rerun()

            except Exception as e:
                st.error(f"❌ Fehler: {e}")

    with col_skip:
        if st.button("Überspringen →", use_container_width=True):
            st.session_state['transcript_queue'][idx]['workflow_step'] = 2
            st.rerun()


def render_step_create_protocol(idx: int):
    """Schritt 3: Protokoll erstellen"""
    st.markdown("### 2️⃣ Protokoll erstellen")

    item = st.session_state['transcript_queue'][idx]
    file_path = Path(item['path'])
    selected_event = item.get('selected_event')

    # Zurück-Button zu Schritt 1
    if st.button("← Zurück zu Schritt 1 (Umbenennen)", key=f"back_to_step2_from_3_{idx}"):
        st.session_state['transcript_queue'][idx]['workflow_step'] = 1

        # Persistiere zu Disk
        wip_dir = _get_user_ctx().wip_dir
        save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)

        st.rerun()

    st.markdown("---")

    wip_dir = _get_user_ctx().wip_dir

    # === ÄNDERUNG 1: Background-Job und Disk-Cache konsultieren ===
    # Direkt prüfen ob ein fertiger Background-Job vorliegt
    if not item.get('protocol'):
        with _bg_jobs_lock:
            bg_job = _bg_protocol_jobs.get(item['id'])
            if bg_job and bg_job['status'] == 'done':
                item['protocol'] = bg_job['protocol']
                _bg_protocol_jobs.pop(item['id'], None)
                st.session_state['transcript_queue'][idx] = item
                save_wip_item(item, wip_dir)
                st.toast("✅ Hintergrund-Protokoll übernommen!")

    # Falls immer noch kein Protokoll: Cache auf Disk prüfen
    # Stabiler Key ist item['id'] — überlebt Renames in Schritt 1.
    # Fallback: alter Stem-basierter Key (Rückwärtskompat für vor-Bugfix erzeugte Caches).
    if not item.get('protocol'):
        cache_dir = _get_user_ctx().protocol_cache
        candidate_files = [
            cache_dir / f"{item['id']}_protocol.md",
            cache_dir / f"{file_path.stem}_protocol.md",
        ]
        for cache_file in candidate_files:
            if cache_file.exists():
                item['protocol'] = cache_file.read_text(encoding='utf-8')
                st.session_state['transcript_queue'][idx] = item
                save_wip_item(item, wip_dir)
                st.toast("✅ Protokoll aus Cache geladen!")
                break

    # Prüfe ob Quelldatei noch existiert (nur wenn noch kein Protokoll erstellt wurde)
    if not item.get('protocol') and not file_path.exists():
        st.error(f"❌ Quelldatei nicht gefunden: `{file_path.name}`")
        st.info("Die Datei existiert nicht mehr. Bitte prüfe ob sie bereits verarbeitet wurde.")
        return

    # Zeige Transkript-Info
    st.info(f"📄 Datei: **{file_path.name}**")

    # Button zum Starten
    if not item.get('protocol'):
        # === ÄNDERUNG 2: Auto-Refresh wenn Background-Job läuft ===
        with _bg_jobs_lock:
            bg_job = _bg_protocol_jobs.get(item['id'])

        if bg_job and bg_job['status'] == 'running':
            st.info(f"⏳ Protokoll wird im Hintergrund erstellt ({bg_job['chunks']} Tokens)...")
            st.progress(min(0.95, bg_job['chunks'] * 0.005), text=f"✨ {bg_job['chunks']} Tokens generiert")
            st.caption("Die Seite aktualisiert sich automatisch wenn das Protokoll fertig ist.")
            # Auto-Refresh alle 3 Sekunden um Fortschritt zu zeigen
            time.sleep(3)
            st.rerun()
            return

        if st.button("🚀 Protokoll jetzt erstellen", type="primary", use_container_width=True):
            st.session_state[f'start_protocol_{idx}'] = True
            st.rerun()

        if not st.session_state.get(f'start_protocol_{idx}', False):
            st.info("💡 Klicke auf den Button um die Protokoll-Erstellung zu starten")
            return

        # Protokoll erstellen mit STREAMING!
        st.markdown("---")
        st.markdown("#### 🎬 Live-Protokoll-Erstellung")

        try:
            # Lade Transkript
            if file_path.suffix.lower() == '.pdf':
                from langchain_community.document_loaders import PyPDFLoader
                loader = PyPDFLoader(str(file_path))
                pages = loader.load()
                transcript_text = "\n\n".join([page.page_content for page in pages])
            else:
                transcript_text = file_path.read_text(encoding='utf-8')

            # LLM
            orch = st.session_state['orchestrator']
            llm = orch.research_agent.llm

            # Meeting-Titel
            meeting_title = file_path.stem.split('_', 2)[-1] if '_' in file_path.stem else file_path.stem

            # Teilnehmer & Datum aus Event
            attendees = None
            meeting_date = None
            if selected_event:
                if selected_event.get('attendees'):
                    attendees = []
                    for att in selected_event['attendees']:
                        if isinstance(att, dict):
                            attendees.append(att.get('name', att.get('email', '')))
                        elif isinstance(att, str):
                            attendees.append(att)

                if selected_event.get('start'):
                    event_start = selected_event['start']
                    if isinstance(event_start, str):
                        event_start_dt = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
                        event_start_dt = convert_to_berlin_time(event_start_dt)
                        meeting_date = event_start_dt.strftime('%d.%m.%Y %H:%M')

            # Fortschrittsanzeige
            progress_bar = st.progress(0, text="⏳ Vorbereitung...")
            status_text = st.empty()
            protocol_preview = st.empty()

            import time
            progress_bar.progress(10, text="📋 Analysiere Transkript...")
            status_text.info(f"📊 {len(transcript_text.split())} Wörter | {len(transcript_text)} Zeichen")
            time.sleep(0.3)

            progress_bar.progress(20, text="🤖 Starte KI-Verarbeitung...")
            status_text.info("🔄 Verbinde mit Claude...")
            time.sleep(0.3)

            progress_bar.progress(30, text="✨ Generiere Protokoll live...")
            status_text.success("🎯 Live-Streaming aktiv!")

            # Lade Agenda falls vorhanden
            agenda_text = None
            if selected_event:
                agenda_dir = _get_user_ctx().data_dir / "agendas"
                if agenda_dir.exists():
                    # Suche nach Agenda-Datei für diesen Termin
                    event_title = selected_event.get('title', '')
                    # Versuche verschiedene Dateinamen-Patterns
                    import re

                    # Extrahiere Datum aus Event
                    event_start = selected_event.get('start')
                    if isinstance(event_start, str):
                        try:
                            event_start_dt = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
                            date_str = event_start_dt.strftime("%Y-%m-%d")
                        except:
                            date_str = None
                    else:
                        date_str = None

                    # Suche nach passender Agenda-Datei
                    for agenda_file in agenda_dir.glob("Agenda_*.pdf"):
                        # Prüfe ob Dateiname passt (Datum oder Titel)
                        file_stem = agenda_file.stem
                        if date_str and date_str in file_stem:
                            # Datum stimmt überein
                            if any(word.lower() in file_stem.lower() for word in event_title.split() if len(word) > 3):
                                # Lade PDF-Inhalt
                                try:
                                    from langchain_community.document_loaders import PyPDFLoader
                                    loader = PyPDFLoader(str(agenda_file))
                                    pages = loader.load()
                                    agenda_text = "\n\n".join([page.page_content for page in pages])
                                    st.info(f"📋 Agenda gefunden: {agenda_file.name}")
                                    break
                                except:
                                    pass

            # STREAMING
            protocol_parts = []
            chunk_count = 0

            for chunk in extract_protocol_from_transcript_streaming(
                transcript_text,
                meeting_title,
                llm,
                attendees=attendees,
                meeting_date=meeting_date,
                agenda_text=agenda_text
            ):
                protocol_parts.append(chunk)
                chunk_count += 1

                # Fortschritt
                estimated_progress = min(90, 30 + (chunk_count * 0.6))
                progress_bar.progress(int(estimated_progress), text=f"✨ {chunk_count} Tokens...")

                # Live-Vorschau
                if chunk_count % 5 == 0:
                    protocol_preview.markdown(''.join(protocol_parts))

            protocol_text = ''.join(protocol_parts)

            # Fertig
            progress_bar.progress(100, text="🎉 Protokoll erstellt!")
            protocol_preview.markdown(protocol_text)
            status_text.success(f"✅ {chunk_count} Tokens | {len(protocol_text)} Zeichen")
            time.sleep(1)

            # Cleanup
            progress_bar.empty()
            status_text.empty()
            protocol_preview.empty()

            # Speichere
            st.session_state['transcript_queue'][idx]['protocol'] = protocol_text
            # WICHTIG: workflow_step NICHT ändern (bleibt bei 2), damit Editor angezeigt wird!
            st.session_state[f'start_protocol_{idx}'] = False

            # Persistiere zu Disk
            save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)

            st.success("✅ Protokoll erstellt!")
            st.rerun()

        except Exception as e:
            st.error(f"❌ Fehler: {e}")
            import traceback
            st.code(traceback.format_exc())

    else:
        # Protokoll bereits erstellt - zeige Editor!
        st.success("✅ Protokoll erstellt - jetzt bearbeiten!")

        st.markdown("---")
        st.markdown("#### ✏️ Protokoll bearbeiten")
        st.info("💡 Bearbeite das Protokoll unten. Änderungen werden automatisch gespeichert.")

        # Zeige Statistiken
        col_stats1, col_stats2, col_stats3 = st.columns(3)
        with col_stats1:
            st.metric("Zeichen", len(item['protocol']))
        with col_stats2:
            st.metric("Wörter", len(item['protocol'].split()))
        with col_stats3:
            # Zähle Platzhalter [?]
            placeholder_count = item['protocol'].count('[?]')
            st.metric("⚠️ Platzhalter [?]", placeholder_count)

        if placeholder_count > 0:
            st.warning(f"⚠️ **{placeholder_count} Platzhalter [?] gefunden** - bitte ergänzen!")

        # GROSSER EDITOR - Volle Höhe!
        edited_protocol = st.text_area(
            "Protokoll-Inhalt:",
            value=item['protocol'],
            height=600,  # ← Größer!
            key=f"protocol_editor_{idx}",
            help="Bearbeite das Protokoll hier. Ersetze [?] durch korrekte Werte."
        )

        # AUTO-SAVE: Speichere bei jeder Änderung
        if edited_protocol != item['protocol']:
            st.session_state['transcript_queue'][idx]['protocol'] = edited_protocol

            # Persistiere zu Disk
            wip_dir = _get_user_ctx().wip_dir
            save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)

            st.info("💾 Auto-Speichern: Änderungen gespeichert")

        st.markdown("---")

        # Vorschau-Option
        with st.expander("👁️ Vorschau (formatiert)", expanded=False):
            st.markdown(edited_protocol)

        # Aktions-Buttons
        col_save, col_next = st.columns(2)

        with col_save:
            if st.button("🔄 Neu generieren", use_container_width=True, help="Protokoll komplett neu erstellen"):
                # Lösche bestehendes Protokoll
                st.session_state['transcript_queue'][idx]['protocol'] = None
                st.session_state[f'start_protocol_{idx}'] = False

                # Persistiere zu Disk
                wip_dir = _get_user_ctx().wip_dir
                save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)

                st.info("Protokoll zurückgesetzt - klicke auf 'Protokoll erstellen' um neu zu generieren")
                st.rerun()

        with col_next:
            if st.button("Weiter zu Tasks →", type="primary", use_container_width=True):
                # Explizit sicherstellen dass die aktuelle Editor-Version gespeichert wird
                st.session_state['transcript_queue'][idx]['protocol'] = edited_protocol
                st.session_state['transcript_queue'][idx]['workflow_step'] = 3

                # Persistiere zu Disk
                wip_dir = _get_user_ctx().wip_dir
                save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)

                st.rerun()


def render_step_extract_tasks(idx: int):
    """Schritt 4: Tasks extrahieren"""
    st.markdown("### 3️⃣ Tasks extrahieren")

    item = st.session_state['transcript_queue'][idx]
    protocol_text = item.get('protocol', '')

    # Zurück-Button zu Schritt 3
    if st.button("← Zurück zu Schritt 2 (Protokoll bearbeiten)", key=f"back_to_step3_from_4_{idx}"):
        st.session_state['transcript_queue'][idx]['workflow_step'] = 2

        # Persistiere zu Disk
        wip_dir = _get_user_ctx().wip_dir
        save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)

        st.rerun()

    st.markdown("---")

    if not protocol_text:
        st.warning("⚠️ Kein Protokoll vorhanden. Bitte erstelle zuerst ein Protokoll.")
        if st.button("← Zurück zu Schritt 2"):
            st.session_state['transcript_queue'][idx]['workflow_step'] = 2

            # Persistiere zu Disk
            wip_dir = _get_user_ctx().wip_dir
            save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)

            st.rerun()
        return

    # Button zum Tasks extrahieren
    if not item.get('tasks'):
        # Prüfe ob Extraktion gestartet wurde
        if not st.session_state.get(f'extract_tasks_{idx}', False):
            # Noch NICHT gestartet - zeige Button
            st.info("💡 Klicke auf den Button, um Tasks aus dem Protokoll zu extrahieren")

            col1, col2 = st.columns([3, 1])
            with col1:
                if st.button("🎯 Tasks jetzt extrahieren", type="primary", use_container_width=True, key=f"extract_btn_{idx}"):
                    st.session_state[f'extract_tasks_{idx}'] = True
                    st.rerun()
            with col2:
                if st.button("Überspringen →", use_container_width=True, key=f"skip_btn_{idx}"):
                    st.session_state['transcript_queue'][idx]['tasks'] = []
                    st.session_state['transcript_queue'][idx]['workflow_step'] = 4

                    # Persistiere zu Disk
                    wip_dir = _get_user_ctx().wip_dir
                    save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)

                    st.rerun()
            return

        # Extraktion wurde gestartet - führe aus
        status_text = st.empty()
        status_text.info("🔄 Extrahiere Tasks aus Protokoll... Dies kann 30-60 Sekunden dauern.")

        try:
            import time

            orch = st.session_state['orchestrator']
            llm = orch.research_agent.llm

            start_time = time.time()
            status_text.info(f"🤖 LLM analysiert das Protokoll... ({int(time.time() - start_time)}s)")

            # LLM-Call mit Timeout-Handling
            tasks = extract_tasks_from_transcript(protocol_text, llm)

            elapsed = int(time.time() - start_time)
            status_text.empty()

            st.session_state['transcript_queue'][idx]['tasks'] = tasks
            st.session_state['transcript_queue'][idx]['workflow_step'] = 4
            st.session_state[f'extract_tasks_{idx}'] = False

            # Persistiere zu Disk
            wip_dir = _get_user_ctx().wip_dir
            save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)

            st.success(f"✅ {len(tasks)} Tasks gefunden in {elapsed}s!")
            st.rerun()

        except Exception as e:
            status_text.empty()
            st.error(f"❌ Fehler beim Extrahieren: {str(e)}")

            # Zeige Traceback für Debugging
            import traceback
            with st.expander("🔍 Details zum Fehler"):
                st.code(traceback.format_exc())

            col1, col2 = st.columns(2)
            with col1:
                if st.button("🔄 Erneut versuchen"):
                    st.session_state[f'extract_tasks_{idx}'] = False
                    st.rerun()
            with col2:
                if st.button("Ohne Tasks fortfahren →"):
                    st.session_state['transcript_queue'][idx]['tasks'] = []
                    st.session_state['transcript_queue'][idx]['workflow_step'] = 4
                    st.session_state[f'extract_tasks_{idx}'] = False

                    # Persistiere zu Disk
                    wip_dir = _get_user_ctx().wip_dir
                    save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)

                    st.rerun()

    else:
        # Tasks bereits extrahiert
        tasks = item['tasks']
        st.success(f"✅ {len(tasks)} Tasks gefunden!")

        if tasks:
            st.markdown("#### 📋 Gefundene Tasks:")
            st.caption("✏️ Bearbeite die Tasks in der Tabelle. Änderungen werden automatisch gespeichert.")

            # Hole Asana-User für Dropdown
            orch = st.session_state.get('orchestrator')
            user_options = ["[?]"]  # Default

            if orch and orch.asana_agent and orch.asana_agent.is_connected():
                try:
                    asana_users = orch.asana_agent.get_workspace_users()
                    user_options = ["[?]"] + [user['name'] for user in asana_users]
                except:
                    pass

            # Konvertiere Tasks zu DataFrame
            import pandas as pd
            from datetime import date

            task_list = []
            for task in tasks:
                # Parse due_date
                due_date_value = None
                due_date_str = task.get('due_date', '')
                if due_date_str and due_date_str != '[?]':
                    try:
                        if '.' in due_date_str:  # DD.MM.YYYY
                            from datetime import datetime
                            parsed = datetime.strptime(due_date_str, '%d.%m.%Y')
                            due_date_value = parsed.date()
                        elif '-' in due_date_str:  # YYYY-MM-DD
                            from datetime import datetime
                            parsed = datetime.strptime(due_date_str, '%Y-%m-%d')
                            due_date_value = parsed.date()
                    except:
                        pass

                task_list.append({
                    'Titel': task.get('title', ''),
                    'Zuständig': task.get('assignee', '[?]'),
                    'Fällig am': due_date_value,
                    'Beschreibung': task.get('description', '')
                })

            df = pd.DataFrame(task_list)

            # Editierbare Tabelle
            edited_df = st.data_editor(
                df,
                use_container_width=True,
                num_rows="dynamic",
                height=min(400, len(df) * 40 + 40),
                column_config={
                    "Titel": st.column_config.TextColumn(
                        "Titel",
                        help="Aufgaben-Titel",
                        max_chars=200,
                        required=True,
                        width="medium"
                    ),
                    "Zuständig": st.column_config.SelectboxColumn(
                        "Zuständig",
                        help="Verantwortliche Person",
                        options=user_options,
                        width="small"
                    ),
                    "Fällig am": st.column_config.DateColumn(
                        "Fällig am",
                        help="Fälligkeitsdatum",
                        format="DD.MM.YYYY",
                        width="small"
                    ),
                    "Beschreibung": st.column_config.TextColumn(
                        "Beschreibung",
                        help="Details zur Aufgabe",
                        width="large"
                    )
                },
                key=f"task_editor_{idx}"
            )

            # Speichere bearbeitete Tasks zurück
            updated_tasks = []
            for index, row in edited_df.iterrows():
                # Konvertiere due_date zurück zu String
                due_date_str = ''
                if pd.notna(row.get('Fällig am')):
                    try:
                        due_date_obj = row['Fällig am']
                        if hasattr(due_date_obj, 'strftime'):
                            due_date_str = due_date_obj.strftime('%Y-%m-%d')
                    except:
                        pass

                updated_tasks.append({
                    'title': row.get('Titel', ''),
                    'assignee': row.get('Zuständig', '[?]'),
                    'due_date': due_date_str or '[?]',
                    'description': row.get('Beschreibung', '')
                })

            st.session_state['transcript_queue'][idx]['tasks'] = updated_tasks

            # Persistiere zu Disk
            wip_dir = _get_user_ctx().wip_dir
            save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)
        else:
            st.info("Keine Tasks gefunden")

            # Möglichkeit, Tasks manuell hinzuzufügen
            if st.button("➕ Task manuell hinzufügen", use_container_width=True):
                new_task = {
                    'title': 'Neuer Task',
                    'assignee': '[?]',
                    'description': '',
                    'due_date': '[?]'
                }
                if 'tasks' not in st.session_state['transcript_queue'][idx]:
                    st.session_state['transcript_queue'][idx]['tasks'] = []
                st.session_state['transcript_queue'][idx]['tasks'].append(new_task)

                # Persistiere zu Disk
                wip_dir = _get_user_ctx().wip_dir
                save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)

                st.rerun()

        # Weiter-Button
        if st.button("Weiter zur Finalisierung →", type="primary", use_container_width=True):
            st.session_state['transcript_queue'][idx]['workflow_step'] = 4

            # Persistiere zu Disk
            wip_dir = _get_user_ctx().wip_dir
            save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)

            st.rerun()


def render_step_finalize(idx: int):
    """Schritt 5: Finalisieren"""
    st.markdown("### 4️⃣ Finalisieren & Archivieren")

    item = st.session_state['transcript_queue'][idx]

    # Zusammenfassung
    st.markdown("#### 📊 Zusammenfassung")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("📄 Protokoll", "✅ Erstellt" if item.get('protocol') else "❌ Fehlt")
    with col2:
        task_count = len(item.get('tasks', []))
        st.metric("📋 Tasks", task_count)
    with col3:
        st.metric("📅 Termin", "✅ Zugeordnet" if item.get('selected_event') else "➖ Kein")

    # Zurück-Button um Tasks zu bearbeiten
    if st.button("← Zurück zu Schritt 3 (Tasks bearbeiten)", key=f"back_to_step4_{idx}"):
        st.session_state['transcript_queue'][idx]['workflow_step'] = 3  # Zurück zu Tasks

        # Persistiere zu Disk
        wip_dir = _get_user_ctx().wip_dir
        save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)

        st.rerun()

    st.markdown("---")

    # Export-Optionen
    st.markdown("#### 💾 Export & Archivierung")

    col_pdf, col_asana = st.columns(2)

    with col_pdf:
        st.markdown("**📎 Protokoll an Termin**")
        selected_event = item.get('selected_event')
        protocol_text = item.get('protocol', '')

        if not selected_event:
            st.caption("Kein Termin zugeordnet")
            st.button("An Termin anhängen", use_container_width=True, disabled=True)
        elif not protocol_text:
            st.caption("Kein Protokoll vorhanden")
            st.button("An Termin anhängen", use_container_width=True, disabled=True)
        else:
            st.caption("Als PDF an Outlook-Termin")
            if st.button("📎 An Termin anhängen", use_container_width=True):
                try:
                    with st.spinner("Erstelle PDF und hänge an Termin..."):
                        from datetime import datetime

                        # Hole Orchestrator und Outlook Tool
                        orch = st.session_state.get('orchestrator')
                        outlook_tool = orch.outlook_tool

                        # Erstelle Protokoll-Verzeichnis
                        protocol_dir = _get_user_ctx().protocols_dir
                        protocol_dir.mkdir(parents=True, exist_ok=True)

                        # Meeting-Titel und Datum
                        meeting_title = selected_event.get('title', 'Meeting').replace('/', '_').replace('\\', '_')
                        date_str = datetime.now().strftime("%Y-%m-%d")

                        # Speichere als Markdown
                        md_filename = f"{date_str}_Protokoll_{meeting_title}.md"
                        md_path = protocol_dir / md_filename

                        with open(md_path, 'w', encoding='utf-8') as f:
                            f.write(protocol_text)

                        # Konvertiere zu PDF
                        pdf_filename = md_filename.replace('.md', '.pdf')
                        pdf_path = protocol_dir / pdf_filename

                        # convert_markdown_to_pdf ist bereits in diesem Modul definiert
                        if convert_markdown_to_pdf(md_path, pdf_path):
                            # Hänge an Outlook-Termin an
                            event_id = selected_event.get('id')
                            result = outlook_tool.add_attachment_to_event(
                                event_id=event_id,
                                file_path=str(pdf_path),
                                file_name=pdf_filename
                            )

                            if result.get('success'):
                                st.success(f"✅ PDF erfolgreich an Termin angehängt!")

                                # Füge Kategorie "Protokoll" zum Termin hinzu + Betreff-Prefix
                                with st.spinner("Markiere Termin als 'Protokoll erstellt'..."):
                                    category_result = outlook_tool.add_category_to_event(
                                        event_id=event_id,
                                        category="Protokoll"
                                    )
                                    if category_result.get('success'):
                                        st.success(f"✅ {category_result.get('message', 'Kategorie hinzugefügt')}")
                                    else:
                                        st.warning(f"⚠️ Kategorie konnte nicht gesetzt werden: {category_result.get('error')}")

                                    prefix_result = outlook_tool.add_protocol_subject_prefix(event_id=event_id)
                                    if prefix_result.get('success'):
                                        st.success(f"✅ {prefix_result.get('message', 'Betreff-Prefix gesetzt')}")
                                    else:
                                        st.warning(f"⚠️ Betreff-Prefix konnte nicht gesetzt werden: {prefix_result.get('error')}")
                            else:
                                st.error(f"❌ Fehler beim Anhängen: {result.get('error')}")
                        else:
                            st.error("❌ PDF-Konvertierung fehlgeschlagen")

                except Exception as e:
                    st.error(f"❌ Fehler: {str(e)}")
                    import traceback
                    st.text(traceback.format_exc())

    with col_asana:
        st.markdown("**🎯 Tasks zu Asana**")
        tasks = item.get('tasks', [])
        st.caption(f"{len(tasks)} Tasks exportieren")

        # Hole Asana Agent
        orch = st.session_state.get('orchestrator')
        if not orch or not orch.asana_agent or not orch.asana_agent.is_connected():
            st.warning("⚠️ Asana nicht verbunden")
            st.button("Zu Asana senden", use_container_width=True, disabled=True)
        elif len(tasks) == 0:
            st.info("ℹ️ Keine Tasks vorhanden")
            st.button("Zu Asana senden", use_container_width=True, disabled=True)
        else:
            # Projekt-Auswahl
            asana_agent = orch.asana_agent
            projects = asana_agent.list_projects()

            if not projects:
                st.warning("⚠️ Keine Asana-Projekte gefunden")
            else:
                project_options = ["[Projekt wählen...]"] + [p['name'] for p in projects]

                # Versuche Auto-Match mit Meeting-Titel
                default_idx = 0
                if item.get('selected_event'):
                    meeting_title = item['selected_event'].get('title', '')
                    for i, p in enumerate(projects, 1):
                        if meeting_title.lower() in p['name'].lower():
                            default_idx = i
                            break

                selected_project_name = st.selectbox(
                    "Asana-Projekt:",
                    project_options,
                    index=default_idx,
                    key=f"asana_project_{idx}",
                    label_visibility="collapsed"
                )

                selected_project_gid = None
                if selected_project_name != "[Projekt wählen...]":
                    for p in projects:
                        if p['name'] == selected_project_name:
                            selected_project_gid = p['gid']
                            break

                # Section-Auswahl für Protokoll
                selected_section_gid = None
                if selected_project_gid:
                    sections = asana_agent.get_project_sections(selected_project_gid)
                    if sections:
                        section_names = ["[Keine Section - Standardposition]"] + [s['name'] for s in sections]

                        # Default: "Protokolle" Section falls vorhanden
                        default_idx = 0
                        for i, s in enumerate(sections, 1):
                            if s['name'].lower() in ['protokolle', 'protokoll']:
                                default_idx = i
                                break

                        selected_section_name = st.selectbox(
                            "📂 In welche Section soll das Protokoll?",
                            section_names,
                            index=default_idx,
                            key=f"protocol_section_{idx}",
                            help="Wähle die Asana-Section für das Protokoll"
                        )

                        if selected_section_name != "[Keine Section - Standardposition]":
                            for s in sections:
                                if s['name'] == selected_section_name:
                                    selected_section_gid = s['gid']
                                    break

                # Asana-Export Button
                button_disabled = (selected_project_gid is None)
                protocol_text = item.get('protocol', '')

                if st.button("🚀 Jetzt zu Asana senden", use_container_width=True, type="primary", disabled=button_disabled):
                    try:
                        import time
                        from datetime import datetime

                        start_time = time.time()

                        # ========================================
                        # SCHRITT 1: PROTOKOLL-AUFGABE ERSTELLEN
                        # ========================================
                        with st.spinner("📄 Erstelle Protokoll-Aufgabe in Asana..."):
                            # Meeting-Titel und Datum
                            meeting_title = "Meeting"
                            if item.get('selected_event'):
                                meeting_title = item['selected_event'].get('title', 'Meeting')

                            # Datum des Meetings verwenden, nicht heute
                            meeting_date_str = None
                            if item.get('selected_event'):
                                raw_start = item['selected_event'].get('start', '')
                                if isinstance(raw_start, str) and len(raw_start) >= 10:
                                    meeting_date_str = raw_start[:10]
                            date_str = meeting_date_str or datetime.now().strftime("%Y-%m-%d")
                            protocol_task_title = f"📄 Protokoll {date_str} - {meeting_title}"

                            # Erstelle Protokoll-Aufgabe (direkt in ausgewählter Section via memberships)
                            protocol_task_result = asana_agent.create_task(
                                name=protocol_task_title,
                                notes=protocol_text,
                                project_gid=selected_project_gid,
                                assignee_gid=None,
                                section_gid=selected_section_gid
                            )

                            if not protocol_task_result.get('success'):
                                st.error(f"❌ Fehler beim Erstellen der Protokoll-Aufgabe: {protocol_task_result.get('error')}")
                                raise Exception("Protokoll-Aufgabe konnte nicht erstellt werden")

                            protocol_task_gid = protocol_task_result.get('task_gid')
                            section_label = f" in '{selected_section_name}'" if selected_section_gid else ""
                            st.success(f"✅ Protokoll-Aufgabe erstellt{section_label}: {protocol_task_title}")

                            # Kategorie wird beim "An Termin anhängen"-Button gesetzt, nicht hier

                        # ========================================
                        # SCHRITT 2: PDF ERSTELLEN UND ANHÄNGEN
                        # ========================================
                        if protocol_text:
                            with st.spinner("📎 Erstelle und hänge PDF an..."):
                                try:
                                    protocol_dir = _get_user_ctx().protocols_dir
                                    protocol_dir.mkdir(parents=True, exist_ok=True)

                                    # Bereinige Meeting-Titel für Dateinamen
                                    clean_title = meeting_title.replace('/', '_').replace('\\', '_')
                                    md_filename = f"{date_str}_Protokoll_{clean_title}.md"
                                    md_path = protocol_dir / md_filename

                                    # Speichere Markdown
                                    with open(md_path, 'w', encoding='utf-8') as f:
                                        f.write(protocol_text)

                                    # Konvertiere zu PDF
                                    pdf_filename = md_filename.replace('.md', '.pdf')
                                    pdf_path = protocol_dir / pdf_filename

                                    # convert_markdown_to_pdf ist bereits in diesem Modul definiert
                                    if convert_markdown_to_pdf(md_path, pdf_path):
                                        # Hänge PDF an Asana-Aufgabe an
                                        attachment_result = asana_agent.attach_file_to_task(
                                            task_gid=protocol_task_gid,
                                            file_path=str(pdf_path),
                                            file_name=pdf_filename
                                        )

                                        if attachment_result.get('success'):
                                            st.success(f"✅ PDF '{pdf_filename}' angehängt")
                                        else:
                                            st.warning(f"⚠️ PDF-Anhang fehlgeschlagen: {attachment_result.get('error')}")
                                    else:
                                        st.warning("⚠️ PDF-Konvertierung fehlgeschlagen")
                                except Exception as e:
                                    st.warning(f"⚠️ PDF-Erstellung fehlgeschlagen: {e}")

                        # ========================================
                        # SCHRITT 3: USER-LOOKUP CACHE
                        # ========================================
                        user_cache = {}
                        try:
                            cached_users = asana_agent.get_workspace_users()
                            for user in cached_users:
                                user_name_lower = user['name'].lower().strip()
                                user_cache[user_name_lower] = user['gid']
                        except Exception as e:
                            st.warning(f"⚠️ User-Cache konnte nicht erstellt werden: {e}")

                        # ========================================
                        # SCHRITT 4: TASKS ALS UNTERAUFGABEN ERSTELLEN
                        # ========================================
                        st.markdown("---")
                        st.markdown("#### 🔄 Erstelle Unteraufgaben...")

                        # Erstelle UI-Elemente EINMAL vor der Schleife
                        progress_bar = st.progress(0)
                        status_text = st.empty()

                        # Stats-Container mit Spalten
                        stats_cols = st.columns(4)
                        stat_progress = stats_cols[0].empty()
                        stat_success = stats_cols[1].empty()
                        stat_errors = stats_cols[2].empty()
                        stat_time = stats_cols[3].empty()

                        success_count = 0
                        errors = []
                        total_tasks = len(tasks)

                        for task_idx, task in enumerate(tasks, start=1):
                            try:
                                title = task.get('title', '')
                                description = task.get('description', '')
                                assignee_name = task.get('assignee', '')
                                due_date_str = task.get('due_date', '')
                                top_name = task.get('top', '')

                                # Berechne Statistiken
                                elapsed_time = time.time() - start_time
                                avg_time_per_task = elapsed_time / task_idx if task_idx > 0 else 0
                                remaining_tasks = total_tasks - task_idx
                                estimated_remaining = avg_time_per_task * remaining_tasks

                                # Update Fortschrittsanzeige
                                status_msg = f"🔄 Erstelle Unteraufgabe {task_idx}/{total_tasks}: {title[:40]}..."
                                if estimated_remaining > 0:
                                    status_msg += f" (~{int(estimated_remaining)}s verbleibend)"
                                status_text.text(status_msg)

                                # Update Progress Bar
                                progress_bar.progress(task_idx / total_tasks)

                                # Update Live-Statistiken
                                stat_progress.metric("Fortschritt", f"{task_idx}/{total_tasks}")
                                stat_success.metric("✅ Erfolg", success_count)
                                stat_errors.metric("❌ Fehler", len(errors))
                                stat_time.metric("⏱️ Zeit", f"{int(elapsed_time)}s")

                                # Parse due_date
                                due_on = None
                                if due_date_str and due_date_str != '[?]':
                                    try:
                                        from datetime import datetime, date
                                        # Versuche verschiedene Formate
                                        if isinstance(due_date_str, date):
                                            due_on = due_date_str.strftime('%Y-%m-%d')
                                        elif '.' in due_date_str:  # DD.MM.YYYY
                                            parsed = datetime.strptime(due_date_str, '%d.%m.%Y')
                                            due_on = parsed.strftime('%Y-%m-%d')
                                        elif '-' in due_date_str:  # YYYY-MM-DD
                                            due_on = due_date_str
                                    except:
                                        pass

                                # Baue erweiterte Beschreibung mit Ursprungsinfo auf
                                origin_lines = []
                                origin_lines.append(f"📎 Ursprung: {protocol_task_title}")
                                if top_name:
                                    origin_lines.append(f"📋 Tagesordnungspunkt: {top_name}")
                                if assignee_name and assignee_name != '[?]':
                                    origin_lines.append(f"👤 Geplanter Verantwortlicher: {assignee_name}")

                                origin_block = "\n".join(origin_lines)
                                if description:
                                    enhanced_description = f"{description}\n\n---\n{origin_block}"
                                else:
                                    enhanced_description = origin_block

                                # Erstelle Subtask ohne Assignee (Verantwortlicher steht in der Beschreibung)
                                result = asana_agent.create_subtask(
                                    parent_task_gid=protocol_task_gid,
                                    name=title,
                                    notes=enhanced_description,
                                    due_on=due_on,
                                    assignee_gid=None
                                )

                                if result.get('success'):
                                    success_count += 1
                                else:
                                    errors.append(f"{title}: {result.get('error')}")

                            except Exception as e:
                                errors.append(f"{task.get('title', 'Unbekannt')}: {str(e)}")

                        # ========================================
                        # ABSCHLUSS
                        # ========================================
                        progress_bar.empty()
                        status_text.empty()
                        stat_progress.empty()
                        stat_success.empty()
                        stat_errors.empty()
                        stat_time.empty()

                        total_time = time.time() - start_time

                        if success_count > 0:
                            st.success(f"✅ {success_count} Task(s) erfolgreich in Asana erstellt in {int(total_time)}s!")

                            with st.expander("📊 Performance-Details", expanded=False):
                                perf_col1, perf_col2, perf_col3 = st.columns(3)
                                with perf_col1:
                                    st.metric("Gesamt-Zeit", f"{int(total_time)}s")
                                with perf_col2:
                                    avg_time = total_time / total_tasks if total_tasks > 0 else 0
                                    st.metric("Ø pro Task", f"{avg_time:.1f}s")
                                with perf_col3:
                                    success_rate = (success_count / total_tasks * 100) if total_tasks > 0 else 0
                                    st.metric("Erfolgsrate", f"{success_rate:.0f}%")

                        if errors:
                            st.error(f"❌ {len(errors)} Fehler:")
                            for err in errors:
                                st.caption(f"• {err}")

                    except Exception as e:
                        st.error(f"❌ Fehler beim Asana-Export: {str(e)}")
                        import traceback
                        st.text(traceback.format_exc())

    st.markdown("---")

    # Abschluss-Button
    st.markdown("#### ✅ Protokoll abschließen")
    st.info("Das Protokoll wird archiviert und aus der Bearbeitungs-Liste entfernt.")

    if st.button("🎉 Abschließen & Archivieren", type="primary", use_container_width=True):
        st.session_state['transcript_queue'][idx]['status'] = 'completed'
        st.session_state['transcript_queue'][idx]['workflow_step'] = 5
        st.session_state['selected_transcript_idx'] = None

        # Lösche WIP-Datei (Workflow abgeschlossen)
        wip_dir = _get_user_ctx().wip_dir
        delete_wip_item(st.session_state['transcript_queue'][idx], wip_dir)

        st.success("🎉 Protokoll erfolgreich abgeschlossen!")
        st.balloons()

        import time
        time.sleep(2)
        st.rerun()
if __name__ == "__main__":
    main()
