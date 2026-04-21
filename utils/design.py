"""
Herbert Gruppe Design System — CSS-Konstante und Komponenten-Helfer
"""
import streamlit as st
from contextlib import contextmanager


HG_CSS = """
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

/* === Typografie === */
html, body, [class*="css"] {
  font-family: "Inter", -apple-system, "Segoe UI", Roboto, sans-serif !important;
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
  color: rgba(255,255,255,0.65) !important;
  border: none !important;
  text-align: left !important;
  padding: 0.625rem 0.75rem !important;
  border-radius: 0.5rem !important;
  font-weight: 500 !important;
  transition: background 0.15s, color 0.15s;
}
section[data-testid="stSidebar"] .stButton > button:hover {
  background-color: rgba(255,255,255,0.06) !important;
  color: #ffffff !important;
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
  max-width: 1200px;
  padding-top: 2rem;
  padding-bottom: 3rem;
}
h1, h2, h3, h4 {
  color: var(--gray-900);
  font-weight: 600;
  letter-spacing: -0.01em;
}
h1 { font-size: 1.875rem; margin-bottom: 0.25rem; }
h2 { font-size: 1.5rem;   margin-top: 2rem; }
h3 { font-size: 1.125rem; }

/* === Cards (hg-card + Rueckwaertskompat alte user-/assistant-message) === */
.hg-card, .user-message, .assistant-message {
  background: #ffffff;
  border: 1px solid var(--gray-200);
  border-radius: 0.75rem;
  box-shadow: 0 1px 2px rgba(0,0,0,0.04);
  padding: 1.5rem;
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

/* === Streamlit-Branding ausblenden === */
#MainMenu { visibility: hidden; }
footer { visibility: hidden; }
header[data-testid="stHeader"] { background: transparent; }

/* === Auto-generierte Pages-Navigation verstecken (wir nutzen st.tabs) === */
[data-testid="stSidebarNav"] { display: none !important; }

/* === Sidebar Expand-Button sichtbar halten wenn Sidebar kollabiert === */
[data-testid="collapsedControl"] {
  display: flex !important;
  visibility: visible !important;
  opacity: 1 !important;
  color: var(--brand-600) !important;
}
</style>
"""


def inject_hg_css():
    """Injiziert das Herbert Gruppe Design System CSS in die App."""
    st.markdown(HG_CSS, unsafe_allow_html=True)


@contextmanager
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


@contextmanager
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
