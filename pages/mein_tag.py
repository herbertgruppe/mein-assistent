"""
Mein Tag Dashboard-Tab: Kalender, Asana-Aufgaben, Dokumenten-Suche, Meeting-Vorbereitung.
"""
import os
import time
import streamlit as st
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from utils.api_cache import cached_get_asana_projects, cached_get_asana_tasks
from utils.state import _get_user_ctx
from utils.protocol import (
    load_agenda_templates, save_agenda_templates, create_agenda_from_template,
    generate_agenda_with_asana_context, sanitize_filename, convert_markdown_to_pdf
)


def convert_to_berlin_time(dt):
    """Konvertiert ein datetime-Objekt nach Europe/Berlin Zeitzone"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo('UTC'))
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


def render_connection_status():
    """Zeigt den Verbindungsstatus für Outlook und Asana an"""
    col1, col2 = st.columns(2)

    with col1:
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
                    projects = cached_get_asana_projects(asana_agent)
                    project_count = len(projects) if projects else 0
                    st.caption(f"{project_count} Projekt(e) verfügbar")
                    asana_status.update(label="✅ **Asana**", state="complete")
                except Exception as e:
                    st.caption(f"⚠️ Fehler: {str(e)[:50]}")
                    asana_status.update(label="✅ **Asana**", state="running")


def render_document_search_section():
    """Rendert die Dokumenten-Suchleiste im Dashboard"""
    st.subheader("🔍 Dokumenten-Suche")

    document_tool = st.session_state.orchestrator.document_tool

    doc_count = document_tool.count_documents()
    st.caption(f"Durchsuchen Sie {doc_count} Dokument(e) in input_docs/")

    search_query = st.text_input(
        "🔍 Suchbegriff eingeben (Enter drücken)",
        placeholder="z.B. 'KHS', 'Dr. Herbert', 'Gesellschafterliste'...",
        key="dashboard_document_search",
        label_visibility="collapsed"
    )

    if search_query:
        if len(search_query.strip()) < 2:
            st.caption("💡 Geben Sie mindestens 2 Zeichen ein um zu suchen")
        else:
            with st.spinner(f"Durchsuche {doc_count} Dokumente..."):
                try:
                    results = document_tool.search_in_documents(search_query)

                    if results:
                        st.success(f"✅ {len(results)} Ergebnis(se) gefunden")

                        for i, result in enumerate(results, 1):
                            doc_name = result.get('document', 'Unbekannt')
                            match_type = result.get('match_type', 'content')
                            snippet = result.get('snippet', '')
                            match_count = result.get('match_count', 1)

                            icon = "📄" if match_type == "filename" else "📝"

                            with st.expander(f"{icon} {doc_name} ({match_count} Treffer)", expanded=(i == 1)):
                                if match_type == "filename":
                                    st.info("📄 **Treffer im Dateinamen**")
                                else:
                                    st.info(f"📝 **Treffer im Inhalt** ({match_count}x)")

                                st.markdown("**Textausschnitt:**")
                                highlighted_snippet = snippet
                                if search_query.lower() in snippet.lower():
                                    import re
                                    pattern = re.compile(re.escape(search_query), re.IGNORECASE)
                                    highlighted_snippet = pattern.sub(
                                        lambda m: f"**{m.group(0)}**",
                                        snippet
                                    )

                                st.markdown(highlighted_snippet)

                                if result.get('all_matches') and len(result['all_matches']) > 1:
                                    st.markdown("**Weitere Fundstellen:**")
                                    for j, match in enumerate(result['all_matches'][1:], start=2):
                                        match_snippet = match.get('snippet', '')[:150]
                                        st.caption(f"{j}. ...{match_snippet}...")

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


def render_task_card(task: dict, asana_agent):
    """Rendert eine einzelne Aufgaben-Karte mit optimiertem Layout für lange Texte"""
    name = task.get('name', 'Unbenannt')
    due_on = task.get('due_on', 'Kein Datum')
    notes = task.get('notes', '')
    projects = task.get('projects', [])
    task_gid = task.get('gid')

    with st.expander(f"📌 {name}", expanded=False):
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

        if projects:
            st.caption(f"📁 Projekt: {', '.join(projects)}")

        st.markdown("---")

        if notes:
            st.markdown("### 📝 Beschreibung")
            if len(notes) > 500:
                st.text_area(
                    "Aufgabenbeschreibung",
                    notes,
                    height=200,
                    key=f"notes_{task_gid}",
                    label_visibility="collapsed",
                    disabled=True
                )
            else:
                st.markdown(notes)
        else:
            st.caption("_Keine Beschreibung vorhanden_")

        st.markdown("---")

        st.markdown("### 💬 Kommentare")
        if task_gid:
            try:
                comments = asana_agent.get_task_stories(task_gid, limit=5)
                if comments:
                    for i, comment in enumerate(comments):
                        author = comment.get('author', 'Unbekannt')
                        text = comment.get('text', '')
                        created_at = comment.get('created_at', '')

                        time_str = ""
                        if created_at:
                            try:
                                dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                                time_str = dt.strftime('%d.%m.%Y %H:%M')
                            except:
                                time_str = created_at[:10]

                        st.markdown(
                            f"""
                            <div style="
                                background-color: #f0f3f8;
                                padding: 0.75rem 1rem;
                                border-radius: 0.5rem;
                                margin-bottom: 0.75rem;
                                border-left: 3px solid #4064a0;
                            ">
                                <strong>{author}</strong> <em style="color: #6b7280; font-size: 0.875em;">{time_str}</em>
                                <p style="margin-top: 0.375rem; margin-bottom: 0;">{text}</p>
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


