"""
Inbox-Tab: E-Mail-Posteingang (asynchrone Architektur, nur DB lesen/schreiben).
Möglicherweise nicht mehr im Tab-Menü aktiv, aber für Vollständigkeit erhalten.
"""
import time
import streamlit as st
from typing import Any, Dict


def render_email_action_chat():
    """
    Vereinfachter Dialog für Forward/Reply - OHNE LLM.
    Nutzt vor-generierte Drafts aus DB.
    """
    if not st.session_state.email_chat_active or not st.session_state.email_chat_data:
        return

    email_data = st.session_state.email_chat_data
    action_type = email_data.get('action_type')
    email_id = email_data.get('email_id')
    email_db_id = email_data.get('email_db_id')
    subject = email_data.get('subject', '')
    sender = email_data.get('sender', '')
    body = email_data.get('body', '')
    forwarding_rule = email_data.get('forwarding_rule')

    if action_type == 'forward':
        st.subheader("↗️ Email weiterleiten")
    else:
        st.subheader("✉️ Email beantworten")

    if action_type == 'forward' and forwarding_rule:
        forward_to = forwarding_rule.get('forward_to', '')
        template = forwarding_rule.get('template', '')
        st.info(f"💡 **Regel-Vorschlag:** An {forward_to} weiterleiten\n\nVorgeschlagener Text: {template}")

    with st.expander("📧 Email-Details", expanded=False):
        st.markdown(f"**Betreff:** {subject}")
        st.markdown(f"**Von:** {sender}")
        st.markdown(f"**Inhalt:**")
        st.text_area("Email-Text", body, height=200, disabled=True, key=f"email_body_{email_id}")

    st.markdown("---")

    from utils.database import EmailDatabase
    db = EmailDatabase()
    email = db.get_email_by_id(email_db_id)
    draft_reply = email.get('draft_reply', '') if email else ''

    if draft_reply:
        st.info("💡 **Vorgeschlagene Antwort:**")
        st.text_area("Draft", draft_reply, height=150, disabled=True, key="draft_preview")

    st.markdown("### Ihre Nachricht")

    if action_type == 'reply':
        reply_all = st.checkbox(
            "An alle antworten (Reply All)",
            value=False,
            key=f'reply_all_{email_id}'
        )

    if action_type == 'forward':
        recipients_input = st.text_input(
            "An (Komma-getrennt)",
            value=forwarding_rule.get('forward_to', '') if forwarding_rule else '',
            placeholder="max@firma.de, anna@firma.de",
            key=f"recipients_{email_id}"
        )

    initial_message = ""
    if action_type == 'reply' and draft_reply:
        initial_message = draft_reply
    elif action_type == 'forward' and forwarding_rule:
        initial_message = forwarding_rule.get('template', '')

    message_input = st.text_area(
        "Nachricht bearbeiten",
        value=initial_message,
        height=250,
        key=f"message_{email_id}"
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("✅ Senden", type="primary", key=f"send_{email_id}"):
            if action_type == 'forward' and not recipients_input:
                st.error("❌ Bitte Empfänger angeben!")
            elif not message_input:
                st.error("❌ Bitte Nachricht eingeben!")
            else:
                action_data = {'comment': message_input}

                if action_type == 'forward':
                    recipients = [r.strip() for r in recipients_input.split(',') if r.strip()]
                    action_data['to_recipients'] = recipients
                    db.create_action(email_db_id, 'forward', action_data)
                    db.update_email_status(email_db_id, 'pending_forward')
                    success_msg = f"✅ Wird an {', '.join(recipients)} weitergeleitet..."
                else:
                    reply_all = st.session_state.get(f'reply_all_{email_id}', False)
                    action_data['reply_all'] = reply_all
                    action_data['archive_after_reply'] = True
                    db.create_action(email_db_id, 'reply', action_data)
                    db.update_email_status(email_db_id, 'pending_reply')
                    success_msg = "✅ Antwort wird gesendet..."

                st.success(success_msg)

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


def render_simple_email_card(email: Dict[str, Any], idx: int, db):
    """Einfache Email-Karte - nur anzeigen und DB-Instruktionen setzen"""
    priority = email.get('priority', 3)
    priority_badges = {
        5: "🔴 Kritisch",
        4: "🟠 Dringend",
        3: "🟡 Normal",
        2: "🟢 Niedrig",
        1: "⚪ Sehr niedrig"
    }
    priority_badge = priority_badges.get(priority, "🟡 Normal")

    sentiment = email.get('sentiment', 'neutral')
    sentiment_emojis = {
        'positiv': '😊',
        'neutral': '😐',
        'negativ': '😟',
        'dringend': '⚡'
    }
    sentiment_emoji = sentiment_emojis.get(sentiment, '😐')

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

        col1, col2, col3 = st.columns([3, 1, 1])
        with col1:
            st.markdown(f"### {email.get('subject', 'Kein Betreff')}")
        with col2:
            st.markdown(f"**{priority_badge}**")
        with col3:
            st.markdown(f"**{sentiment_emoji} {sentiment.title()}**")

        col1, col2 = st.columns([2, 1])
        with col1:
            st.markdown(f"**Von:** {email.get('sender_name', 'Unbekannt')} `<{email.get('sender_email', '')}>`")
        with col2:
            st.markdown(f"**{email.get('received_dt', '')[:16]}**")

        st.markdown(f"**Kategorie:** {email.get('category', 'Sonstiges')}")

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

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            if st.button("💬 Bearbeiten", key=f"edit_{idx}", use_container_width=True):
                st.session_state.email_chat_active = True
                st.session_state.email_chat_data = email
                st.session_state.email_chat_history = []
                st.rerun()

        with col2:
            if st.button("📤 Asana", key=f"asana_{idx}", use_container_width=True):
                db.set_instruction(email['id'], 'asana', {'project_gid': 'default'})
                db.hide_email(email['id'])
                st.success("✅ Wird an Asana gesendet...")
                st.rerun()

        with col3:
            if st.button("🗄️ Archiv", key=f"arch_{idx}", use_container_width=True):
                db.set_instruction(email['id'], 'archive')
                db.hide_email(email['id'])
                st.success("✅ Wird archiviert...")
                st.rerun()

        with col4:
            if st.button("🗑️", key=f"del_{idx}", use_container_width=True):
                db.delete_email(email['id'])
                st.success("✅ Gelöscht!")
                st.rerun()

        st.markdown("---")


def render_inbox_tab():
    """
    Radikale Vereinfachung: Nur DB lesen/schreiben, KEINE API-Calls!
    """
    start_time = time.time()
    print(f"[DEBUG] render_inbox_tab START @ {start_time}")

    if 'email_chat_active' in st.session_state and st.session_state.email_chat_active:
        from render_email_chat import render_email_chat
        render_email_chat()
        return

    st.markdown("## 📬 Posteingang")
    st.caption("💡 **Neue asynchrone Architektur** - UI reagiert sofort, Worker verarbeitet im Hintergrund")
    st.divider()

    from database.email_db import EmailDB
    db_start = time.time()
    db = EmailDB()
    print(f"[DEBUG] EmailDB init took {(time.time()-db_start)*1000:.2f}ms")

    stats = db.get_stats()
    total_unread = stats.get('unread', 0)
    processing = stats.get('processing', 0)
    done = stats.get('done', 0)
    errors = stats.get('error', 0)

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

    emails = db.get_unread_emails(limit=50)

    if not emails:
        st.info("✅ Keine ungelesenen E-Mails!")
        st.markdown("💡 Der Background Worker analysiert neue E-Mails automatisch.")
        return

    st.markdown(f"**{len(emails)} ungelesene E-Mails**")
    st.divider()

    cards_start = time.time()
    for idx, email in enumerate(emails):
        render_simple_email_card(email, idx, db)
    print(f"[DEBUG] Rendering cards took {(time.time()-cards_start)*1000:.2f}ms")
    print(f"[DEBUG] render_inbox_tab TOTAL took {(time.time()-start_time)*1000:.2f}ms")
