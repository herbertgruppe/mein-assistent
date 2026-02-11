"""
Neue Meeting Manager Struktur - Perfekter Workflow
"""
import streamlit as st
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

def render_transcripts_tab_new():
    """
    Neue Meeting Manager Struktur mit Liste-basierter Navigation

    Workflow:
    1. Upload-Bereich (immer oben)
    2. Transkript-Liste (gruppiert nach Status)
    3. Detail-Ansicht (nur für ausgewähltes Transkript)
    """

    st.header("🎙️ Meeting Manager - Nachbereitung")

    st.markdown("""
    Der Meeting Manager hilft bei der **Nachbereitung** von Meetings durch
    automatische Protokoll-Erstellung und Task-Extraktion aus Transkripten.

    💡 **Hinweis:** Die Meeting-Vorbereitung findest du jetzt im Tab **"Mein Tag"**.
    """)

    st.markdown("---")

    # ========================================================================
    # 1. UPLOAD-BEREICH (immer oben, immer sichtbar)
    # ========================================================================

    st.markdown("### 📤 Transkripte hochladen")

    uploaded_files = st.file_uploader(
        "Dateien auswählen",
        type=['txt', 'md', 'pdf'],
        accept_multiple_files=True,
        help="Lade ein oder mehrere Meeting-Transkripte hoch (TXT, MD oder PDF)",
        key="transcript_uploader"
    )

    processed_dir = Path("transcripts/processed")
    processed_dir.mkdir(parents=True, exist_ok=True)

    # Initialisiere Transcript Queue im Session State
    if 'transcript_queue' not in st.session_state:
        st.session_state['transcript_queue'] = []

    # Initialisiere selected_transcript_idx
    if 'selected_transcript_idx' not in st.session_state:
        st.session_state['selected_transcript_idx'] = None

    # Initialisiere show_archive
    if 'show_archive' not in st.session_state:
        st.session_state['show_archive'] = False

    # Verarbeite hochgeladene Files
    if uploaded_files:
        newly_uploaded = []
        for uploaded_file in uploaded_files:
            # Speichere Datei
            file_path = processed_dir / uploaded_file.name

            # Prüfe ob bereits in Queue
            existing_files = [item['filename'] for item in st.session_state['transcript_queue']]
            if uploaded_file.name not in existing_files:
                with open(file_path, 'wb') as f:
                    f.write(uploaded_file.getbuffer())

                # Füge zur Queue hinzu
                st.session_state['transcript_queue'].append({
                    'filename': uploaded_file.name,
                    'path': str(file_path),
                    'status': 'new',  # new, processing, completed, error
                    'selected_event': None,
                    'protocol': None,
                    'tasks': None,
                    'error': None,
                    'uploaded_at': datetime.now().isoformat(),
                    'workflow_step': 0  # 0=neu, 1=termin, 2=umbenennen, 3=protokoll, 4=tasks, 5=fertig
                })
                newly_uploaded.append(uploaded_file.name)

        if newly_uploaded:
            st.success(f"✅ {len(newly_uploaded)} Transkript(e) hochgeladen!")
            st.rerun()

    st.markdown("---")

    # ========================================================================
    # 2. TRANSKRIPT-LISTE (gruppiert nach Status)
    # ========================================================================

    queue = st.session_state['transcript_queue']

    if not queue:
        st.info("📭 Keine Transkripte vorhanden. Lade Dateien hoch um zu beginnen.")
        return

    st.markdown("### 📋 Meine Transkripte")

    # Gruppiere nach Status
    new_items = [(idx, item) for idx, item in enumerate(queue) if item['status'] == 'new']
    processing_items = [(idx, item) for idx, item in enumerate(queue) if item['status'] == 'processing']
    completed_items = [(idx, item) for idx, item in enumerate(queue) if item['status'] == 'completed']
    error_items = [(idx, item) for idx, item in enumerate(queue) if item['status'] == 'error']

    # Statistiken
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("🟡 Neu", len(new_items))
    with col2:
        st.metric("🟠 In Bearbeitung", len(processing_items))
    with col3:
        st.metric("🟢 Fertig", len(completed_items))
    with col4:
        st.metric("🔴 Fehler", len(error_items))

    # Batch-Aktionen
    col_batch1, col_batch2, col_batch3 = st.columns(3)
    with col_batch1:
        if st.button("🚀 Alle Neuen verarbeiten", disabled=(len(new_items) == 0), use_container_width=True):
            st.session_state['batch_process_all'] = True
            st.rerun()

    with col_batch2:
        if st.button("🧹 Queue leeren", disabled=(len(queue) == 0), use_container_width=True):
            if st.session_state.get('confirm_clear_queue', False):
                st.session_state['transcript_queue'] = []
                st.session_state['selected_transcript_idx'] = None
                st.session_state['confirm_clear_queue'] = False
                st.success("✅ Queue geleert!")
                st.rerun()
            else:
                st.session_state['confirm_clear_queue'] = True
                st.warning("⚠️ Nochmal klicken zum Bestätigen!")

    with col_batch3:
        # Toggle Archiv
        if st.button(
            "📁 Archiv verbergen" if st.session_state['show_archive'] else "📁 Archiv anzeigen",
            disabled=(len(completed_items) == 0),
            use_container_width=True
        ):
            st.session_state['show_archive'] = not st.session_state['show_archive']
            st.rerun()

    st.markdown("---")

    # NEUE TRANSKRIPTE
    if new_items:
        st.markdown("#### 🟡 Neue Transkripte")
        for idx, item in new_items:
            col_name, col_action = st.columns([4, 1])
            with col_name:
                st.write(f"📄 **{item['filename']}**")
                st.caption(f"Hochgeladen: {datetime.fromisoformat(item['uploaded_at']).strftime('%d.%m.%Y %H:%M')}")
            with col_action:
                if st.button("▶️ Bearbeiten", key=f"edit_new_{idx}", use_container_width=True):
                    st.session_state['selected_transcript_idx'] = idx
                    st.session_state['transcript_queue'][idx]['status'] = 'processing'
                    st.rerun()

    # IN BEARBEITUNG
    if processing_items:
        st.markdown("#### 🟠 In Bearbeitung")
        for idx, item in processing_items:
            col_name, col_action = st.columns([4, 1])
            with col_name:
                st.write(f"📄 **{item['filename']}**")
                workflow_steps = ["Neu", "Termin zuordnen", "Umbenennen", "Protokoll erstellen", "Tasks extrahieren", "Fertig"]
                current_step = item.get('workflow_step', 0)
                st.caption(f"Schritt {current_step}/5: {workflow_steps[min(current_step, 5)]}")
            with col_action:
                if st.button("📝 Fortsetzen", key=f"edit_proc_{idx}", use_container_width=True):
                    st.session_state['selected_transcript_idx'] = idx
                    st.rerun()

    # FERTIG (Archiv - optional ausblendbat)
    if completed_items and st.session_state['show_archive']:
        st.markdown("#### 🟢 Fertig (Archiv)")
        for idx, item in completed_items:
            with st.expander(f"✅ {item['filename']}", expanded=False):
                st.caption(f"Abgeschlossen: {datetime.fromisoformat(item['uploaded_at']).strftime('%d.%m.%Y %H:%M')}")

                col_view, col_export, col_reopen = st.columns(3)
                with col_view:
                    if st.button("👁️ Ansehen", key=f"view_{idx}"):
                        st.session_state['selected_transcript_idx'] = idx
                        st.rerun()

                with col_export:
                    if st.button("📥 Export", key=f"export_{idx}"):
                        # TODO: Export Funktion
                        st.info("Export-Funktion folgt...")

                with col_reopen:
                    if st.button("🔄 Wieder öffnen", key=f"reopen_{idx}"):
                        st.session_state['transcript_queue'][idx]['status'] = 'processing'
                        st.session_state['selected_transcript_idx'] = idx
                        st.rerun()

    # FEHLER
    if error_items:
        st.markdown("#### 🔴 Fehler")
        for idx, item in error_items:
            with st.expander(f"❌ {item['filename']}", expanded=False):
                st.error(f"Fehler: {item.get('error', 'Unbekannter Fehler')}")
                if st.button("🔄 Erneut versuchen", key=f"retry_{idx}"):
                    st.session_state['transcript_queue'][idx]['status'] = 'new'
                    st.session_state['transcript_queue'][idx]['error'] = None
                    st.rerun()

    st.markdown("---")

    # ========================================================================
    # 3. DETAIL-ANSICHT (nur wenn Transkript ausgewählt)
    # ========================================================================

    selected_idx = st.session_state.get('selected_transcript_idx')

    if selected_idx is not None and selected_idx < len(queue):
        render_transcript_detail_view(selected_idx)
    else:
        st.info("💡 Wähle ein Transkript aus der Liste oben um zu beginnen")