def render_asana_chat_assistant(asana_agent):
    """Rendert einen Chat-Assistenten für Asana-Analysen und Abfragen"""
    st.caption("Stellen Sie Fragen zu Ihren Asana-Projekten und Aufgaben.")

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

    if 'asana_chat_messages' not in st.session_state:
        st.session_state['asana_chat_messages'] = []

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

    if selected_project_gid:
        st.session_state['asana_chat_selected_project'] = selected_project_gid

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

    user_input = st.chat_input("Ihre Frage zu Asana...", key="asana_chat_input")

    if user_input:
        st.session_state['asana_chat_messages'].append({
            'role': 'user',
            'content': user_input
        })

        with st.status("📊 Analysiere Asana-Daten...", expanded=True) as status:
            try:
                from langchain_core.tools import tool
                from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage

                @tool
                def list_asana_projects() -> str:
                    """Liste alle Asana-Projekte im Workspace auf."""
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
                    """Ruft alle Aufgaben eines bestimmten Projekts ab."""
                    try:
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
                            status_icon = "✅" if completed else "⭕"
                            result += f"{status_icon} {name} (Fällig: {due_on})\n"
                        return result
                    except Exception as e:
                        return f"Fehler beim Abrufen der Aufgaben: {str(e)}"

                @tool
                def get_my_tasks(days: int = 30) -> str:
                    """Ruft die eigenen Aufgaben ab."""
                    try:
                        tasks = asana_agent.get_my_tasks(limit=100)
                        if not tasks:
                            return "Keine eigenen Aufgaben gefunden."
                        overdue = []
                        today_list = []
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
                                        today_list.append(task)
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
                        if today_list:
                            result += f"🔴 **Heute fällig ({len(today_list)}):**\n"
                            for task in today_list:
                                result += f"- {task['name']}\n"
                            result += "\n"
                        if upcoming:
                            result += f"📅 **Kommende Aufgaben ({len(upcoming)}):**\n"
                            for task in upcoming[:10]:
                                result += f"- {task['name']} (Fällig: {task['due_on']})\n"
                        return result
                    except Exception as e:
                        return f"Fehler beim Abrufen der Aufgaben: {str(e)}"

                orch = st.session_state.orchestrator
                tools = [list_asana_projects, get_project_tasks, get_my_tasks]
                llm_with_tools = orch.research_agent.llm.bind_tools(tools)

                lc_messages = []
                for msg in st.session_state['asana_chat_messages']:
                    if msg['role'] == 'system':
                        lc_messages.append(SystemMessage(content=msg['content']))
                    elif msg['role'] == 'user':
                        lc_messages.append(HumanMessage(content=msg['content']))
                    elif msg['role'] == 'assistant':
                        if 'tool_calls' in msg and msg['tool_calls']:
                            lc_messages.append(AIMessage(content=msg['content'] if msg['content'] else "", tool_calls=msg['tool_calls']))
                        else:
                            lc_messages.append(AIMessage(content=msg['content']))
                    elif msg['role'] == 'tool':
                        lc_messages.append(ToolMessage(content=msg['content'], tool_call_id=msg.get('tool_call_id', '')))

                st.write("📤 Sende Anfrage an LLM...")
                response = llm_with_tools.invoke(lc_messages)

                if hasattr(response, 'tool_calls') and response.tool_calls:
                    st.session_state['asana_chat_messages'].append({
                        'role': 'assistant',
                        'content': response.content if response.content else "",
                        'tool_calls': response.tool_calls
                    })
                    tool_messages = []
                    for tool_call in response.tool_calls:
                        tool_name = tool_call['name']
                        tool_args = tool_call['args']
                        tool_func = next((t for t in tools if t.name == tool_name), None)
                        if tool_func:
                            tool_result = tool_func.invoke(tool_args)
                            st.session_state['asana_chat_messages'].append({
                                'role': 'tool',
                                'content': tool_result,
                                'tool_call_id': tool_call.get('id')
                            })
                            tool_messages.append(ToolMessage(content=tool_result, tool_call_id=tool_call.get('id')))

                    lc_messages.append(response)
                    lc_messages.extend(tool_messages)
                    follow_up_response = llm_with_tools.invoke(lc_messages)
                    st.session_state['asana_chat_messages'].append({
                        'role': 'assistant',
                        'content': follow_up_response.content
                    })
                else:
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

    if st.button("🗑️ Chat zurücksetzen", key="reset_asana_chat"):
        st.session_state['asana_chat_messages'] = []
        st.rerun()


