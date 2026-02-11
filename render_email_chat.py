"""
Email-Chat-Funktion - Für detaillierte Email-Bearbeitung
"""

import streamlit as st
from typing import Dict, Any


def render_email_chat():
    """
    Rendert Chat-Interface für Email-Bearbeitung

    Zeigt:
    - Vollständige Email
    - Anhänge
    - LLM-Chat für Analyse/Entwürfe
    - Aktions-Buttons
    """
    email = st.session_state.email_chat_data

    if not email:
        st.error("Keine Email-Daten gefunden")
        return

    # Header
    st.markdown("## 💬 Email bearbeiten")

    # Zurück-Button
    col1, col2 = st.columns([1, 5])
    with col1:
        if st.button("← Zurück", use_container_width=True):
            st.session_state.email_chat_active = False
            st.session_state.email_chat_data = None
            st.session_state.email_chat_history = []
            st.rerun()

    st.divider()

    # Email-Details in 2 Spalten
    col_left, col_right = st.columns([1, 1])

    with col_left:
        render_email_details(email)

    with col_right:
        render_email_chat_interface(email)


def render_email_details(email: Dict[str, Any]):
    """Zeigt Email-Details"""
    st.subheader("📧 Email-Details")

    # Meta-Informationen
    st.markdown(f"**Betreff:** {email.get('subject', 'Kein Betreff')}")
    st.markdown(f"**Von:** {email.get('sender_name', 'Unbekannt')} `<{email.get('sender_email', '')}>`")
    st.markdown(f"**Datum:** {email.get('received_dt', '')[:16]}")
    st.markdown(f"**Kategorie:** {email.get('category', 'Sonstiges')}")
    st.markdown(f"**Priorität:** {'🔴' * email.get('priority', 3)} ({email.get('priority', 3)}/5)")

    st.divider()

    # KI-Analyse
    with st.expander("🤖 KI-Analyse", expanded=True):
        st.markdown(f"**Zusammenfassung:**")
        st.info(email.get('summary', 'Keine Zusammenfassung'))

        action_items = email.get('action_items', [])
        if action_items:
            st.markdown("**Handlungspunkte:**")
            for item in action_items:
                st.markdown(f"- {item}")

        if email.get('deadline'):
            st.markdown(f"**Deadline:** {email.get('deadline')}")

    st.divider()

    # Volltext
    with st.expander("📄 Volltext anzeigen", expanded=False):
        # Fallback-Kette für body_full
        body_full = email.get('body_full')
        if not body_full:
            body_full = email.get('body_preview')
        if not body_full:
            body_full = 'Kein Text verfügbar'

        # Warnung wenn nur Preview
        if body_full == email.get('body_preview') and body_full != 'Kein Text verfügbar':
            st.warning("⚠️ Volltext noch nicht verfügbar. Zeige Preview. (Email wurde vor Migration erstellt)")

        # Toggle zwischen Text und HTML
        view_mode = st.radio(
            "Ansicht",
            ["📝 Sauberer Text", "🌐 HTML (Original)", "💻 HTML-Code"],
            horizontal=True,
            label_visibility="collapsed",
            key="email_view_mode"
        )

        if body_full == 'Kein Text verfügbar':
            st.info("📭 Kein Email-Text verfügbar")
        elif view_mode == "🌐 HTML (Original)":
            # Zeige HTML gerendert
            st.markdown(body_full, unsafe_allow_html=True)

        elif view_mode == "💻 HTML-Code":
            # Zeige rohen HTML-Code
            st.code(body_full, language="html")

        else:  # Sauberer Text
            # Konvertiere HTML zu lesbarem Text
            try:
                from bs4 import BeautifulSoup
                import html

                # Parse HTML
                soup = BeautifulSoup(body_full, 'html.parser')

                # Entferne Script und Style Tags
                for script in soup(["script", "style"]):
                    script.decompose()

                # Hole Text
                text = soup.get_text()

                # Decode HTML entities (&nbsp; etc.)
                text = html.unescape(text)

                # Bereinige Whitespace
                lines = (line.strip() for line in text.splitlines())
                chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
                text = '\n'.join(chunk for chunk in chunks if chunk)

                body_clean = text

            except ImportError:
                # Fallback ohne BeautifulSoup
                import re
                import html as html_module

                body_clean = html_module.unescape(body_full)
                body_clean = re.sub(r'<[^>]+>', '', body_clean)
                body_clean = re.sub(r'\n\n+', '\n\n', body_clean)
                body_clean = re.sub(r'&[a-zA-Z]+;', ' ', body_clean)

            st.text_area(
                "Email-Inhalt",
                body_clean,
                height=400,
                disabled=True,
                label_visibility="collapsed"
            )

    # Anhänge
    attachments = email.get('attachments', [])
    if isinstance(attachments, str):
        import json
        try:
            attachments = json.loads(attachments)
        except:
            attachments = []

    if email.get('has_attachments') or attachments:
        with st.expander(f"📎 Anhänge", expanded=False):
            if not attachments:
                # Versuche Anhänge on-demand zu laden
                if st.button("🔄 Anhänge laden", key="load_attachments", use_container_width=True):
                    with st.spinner("Lade Anhänge..."):
                        try:
                            # Importiere Tool und lade Anhänge
                            from tools.outlook_graph_tool import OutlookGraphTool

                            outlook = OutlookGraphTool()
                            result = outlook.get_email_attachments(email['id'])

                            if result.get('success'):
                                attachments = result.get('attachments', [])

                                # Speichere in DB
                                from database.email_db import EmailDB
                                import json

                                db = EmailDB()
                                with db._get_connection() as conn:
                                    conn.execute(
                                        "UPDATE emails SET attachments_json = ? WHERE id = ?",
                                        (json.dumps(attachments), email['id'])
                                    )
                                    conn.commit()

                                st.success(f"✅ {len(attachments)} Anhänge geladen!")
                                st.rerun()
                            else:
                                st.error(f"❌ Fehler: {result.get('error', 'Unbekannt')}")
                        except Exception as e:
                            st.error(f"❌ Fehler beim Laden: {e}")
                            import traceback
                            st.code(traceback.format_exc())

                st.caption("⚠️ Anhänge vorhanden, aber Metadaten noch nicht geladen")
            else:
                st.caption(f"**{len(attachments)} Anhang(e)**")
                st.divider()

                for att in attachments:
                    col1, col2, col3 = st.columns([3, 1, 1])
                    with col1:
                        st.markdown(f"**{att.get('name', 'Unbekannt')}**")
                        st.caption(att.get('contentType', 'Unknown type'))
                    with col2:
                        size_kb = att.get('size', 0) / 1024
                        if size_kb > 1024:
                            st.caption(f"{size_kb/1024:.1f} MB")
                        else:
                            st.caption(f"{size_kb:.1f} KB")
                    with col3:
                        if st.button("📥", key=f"dl_{att.get('id')}", help="Herunterladen"):
                            st.info("💡 Download-Funktion kommt bald...")


