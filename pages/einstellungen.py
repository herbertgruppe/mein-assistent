"""
Einstellungen-Tab: Per-User Credentials für Outlook und Asana.
"""
import streamlit as st

from utils.auth import _load_users_config, _save_users_config


def render_settings_tab():
    """Einstellungen-Tab: Per-User Credentials für Outlook und Asana."""
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
                user_ctx.outlook_token_file.unlink(missing_ok=True)
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