def render_asana_tasks_section():
    """Rendert die Asana-Aufgaben Sektion (rechts) mit Live-Daten"""
    st.subheader("✅ Ihre Prioritäten in Asana")

    asana_agent = st.session_state.orchestrator.asana_agent

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

    if 'show_asana_tasks_dashboard' not in st.session_state:
        st.session_state.show_asana_tasks_dashboard = False

    if st.button("🔄 Asana-Aufgaben laden", key="load_asana_dashboard", use_container_width=True):
        st.session_state.show_asana_tasks_dashboard = True
        cached_get_asana_projects.clear()
        cached_get_asana_tasks.clear()
        st.rerun()

    if not st.session_state.show_asana_tasks_dashboard:
        st.info("💡 Klicke auf 'Asana-Aufgaben laden' um deine Tasks zu sehen")
        return

    st.markdown("**📁 Projekt auswählen:**")

    try:
        projects = cached_get_asana_projects(asana_agent)

        if not projects:
            st.error("❌ Keine Projekte gefunden")
            return

        project_names = [p['name'] for p in projects]
        project_dict = {p['name']: p['gid'] for p in projects}

        project_names.insert(0, "📋 Alle Projekte")

        selected_project_name = st.selectbox(
            "Projekt",
            project_names,
            key="dashboard_project_select",
            label_visibility="collapsed"
        )

        st.markdown(f"**Aktives Filter:** {selected_project_name}")
        st.markdown("---")

        if selected_project_name == "📋 Alle Projekte":
            tasks = cached_get_asana_tasks(asana_agent, days=7)
        else:
            selected_project_gid = project_dict[selected_project_name]
            tasks = cached_get_asana_tasks(asana_agent, project_gid=selected_project_gid, days=7)

        if not tasks:
            st.info("📭 Keine anstehenden Aufgaben gefunden")
            st.caption("Alle Aufgaben sind erledigt oder es gibt keine Aufgaben im gewählten Projekt.")
            return

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

        if today_tasks:
            st.markdown("### 🔴 Heute fällig")
            st.caption(f"{len(today_tasks)} Aufgabe(n)")
            for task in today_tasks:
                render_task_card(task, asana_agent)

        if upcoming_tasks:
            st.markdown("### 📅 Diese Woche")
            st.caption(f"{len(upcoming_tasks)} Aufgabe(n)")
            for task in upcoming_tasks[:10]:
                render_task_card(task, asana_agent)

        st.markdown("---")
        with st.expander("💬 Asana-Assistent (Fragen & Analysen)", expanded=False):
            render_asana_chat_assistant(asana_agent)

    except Exception as e:
        print(f"[Dashboard] ❌ FEHLER beim Laden der Asana-Aufgaben: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        st.error(f"❌ **Fehler beim Laden der Aufgaben**")
        st.caption(f"**Fehlertyp:** {type(e).__name__}")
        st.caption(f"**Details:** {str(e)}")


def render_calendar_section():
    """Rendert die Kalender-Sektion mit Datumsnavigation und Meeting-Vorbereitung"""
    if 'dashboard_date' not in st.session_state:
        st.session_state['dashboard_date'] = datetime.now().date()

    col1, col2, col3 = st.columns([1, 4, 1])

    with col1:
        if st.button("◀", key="prev_day", help="Vorheriger Tag"):
            st.session_state['dashboard_date'] -= timedelta(days=1)
            st.rerun()

    with col2:
        selected_date = st.session_state['dashboard_date']
        weekday_names = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']
        weekday = weekday_names[selected_date.weekday()]

        is_today = selected_date == datetime.now().date()
        if is_today:
            st.markdown(f"### 📅 Heute - {weekday}, {selected_date.strftime('%d.%m.%Y')}")
        else:
            st.markdown(f"### 📅 {weekday}, {selected_date.strftime('%d.%m.%Y')}")

    with col3:
        if st.button("▶", key="next_day", help="Nächster Tag"):
            st.session_state['dashboard_date'] += timedelta(days=1)
            st.rerun()

    if not is_today:
        if st.button("🏠 Zurück zu Heute"):
            st.session_state['dashboard_date'] = datetime.now().date()
            st.rerun()

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
        outlook_tool = st.session_state.orchestrator.outlook_tool

        if not outlook_tool.is_authenticated():
            st.warning("⚠️ **Microsoft Kalender verbunden, aber nicht authentifiziert**")
            st.markdown("""
Bitte authentifizieren Sie sich in der Sidebar unter "Microsoft Graph API",
um Ihre echten Outlook-Termine zu sehen.
            """)

        else:
            st.success("✅ Microsoft Kalender authentifiziert")

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
                    start_of_day = datetime.combine(selected_date, datetime.min.time())
                    end_of_day = datetime.combine(selected_date, datetime.max.time())
                    events = outlook_tool.get_events_for_date_range(start_of_day, end_of_day)

                    if events:
                        st.info(f"**{len(events)} Termin(e) am {selected_date.strftime('%d.%m.%Y')}**")

                        for idx, event in enumerate(events):
                            try:
                                start_dt = event.get('start')
                                end_dt = event.get('end')
                                if isinstance(start_dt, str):
                                    start_dt = datetime.fromisoformat(start_dt.replace('Z', '+00:00'))
                                if isinstance(end_dt, str):
                                    end_dt = datetime.fromisoformat(end_dt.replace('Z', '+00:00'))
                                start_dt = convert_to_berlin_time(start_dt)
                                end_dt = convert_to_berlin_time(end_dt)
                                time_str = f"{start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}"
                            except:
                                time_str = "Zeit nicht verfügbar"

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

    col1, col2 = st.columns([5, 1])
    with col1:
        st.header("📝 Meeting-Vorbereitung")
    with col2:
        if st.button("← Zurück"):
            for key in ['preparing_event', 'preparing_event_idx', 'preparation_messages']:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()

    st.markdown("---")

    try:
        start_dt = event.get('start')
        end_dt = event.get('end')
        if isinstance(start_dt, str):
            start_dt = datetime.fromisoformat(start_dt.replace('Z', '+00:00'))
        if isinstance(end_dt, str):
            end_dt = datetime.fromisoformat(end_dt.replace('Z', '+00:00'))
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

    if event.get('attachments'):
        with st.expander(f"📎 Anhänge ({len(event['attachments'])})"):
            for att in event['attachments']:
                st.text(f"• {att.get('name', 'Unbekannt')}")

    st.markdown("---")

    st.subheader("💬 Assistent zur Meeting-Vorbereitung")
    st.caption("Der Assistent kann Dokumente erstellen und direkt an den Termin anhängen. Sagen Sie z.B. 'Erstelle eine Agenda' oder 'Hänge diese Recherche als Dokument an'.")

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
                event_title = event.get('title', '').lower().strip()

                for p in projects:
                    project_name_lower = p['name'].lower()
                    if event_title == project_name_lower:
                        auto_matched_project = p['name']
                        break
                    elif event_title in project_name_lower or project_name_lower in event_title:
                        if len(event_title) >= 5 or len(project_name_lower) >= 5:
                            auto_matched_project = p['name']
                            break

                project_options = ["[Kein Projekt]"] + [p['name'] for p in projects]

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
                    help="Automatisch vorausgewählt basierend auf Terminnamen."
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
    # AGENDA-ERSTELLUNG WORKFLOW
    # ========================================================================
    st.subheader("📝 Agenda erstellen")

    if 'agenda_workflow_step' not in st.session_state:
        st.session_state['agenda_workflow_step'] = 1
    if 'agenda_sections_loaded' not in st.session_state:
        st.session_state['agenda_sections_loaded'] = False
    if 'agenda_generated_content' not in st.session_state:
        st.session_state['agenda_generated_content'] = ""
    if 'agenda_preview_data' not in st.session_state:
        st.session_state['agenda_preview_data'] = {}

    # Template-Verwaltung
    with st.expander("📚 Agenda-Vorlagen verwalten", expanded=False):
        st.caption("Erstellen und verwalten Sie wiederverwendbare Agenda-Vorlagen für verschiedene Meeting-Typen.")

        templates = load_agenda_templates()

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
                st.info("📭 Noch keine Vorlagen vorhanden.")

        with tab_create:
            editing_mode = 'editing_template_idx' in st.session_state
            if editing_mode:
                st.info("✏️ **Bearbeitungsmodus** - Ändern Sie die Vorlage unten")
                edit_template = st.session_state.get('editing_template', {})
            else:
                st.markdown("**Erstellen Sie eine neue Agenda-Vorlage:**")
                edit_template = {}

            if 'template_sections' not in st.session_state:
                st.session_state['template_sections'] = edit_template.get('sections', [{'title': '', 'content': ''}])

            col_add1, col_add2 = st.columns([3, 1])
            with col_add1:
                if st.button("➕ Weiteren Abschnitt hinzufügen", use_container_width=True, key="add_section_btn"):
                    for idx in range(len(st.session_state['template_sections'])):
                        if f'section_title_{idx}' in st.session_state:
                            st.session_state['template_sections'][idx]['title'] = st.session_state[f'section_title_{idx}']
                        if f'section_content_{idx}' in st.session_state:
                            st.session_state['template_sections'][idx]['content'] = st.session_state[f'section_content_{idx}']
                    st.session_state['template_sections'].append({'title': '', 'content': ''})
                    st.rerun()
            with col_add2:
                st.caption(f"📝 {len(st.session_state['template_sections'])} Abschnitt(e)")

            with st.form(key="template_form", clear_on_submit=False):
                template_name = st.text_input("Vorlagen-Name", value=edit_template.get('name', ''), placeholder="z.B. Weekly Team Meeting", key="new_template_name")
                template_desc = st.text_area("Beschreibung (optional)", value=edit_template.get('description', ''), height=80, key="new_template_desc")

                st.markdown("---")
                st.markdown("**📝 Tagesordnungspunkte:**")

                for idx, section in enumerate(st.session_state['template_sections']):
                    st.markdown(f"**Abschnitt {idx + 1}**")
                    col1, col2 = st.columns([5, 1])
                    with col1:
                        st.text_input("Titel", value=section.get('title', ''), placeholder="z.B. Status Updates", key=f"section_title_{idx}", label_visibility="collapsed")
                        st.text_area("Inhalt", value=section.get('content', ''), height=100, key=f"section_content_{idx}", label_visibility="collapsed")
                    st.markdown("---")

                col1, col2 = st.columns(2)
                with col1:
                    submit_label = "💾 Änderungen speichern" if editing_mode else "💾 Vorlage speichern"
                    submitted = st.form_submit_button(submit_label, type="primary", use_container_width=True)

            if submitted:
                form_name = st.session_state.get('new_template_name', '').strip()
                form_desc = st.session_state.get('new_template_desc', '').strip()
                form_sections = []
                for idx in range(len(st.session_state['template_sections'])):
                    section_title = st.session_state.get(f'section_title_{idx}', '').strip()
                    section_content = st.session_state.get(f'section_content_{idx}', '').strip()
                    form_sections.append({'title': section_title, 'content': section_content})

                if not form_name:
                    st.error("❌ Bitte geben Sie einen Namen ein")
                else:
                    if editing_mode:
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
                            del st.session_state['editing_template_idx']
                            del st.session_state['editing_template']
                            del st.session_state['template_sections']
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error("❌ Fehler beim Speichern")
                    else:
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
                            del st.session_state['template_sections']
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error("❌ Fehler beim Speichern")

            if editing_mode:
                if st.button("❌ Bearbeitung abbrechen", use_container_width=True, key="cancel_edit_btn"):
                    del st.session_state['editing_template_idx']
                    del st.session_state['editing_template']
                    del st.session_state['template_sections']
                    st.rerun()

    st.markdown("---")

    # Schritt 1: Asana-Kontext (nur wenn Asana verfügbar)
    if selected_project_gid and asana_agent.is_connected():
        with st.expander("📋 Schritt 1: Asana-Kontext wählen (optional)", expanded=(st.session_state['agenda_workflow_step'] == 1)):
            st.caption("Wählen Sie die Asana-Sections aus denen die Agenda-Inhalte geladen werden sollen.")

            try:
                all_sections = asana_agent.get_project_sections(selected_project_gid)

                if all_sections:
                    section_names = [s['name'] for s in all_sections]

                    col1, col2 = st.columns(2)

                    with col1:
                        protokolle_default = next((i for i, n in enumerate(section_names) if 'protokoll' in n.lower()), 0)
                        selected_protokolle_section = st.selectbox("📋 Section für Protokolle", section_names, index=protokolle_default, key="agenda_protokolle_section")

                    with col2:
                        agenda_default = next((i for i, n in enumerate(section_names) if 'agenda' in n.lower()), 0)
                        selected_agenda_section = st.selectbox("📝 Section für Agenda-Themen", section_names, index=agenda_default, key="agenda_agenda_section")

                    if st.button("🔄 Kontext laden", use_container_width=True):
                        with st.spinner("Lade Asana-Daten..."):
                            protokolle_section_gid = None
                            agenda_section_gid = None

                            for section in all_sections:
                                if section['name'] == selected_protokolle_section:
                                    protokolle_section_gid = section['gid']
                                if section['name'] == selected_agenda_section:
                                    agenda_section_gid = section['gid']

                            preview_data = {
                                'open_protocols': [],
                                'agenda_items': [],
                                'protokolle_section_name': selected_protokolle_section,
                                'agenda_section_name': selected_agenda_section
                            }

                            if protokolle_section_gid:
                                preview_data['open_protocols'] = asana_agent.find_protocol_tasks_with_open_items(
                                    project_gid=selected_project_gid,
                                    protocol_section_name=selected_protokolle_section
                                )

                            if agenda_section_gid:
                                preview_data['agenda_items'] = asana_agent.get_tasks_from_section(
                                    section_gid=agenda_section_gid,
                                    limit=50,
                                    include_completed=False
                                )

                            st.session_state['agenda_preview_data'] = preview_data
                            st.session_state['agenda_sections_loaded'] = True
                            st.session_state['agenda_workflow_step'] = 2
                            st.rerun()

                    if st.session_state['agenda_sections_loaded']:
                        st.success("✅ Kontext erfolgreich geladen!")
                        preview = st.session_state['agenda_preview_data']
                        open_points_count = sum(len(p['open_items']) for p in preview['open_protocols'])
                        agenda_items_count = len(preview['agenda_items'])
                        st.info(f"**Gefunden:** {open_points_count} offene Punkte, {agenda_items_count} neue Themen")

                else:
                    st.warning("Keine Sections in diesem Projekt gefunden.")

            except Exception as e:
                st.error(f"Fehler beim Laden der Sections: {e}")
    else:
        st.info("💡 **Hinweis:** Kein Asana-Projekt ausgewählt. Sie können trotzdem eine Agenda aus einer Vorlage erstellen (siehe Schritt 2).")

    # Schritt 2: Agenda generieren
    with st.expander("✏️ Schritt 2: Agenda generieren & bearbeiten", expanded=(st.session_state['agenda_workflow_step'] == 2)):
        st.caption("Generieren Sie die Agenda mit den geladenen Daten oder verwenden Sie eine Vorlage.")

        templates = load_agenda_templates()

        use_template = st.checkbox("📚 Vorlage verwenden", value=False, key="use_template_checkbox")

        selected_template = None
        combine_with_asana = False

        if use_template and templates:
            template_options = ["[Keine Vorlage]"] + [t['name'] for t in templates]
            selected_template_name = st.selectbox("Vorlage auswählen", template_options, key="selected_template")

            if selected_template_name != "[Keine Vorlage]":
                for t in templates:
                    if t['name'] == selected_template_name:
                        selected_template = t
                        break

                if selected_template:
                    st.info(f"📋 **{selected_template['name']}**")
                    if st.session_state['agenda_sections_loaded']:
                        combine_with_asana = st.checkbox("🔄 Mit Asana-Daten kombinieren", value=False, key="combine_with_asana_checkbox")

        st.markdown("---")

        if not st.session_state['agenda_sections_loaded'] and not use_template:
            st.info("👆 Bitte zuerst Schritt 1 abschließen oder eine Vorlage auswählen")
        else:
            col1, col2 = st.columns(2)

            with col1:
                if use_template and selected_template:
                    if st.button("🔄 Aus Vorlage generieren", use_container_width=True, type="primary"):
                        with st.spinner("Generiere Agenda aus Vorlage..."):
                            meeting_title = event.get('title', 'Meeting') if event else 'Meeting'
                            asana_data = st.session_state['agenda_preview_data'] if combine_with_asana else None
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
                if st.session_state['agenda_sections_loaded'] and not use_template:
                    if st.button("🔄 Aus Asana generieren", use_container_width=True, type="primary"):
                        with st.spinner("Generiere Agenda aus Asana..."):
                            meeting_title = event.get('title', 'Meeting') if event else 'Meeting'
                            date_str = datetime.now().strftime("%d.%m.%Y")

                            agenda_content = f"""# Agenda: {meeting_title}
**Datum:** {date_str}

⚠️ **Keine Besprechung ohne Protokoll - Aufzeichnung aktivieren!**

---

"""
                            preview = st.session_state['agenda_preview_data']
                            top_number = 1

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

        if st.session_state['agenda_generated_content']:
            st.markdown("**📄 Generierte Agenda** (vollständig bearbeitbar):")
            edited_content = st.text_area(
                "Agenda bearbeiten",
                value=st.session_state['agenda_generated_content'],
                height=500,
                key="agenda_editor",
                label_visibility="collapsed"
            )
            st.session_state['agenda_generated_content'] = edited_content
            char_count = len(edited_content)
            st.caption(f"📊 {char_count:,} Zeichen | ✏️ Änderungen werden automatisch gespeichert")

    # Schritt 3: Speichern & Anhängen
    with st.expander("💾 Schritt 3: Speichern & an Termin anhängen", expanded=(st.session_state['agenda_workflow_step'] == 3)):
        if not st.session_state['agenda_generated_content']:
            st.info("👆 Bitte zuerst Schritt 2 abschließen")
        else:
            st.caption("Speichern Sie die finale Agenda und hängen Sie sie als PDF an den Outlook-Termin an.")

            with st.expander("📄 Agenda-Vorschau"):
                st.markdown(st.session_state['agenda_generated_content'])

            if st.button("✅ Agenda speichern & an Termin anhängen", type="primary", use_container_width=True):
                with st.spinner("Speichere und hänge Agenda an..."):
                    try:
                        outlook_tool = orch.outlook_tool
                        prep_dir = _get_user_ctx().meeting_prep
                        prep_dir.mkdir(parents=True, exist_ok=True)

                        event_title = event.get('title', 'Meeting') if event else 'Meeting'
                        safe_title = event_title.replace(' ', '_').replace('/', '-')[:50]
                        date_str = datetime.now().strftime("%Y-%m-%d_%H%M")
                        filename = f"{date_str}_Agenda_{safe_title}.md"
                        filepath = prep_dir / filename

                        with open(filepath, 'w', encoding='utf-8') as f:
                            f.write(st.session_state['agenda_generated_content'])

                        pdf_filename = filename.replace('.md', '.pdf')
                        pdf_path = prep_dir / pdf_filename

                        if convert_markdown_to_pdf(filepath, pdf_path):
                            agenda_dir = _get_user_ctx().data_dir / "agendas"
                            agenda_dir.mkdir(parents=True, exist_ok=True)

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

                            import shutil
                            shutil.copy2(pdf_path, agenda_path)

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
                                    time.sleep(2)

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

    # Relevante E-Mails
    st.caption("📧 **Relevante E-Mails:**")

    outlook_tool = orch.outlook_tool
    if outlook_tool.is_authenticated():
        try:
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

    # Chat-Interface
    if 'preparation_messages' not in st.session_state:
        st.session_state['preparation_messages'] = []

        attendee_list = ', '.join(get_attendee_names(event.get('attendees', []))) if event.get('attendees') else 'Keine Teilnehmer'
        asana_context = ""
        if selected_project_gid:
            asana_context = f"\n\n**Asana-Kontext:** Projekt '{selected_project_name}' ist ausgewählt."

        system_context = f"""Du bist ein Meeting-Vorbereitungs-Assistent für folgendes Meeting:

**Titel:** {event.get('title', 'Unbekannt')}
**Zeit:** {time_str}
**Ort:** {event.get('location', 'Nicht angegeben')}
**Teilnehmer:** {attendee_list}{asana_context}

Deine Aufgaben:
- Meeting-Agenden erstellen
- Themen recherchieren
- Informationen zu Teilnehmern bereitstellen
- Asana-Aufgaben für das Meeting identifizieren"""

        st.session_state['preparation_messages'].append({
            'role': 'system',
            'content': system_context
        })

    for msg in st.session_state['preparation_messages']:
        if msg['role'] == 'system':
            continue
        elif msg['role'] == 'user':
            with st.chat_message("user"):
                st.write(msg['content'])
        elif msg['role'] == 'assistant':
            with st.chat_message("assistant"):
                if msg['content']:
                    st.write(msg['content'])

    chat_input = st.chat_input("Ihre Nachricht für die Meeting-Vorbereitung...")

    if chat_input:
        st.session_state['preparation_messages'].append({
            'role': 'user',
            'content': chat_input
        })

        with st.status("🤖 Bereite Antwort vor...", expanded=True) as status:
            try:
                research_agent = orch.research_agent
                from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

                lc_messages = []
                for msg in st.session_state['preparation_messages']:
                    if msg['role'] == 'system':
                        lc_messages.append(SystemMessage(content=msg['content']))
                    elif msg['role'] == 'user':
                        lc_messages.append(HumanMessage(content=msg['content']))
                    elif msg['role'] == 'assistant':
                        lc_messages.append(AIMessage(content=msg['content']))

                response = research_agent.llm.invoke(lc_messages)

                st.session_state['preparation_messages'].append({
                    'role': 'assistant',
                    'content': response.content
                })

                status.update(label="✅ Fertig!", state="complete")
                st.rerun()

            except Exception as e:
                status.update(label="❌ Fehler", state="error")
                st.error(f"Fehler: {e}")


@st.fragment
def render_dashboard_tab():
    """Rendert den Mein Tag Dashboard-Tab mit optimierter Typografie.

    Performance: @st.fragment isoliert Re-Renders auf diesen Tab.
    """
    st.markdown("""
    <style>
    .stMarkdown p { line-height: 1.6; font-size: 1rem; }

    textarea[disabled] {
        background-color: var(--gray-50) !important;
        color: var(--gray-900) !important;
        cursor: text !important;
        opacity: 1 !important;
        border: 1px solid var(--gray-200) !important;
        border-radius: 0.5rem !important;
    }

    .comment-box {
        background-color: var(--brand-50);
        padding: 0.75rem 1rem;
        border-radius: 0.5rem;
        margin-bottom: 0.75rem;
        border-left: 3px solid var(--brand-500);
    }

    .search-results { margin-top: 1rem; }
    </style>
    """, unsafe_allow_html=True)

    st.header("📊 Mein Tag - Management Dashboard")

    render_connection_status()

    st.markdown("---")

    st.info("""
    **Willkommen in Ihrer Management-Zentrale!**

    Hier sehen Sie auf einen Blick:
    - 📅 Ihre heutigen Termine (Microsoft Kalender)
    - ✅ Ihre Asana-Aufgaben mit Prioritäten
    - 👤 Intelligente Kontext-Infos zu Gesprächspartnern
    """)

    st.markdown("---")

    should_return_to_dashboard = st.session_state.get('return_to_dashboard', False)
    if should_return_to_dashboard:
        del st.session_state['return_to_dashboard']

    if 'preparing_event' in st.session_state and not should_return_to_dashboard:
        render_meeting_preparation_view()
    else:
        col_left, col_right = st.columns([1, 1])

        with col_left:
            render_calendar_section()

        with col_right:
            render_asana_tasks_section()
