"""
Chat-Tab: Gedächtnis-Anzeige, Chat-Nachrichten-Rendering, Chat-Eingabe.
(Möglicherweise nicht mehr im Tab-Menü aktiv, aber für Vollständigkeit erhalten.)
"""
import streamlit as st
from datetime import datetime


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
            if "agents_used" in message:
                agents_str = ", ".join(message["agents_used"])
                st.caption(f"🤖 Verwendete Agenten: {agents_str}")

            if "research" in message:
                with st.expander("🔍 Research Agent", expanded=True):
                    research = message["research"]
                    status = research.get("status", "unknown")
                    if status == "success":
                        st.markdown(research.get("findings", ""))
                    elif status == "error":
                        error_msg = research.get("error") or research.get("findings", "Unbekannter Fehler")
                        st.error(f"❌ Fehler: {error_msg}")
                    else:
                        findings = research.get("findings", "")
                        if findings:
                            st.markdown(findings)
                        else:
                            st.warning(f"⚠️ Status: {status}")

            if "task" in message:
                with st.expander("⚙️ Task Agent", expanded=True):
                    task = message["task"]
                    status = task.get("status", "unknown")

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
                        if "output" in task and task["output"] != error_msg:
                            with st.expander("🔍 Fehler-Details"):
                                st.code(task["output"])
                    else:
                        output = task.get("output", "")
                        if output:
                            st.markdown(output)
                        else:
                            st.warning(f"⚠️ Status: {status} - Keine Ausgabe vorhanden")
                            st.json(task)

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
                        result = asana.get("result", "")
                        if result:
                            st.markdown(result)
                        else:
                            st.warning(f"⚠️ Status: {status}")

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

                        if "tools_used" in calendar_email and calendar_email["tools_used"]:
                            tools_str = ", ".join(calendar_email["tools_used"])
                            st.caption(f"🔧 Verwendete Tools: {tools_str}")
                    elif status == "error":
                        error_msg = calendar_email.get("error") or calendar_email.get("result", "Unbekannter Fehler")
                        st.error(f"❌ Fehler: {error_msg}")
                    else:
                        result = calendar_email.get("result", "")
                        if result:
                            st.markdown(result)
                        else:
                            st.warning(f"⚠️ Status: {status}")

            if "error" in message:
                st.error(f"❌ Fehler: {message['error']}")


def render_chat_tab():
    """Rendert den Chat-Tab"""
    st.header("💬 Chat mit Ihrem Assistenten")

    render_memory_display()

    for message in st.session_state.chat_history:
        render_chat_message(message)

    user_input = st.chat_input("Stellen Sie Ihre Frage oder geben Sie eine Aufgabe ein...")

    if user_input:
        user_message = {
            "role": "user",
            "content": user_input,
            "timestamp": datetime.now().isoformat()
        }
        st.session_state.chat_history.append(user_message)

        workflow = st.session_state.workflow_mode
        results = st.session_state.orchestrator.process_request(user_input, workflow)

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
        st.rerun()

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
