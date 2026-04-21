"""
Mein Assistent – Herbert Gruppe
Streamlit-App (kompaktes Einstiegsskript, ~200 Zeilen).

Modulstruktur:
  utils/design.py          – HG_CSS, inject_hg_css(), hg_card(), ...
  utils/auth.py            – _load_users_config, get_user_role, get_username
  utils/state.py           – initialize_session_state, reset_chat_session, ...
  utils/api_cache.py       – @st.cache_data Wrapper
  utils/background.py      – _bg_protocol_jobs, start_bg_protocol_generation
  utils/orchestrator.py    – StreamlitOrchestrator
  utils/protocol.py        – extract_*, convert_markdown_to_pdf, ...
  pages/sidebar.py         – render_sidebar
  pages/mein_tag.py        – render_dashboard_tab
  pages/meeting_manager.py – render_transcripts_tab
  pages/dokumente.py       – render_documents_tab
  pages/archiv.py          – render_archive_tab
  pages/einstellungen.py   – render_settings_tab
  pages/admin.py           – render_admin_panel
"""

import time
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

# WICHTIG: load_dotenv() MUSS vor dem Import der Agenten aufgerufen werden!
load_dotenv()

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
# CSS — Herbert Gruppe Design System
# ============================================================================
from utils.design import inject_hg_css
inject_hg_css()

# ============================================================================
# AUTH / STATE HELPERS
# ============================================================================
from utils.auth import get_user_role, get_username
from utils.state import initialize_session_state, check_and_reset_cache_if_env_changed
from user_context import UserContext


# ============================================================================
# HAUPT-APP
# ============================================================================

def main():
    """Haupt-App-Logik"""
    main_start = time.time()
    print(f"\n[DEBUG] ========== MAIN START @ {main_start} ==========")

    # ---- Authentication Gate (Authentik OIDC via st.login) ----
    if not st.user.is_logged_in:
        # Login-Screen: dunkler Brand-Hintergrund, zentriertes weißes Card mit Logo
        st.markdown("""
        <style>
        [data-testid="stAppViewContainer"] {
            background-color: #1B2D4F !important;
        }
        [data-testid="stAppViewContainer"] > .main {
            background: transparent;
        }
        .main .block-container {
            background: transparent !important;
            padding-top: 6rem;
        }
        </style>
        """, unsafe_allow_html=True)

        _, mid, _ = st.columns([1, 2, 1])
        with mid:
            st.markdown('<div class="hg-card" style="text-align:center;">', unsafe_allow_html=True)
            _logo_claim = Path(__file__).parent / "assets" / "Herbert-Gruppe-Logo-Claim_RGB.jpg"
            if _logo_claim.exists():
                st.image(str(_logo_claim), width=260)
            else:
                st.markdown("### Herbert Gruppe")
            st.markdown(
                '<p style="color:var(--gray-500); margin:0.5rem 0 1.5rem;">Mein Assistent &mdash; Interner Zugang</p>',
                unsafe_allow_html=True,
            )
            st.markdown('<hr style="border-color:var(--gray-200); margin-bottom:1.5rem;">', unsafe_allow_html=True)
            if st.button("Mit Herbert-Portal anmelden", type="primary", use_container_width=True):
                st.login("authentik")
            st.markdown('</div>', unsafe_allow_html=True)
        st.stop()

    # ---- Authentifiziert: User-Kontext aufbauen ----
    email = (st.user.email or '').lower()
    display_name = st.user.name or (email.split('@')[0] if email else 'User')
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
        /* Streamlit Header: Inhalt verstecken, aber Expand-Button freilassen */
        header[data-testid="stHeader"] {
            background: transparent !important;
            height: 0 !important;
            min-height: 0 !important;
            overflow: visible !important;
        }
        header[data-testid="stHeader"] > * { display: none !important; }
        [data-testid="collapsedControl"]   { display: flex !important; visibility: visible !important; }
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
    from pages.sidebar import render_sidebar
    sidebar_start = time.time()
    render_sidebar()
    print(f"[DEBUG] render_sidebar took {(time.time()-sidebar_start)*1000:.2f}ms")

    # Hauptbereich mit Tabs - Admin-Tab nur für Admins
    from pages.mein_tag import render_dashboard_tab
    from pages.meeting_manager import render_transcripts_tab
    from pages.dokumente import render_documents_tab
    from pages.archiv import render_archive_tab
    from pages.einstellungen import render_settings_tab
    from pages.admin import render_admin_panel

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


if __name__ == "__main__":
    main()