def render_email_chat_interface(email: Dict[str, Any]):
    """Rendert Chat-Interface mit LLM"""
    st.subheader("💬 Chat & Aktionen")

    # Chat-History anzeigen
    chat_history = st.session_state.get('email_chat_history', [])

    if chat_history:
        for msg in chat_history:
            role = msg.get('role', 'user')
            content = msg.get('content', '')

            if role == 'user':
                st.markdown(f"**Du:** {content}")
            else:
                st.markdown(f"**🤖 Assistent:** {content}")
            st.markdown("---")

    # Chat-Input
    st.markdown("### Frage zum Email stellen")
    user_question = st.text_area(
        "Was möchtest du über diese Email wissen?",
        placeholder="z.B. 'Erstelle einen Antwort-Entwurf' oder 'Fasse die wichtigsten Punkte zusammen'",
        height=100,
        key="email_chat_input"
    )

    if st.button("💬 Frage stellen", use_container_width=True):
        if not user_question:
            st.warning("Bitte gib eine Frage ein")
        else:
            with st.spinner("🤔 Denke nach..."):
                # Baue Kontext für LLM
                body_text = email.get('body_full') or email.get('body_preview') or 'Nicht verfügbar'

                email_context = f"""
Email-Details:
- Betreff: {email.get('subject', 'N/A')}
- Von: {email.get('sender_name', 'N/A')} <{email.get('sender_email', 'N/A')}>
- Datum: {email.get('received_dt', 'N/A')}
- Kategorie: {email.get('category', 'N/A')}
- Priorität: {email.get('priority', 'N/A')}/5

KI-Analyse:
{email.get('summary', 'Keine Zusammenfassung')}

Email-Inhalt:
{body_text[:2000]}
"""

                # LLM aufrufen
                try:
                    from langchain_anthropic import ChatAnthropic
                    from langchain_core.messages import SystemMessage, HumanMessage
                    import os

                    llm = ChatAnthropic(
                        model=os.getenv("RESEARCH_MODEL", "claude-sonnet-4-5"),
                        temperature=0,
                        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY")
                    )

                    system_prompt = """Du bist ein hilfreicher Email-Assistent.
Du hilfst bei der Analyse und Bearbeitung von Emails.

Aufgaben:
- Antwort-Entwürfe erstellen
- Wichtige Punkte zusammenfassen
- Termine/Deadlines extrahieren
- Handlungsempfehlungen geben

Antworte präzise und professionell auf Deutsch."""

                    user_prompt = f"{email_context}\n\nFrage: {user_question}"

                    response = llm.invoke([
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=user_prompt)
                    ])

                    answer = response.content

                    # Speichere in History
                    if 'email_chat_history' not in st.session_state:
                        st.session_state.email_chat_history = []

                    st.session_state.email_chat_history.append({
                        'role': 'user',
                        'content': user_question
                    })
                    st.session_state.email_chat_history.append({
                        'role': 'assistant',
                        'content': answer
                    })

                    st.rerun()

                except Exception as e:
                    st.error(f"Fehler beim LLM-Aufruf: {e}")

    st.divider()

    # Schnell-Aktionen
    st.markdown("### 🚀 Schnell-Aktionen")

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("📝 Antwort-Entwurf erstellen", use_container_width=True):
            # Führe Aktion direkt aus
            question = "Erstelle einen professionellen Antwort-Entwurf auf diese Email. Halte ihn kurz und präzise."

            with st.spinner("✍️ Erstelle Entwurf..."):
                # Baue Kontext für LLM
                body_text = email.get('body_full') or email.get('body_preview') or 'Nicht verfügbar'

                email_context = f"""
Email-Details:
- Betreff: {email.get('subject', 'N/A')}
- Von: {email.get('sender_name', 'N/A')} <{email.get('sender_email', 'N/A')}>
- Datum: {email.get('received_dt', 'N/A')}
- Kategorie: {email.get('category', 'N/A')}
- Priorität: {email.get('priority', 'N/A')}/5

KI-Analyse:
{email.get('summary', 'Keine Zusammenfassung')}

Email-Inhalt:
{body_text[:2000]}
"""

                # LLM aufrufen
                try:
                    from langchain_anthropic import ChatAnthropic
                    from langchain_core.messages import SystemMessage, HumanMessage
                    import os

                    llm = ChatAnthropic(
                        model=os.getenv("RESEARCH_MODEL", "claude-sonnet-4-5"),
                        temperature=0,
                        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY")
                    )

                    system_prompt = """Du bist ein hilfreicher Email-Assistent.
Du hilfst bei der Analyse und Bearbeitung von Emails.

Aufgaben:
- Antwort-Entwürfe erstellen
- Wichtige Punkte zusammenfassen
- Termine/Deadlines extrahieren
- Handlungsempfehlungen geben

Antworte präzise und professionell auf Deutsch."""

                    user_prompt = f"{email_context}\n\nFrage: {question}"

                    response = llm.invoke([
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=user_prompt)
                    ])

                    answer = response.content

                    # Speichere in History
                    if 'email_chat_history' not in st.session_state:
                        st.session_state.email_chat_history = []

                    st.session_state.email_chat_history.append({
                        'role': 'user',
                        'content': question
                    })
                    st.session_state.email_chat_history.append({
                        'role': 'assistant',
                        'content': answer
                    })

                    # Markiere letzten Entwurf für Editor
                    st.session_state.last_draft = answer
                    st.session_state.draft_email = email

                    st.rerun()

                except Exception as e:
                    st.error(f"Fehler beim Erstellen des Entwurfs: {e}")

    with col2:
        if st.button("📋 Zusammenfassung", use_container_width=True):
            # Zeige existierende Zusammenfassung
            st.info(email.get('summary', 'Keine Zusammenfassung'))

    with col3:
        if st.button("📤 Weiterleiten", use_container_width=True):
            st.session_state.show_forward_interface = True
            st.rerun()

    # Email-Editor anzeigen wenn Entwurf vorhanden
    if st.session_state.get('last_draft'):
        st.divider()
        st.markdown("### ✏️ Email-Entwurf bearbeiten")

        # Button zum Öffnen/Schließen des Editors
        if 'show_draft_editor' not in st.session_state:
            st.session_state.show_draft_editor = False

        if st.button("✉️ Entwurf bearbeiten & versenden", use_container_width=True, type="primary"):
            st.session_state.show_draft_editor = not st.session_state.show_draft_editor
            st.rerun()

        # Zeige Editor wenn aktiviert
        if st.session_state.show_draft_editor:
            render_email_draft_editor(email, st.session_state.last_draft)

    # Weiterleitungs-Interface
    if st.session_state.get('show_forward_interface'):
        st.divider()
        st.markdown("### 📤 Email weiterleiten")
        render_forward_interface(email)

    st.divider()

    # Finale Aktionen
    st.markdown("### ✅ Email-Aktionen")

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("📤 An Asana", use_container_width=True, type="primary"):
            from database.email_db import EmailDB
            db = EmailDB()
            db.set_instruction(email['id'], 'asana', {'project_gid': 'default'})
            db.hide_email(email['id'])
            st.success("✅ Wird an Asana gesendet!")
            st.session_state.email_chat_active = False
            st.rerun()

    with col2:
        if st.button("🗄️ Archivieren", use_container_width=True):
            from database.email_db import EmailDB
            db = EmailDB()
            db.set_instruction(email['id'], 'archive')
            db.hide_email(email['id'])
            st.success("✅ Wird archiviert!")
            st.session_state.email_chat_active = False
            st.rerun()

    with col3:
        if st.button("🗑️ Löschen", use_container_width=True):
            from database.email_db import EmailDB
            db = EmailDB()
            db.delete_email(email['id'])
            st.success("✅ Gelöscht!")
            st.session_state.email_chat_active = False
            st.rerun()