def render_transcript_detail_view(idx: int):
    """
    Rendert die Detail-Ansicht für ein ausgewähltes Transkript

    Workflow-Schritte:
    1. Termin zuordnen
    2. Umbenennen
    3. Protokoll erstellen
    4. Tasks extrahieren
    5. Finalisieren
    """

    item = st.session_state['transcript_queue'][idx]

    st.markdown("---")
    st.markdown(f"## 📄 {item['filename']}")

    # Zurück-Button
    col_back, col_status = st.columns([1, 3])
    with col_back:
        if st.button("← Zurück zur Liste"):
            st.session_state['selected_transcript_idx'] = None
            st.rerun()

    with col_status:
        current_step = item.get('workflow_step', 0)
        st.progress(current_step / 5, text=f"Fortschritt: Schritt {current_step}/5")

    st.markdown("---")

    # Workflow-Schritte als Checkboxen
    st.markdown("### 📝 Workflow")

    steps = [
        ("1️⃣ Termin zuordnen", 1),
        ("2️⃣ Umbenennen", 2),
        ("3️⃣ Protokoll erstellen", 3),
        ("4️⃣ Tasks extrahieren", 4),
        ("5️⃣ Finalisieren", 5)
    ]

    for step_name, step_num in steps:
        if current_step >= step_num:
            st.success(f"✅ {step_name}")
        elif current_step == step_num - 1:
            st.info(f"⏳ {step_name} - **Aktueller Schritt**")
        else:
            st.caption(f"⬜ {step_name}")

    st.markdown("---")

    # Zeige aktuellen Schritt
    if current_step == 0:
        render_step_assign_meeting(idx)
    elif current_step == 1:
        render_step_rename(idx)
    elif current_step == 2:
        render_step_create_protocol(idx)
    elif current_step == 3:
        render_step_extract_tasks(idx)
    elif current_step == 4:
        render_step_finalize(idx)
    else:
        st.success("🎉 Protokoll abgeschlossen!")


