"""
Admin-Panel: Rollen-Verwaltung (nur für Admins).
"""
import streamlit as st

from utils.auth import _load_users_config, _save_users_config


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