def render_email_draft_editor(original_email: Dict[str, Any], draft_content: str):
    """
    Rendert einen Email-Editor zum Bearbeiten und Versenden von Entwürfen

    Args:
        original_email: Die Original-Email auf die geantwortet wird
        draft_content: Der generierte Email-Entwurf
    """
    st.markdown("---")

    # Vorausgefüllte Werte
    default_to = original_email.get('sender_email', '')
    default_subject = f"Re: {original_email.get('subject', '')}"

    # Editor-Formular
    with st.form(key="email_draft_form", clear_on_submit=False):
        st.markdown("#### 📤 Email versenden")

        # Empfänger
        to_address = st.text_input(
            "An:",
            value=default_to,
            placeholder="empfaenger@example.com",
            help="Email-Adresse des Empfängers"
        )

        # CC (optional)
        cc_addresses = st.text_input(
            "CC: (optional)",
            value="",
            placeholder="cc1@example.com, cc2@example.com",
            help="Mehrere Adressen mit Komma trennen"
        )

        # Betreff
        subject = st.text_input(
            "Betreff:",
            value=default_subject,
            placeholder="Email-Betreff"
        )

        # Email-Body
        body = st.text_area(
            "Nachricht:",
            value=draft_content,
            height=300,
            help="Bearbeite den Email-Text nach Bedarf"
        )

        # Buttons
        col1, col2, col3 = st.columns([2, 1, 1])

        with col1:
            send_button = st.form_submit_button(
                "📤 Email senden",
                use_container_width=True,
                type="primary"
            )

        with col2:
            save_draft_button = st.form_submit_button(
                "💾 Als Entwurf",
                use_container_width=True
            )

        with col3:
            cancel_button = st.form_submit_button(
                "❌ Abbrechen",
                use_container_width=True
            )

    # Formular-Aktionen
    if send_button:
        # Validierung
        if not to_address or not subject or not body:
            st.error("❌ Bitte fülle alle Pflichtfelder aus (An, Betreff, Nachricht)")
        else:
            # Email versenden
            with st.spinner("📤 Sende Email..."):
                result = send_email_via_outlook(
                    to_address=to_address,
                    cc_addresses=cc_addresses,
                    subject=subject,
                    body=body
                )

                if result.get('success'):
                    st.success("✅ Email erfolgreich gesendet!")

                    # Cleanup
                    st.session_state.show_draft_editor = False
                    st.session_state.last_draft = None

                    # Original-Email archivieren
                    from database.email_db import EmailDB
                    db = EmailDB()
                    db.set_instruction(original_email['id'], 'archive')
                    db.hide_email(original_email['id'])

                    st.session_state.email_chat_active = False
                    st.rerun()
                else:
                    st.error(f"❌ Fehler beim Senden: {result.get('error', 'Unbekannter Fehler')}")

    elif save_draft_button:
        # Als Entwurf in Outlook speichern
        with st.spinner("💾 Speichere Entwurf..."):
            result = save_email_draft_to_outlook(
                to_address=to_address,
                cc_addresses=cc_addresses,
                subject=subject,
                body=body
            )

            if result.get('success'):
                st.success("✅ Entwurf in Outlook gespeichert!")
                st.session_state.show_draft_editor = False
                st.rerun()
            else:
                st.error(f"❌ Fehler beim Speichern: {result.get('error', 'Unbekannter Fehler')}")

    elif cancel_button:
        # Editor schließen
        st.session_state.show_draft_editor = False
        st.rerun()