def render_step_assign_meeting(idx: int):
    """Schritt 1: Termin zuordnen"""
    st.markdown("### 1️⃣ Termin zuordnen")

    item = st.session_state['transcript_queue'][idx]
    file_path = Path(item['path'])

    # Datums-Extraktion aus Dateiname oder default heute
    import re
    date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', file_path.stem)
    if date_match:
        extracted_date = datetime.strptime(date_match.group(0), '%Y-%m-%d').date()
    else:
        extracted_date = datetime.now().date()

    # Datum-Picker
    st.markdown("#### 📅 Wann war das Meeting?")
    meeting_date = st.date_input(
        "Meeting-Datum:",
        value=extracted_date,
        key=f"meeting_date_{idx}"
    )

    # Termine laden
    orch = st.session_state.get('orchestrator')
    if not orch or not orch.outlook_tool.is_authenticated():
        st.warning("⚠️ Outlook nicht authentifiziert. Bitte authentifiziere dich in der Sidebar.")
        if st.button("Ohne Termin fortfahren →"):
            st.session_state['transcript_queue'][idx]['workflow_step'] = 1
            st.rerun()
        return

    outlook_tool = orch.outlook_tool

    # Button zum Laden der Termine
    if st.button("🔄 Termine für diesen Tag laden", use_container_width=True):
        st.session_state[f'load_events_{idx}'] = True
        st.rerun()

    # Zeige Termine falls geladen
    if st.session_state.get(f'load_events_{idx}', False):
        with st.spinner("📅 Lade Termine..."):
            start_of_day = datetime.combine(meeting_date, datetime.min.time())
            end_of_day = datetime.combine(meeting_date, datetime.max.time())

            try:
                events = outlook_tool.get_events_for_date_range(start_of_day, end_of_day)

                if events:
                    st.success(f"✓ {len(events)} Termin(e) gefunden")

                    # Termin-Auswahl
                    st.markdown("#### 📋 Wähle den passenden Termin:")

                    event_options = ["Kein Termin zuordnen"]
                    event_dict = {}

                    for event in events:
                        event_start = event.get('start')
                        event_title = event.get('title', 'Ohne Titel')

                        # Parse Zeit
                        if isinstance(event_start, str):
                            try:
                                from utils.helpers import convert_to_berlin_time
                                event_start_dt = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
                                event_start_dt = convert_to_berlin_time(event_start_dt)
                                time_str = event_start_dt.strftime('%H:%M')
                            except:
                                time_str = event_start[:5] if len(event_start) >= 5 else "??:??"
                        else:
                            time_str = "??:??"

                        option_label = f"{time_str} - {event_title}"
                        event_options.append(option_label)
                        event_dict[option_label] = event

                    # Selectbox
                    selected_option = st.selectbox(
                        "Termin:",
                        options=event_options,
                        key=f"event_select_{idx}"
                    )

                    # Speichere Auswahl
                    if selected_option != "Kein Termin zuordnen":
                        selected_event = event_dict[selected_option]
                        st.session_state['transcript_queue'][idx]['selected_event'] = selected_event

                        # Zeige Details
                        with st.expander("📋 Termin-Details", expanded=False):
                            st.write(f"**Titel:** {selected_event.get('title')}")
                            st.write(f"**Ort:** {selected_event.get('location', 'Kein Ort')}")
                            if selected_event.get('attendees'):
                                st.write(f"**Teilnehmer:** {len(selected_event['attendees'])}")

                        # Weiter-Button
                        if st.button("✅ Termin zugeordnet - Weiter →", type="primary", use_container_width=True):
                            st.session_state['transcript_queue'][idx]['workflow_step'] = 1
                            st.rerun()
                    else:
                        if st.button("Ohne Termin fortfahren →", use_container_width=True):
                            st.session_state['transcript_queue'][idx]['selected_event'] = None
                            st.session_state['transcript_queue'][idx]['workflow_step'] = 1
                            st.rerun()

                else:
                    st.info(f"📭 Keine Termine am {meeting_date.strftime('%d.%m.%Y')} gefunden")
                    if st.button("Ohne Termin fortfahren →"):
                        st.session_state['transcript_queue'][idx]['workflow_step'] = 1
                        st.rerun()

            except Exception as e:
                st.error(f"❌ Fehler: {e}")
                if st.button("Ohne Termin fortfahren →"):
                    st.session_state['transcript_queue'][idx]['workflow_step'] = 1
                    st.rerun()
    else:
        st.info("💡 Klicke auf 'Termine laden' um fortzufahren")
        if st.button("Ohne Termin fortfahren →"):
            st.session_state['transcript_queue'][idx]['workflow_step'] = 1
            st.rerun()


