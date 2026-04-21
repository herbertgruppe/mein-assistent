"""
Session State Management: Initialisierung, Reset, Cache-Prüfung.
"""
import os
import hashlib
import streamlit as st
from user_context import UserContext


def _get_user_ctx():
    """Holt den UserContext aus dem Session-State. Fallback auf Legacy-Pfade."""
    ctx = st.session_state.get('user_ctx')
    if ctx:
        return ctx
    # Fallback für Übergangszeitraum (kein Login aktiv)
    return UserContext("_default")


def initialize_session_state():
    """Initialisiere Session State"""
    from utils.orchestrator import StreamlitOrchestrator

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


def check_and_reset_cache_if_env_changed():
    """
    Prüft ob .env-Konfiguration geändert wurde und löscht Cache automatisch.

    Speichert Hash der wichtigen .env-Werte in session_state.
    Bei Änderung wird der Cache geleert.
    """
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