def send_email_via_outlook(to_address: str, cc_addresses: str, subject: str, body: str) -> Dict[str, Any]:
    """
    Sendet eine Email über Outlook Graph API

    Args:
        to_address: Empfänger-Email
        cc_addresses: CC-Empfänger (kommagetrennt)
        subject: Email-Betreff
        body: Email-Inhalt

    Returns:
        Dict mit 'success' und optional 'error'
    """
    try:
        from tools.outlook_graph_tool import OutlookGraphTool

        outlook = OutlookGraphTool()

        # Parse CC-Adressen
        cc_list = []
        if cc_addresses:
            cc_list = [addr.strip() for addr in cc_addresses.split(',') if addr.strip()]

        # Sende Email
        result = outlook.send_email(
            to_email=to_address,
            subject=subject,
            body=body,
            cc_emails=cc_list
        )

        return result

    except Exception as e:
        import traceback
        return {
            'success': False,
            'error': f"Exception: {str(e)}\n{traceback.format_exc()}"
        }


def save_email_draft_to_outlook(to_address: str, cc_addresses: str, subject: str, body: str) -> Dict[str, Any]:
    """
    Speichert einen Email-Entwurf in Outlook

    Args:
        to_address: Empfänger-Email
        cc_addresses: CC-Empfänger (kommagetrennt)
        subject: Email-Betreff
        body: Email-Inhalt

    Returns:
        Dict mit 'success' und optional 'error'
    """
    try:
        from tools.outlook_graph_tool import OutlookGraphTool

        outlook = OutlookGraphTool()

        # Parse CC-Adressen
        cc_list = []
        if cc_addresses:
            cc_list = [addr.strip() for addr in cc_addresses.split(',') if addr.strip()]

        # Speichere Entwurf
        result = outlook.create_email_draft(
            subject=subject,
            body=body,
            to_recipients=[to_address] if to_address else None,
            cc_recipients=cc_list if cc_list else None
        )

        return result

    except Exception as e:
        import traceback
        return {
            'success': False,
            'error': f"Exception: {str(e)}\n{traceback.format_exc()}"
        }