def render_step_rename(idx: int):
    """Schritt 2: Umbenennen"""
    st.markdown("### 2️⃣ Datei umbenennen")

    item = st.session_state['transcript_queue'][idx]
    file_path = Path(item['path'])
    selected_event = item.get('selected_event')

    if not selected_event:
        st.info("ℹ️ Kein Termin zugeordnet - Umbenennung übersprungen")
        if st.button("Weiter →", type="primary"):
            st.session_state['transcript_queue'][idx]['workflow_step'] = 2
            st.rerun()
        return

    # Erstelle neuen Dateinamen
    def sanitize_filename(name: str, max_length: int = 100) -> str:
        """Bereinigt String für Dateinamen"""
        invalid_chars = '<>:"/\\|?*\n\r\t'
        for char in invalid_chars:
            name = name.replace(char, '')
        name = name.replace(' ', '_')
        while '__' in name:
            name = name.replace('__', '_')
        return name[:max_length].strip('_')

    # Extrahiere Datum aus Event
    event_start = selected_event.get('start')
    if isinstance(event_start, str):
        try:
            event_start_dt = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
            date_str = event_start_dt.strftime("%Y-%m-%d")
        except:
            date_str = datetime.now().strftime("%Y-%m-%d")
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")

    meeting_title = selected_event.get('title', 'Ohne_Titel')
    sanitized_title = sanitize_filename(meeting_title, max_length=100)
    file_extension = file_path.suffix
    new_filename = f"{date_str}_Protokoll_{sanitized_title}{file_extension}"

    # Zeige Vorschau
    st.markdown("#### 📝 Neuer Dateiname:")
    col1, col2 = st.columns([1, 4])
    with col1:
        st.caption("Alt:")
        st.caption("Neu:")
    with col2:
        st.code(file_path.name)
        st.code(new_filename)

    # Umbenennen-Button
    col_rename, col_skip = st.columns(2)
    with col_rename:
        if st.button("✅ Umbenennen", type="primary", use_container_width=True):
            try:
                processed_dir = Path("transcripts/processed")
                new_file_path = processed_dir / new_filename

                # Duplikat-Check
                counter = 1
                while new_file_path.exists():
                    new_filename = f"{date_str}_Protokoll_{sanitized_title}_{counter}{file_extension}"
                    new_file_path = processed_dir / new_filename
                    counter += 1

                # Benenne um
                import shutil
                shutil.move(str(file_path), str(new_file_path))

                # Update Queue
                st.session_state['transcript_queue'][idx]['path'] = str(new_file_path)
                st.session_state['transcript_queue'][idx]['filename'] = new_filename
                st.session_state['transcript_queue'][idx]['workflow_step'] = 2

                st.success(f"✅ Umbenannt zu: {new_filename}")
                st.balloons()
                import time
                time.sleep(1)
                st.rerun()

            except Exception as e:
                st.error(f"❌ Fehler: {e}")

    with col_skip:
        if st.button("Überspringen →", use_container_width=True):
            st.session_state['transcript_queue'][idx]['workflow_step'] = 2
            st.rerun()


