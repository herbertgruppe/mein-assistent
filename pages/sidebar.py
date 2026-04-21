"""
Sidebar-Rendering: Status, Memory, Dokumente, Microsoft Graph API, Workflow-Einstellungen.
"""
import os
import streamlit as st
import streamlit.components.v1 as components
from pathlib import Path

from utils.state import _get_user_ctx, reset_chat_session


def render_sidebar():
    """Rendert die Sidebar mit Status-Informationen"""
    with st.sidebar:
        # Herbert Gruppe Logo (weiss auf brand-600 Hintergrund — vom globalen CSS gesteuert)
        username = st.session_state.get('username', '')
        display_name = st.session_state.get('name', username)
        _logo_path = Path(__file__).parent.parent / "assets" / "Logo Herbert Gruppe white ohne Hintergrund.png"
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
                for key in ['username', 'email', 'name', 'role', 'user_ctx']:
                    if key in st.session_state:
                        del st.session_state[key]
                st.logout(redirect_to_provider=True)

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

        if profile.get("name"):
            st.markdown(f"**Name:** {profile['name']}")

        if profile.get("profession"):
            with st.expander("👔 Beruf"):
                st.write(profile['profession'])

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

                if "outlook_device_code" not in st.session_state:
                    if st.button("🔐 Mit Microsoft anmelden", type="primary", use_container_width=True):
                        with st.spinner("Initiiere Anmeldung..."):
                            result = outlook_tool.initiate_device_flow()

                            if result.get("success"):
                                st.session_state.outlook_device_code = result["device_info"]
                                st.rerun()
                            else:
                                st.error(f"❌ Fehler: {result.get('error')}")
                else:
                    device_info = st.session_state.outlook_device_code

                    st.success("✅ **Anmeldecode generiert!**")

                    user_code = device_info["user_code"]
                    st.markdown("### 📋 Ihr Anmeldecode:")
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
                                    del st.session_state.outlook_device_code
                                    st.rerun()
                                else:
                                    st.error(f"❌ Anmeldung fehlgeschlagen")
                                    st.caption(f"Fehler: {result.get('error')}")
                                    st.caption("Haben Sie den Code eingegeben und die Anmeldung abgeschlossen?")

                    with col2:
                        if st.button("❌ Abbrechen", use_container_width=True):
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

        if st.button("📋 Gedächtnis anzeigen", use_container_width=True):
            st.session_state.show_memory = not st.session_state.get('show_memory', False)