def render_forward_interface(email: Dict[str, Any]):
    """
    Rendert Interface zum Weiterleiten einer Email

    Args:
        email: Die weiterzuleitende Email
    """
    st.markdown("---")

    # Formular für Weiterleitung
    with st.form(key="forward_email_form", clear_on_submit=False):
        st.markdown("#### 📤 Email weiterleiten")

        # Empfänger
        to_addresses = st.text_input(
            "An:",
            value="",
            placeholder="empfaenger@example.com, empfaenger2@example.com",
            help="Eine oder mehrere Email-Adressen (mit Komma trennen)"
        )

        # Optionaler Kommentar
        comment = st.text_area(
            "Kommentar (optional):",
            value="",
            height=100,
            placeholder="Füge optional einen Kommentar zur Weiterleitung hinzu..."
        )

        # Buttons
        col1, col2 = st.columns([2, 1])

        with col1:
            forward_button = st.form_submit_button(
                "📤 Weiterleiten",
                use_container_width=True,
                type="primary"
            )

        with col2:
            cancel_button = st.form_submit_button(
                "❌ Abbrechen",
                use_container_width=True
            )

    # Formular-Aktionen
    if forward_button:
        # Validierung
        if not to_addresses:
            st.error("❌ Bitte gib mindestens einen Empfänger an")
        else:
            # Parse Empfänger
            recipients = [addr.strip() for addr in to_addresses.split(',') if addr.strip()]

            if not recipients:
                st.error("❌ Bitte gib gültige Email-Adressen an")
            else:
                # Email weiterleiten
                with st.spinner("📤 Leite Email weiter..."):
                    result = forward_email_via_outlook(
                        email_id=email['id'],
                        to_recipients=recipients,
                        comment=comment
                    )

                    if result.get('success'):
                        st.success(f"✅ Email erfolgreich weitergeleitet an {', '.join(recipients)}!")

                        # Cleanup
                        st.session_state.show_forward_interface = False
                        st.rerun()
                    else:
                        st.error(f"❌ Fehler beim Weiterleiten: {result.get('error', 'Unbekannter Fehler')}")

    elif cancel_button:
        # Interface schließen
        st.session_state.show_forward_interface = False
        st.rerun()


def forward_email_via_outlook(email_id: str, to_recipients: list, comment: str = "") -> Dict[str, Any]:
    """
    Leitet eine Email über Outlook Graph API weiter

    Args:
        email_id: ID der weiterzuleitenden Email
        to_recipients: Liste von Empfänger-Emails
        comment: Optionaler Kommentar

    Returns:
        Dict mit 'success' und optional 'error'
    """
    try:
        from tools.outlook_graph_tool import OutlookGraphTool

        outlook = OutlookGraphTool()

        # Leite Email weiter
        result = outlook.forward_email(
            email_id=email_id,
            to_recipients=to_recipients,
            comment=comment
        )

        return result

    except Exception as e:
        import traceback
        return {
            'success': False,
            'error': f"Exception: {str(e)}\n{traceback.format_exc()}"
        }