def render_step_create_protocol(idx: int):
    """Schritt 3: Protokoll erstellen"""
    st.markdown("### 3️⃣ Protokoll erstellen")

    item = st.session_state['transcript_queue'][idx]
    file_path = Path(item['path'])
    selected_event = item.get('selected_event')

    # Zeige Transkript-Info
    st.info(f"📄 Datei: **{file_path.name}**")

    # Button zum Starten
    if not item.get('protocol'):
        if st.button("🚀 Protokoll jetzt erstellen", type="primary", use_container_width=True):
            st.session_state[f'start_protocol_{idx}'] = True
            st.rerun()

        if not st.session_state.get(f'start_protocol_{idx}', False):
            st.info("💡 Klicke auf den Button um die Protokoll-Erstellung zu starten")
            return

        # Protokoll erstellen mit STREAMING!
        st.markdown("---")
        st.markdown("#### 🎬 Live-Protokoll-Erstellung")

        try:
            # Lade Transkript
            if file_path.suffix.lower() == '.pdf':
                from langchain_community.document_loaders import PyPDFLoader
                loader = PyPDFLoader(str(file_path))
                pages = loader.load()
                transcript_text = "\n\n".join([page.page_content for page in pages])
            else:
                transcript_text = file_path.read_text(encoding='utf-8')

            # LLM
            orch = st.session_state['orchestrator']
            llm = orch.research_agent.llm

            # Meeting-Titel
            meeting_title = file_path.stem.split('_', 2)[-1] if '_' in file_path.stem else file_path.stem

            # Teilnehmer & Datum aus Event
            attendees = None
            meeting_date = None
            if selected_event:
                if selected_event.get('attendees'):
                    attendees = []
                    for att in selected_event['attendees']:
                        if isinstance(att, dict):
                            attendees.append(att.get('name', att.get('email', '')))
                        elif isinstance(att, str):
                            attendees.append(att)

                if selected_event.get('start'):
                    event_start = selected_event['start']
                    if isinstance(event_start, str):
                        from utils.helpers import convert_to_berlin_time
                        event_start_dt = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
                        event_start_dt = convert_to_berlin_time(event_start_dt)
                        meeting_date = event_start_dt.strftime('%d.%m.%Y %H:%M')

            # Fortschrittsanzeige
            progress_bar = st.progress(0, text="⏳ Vorbereitung...")
            status_text = st.empty()
            protocol_preview = st.empty()

            import time
            progress_bar.progress(10, text="📋 Analysiere Transkript...")
            status_text.info(f"📊 {len(transcript_text.split())} Wörter | {len(transcript_text)} Zeichen")
            time.sleep(0.3)

            progress_bar.progress(20, text="🤖 Starte KI-Verarbeitung...")
            status_text.info("🔄 Verbinde mit Claude...")
            time.sleep(0.3)

            progress_bar.progress(30, text="✨ Generiere Protokoll live...")
            status_text.success("🎯 Live-Streaming aktiv!")

            # STREAMING
            from app import extract_protocol_from_transcript_streaming

            protocol_parts = []
            chunk_count = 0

            for chunk in extract_protocol_from_transcript_streaming(
                transcript_text,
                meeting_title,
                llm,
                attendees=attendees,
                meeting_date=meeting_date,
                agenda_text=None
            ):
                protocol_parts.append(chunk)
                chunk_count += 1

                # Fortschritt
                estimated_progress = min(90, 30 + (chunk_count * 0.6))
                progress_bar.progress(int(estimated_progress), text=f"✨ {chunk_count} Tokens...")

                # Live-Vorschau
                if chunk_count % 5 == 0:
                    protocol_preview.markdown(''.join(protocol_parts))

            protocol_text = ''.join(protocol_parts)

            # Fertig
            progress_bar.progress(100, text="🎉 Protokoll erstellt!")
            protocol_preview.markdown(protocol_text)
            status_text.success(f"✅ {chunk_count} Tokens | {len(protocol_text)} Zeichen")
            time.sleep(1)

            # Cleanup
            progress_bar.empty()
            status_text.empty()
            protocol_preview.empty()

            # Speichere
            st.session_state['transcript_queue'][idx]['protocol'] = protocol_text
            st.session_state['transcript_queue'][idx]['workflow_step'] = 3
            st.session_state[f'start_protocol_{idx}'] = False

            st.success("✅ Protokoll erstellt!")
            st.rerun()

        except Exception as e:
            st.error(f"❌ Fehler: {e}")
            import traceback
            st.code(traceback.format_exc())

    else:
        # Protokoll bereits erstellt - zeige es
        st.success("✅ Protokoll bereits erstellt!")

        with st.expander("📄 Protokoll anzeigen", expanded=True):
            st.markdown(item['protocol'])

        # Editor für Bearbeitung
        edited_protocol = st.text_area(
            "Protokoll bearbeiten:",
            value=item['protocol'],
            height=400,
            key=f"protocol_editor_{idx}"
        )

        # Speichere Änderungen
        if st.button("💾 Änderungen speichern"):
            st.session_state['transcript_queue'][idx]['protocol'] = edited_protocol
            st.success("✅ Gespeichert!")
            st.rerun()

        # Weiter-Button
        if st.button("Weiter zu Tasks →", type="primary", use_container_width=True):
            st.session_state['transcript_queue'][idx]['workflow_step'] = 3
            st.rerun()


def render_step_extract_tasks(idx: int):
    """Schritt 4: Tasks extrahieren"""
    st.markdown("### 4️⃣ Tasks extrahieren")

    item = st.session_state['transcript_queue'][idx]
    protocol_text = item.get('protocol', '')

    if not protocol_text:
        st.warning("⚠️ Kein Protokoll vorhanden. Bitte erstelle zuerst ein Protokoll.")
        if st.button("← Zurück zu Schritt 3"):
            st.session_state['transcript_queue'][idx]['workflow_step'] = 2
            st.rerun()
        return

    # Button zum Tasks extrahieren
    if not item.get('tasks'):
        if st.button("🎯 Tasks jetzt extrahieren", type="primary", use_container_width=True):
            st.session_state[f'extract_tasks_{idx}'] = True
            st.rerun()

        if not st.session_state.get(f'extract_tasks_{idx}', False):
            st.info("💡 Tasks werden aus dem Protokoll extrahiert")
            if st.button("Überspringen (keine Tasks) →"):
                st.session_state['transcript_queue'][idx]['tasks'] = []
                st.session_state['transcript_queue'][idx]['workflow_step'] = 4
                st.rerun()
            return

        # Tasks extrahieren
        with st.spinner("🔍 Extrahiere Tasks aus Protokoll..."):
            try:
                from app import extract_tasks_from_transcript
                orch = st.session_state['orchestrator']
                llm = orch.research_agent.llm

                tasks = extract_tasks_from_transcript(protocol_text, llm)

                st.session_state['transcript_queue'][idx]['tasks'] = tasks
                st.session_state['transcript_queue'][idx]['workflow_step'] = 4
                st.session_state[f'extract_tasks_{idx}'] = False

                st.success(f"✅ {len(tasks)} Tasks gefunden!")
                st.rerun()

            except Exception as e:
                st.error(f"❌ Fehler: {e}")
                if st.button("Ohne Tasks fortfahren →"):
                    st.session_state['transcript_queue'][idx]['tasks'] = []
                    st.session_state['transcript_queue'][idx]['workflow_step'] = 4
                    st.rerun()

    else:
        # Tasks bereits extrahiert
        tasks = item['tasks']
        st.success(f"✅ {len(tasks)} Tasks gefunden!")

        if tasks:
            st.markdown("#### 📋 Gefundene Tasks:")
            for i, task in enumerate(tasks, 1):
                with st.expander(f"Task {i}: {task.get('title', 'Ohne Titel')}", expanded=False):
                    st.write(f"**Zuständig:** {task.get('assignee', '[?]')}")
                    st.write(f"**Beschreibung:** {task.get('description', '')}")
                    st.write(f"**Fällig:** {task.get('due_date', '[?]')}")
        else:
            st.info("Keine Tasks gefunden")

        # Weiter-Button
        if st.button("Weiter zur Finalisierung →", type="primary", use_container_width=True):
            st.session_state['transcript_queue'][idx]['workflow_step'] = 4
            st.rerun()


def render_step_finalize(idx: int):
    """Schritt 5: Finalisieren"""
    st.markdown("### 5️⃣ Finalisieren & Archivieren")

    item = st.session_state['transcript_queue'][idx]

    # Zusammenfassung
    st.markdown("#### 📊 Zusammenfassung")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("📄 Protokoll", "✅ Erstellt" if item.get('protocol') else "❌ Fehlt")
    with col2:
        task_count = len(item.get('tasks', []))
        st.metric("📋 Tasks", task_count)
    with col3:
        st.metric("📅 Termin", "✅ Zugeordnet" if item.get('selected_event') else "➖ Kein")

    st.markdown("---")

    # Export-Optionen
    st.markdown("#### 💾 Export & Archivierung")

    col_pdf, col_asana = st.columns(2)

    with col_pdf:
        st.markdown("**📥 Als PDF exportieren**")
        st.caption("Protokoll als PDF-Datei speichern")
        if st.button("PDF erstellen", use_container_width=True):
            st.info("PDF-Export folgt in nächster Version...")

    with col_asana:
        st.markdown("**🎯 Tasks zu Asana**")
        st.caption(f"{len(item.get('tasks', []))} Tasks exportieren")
        if st.button("Zu Asana senden", use_container_width=True, disabled=(len(item.get('tasks', [])) == 0)):
            st.info("Asana-Export folgt in nächster Version...")

    st.markdown("---")

    # Abschluss-Button
    st.markdown("#### ✅ Protokoll abschließen")
    st.info("Das Protokoll wird archiviert und aus der Bearbeitungs-Liste entfernt.")

    if st.button("🎉 Abschließen & Archivieren", type="primary", use_container_width=True):
        st.session_state['transcript_queue'][idx]['status'] = 'completed'
        st.session_state['transcript_queue'][idx]['workflow_step'] = 5
        st.session_state['selected_transcript_idx'] = None

        st.success("🎉 Protokoll erfolgreich abgeschlossen!")
        st.balloons()

        import time
        time.sleep(2)
        st.rerun()
