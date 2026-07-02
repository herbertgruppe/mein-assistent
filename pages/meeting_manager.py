"""
Meeting Manager Tab: Transkript-Upload, Protokoll-Erstellung (Streaming),
Task-Extraktion, Finalisierung & Asana-Export.
"""
import json
import re
import shutil
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import streamlit as st

from utils.background import (
    _bg_jobs_lock,
    _bg_protocol_jobs,
    start_bg_protocol_generation,
)
from utils.state import _get_user_ctx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _convert_to_berlin_time(dt):
    """Konvertiert ein datetime-Objekt in Berliner Zeit (MEZ/MESZ)."""
    try:
        import pytz
        berlin_tz = pytz.timezone('Europe/Berlin')
        if dt.tzinfo is None:
            dt = pytz.utc.localize(dt)
        return dt.astimezone(berlin_tz)
    except Exception:
        return dt


TRANSCRIPT_PREVIEW_CHAR_LIMIT = 1500


@st.cache_data(ttl=600, show_spinner=False)
def _read_transcript_text(file_path_str: str, mtime: float) -> str:
    """Liest Transkript-Text aus PDF oder Plain-Text. mtime erzwingt Cache-Invalidierung bei Datei-Änderung."""
    file_path = Path(file_path_str)
    if not file_path.exists():
        return ""
    if file_path.suffix.lower() == '.pdf':
        try:
            from langchain_community.document_loaders import PyPDFLoader
            loader = PyPDFLoader(str(file_path))
            pages = loader.load()
            return "\n\n".join([page.page_content for page in pages])
        except Exception as e:
            return f"[Fehler beim Lesen der PDF: {e}]"
    try:
        return file_path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        try:
            return file_path.read_text(encoding='latin-1')
        except Exception as e:
            return f"[Fehler beim Lesen der Datei: {e}]"
    except Exception as e:
        return f"[Fehler beim Lesen der Datei: {e}]"


def _render_transcript_preview(idx: int, file_path: Path) -> None:
    """Zeigt eine Vorschau des Transkripts unter dem Termin-Picker (HBE-287).

    Erste 1.500 Zeichen monospace + scrollbar; Toggle fuer Volltext.
    Vermeidet Tab-Wechsel nach Plaud waehrend der Termin-Zuordnung.
    """
    if not file_path or not str(file_path):
        return
    try:
        mtime = file_path.stat().st_mtime if file_path.exists() else 0.0
    except Exception:
        mtime = 0.0

    text = _read_transcript_text(str(file_path), mtime)
    if not text or not text.strip():
        st.caption("📄 Transkript-Vorschau: (Datei leer oder nicht lesbar)")
        return

    total_chars = len(text)
    show_full_key = f"transcript_preview_full_{idx}"
    show_full = st.session_state.get(show_full_key, False) and total_chars > TRANSCRIPT_PREVIEW_CHAR_LIMIT

    if total_chars <= TRANSCRIPT_PREVIEW_CHAR_LIMIT:
        label = f"📄 Transkript-Vorschau ({total_chars:,} Zeichen)"
    elif show_full:
        label = f"📄 Transkript-Vorschau (Volltext, {total_chars:,} Zeichen)"
    else:
        label = (
            f"📄 Transkript-Vorschau (erste {TRANSCRIPT_PREVIEW_CHAR_LIMIT:,} "
            f"von {total_chars:,} Zeichen)"
        )

    display_text = text if show_full else text[:TRANSCRIPT_PREVIEW_CHAR_LIMIT]

    with st.expander(label, expanded=True):
        st.text_area(
            "Transkript-Vorschau",
            value=display_text,
            height=260,
            disabled=True,
            label_visibility="collapsed",
            key=f"transcript_preview_text_{idx}_{'full' if show_full else 'short'}",
        )
        if total_chars > TRANSCRIPT_PREVIEW_CHAR_LIMIT:
            if show_full:
                if st.button(
                    "↑ Kurzfassung zeigen",
                    key=f"transcript_preview_short_btn_{idx}",
                ):
                    st.session_state[show_full_key] = False
                    st.rerun()
            else:
                if st.button(
                    "⤓ Volltext anzeigen",
                    key=f"transcript_preview_more_btn_{idx}",
                ):
                    st.session_state[show_full_key] = True
                    st.rerun()


def save_wip_item(item: Dict[str, Any], wip_dir: Path):
    """Speichert ein WIP-Item persistent."""
    try:
        item_id = item.get('id', 'unknown')
        wip_file = wip_dir / f"item_{item_id}.json"
        item_copy = item.copy()
        with open(wip_file, 'w', encoding='utf-8') as f:
            json.dump(item_copy, f, indent=2, ensure_ascii=False, default=str)
    except Exception as e:
        print(f"Fehler beim Speichern von WIP-Item: {e}")


def delete_wip_item(item: Dict[str, Any], wip_dir: Path):
    """Löscht ein WIP-Item von Disk."""
    try:
        item_id = item.get('id', 'unknown')
        wip_file = wip_dir / f"item_{item_id}.json"
        if wip_file.exists():
            wip_file.unlink()
            print(f"[delete_wip_item] ✓ WIP-Item gelöscht: {item_id}")
        else:
            print(f"[delete_wip_item] ⚠️ WIP-Datei nicht gefunden: {wip_file}")
    except Exception as e:
        print(f"[delete_wip_item] ✗ Fehler beim Löschen: {e}")


# ---------------------------------------------------------------------------
# Schritt-Funktionen
# ---------------------------------------------------------------------------

def render_step_rename(idx: int):
    """Schritt 1: Umbenennen."""
    st.markdown("### 1️⃣ Datei umbenennen")

    item = st.session_state['transcript_queue'][idx]
    file_path = Path(item['path'])
    selected_event = item.get('selected_event')

    if not file_path.exists():
        st.warning(f"⚠️ Quelldatei nicht mehr vorhanden: `{file_path.name}`")
        st.info("Die Datei wurde möglicherweise bereits umbenannt oder gelöscht. Umbenennung wird übersprungen.")
        if st.button("Weiter →", type="primary", key=f"skip_rename_missing_{idx}"):
            st.session_state['transcript_queue'][idx]['workflow_step'] = 2
            wip_dir = _get_user_ctx().wip_dir
            save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)
            st.rerun()
        return

    if not selected_event:
        st.info("ℹ️ Kein Termin zugeordnet – Umbenennung übersprungen. Du kannst in der Liste noch einen Termin zuordnen.")
        if st.button("Weiter →", type="primary"):
            st.session_state['transcript_queue'][idx]['workflow_step'] = 2
            st.rerun()
        return

    def _sanitize(name: str, max_length: int = 100) -> str:
        for char in '<>:"/\\|?*\n\r\t':
            name = name.replace(char, '')
        name = name.replace(' ', '_')
        while '__' in name:
            name = name.replace('__', '_')
        return name[:max_length].strip('_')

    event_start = selected_event.get('start')
    if isinstance(event_start, str):
        try:
            event_start_dt = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
            date_str = event_start_dt.strftime("%Y-%m-%d")
        except Exception:
            date_str = datetime.now().strftime("%Y-%m-%d")
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")

    meeting_title = selected_event.get('title', 'Ohne_Titel')
    sanitized_title = _sanitize(meeting_title, max_length=100)
    file_extension = file_path.suffix
    suggested_name = f"{date_str}_Protokoll_{sanitized_title}"

    st.markdown("#### 📝 Datei umbenennen")
    st.caption(f"Aktueller Name: `{file_path.name}`")

    new_name_stem = st.text_input(
        "Neuer Dateiname (ohne Endung):",
        value=suggested_name,
        key=f"rename_input_{idx}",
        help=f"Dateiendung '{file_extension}' wird automatisch angehängt"
    )

    new_name_stem = _sanitize(new_name_stem, max_length=150)
    new_filename = f"{new_name_stem}{file_extension}"

    st.code(new_filename)

    col_rename, col_skip = st.columns(2)
    with col_rename:
        if st.button("✅ Umbenennen", type="primary", use_container_width=True):
            try:
                processed_dir = _get_user_ctx().transcripts_processed
                new_file_path = processed_dir / new_filename

                counter = 1
                while new_file_path.exists():
                    new_filename = f"{new_name_stem}_{counter}{file_extension}"
                    new_file_path = processed_dir / new_filename
                    counter += 1

                shutil.move(str(file_path), str(new_file_path))

                st.session_state['transcript_queue'][idx]['path'] = str(new_file_path)
                st.session_state['transcript_queue'][idx]['filename'] = new_filename
                st.session_state['transcript_queue'][idx]['workflow_step'] = 2

                st.success(f"✅ Umbenannt zu: {new_filename}")
                st.balloons()
                time.sleep(1)
                st.rerun()

            except Exception as e:
                st.error(f"❌ Fehler: {e}")

    with col_skip:
        if st.button("Überspringen →", use_container_width=True):
            st.session_state['transcript_queue'][idx]['workflow_step'] = 2
            st.rerun()


def render_step_create_protocol(idx: int):
    """Schritt 2: Protokoll erstellen."""
    st.markdown("### 2️⃣ Protokoll erstellen")

    item = st.session_state['transcript_queue'][idx]
    file_path = Path(item['path'])
    selected_event = item.get('selected_event')

    if st.button("← Zurück zu Schritt 1 (Umbenennen)", key=f"back_to_step2_from_3_{idx}"):
        st.session_state['transcript_queue'][idx]['workflow_step'] = 1
        wip_dir = _get_user_ctx().wip_dir
        save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)
        st.rerun()

    st.markdown("---")

    wip_dir = _get_user_ctx().wip_dir

    # Prüfe Background-Job
    if not item.get('protocol'):
        with _bg_jobs_lock:
            bg_job = _bg_protocol_jobs.get(item['id'])
            if bg_job and bg_job['status'] == 'done':
                item['protocol'] = bg_job['protocol']
                _bg_protocol_jobs.pop(item['id'], None)
                st.session_state['transcript_queue'][idx] = item
                save_wip_item(item, wip_dir)
                st.toast("✅ Hintergrund-Protokoll übernommen!")

    # Disk-Cache prüfen
    if not item.get('protocol'):
        cache_dir = _get_user_ctx().protocol_cache
        candidate_files = [
            cache_dir / f"{item['id']}_protocol.md",
            cache_dir / f"{file_path.stem}_protocol.md",
        ]
        for cache_file in candidate_files:
            if cache_file.exists():
                item['protocol'] = cache_file.read_text(encoding='utf-8')
                st.session_state['transcript_queue'][idx] = item
                save_wip_item(item, wip_dir)
                st.toast("✅ Protokoll aus Cache geladen!")
                break

    if not item.get('protocol') and not file_path.exists():
        st.error(f"❌ Quelldatei nicht gefunden: `{file_path.name}`")
        st.info("Die Datei existiert nicht mehr. Bitte prüfe ob sie bereits verarbeitet wurde.")
        return

    st.info(f"📄 Datei: **{file_path.name}**")

    if not item.get('protocol'):
        with _bg_jobs_lock:
            bg_job = _bg_protocol_jobs.get(item['id'])

        if bg_job and bg_job['status'] == 'running':
            st.info(f"⏳ Protokoll wird im Hintergrund erstellt ({bg_job['chunks']} Tokens)...")
            st.progress(min(0.95, bg_job['chunks'] * 0.005), text=f"✨ {bg_job['chunks']} Tokens generiert")
            st.caption("Die Seite aktualisiert sich automatisch wenn das Protokoll fertig ist.")
            time.sleep(3)
            st.rerun()
            return

        if st.button("🚀 Protokoll jetzt erstellen", type="primary", use_container_width=True):
            st.session_state[f'start_protocol_{idx}'] = True
            st.rerun()

        if not st.session_state.get(f'start_protocol_{idx}', False):
            st.info("💡 Klicke auf den Button um die Protokoll-Erstellung zu starten")
            return

        # Protokoll erstellen mit STREAMING
        st.markdown("---")
        st.markdown("#### 🎬 Live-Protokoll-Erstellung")

        try:
            if file_path.suffix.lower() == '.pdf':
                from langchain_community.document_loaders import PyPDFLoader
                loader = PyPDFLoader(str(file_path))
                pages = loader.load()
                transcript_text = "\n\n".join([page.page_content for page in pages])
            else:
                transcript_text = file_path.read_text(encoding='utf-8')

            orch = st.session_state['orchestrator']
            llm = orch.research_agent.llm
            meeting_title = file_path.stem.split('_', 2)[-1] if '_' in file_path.stem else file_path.stem

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
                        event_start_dt = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
                        event_start_dt = _convert_to_berlin_time(event_start_dt)
                        meeting_date = event_start_dt.strftime('%d.%m.%Y %H:%M')

            progress_bar = st.progress(0, text="⏳ Vorbereitung...")
            status_text = st.empty()
            protocol_preview = st.empty()

            progress_bar.progress(10, text="📋 Analysiere Transkript...")
            status_text.info(f"📊 {len(transcript_text.split())} Wörter | {len(transcript_text)} Zeichen")
            time.sleep(0.3)

            progress_bar.progress(20, text="🤖 Starte KI-Verarbeitung...")
            status_text.info("🔄 Verbinde mit Claude...")
            time.sleep(0.3)

            progress_bar.progress(30, text="✨ Generiere Protokoll live...")
            status_text.success("🎯 Live-Streaming aktiv!")

            # Agenda laden
            agenda_text = None
            if selected_event:
                agenda_dir = _get_user_ctx().data_dir / "agendas"
                if agenda_dir.exists():
                    event_title = selected_event.get('title', '')
                    event_start = selected_event.get('start')
                    date_str = None
                    if isinstance(event_start, str):
                        try:
                            event_start_dt = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
                            date_str = event_start_dt.strftime("%Y-%m-%d")
                        except Exception:
                            pass

                    for agenda_file in agenda_dir.glob("Agenda_*.pdf"):
                        file_stem = agenda_file.stem
                        if date_str and date_str in file_stem:
                            if any(word.lower() in file_stem.lower() for word in event_title.split() if len(word) > 3):
                                try:
                                    from langchain_community.document_loaders import PyPDFLoader
                                    loader = PyPDFLoader(str(agenda_file))
                                    pages = loader.load()
                                    agenda_text = "\n\n".join([page.page_content for page in pages])
                                    st.info(f"📋 Agenda gefunden: {agenda_file.name}")
                                    break
                                except Exception:
                                    pass

            from utils.protocol import extract_protocol_from_transcript_streaming

            protocol_parts = []
            chunk_count = 0

            for chunk in extract_protocol_from_transcript_streaming(
                transcript_text,
                meeting_title,
                llm,
                attendees=attendees,
                meeting_date=meeting_date,
                agenda_text=agenda_text
            ):
                protocol_parts.append(chunk)
                chunk_count += 1
                estimated_progress = min(90, 30 + (chunk_count * 0.6))
                progress_bar.progress(int(estimated_progress), text=f"✨ {chunk_count} Tokens...")
                if chunk_count % 5 == 0:
                    protocol_preview.markdown(''.join(protocol_parts))

            protocol_text = ''.join(protocol_parts)

            progress_bar.progress(100, text="🎉 Protokoll erstellt!")
            protocol_preview.markdown(protocol_text)
            status_text.success(f"✅ {chunk_count} Tokens | {len(protocol_text)} Zeichen")
            time.sleep(1)

            progress_bar.empty()
            status_text.empty()
            protocol_preview.empty()

            st.session_state['transcript_queue'][idx]['protocol'] = protocol_text
            st.session_state[f'start_protocol_{idx}'] = False

            save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)

            st.success("✅ Protokoll erstellt!")
            st.rerun()

        except Exception as e:
            st.error(f"❌ Fehler: {e}")
            st.code(traceback.format_exc())

    else:
        # Protokoll bereits erstellt – zeige Editor
        st.success("✅ Protokoll erstellt - jetzt bearbeiten!")

        st.markdown("---")
        st.markdown("#### ✏️ Protokoll bearbeiten")
        st.info("💡 Bearbeite das Protokoll unten. Änderungen werden automatisch gespeichert.")

        col_stats1, col_stats2, col_stats3 = st.columns(3)
        with col_stats1:
            st.metric("Zeichen", len(item['protocol']))
        with col_stats2:
            st.metric("Wörter", len(item['protocol'].split()))
        with col_stats3:
            placeholder_count = item['protocol'].count('[?]')
            st.metric("⚠️ Platzhalter [?]", placeholder_count)

        if placeholder_count > 0:
            st.warning(f"⚠️ **{placeholder_count} Platzhalter [?] gefunden** - bitte ergänzen!")

        edited_protocol = st.text_area(
            "Protokoll-Inhalt:",
            value=item['protocol'],
            height=600,
            key=f"protocol_editor_{idx}",
            help="Bearbeite das Protokoll hier. Ersetze [?] durch korrekte Werte."
        )

        if edited_protocol != item['protocol']:
            st.session_state['transcript_queue'][idx]['protocol'] = edited_protocol
            wip_dir = _get_user_ctx().wip_dir
            save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)
            st.info("💾 Auto-Speichern: Änderungen gespeichert")

        st.markdown("---")

        with st.expander("👁️ Vorschau (formatiert)", expanded=False):
            st.markdown(edited_protocol)

        col_save, col_next = st.columns(2)

        with col_save:
            if st.button("🔄 Neu generieren", use_container_width=True, help="Protokoll komplett neu erstellen"):
                st.session_state['transcript_queue'][idx]['protocol'] = None
                st.session_state[f'start_protocol_{idx}'] = False
                wip_dir = _get_user_ctx().wip_dir
                save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)
                st.info("Protokoll zurückgesetzt - klicke auf 'Protokoll erstellen' um neu zu generieren")
                st.rerun()

        with col_next:
            if st.button("Weiter zu Tasks →", type="primary", use_container_width=True):
                st.session_state['transcript_queue'][idx]['protocol'] = edited_protocol
                st.session_state['transcript_queue'][idx]['workflow_step'] = 3
                wip_dir = _get_user_ctx().wip_dir
                save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)
                st.rerun()


def render_step_extract_tasks(idx: int):
    """Schritt 3: Tasks extrahieren."""
    st.markdown("### 3️⃣ Tasks extrahieren")

    item = st.session_state['transcript_queue'][idx]
    protocol_text = item.get('protocol', '')

    if st.button("← Zurück zu Schritt 2 (Protokoll bearbeiten)", key=f"back_to_step3_from_4_{idx}"):
        st.session_state['transcript_queue'][idx]['workflow_step'] = 2
        wip_dir = _get_user_ctx().wip_dir
        save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)
        st.rerun()

    st.markdown("---")

    if not protocol_text:
        st.warning("⚠️ Kein Protokoll vorhanden. Bitte erstelle zuerst ein Protokoll.")
        if st.button("← Zurück zu Schritt 2"):
            st.session_state['transcript_queue'][idx]['workflow_step'] = 2
            wip_dir = _get_user_ctx().wip_dir
            save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)
            st.rerun()
        return

    if not item.get('tasks'):
        if not st.session_state.get(f'extract_tasks_{idx}', False):
            st.info("💡 Klicke auf den Button, um Tasks aus dem Protokoll zu extrahieren")

            col1, col2 = st.columns([3, 1])
            with col1:
                if st.button("🎯 Tasks jetzt extrahieren", type="primary", use_container_width=True, key=f"extract_btn_{idx}"):
                    st.session_state[f'extract_tasks_{idx}'] = True
                    st.rerun()
            with col2:
                if st.button("Überspringen →", use_container_width=True, key=f"skip_btn_{idx}"):
                    st.session_state['transcript_queue'][idx]['tasks'] = []
                    st.session_state['transcript_queue'][idx]['workflow_step'] = 4
                    wip_dir = _get_user_ctx().wip_dir
                    save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)
                    st.rerun()
            return

        # Extraktion gestartet – führe aus
        status_text = st.empty()
        status_text.info("🔄 Extrahiere Tasks aus Protokoll... Dies kann 30-60 Sekunden dauern.")

        try:
            from utils.protocol import extract_tasks_from_protocol_text

            orch = st.session_state['orchestrator']
            llm = orch.research_agent.llm

            start_time = time.time()
            status_text.info(f"🤖 LLM analysiert das Protokoll... ({int(time.time() - start_time)}s)")

            tasks = extract_tasks_from_protocol_text(protocol_text, llm)

            elapsed = int(time.time() - start_time)
            status_text.empty()

            st.session_state['transcript_queue'][idx]['tasks'] = tasks
            st.session_state['transcript_queue'][idx]['workflow_step'] = 4
            st.session_state[f'extract_tasks_{idx}'] = False

            wip_dir = _get_user_ctx().wip_dir
            save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)

            st.success(f"✅ {len(tasks)} Tasks gefunden in {elapsed}s!")
            st.rerun()

        except Exception as e:
            status_text.empty()
            st.error(f"❌ Fehler beim Extrahieren: {str(e)}")

            with st.expander("🔍 Details zum Fehler"):
                st.code(traceback.format_exc())

            col1, col2 = st.columns(2)
            with col1:
                if st.button("🔄 Erneut versuchen"):
                    st.session_state[f'extract_tasks_{idx}'] = False
                    st.rerun()
            with col2:
                if st.button("Ohne Tasks fortfahren →"):
                    st.session_state['transcript_queue'][idx]['tasks'] = []
                    st.session_state['transcript_queue'][idx]['workflow_step'] = 4
                    st.session_state[f'extract_tasks_{idx}'] = False
                    wip_dir = _get_user_ctx().wip_dir
                    save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)
                    st.rerun()

    else:
        # Tasks bereits extrahiert
        tasks = item['tasks']
        st.success(f"✅ {len(tasks)} Tasks gefunden!")

        if tasks:
            st.markdown("#### 📋 Gefundene Tasks:")
            st.caption("✏️ Bearbeite die Tasks in der Tabelle. Änderungen werden automatisch gespeichert.")

            orch = st.session_state.get('orchestrator')
            user_options = ["[?]"]

            if orch and orch.asana_agent and orch.asana_agent.is_connected():
                try:
                    asana_users = orch.asana_agent.get_workspace_users()
                    user_options = ["[?]"] + [user['name'] for user in asana_users]
                except Exception:
                    pass

            import pandas as pd
            from datetime import date

            task_list = []
            for task in tasks:
                due_date_value = None
                due_date_str = task.get('due_date', '')
                if due_date_str and due_date_str != '[?]':
                    try:
                        if '.' in due_date_str:
                            parsed = datetime.strptime(due_date_str, '%d.%m.%Y')
                            due_date_value = parsed.date()
                        elif '-' in due_date_str:
                            parsed = datetime.strptime(due_date_str, '%Y-%m-%d')
                            due_date_value = parsed.date()
                    except Exception:
                        pass

                task_list.append({
                    'Titel': task.get('title', ''),
                    'Zuständig': task.get('assignee', '[?]'),
                    'Fällig am': due_date_value,
                    'Beschreibung': task.get('description', '')
                })

            df = pd.DataFrame(task_list)

            edited_df = st.data_editor(
                df,
                use_container_width=True,
                num_rows="dynamic",
                height=min(400, len(df) * 40 + 40),
                column_config={
                    "Titel": st.column_config.TextColumn(
                        "Titel",
                        help="Aufgaben-Titel",
                        max_chars=200,
                        required=True,
                        width="medium"
                    ),
                    "Zuständig": st.column_config.SelectboxColumn(
                        "Zuständig",
                        help="Verantwortliche Person",
                        options=user_options,
                        width="small"
                    ),
                    "Fällig am": st.column_config.DateColumn(
                        "Fällig am",
                        help="Fälligkeitsdatum",
                        format="DD.MM.YYYY",
                        width="small"
                    ),
                    "Beschreibung": st.column_config.TextColumn(
                        "Beschreibung",
                        help="Details zur Aufgabe",
                        width="large"
                    )
                },
                key=f"task_editor_{idx}"
            )

            updated_tasks = []
            for _, row in edited_df.iterrows():
                due_date_str = ''
                if pd.notna(row.get('Fällig am')):
                    try:
                        due_date_obj = row['Fällig am']
                        if hasattr(due_date_obj, 'strftime'):
                            due_date_str = due_date_obj.strftime('%Y-%m-%d')
                    except Exception:
                        pass

                updated_tasks.append({
                    'title': row.get('Titel', ''),
                    'assignee': row.get('Zuständig', '[?]'),
                    'due_date': due_date_str or '[?]',
                    'description': row.get('Beschreibung', '')
                })

            st.session_state['transcript_queue'][idx]['tasks'] = updated_tasks
            wip_dir = _get_user_ctx().wip_dir
            save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)

        else:
            st.info("Keine Tasks gefunden")

            if st.button("➕ Task manuell hinzufügen", use_container_width=True):
                new_task = {
                    'title': 'Neuer Task',
                    'assignee': '[?]',
                    'description': '',
                    'due_date': '[?]'
                }
                if 'tasks' not in st.session_state['transcript_queue'][idx]:
                    st.session_state['transcript_queue'][idx]['tasks'] = []
                st.session_state['transcript_queue'][idx]['tasks'].append(new_task)
                wip_dir = _get_user_ctx().wip_dir
                save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)
                st.rerun()

        if st.button("Weiter zur Finalisierung →", type="primary", use_container_width=True):
            st.session_state['transcript_queue'][idx]['workflow_step'] = 4
            wip_dir = _get_user_ctx().wip_dir
            save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)
            st.rerun()


def render_step_finalize(idx: int):
    """Schritt 4: Finalisieren & Archivieren."""
    st.markdown("### 4️⃣ Finalisieren & Archivieren")

    item = st.session_state['transcript_queue'][idx]

    st.markdown("#### 📊 Zusammenfassung")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("📄 Protokoll", "✅ Erstellt" if item.get('protocol') else "❌ Fehlt")
    with col2:
        task_count = len(item.get('tasks', []))
        st.metric("📋 Tasks", task_count)
    with col3:
        st.metric("📅 Termin", "✅ Zugeordnet" if item.get('selected_event') else "➖ Kein")

    if st.button("← Zurück zu Schritt 3 (Tasks bearbeiten)", key=f"back_to_step4_{idx}"):
        st.session_state['transcript_queue'][idx]['workflow_step'] = 3
        wip_dir = _get_user_ctx().wip_dir
        save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)
        st.rerun()

    st.markdown("---")

    st.markdown("#### 💾 Export & Archivierung")

    col_pdf, col_asana = st.columns(2)

    with col_pdf:
        st.markdown("**📎 Protokoll an Termin**")
        selected_event = item.get('selected_event')
        protocol_text = item.get('protocol', '')

        if not selected_event:
            st.caption("Kein Termin zugeordnet")
            st.button("An Termin anhängen", use_container_width=True, disabled=True)
        elif not protocol_text:
            st.caption("Kein Protokoll vorhanden")
            st.button("An Termin anhängen", use_container_width=True, disabled=True)
        else:
            st.caption("Als PDF an Outlook-Termin")
            if st.button("📎 An Termin anhängen", use_container_width=True):
                try:
                    with st.spinner("Erstelle PDF und hänge an Termin..."):
                        from utils.protocol import convert_markdown_to_pdf

                        orch = st.session_state.get('orchestrator')
                        outlook_tool = orch.outlook_tool

                        protocol_dir = _get_user_ctx().protocols_dir
                        protocol_dir.mkdir(parents=True, exist_ok=True)

                        meeting_title = selected_event.get('title', 'Meeting').replace('/', '_').replace('\\', '_')
                        date_str = datetime.now().strftime("%Y-%m-%d")

                        md_filename = f"{date_str}_Protokoll_{meeting_title}.md"
                        md_path = protocol_dir / md_filename

                        with open(md_path, 'w', encoding='utf-8') as f:
                            f.write(protocol_text)

                        pdf_filename = md_filename.replace('.md', '.pdf')
                        pdf_path = protocol_dir / pdf_filename

                        if convert_markdown_to_pdf(md_path, pdf_path):
                            event_id = selected_event.get('id')
                            result = outlook_tool.add_attachment_to_event(
                                event_id=event_id,
                                file_path=str(pdf_path),
                                file_name=pdf_filename
                            )

                            if result.get('success'):
                                st.success("✅ PDF erfolgreich an Termin angehängt!")

                                with st.spinner("Markiere Termin als 'Protokoll erstellt'..."):
                                    category_result = outlook_tool.add_category_to_event(
                                        event_id=event_id,
                                        category="Protokoll"
                                    )
                                    if category_result.get('success'):
                                        st.success(f"✅ {category_result.get('message', 'Kategorie hinzugefügt')}")
                                    else:
                                        st.warning(f"⚠️ Kategorie konnte nicht gesetzt werden: {category_result.get('error')}")

                                    prefix_result = outlook_tool.add_protocol_subject_prefix(event_id=event_id)
                                    if prefix_result.get('success'):
                                        st.success(f"✅ {prefix_result.get('message', 'Betreff-Prefix gesetzt')}")
                                    else:
                                        st.warning(f"⚠️ Betreff-Prefix konnte nicht gesetzt werden: {prefix_result.get('error')}")
                            else:
                                st.error(f"❌ Fehler beim Anhängen: {result.get('error')}")
                        else:
                            st.error("❌ PDF-Konvertierung fehlgeschlagen")

                except Exception as e:
                    st.error(f"❌ Fehler: {str(e)}")
                    st.text(traceback.format_exc())

    with col_asana:
        st.markdown("**🎯 Tasks zu Asana**")
        tasks = item.get('tasks', [])
        st.caption(f"{len(tasks)} Tasks exportieren")

        orch = st.session_state.get('orchestrator')
        if not orch or not orch.asana_agent or not orch.asana_agent.is_connected():
            st.warning("⚠️ Asana nicht verbunden")
            st.button("Zu Asana senden", use_container_width=True, disabled=True)
        elif len(tasks) == 0:
            st.info("ℹ️ Keine Tasks vorhanden")
            st.button("Zu Asana senden", use_container_width=True, disabled=True)
        else:
            asana_agent = orch.asana_agent
            projects = asana_agent.list_projects()

            if not projects:
                st.warning("⚠️ Keine Asana-Projekte gefunden")
            else:
                project_options = ["[Projekt wählen...]"] + [p['name'] for p in projects]

                default_idx = 0
                if item.get('selected_event'):
                    meeting_title = item['selected_event'].get('title', '')
                    for i, p in enumerate(projects, 1):
                        if meeting_title.lower() in p['name'].lower():
                            default_idx = i
                            break

                selected_project_name = st.selectbox(
                    "Asana-Projekt:",
                    project_options,
                    index=default_idx,
                    key=f"asana_project_{idx}",
                    label_visibility="collapsed"
                )

                selected_project_gid = None
                if selected_project_name != "[Projekt wählen...]":
                    for p in projects:
                        if p['name'] == selected_project_name:
                            selected_project_gid = p['gid']
                            break

                selected_section_gid = None
                selected_section_name = "[Keine Section - Standardposition]"
                if selected_project_gid:
                    sections = asana_agent.get_project_sections(selected_project_gid)
                    if sections:
                        section_names = ["[Keine Section - Standardposition]"] + [s['name'] for s in sections]

                        default_section_idx = 0
                        for i, s in enumerate(sections, 1):
                            if s['name'].lower() in ['protokolle', 'protokoll']:
                                default_section_idx = i
                                break

                        selected_section_name = st.selectbox(
                            "📂 In welche Section soll das Protokoll?",
                            section_names,
                            index=default_section_idx,
                            key=f"protocol_section_{idx}",
                            help="Wähle die Asana-Section für das Protokoll"
                        )

                        if selected_section_name != "[Keine Section - Standardposition]":
                            for s in sections:
                                if s['name'] == selected_section_name:
                                    selected_section_gid = s['gid']
                                    break

                button_disabled = (selected_project_gid is None)
                protocol_text = item.get('protocol', '')

                if st.button("🚀 Jetzt zu Asana senden", use_container_width=True, type="primary", disabled=button_disabled):
                    try:
                        from utils.protocol import convert_markdown_to_pdf

                        start_time = time.time()

                        # --- Schritt 1: Protokoll-Aufgabe ---
                        with st.spinner("📄 Erstelle Protokoll-Aufgabe in Asana..."):
                            meeting_title = "Meeting"
                            if item.get('selected_event'):
                                meeting_title = item['selected_event'].get('title', 'Meeting')

                            meeting_date_str = None
                            if item.get('selected_event'):
                                raw_start = item['selected_event'].get('start', '')
                                if isinstance(raw_start, str) and len(raw_start) >= 10:
                                    meeting_date_str = raw_start[:10]
                            date_str = meeting_date_str or datetime.now().strftime("%Y-%m-%d")
                            protocol_task_title = f"📄 Protokoll {date_str} - {meeting_title}"

                            protocol_task_result = asana_agent.create_task(
                                name=protocol_task_title,
                                notes=protocol_text,
                                project_gid=selected_project_gid,
                                assignee_gid=None,
                                section_gid=selected_section_gid
                            )

                            if not protocol_task_result.get('success'):
                                st.error(f"❌ Fehler beim Erstellen der Protokoll-Aufgabe: {protocol_task_result.get('error')}")
                                raise Exception("Protokoll-Aufgabe konnte nicht erstellt werden")

                            protocol_task_gid = protocol_task_result.get('task_gid')
                            section_label = f" in '{selected_section_name}'" if selected_section_gid else ""
                            st.success(f"✅ Protokoll-Aufgabe erstellt{section_label}: {protocol_task_title}")

                        # --- Schritt 2: PDF erstellen und anhängen ---
                        if protocol_text:
                            with st.spinner("📎 Erstelle und hänge PDF an..."):
                                try:
                                    protocol_dir = _get_user_ctx().protocols_dir
                                    protocol_dir.mkdir(parents=True, exist_ok=True)

                                    clean_title = meeting_title.replace('/', '_').replace('\\', '_')
                                    md_filename = f"{date_str}_Protokoll_{clean_title}.md"
                                    md_path = protocol_dir / md_filename

                                    with open(md_path, 'w', encoding='utf-8') as f:
                                        f.write(protocol_text)

                                    pdf_filename = md_filename.replace('.md', '.pdf')
                                    pdf_path = protocol_dir / pdf_filename

                                    if convert_markdown_to_pdf(md_path, pdf_path):
                                        attachment_result = asana_agent.attach_file_to_task(
                                            task_gid=protocol_task_gid,
                                            file_path=str(pdf_path),
                                            file_name=pdf_filename
                                        )
                                        if attachment_result.get('success'):
                                            st.success(f"✅ PDF '{pdf_filename}' angehängt")
                                        else:
                                            st.warning(f"⚠️ PDF-Anhang fehlgeschlagen: {attachment_result.get('error')}")
                                    else:
                                        st.warning("⚠️ PDF-Konvertierung fehlgeschlagen")
                                except Exception as e:
                                    st.warning(f"⚠️ PDF-Erstellung fehlgeschlagen: {e}")

                        # --- Schritt 3: User-Cache ---
                        user_cache = {}
                        try:
                            cached_users = asana_agent.get_workspace_users()
                            for user in cached_users:
                                user_cache[user['name'].lower().strip()] = user['gid']
                        except Exception as e:
                            st.warning(f"⚠️ User-Cache konnte nicht erstellt werden: {e}")

                        # --- Schritt 4: Tasks als Unteraufgaben ---
                        st.markdown("---")
                        st.markdown("#### 🔄 Erstelle Unteraufgaben...")

                        progress_bar = st.progress(0)
                        status_text = st.empty()

                        stats_cols = st.columns(4)
                        stat_progress = stats_cols[0].empty()
                        stat_success = stats_cols[1].empty()
                        stat_errors = stats_cols[2].empty()
                        stat_time = stats_cols[3].empty()

                        success_count = 0
                        errors = []
                        total_tasks = len(tasks)

                        for task_idx, task in enumerate(tasks, start=1):
                            try:
                                title = task.get('title', '')
                                description = task.get('description', '')
                                assignee_name = task.get('assignee', '')
                                due_date_str = task.get('due_date', '')
                                top_name = task.get('top', '')

                                elapsed_time = time.time() - start_time
                                avg_time_per_task = elapsed_time / task_idx if task_idx > 0 else 0
                                remaining_tasks = total_tasks - task_idx
                                estimated_remaining = avg_time_per_task * remaining_tasks

                                status_msg = f"🔄 Erstelle Unteraufgabe {task_idx}/{total_tasks}: {title[:40]}..."
                                if estimated_remaining > 0:
                                    status_msg += f" (~{int(estimated_remaining)}s verbleibend)"
                                status_text.text(status_msg)

                                progress_bar.progress(task_idx / total_tasks)
                                stat_progress.metric("Fortschritt", f"{task_idx}/{total_tasks}")
                                stat_success.metric("✅ Erfolg", success_count)
                                stat_errors.metric("❌ Fehler", len(errors))
                                stat_time.metric("⏱️ Zeit", f"{int(elapsed_time)}s")

                                due_on = None
                                if due_date_str and due_date_str != '[?]':
                                    try:
                                        from datetime import date as _date
                                        if isinstance(due_date_str, _date):
                                            due_on = due_date_str.strftime('%Y-%m-%d')
                                        elif '.' in due_date_str:
                                            parsed = datetime.strptime(due_date_str, '%d.%m.%Y')
                                            due_on = parsed.strftime('%Y-%m-%d')
                                        elif '-' in due_date_str:
                                            due_on = due_date_str
                                    except Exception:
                                        pass

                                origin_lines = [f"📎 Ursprung: {protocol_task_title}"]
                                if top_name:
                                    origin_lines.append(f"📋 Tagesordnungspunkt: {top_name}")
                                if assignee_name and assignee_name != '[?]':
                                    origin_lines.append(f"👤 Geplanter Verantwortlicher: {assignee_name}")

                                origin_block = "\n".join(origin_lines)
                                enhanced_description = (
                                    f"{description}\n\n---\n{origin_block}" if description else origin_block
                                )

                                result = asana_agent.create_subtask(
                                    parent_task_gid=protocol_task_gid,
                                    name=title,
                                    notes=enhanced_description,
                                    due_on=due_on,
                                    assignee_gid=None
                                )

                                if result.get('success'):
                                    success_count += 1
                                else:
                                    errors.append(f"{title}: {result.get('error')}")

                            except Exception as e:
                                errors.append(f"{task.get('title', 'Unbekannt')}: {str(e)}")

                        # Abschluss
                        for widget in [progress_bar, status_text, stat_progress, stat_success, stat_errors, stat_time]:
                            widget.empty()

                        total_time = time.time() - start_time

                        if success_count > 0:
                            st.success(f"✅ {success_count} Task(s) erfolgreich in Asana erstellt in {int(total_time)}s!")

                            with st.expander("📊 Performance-Details", expanded=False):
                                perf_col1, perf_col2, perf_col3 = st.columns(3)
                                with perf_col1:
                                    st.metric("Gesamt-Zeit", f"{int(total_time)}s")
                                with perf_col2:
                                    avg_time = total_time / total_tasks if total_tasks > 0 else 0
                                    st.metric("Ø pro Task", f"{avg_time:.1f}s")
                                with perf_col3:
                                    success_rate = (success_count / total_tasks * 100) if total_tasks > 0 else 0
                                    st.metric("Erfolgsrate", f"{success_rate:.0f}%")

                        if errors:
                            st.error(f"❌ {len(errors)} Fehler:")
                            for err in errors:
                                st.caption(f"• {err}")

                    except Exception as e:
                        st.error(f"❌ Fehler beim Asana-Export: {str(e)}")
                        st.text(traceback.format_exc())

    st.markdown("---")

    st.markdown("#### ✅ Protokoll abschließen")
    st.info("Das Protokoll wird archiviert und aus der Bearbeitungs-Liste entfernt.")

    if st.button("🎉 Abschließen & Archivieren", type="primary", use_container_width=True):
        st.session_state['transcript_queue'][idx]['status'] = 'completed'
        st.session_state['transcript_queue'][idx]['workflow_step'] = 5
        st.session_state['selected_transcript_idx'] = None

        wip_dir = _get_user_ctx().wip_dir
        delete_wip_item(st.session_state['transcript_queue'][idx], wip_dir)

        st.success("🎉 Protokoll erfolgreich abgeschlossen!")
        st.balloons()
        time.sleep(2)
        st.rerun()


# ---------------------------------------------------------------------------
# Termin-Zuordnung (Inline)
# ---------------------------------------------------------------------------

def _start_bg_protocol_for_item(idx: int, selected_event: dict):
    """Startet die Hintergrund-Protokoll-Generierung für ein Item mit zugeordnetem Termin."""
    item = st.session_state['transcript_queue'][idx]
    orch = st.session_state.get('orchestrator')

    if not orch or not orch.research_agent:
        st.warning("⚠️ Orchestrator nicht bereit – Protokoll-Start nicht möglich.")
        return

    if item.get('protocol'):
        st.toast("ℹ️ Protokoll bereits vorhanden – wird neu erstellt.", icon="🔄")
        item['protocol'] = None
        st.session_state['transcript_queue'][idx]['protocol'] = None

    with _bg_jobs_lock:
        already_running = _bg_protocol_jobs.get(item['id'], {}).get('status') == 'running'
    if already_running:
        st.toast("⏳ Protokoll-Erstellung läuft bereits...", icon="ℹ️")
        return

    bg_attendees = []
    for att in selected_event.get('attendees', []):
        if isinstance(att, dict):
            bg_attendees.append(att.get('name', att.get('email', '')))
        elif isinstance(att, str):
            bg_attendees.append(att)

    bg_date = None
    ev_start = selected_event.get('start')
    if isinstance(ev_start, str):
        try:
            ev_dt = datetime.fromisoformat(ev_start.replace('Z', '+00:00'))
            ev_dt = _convert_to_berlin_time(ev_dt)
            bg_date = ev_dt.strftime('%d.%m.%Y %H:%M')
        except Exception:
            pass

    bg_agenda = None
    agenda_dir = _get_user_ctx().data_dir / "agendas"
    if agenda_dir.exists() and bg_date:
        date_str_short = bg_date[:10]
        ev_title = selected_event.get('title', '')
        for af in agenda_dir.glob("Agenda_*.pdf"):
            if date_str_short in af.stem and any(
                w.lower() in af.stem.lower() for w in ev_title.split() if len(w) > 3
            ):
                try:
                    from langchain_community.document_loaders import PyPDFLoader
                    bg_agenda = "\n\n".join(
                        p.page_content for p in PyPDFLoader(str(af)).load()
                    )
                except Exception:
                    pass
                break

    fp = Path(item['path'])
    title = fp.stem.split('_', 2)[-1] if '_' in fp.stem else fp.stem
    start_bg_protocol_generation(
        item['id'], item['path'], title,
        orch.research_agent.llm, item['filename'],
        attendees=bg_attendees or None,
        meeting_date=bg_date,
        agenda_text=bg_agenda,
        protocol_cache_dir=str(_get_user_ctx().protocol_cache),
        wip_dir_str=str(_get_user_ctx().wip_dir)
    )
    st.toast("⏳ Protokoll wird im Hintergrund erstellt...", icon="🚀")


def render_inline_termin_assignment(idx: int):
    """Inline Termin-Zuordnung in der Listenansicht (aufklappbar)."""
    item = st.session_state['transcript_queue'][idx]
    file_path = Path(item.get('path', ''))
    wip_dir = _get_user_ctx().wip_dir

    with st.container():
        st.markdown("---")
        st.markdown("##### 📅 Termin zuordnen")

        date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', file_path.stem)
        if date_match:
            extracted_date = datetime.strptime(date_match.group(0), '%Y-%m-%d').date()
        else:
            extracted_date = datetime.now().date()

        meeting_date = st.date_input(
            "Meeting-Datum:",
            value=extracted_date,
            key=f"meeting_date_{idx}"
        )

        orch = st.session_state.get('orchestrator')
        if not orch or not orch.outlook_tool.is_authenticated():
            st.warning("⚠️ Outlook nicht authentifiziert. Bitte in der Sidebar authentifizieren.")
            if st.button("✖️ Schließen", key=f"close_assign_noauth_{idx}"):
                st.session_state['assign_termin_idx'] = None
                st.rerun()
            st.markdown("---")
            return

        outlook_tool = orch.outlook_tool

        cache_key = f"cached_events_{meeting_date.isoformat()}_{idx}"

        col_load, col_close = st.columns([2, 1])
        with col_load:
            if st.button("🔄 Termine laden", key=f"load_ev_{idx}", use_container_width=True):
                try:
                    from datetime import datetime as _dt
                    start_of_day = _dt.combine(meeting_date, _dt.min.time())
                    end_of_day = _dt.combine(meeting_date, _dt.max.time())
                    events = outlook_tool.get_events_for_date_range(start_of_day, end_of_day)
                    st.session_state[cache_key] = events or []
                except Exception as e:
                    st.error(f"❌ Fehler beim Laden: {e}")
                    st.session_state[cache_key] = []
                st.rerun()
        with col_close:
            if st.button("✖️ Schließen", key=f"close_assign_{idx}", use_container_width=True):
                st.session_state['assign_termin_idx'] = None
                st.rerun()

        events = st.session_state.get(cache_key)
        if events is not None:
            if events:
                st.success(f"✓ {len(events)} Termin(e) gefunden")

                event_options = ["— Bitte wählen —"]
                event_dict = {}

                for event in events:
                    event_start = event.get('start')
                    event_title = event.get('title', 'Ohne Titel')

                    if isinstance(event_start, str):
                        try:
                            event_start_dt = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
                            event_start_dt = _convert_to_berlin_time(event_start_dt)
                            time_str = event_start_dt.strftime('%H:%M')
                        except Exception:
                            time_str = event_start[:5] if len(event_start) >= 5 else "??:??"
                    else:
                        time_str = "??:??"

                    option_label = f"{time_str} - {event_title}"
                    event_options.append(option_label)
                    event_dict[option_label] = event

                st.session_state[f'_event_dict_{idx}'] = event_dict

                selected_option = st.selectbox(
                    "Termin:",
                    options=event_options,
                    key=f"event_select_{idx}"
                )

                # HBE-287: Transkript-Vorschau direkt am Termin-Picker, um Plaud-Tab-Wechsel zu vermeiden.
                _render_transcript_preview(idx, file_path)

                if selected_option != "— Bitte wählen —" and selected_option in event_dict:
                    selected_event = event_dict[selected_option]

                    with st.expander("📋 Details", expanded=False):
                        st.write(f"**Titel:** {selected_event.get('title')}")
                        st.write(f"**Ort:** {selected_event.get('location', 'Kein Ort')}")
                        if selected_event.get('attendees'):
                            st.write(f"**Teilnehmer:** {len(selected_event['attendees'])}")

                    if st.button("✅ Zuordnen & Protokoll starten", type="primary", key=f"confirm_assign_{idx}", use_container_width=True):
                        st.session_state['transcript_queue'][idx]['selected_event'] = selected_event
                        save_wip_item(st.session_state['transcript_queue'][idx], wip_dir)
                        print(f"[Termin-Zuordnung] ✅ Event '{selected_event.get('title')}' gespeichert für Item {item.get('id')} (idx={idx})")

                        try:
                            _start_bg_protocol_for_item(idx, selected_event)
                        except Exception as e:
                            print(f"[BG-Protocol] Fehler beim Starten: {e}")
                            st.error(f"❌ Protokoll-Start fehlgeschlagen: {e}")

                        st.session_state['assign_termin_idx'] = None
                        st.rerun()

                    st.info(f"👆 Klicke **'Zuordnen & Protokoll starten'** um **{selected_event.get('title', 'den Termin')}** zuzuordnen.")
            else:
                st.info(f"📭 Keine Termine am {meeting_date.strftime('%d.%m.%Y')} gefunden")
        else:
            st.caption("💡 Klicke 'Termine laden' um Outlook-Termine abzurufen")

        st.markdown("---")


def render_transcript_detail_view(idx: int):
    """
    Rendert die Detail-Ansicht für ein ausgewähltes Transkript.

    Workflow-Schritte:
    1. Umbenennen
    2. Protokoll erstellen
    3. Tasks extrahieren
    4. Finalisieren
    """
    item = st.session_state['transcript_queue'][idx]

    if item.get('workflow_step', 0) < 1:
        st.session_state['transcript_queue'][idx]['workflow_step'] = 1
        item['workflow_step'] = 1

    st.markdown("---")
    st.markdown(f"## 📄 {item['filename']}")

    selected_event = item.get('selected_event')
    if selected_event:
        ev_title = selected_event.get('title', 'Termin')
        st.caption(f"📅 Zugeordneter Termin: **{ev_title}**")
    else:
        st.caption("📅 Kein Termin zugeordnet (kann in der Liste nachgeholt werden)")

    col_back, col_status = st.columns([1, 3])
    with col_back:
        if st.button("← Zurück zur Liste"):
            st.session_state['selected_transcript_idx'] = None
            st.rerun()

    with col_status:
        current_step = item.get('workflow_step', 1)
        progress = max(0, (current_step - 1)) / 4
        st.progress(progress, text=f"Fortschritt: Schritt {current_step}/4")

    st.markdown("---")

    st.markdown("### 📝 Workflow")

    steps = [
        ("1️⃣ Umbenennen", 1),
        ("2️⃣ Protokoll erstellen", 2),
        ("3️⃣ Tasks extrahieren", 3),
        ("4️⃣ Finalisieren", 4)
    ]

    for step_name, step_num in steps:
        if current_step > step_num:
            st.success(f"✅ {step_name}")
        elif current_step == step_num:
            st.info(f"⏳ {step_name} - **Aktueller Schritt**")
        else:
            st.caption(f"⬜ {step_name}")

    st.markdown("---")

    if current_step == 1:
        render_step_rename(idx)
    elif current_step == 2:
        render_step_create_protocol(idx)
    elif current_step == 3:
        render_step_extract_tasks(idx)
    elif current_step == 4:
        render_step_finalize(idx)
    else:
        st.success("🎉 Protokoll abgeschlossen!")


# ---------------------------------------------------------------------------
# Haupt-Tab-Funktion
# ---------------------------------------------------------------------------

@st.fragment
def render_transcripts_tab():
    """
    Meeting Manager: Liste-basierte Navigation mit Upload, Queue und Detail-Ansicht.

    Performance: @st.fragment isoliert Re-Renders auf diesen Tab.

    Workflow:
    1. Upload-Bereich (immer oben)
    2. Transkript-Liste (gruppiert nach Status)
    3. Detail-Ansicht (nur für ausgewähltes Transkript)
    """
    st.header("🎙️ Meeting Manager")

    st.markdown("""
    Der Meeting Manager hilft bei der **Nachbereitung** von Meetings durch
    automatische Protokoll-Erstellung und Task-Extraktion aus Transkripten.

    💡 **Hinweis:** Die Meeting-Vorbereitung findest du im Tab **"Mein Tag"**.
    """)

    st.markdown("---")

    # ── Protokoll-Übersicht (HBE-1527) ─────────────────────────────────────────
    with st.expander("📋 Plaud-Aufnahmen Übersicht", expanded=True):
        import requests as _req_track
        import os as _os_track
        _api_url = _os_track.getenv("MEIN_ASSISTENT_INTERNAL_URL", "http://api:8502")
        _api_key = _os_track.getenv("API_SECRET_KEY", "")

        # ── Plaud Auth Status ─────────────────────────────────────────────────
        try:
            _status_resp = _req_track.get(
                f"{_api_url}/plaud/auth/status",
                headers={"X-API-Key": _api_key},
                timeout=5,
            )
            if _status_resp.status_code == 200:
                _auth = _status_resp.json()
                if not _auth.get("authenticated"):
                    st.warning("⚠️ Plaud nicht authentifiziert")
                elif _auth.get("access_token_expired"):
                    _refresh_resp = _req_track.post(
                        f"{_api_url}/plaud/auth/refresh",
                        headers={"X-API-Key": _api_key},
                        timeout=10,
                    )
                    if _refresh_resp.status_code == 200:
                        st.success("🔄 Plaud Token automatisch erneuert")
                    else:
                        st.warning("⚠️ Plaud Token abgelaufen — automatischer Refresh fehlgeschlagen")
                else:
                    _exp_min = _auth.get("access_token_expires_in_minutes", 0)
                    _rt_exp = (_auth.get("refresh_token_expires_at", "") or "")[:10] or "?"
                    st.caption(f"🔑 Plaud: ✅ aktiv noch {_exp_min} Min · Refresh-Token bis {_rt_exp}")

                if st.button("🔑 Plaud neu anmelden", key="plaud_reauth"):
                    _start_resp = _req_track.get(
                        f"{_api_url}/plaud/auth/start",
                        headers={"X-API-Key": _api_key},
                        timeout=5,
                    )
                    if _start_resp.status_code == 200:
                        _auth_url = _start_resp.json().get("auth_url", "")
                        st.markdown(f"**[👉 Hier klicken um Plaud zu autorisieren]({_auth_url})**")
                        st.info("Nach der Autorisierung kannst du dieses Fenster schließen und die Seite neu laden.")
                    else:
                        st.error("Konnte Auth-URL nicht generieren.")
        except Exception as _e_auth:
            st.caption(f"Plaud Auth-Status nicht verfügbar: {_e_auth}")

        # ── Aufnahmen-Übersicht ───────────────────────────────────────────────
        try:
            _resp = _req_track.get(
                f"{_api_url}/api/plaud/recordings",
                headers={"X-API-Key": _api_key},
                timeout=5
            )
            if _resp.status_code == 200:
                _data = _resp.json()
                _recs = _data.get("recordings", [])
                if _recs:
                    import pandas as _pd
                    _rows = []
                    for r in _recs:
                        _status_map = {
                            None: "🆕 Neu",
                            "new": "🆕 Neu",
                            "speakers_ok": "✅ Sprecher OK",
                            "review_ready": "👁 Review bereit",
                            "done": "✅ Fertig",
                            "abandoned": "❌ Nicht verfolgt",
                        }
                        _poller_map = {
                            None: "📝 Issue erstellt",
                            "skipped:too_short": "⏭ Zu kurz",
                            "cancelled_by_user": "🚫 Abgebrochen",
                        }
                        _rows.append({
                            "Datum": r.get("start_at", "")[:16].replace("T", " ") if r.get("start_at") else "?",
                            "Titel": (r.get("recording_title") or r.get("issue_identifier") or r.get("recording_id", "")[:8])[:60],
                            "Issue": r.get("issue_identifier") or "-",
                            "Status": _status_map.get(r.get("tracking_status"), r.get("tracking_status") or "?"),
                            "Poller": _poller_map.get(r.get("poller_status"), r.get("poller_status") or "OK"),
                            "Notiz": r.get("tracking_notes") or "",
                            "Review": r.get("review_link") or "",
                        })
                    st.dataframe(
                        _pd.DataFrame(_rows),
                        column_config={
                            "Review": st.column_config.LinkColumn("Review", display_text="🔗 Öffnen"),
                        },
                        use_container_width=True,
                        hide_index=True,
                    )
                    _done = sum(1 for r in _recs if r.get("tracking_status") == "done")
                    _open = sum(1 for r in _recs if r.get("tracking_status") not in ("done", "abandoned") and r.get("poller_status") not in ("skipped:too_short",))
                    st.caption(f"Gesamt: {len(_recs)} · Fertig: {_done} · Offen: {_open}")
                else:
                    st.info("Keine Plaud-Aufnahmen gefunden.")
            else:
                st.warning(f"Tracking-API nicht erreichbar (HTTP {_resp.status_code})")
        except Exception as _e:
            st.warning(f"Protokoll-Übersicht nicht verfügbar: {_e}")

    st.markdown("---")

    # -------------------------------------------------------------------------
    # 1. Upload-Bereich
    # -------------------------------------------------------------------------
    st.markdown("### 📤 Transkripte hochladen")

    uploaded_files = st.file_uploader(
        "Dateien auswählen",
        type=['txt', 'md', 'pdf'],
        accept_multiple_files=True,
        help="Lade ein oder mehrere Meeting-Transkripte hoch (TXT, MD oder PDF)",
        key="transcript_uploader"
    )

    processed_dir = _get_user_ctx().transcripts_processed
    processed_dir.mkdir(parents=True, exist_ok=True)

    wip_dir = _get_user_ctx().wip_dir
    wip_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------------
    # Persistente Queue aus WIP-Verzeichnis laden
    # -------------------------------------------------------------------------
    if 'transcript_queue' not in st.session_state:
        st.session_state['transcript_queue'] = []

        wip_files = list(wip_dir.glob("item_*.json"))

        if wip_files:
            for wip_file in wip_files:
                try:
                    with open(wip_file, 'r', encoding='utf-8') as f:
                        item = json.load(f)
                        st.session_state['transcript_queue'].append(item)
                except Exception as e:
                    st.error(f"Fehler beim Laden von {wip_file.name}: {e}")

            if len(wip_files) > 0:
                st.toast(f"📂 {len(wip_files)} gespeicherte Workflows wiederhergestellt!", icon="✅")

    if 'selected_transcript_idx' not in st.session_state:
        st.session_state['selected_transcript_idx'] = None

    if 'assign_termin_idx' not in st.session_state:
        st.session_state['assign_termin_idx'] = None

    if 'show_archive' not in st.session_state:
        st.session_state['show_archive'] = False

    if 'processed_upload_ids' not in st.session_state:
        st.session_state['processed_upload_ids'] = set()

    # -------------------------------------------------------------------------
    # Verarbeite hochgeladene Files
    # -------------------------------------------------------------------------
    if uploaded_files:
        newly_uploaded = []
        updated_files = []

        for uploaded_file in uploaded_files:
            # Jede Streamlit-UploadedFile bekommt eine eindeutige file_id pro
            # Upload-Aktion. Bereits verarbeitete Uploads überspringen, damit
            # der Handler nicht bei jedem Rerun das Item (inkl. selected_event)
            # zurücksetzt.
            if uploaded_file.file_id in st.session_state['processed_upload_ids']:
                continue

            file_path = processed_dir / uploaded_file.name

            existing_idx = None
            for idx, item in enumerate(st.session_state['transcript_queue']):
                if item['filename'] == uploaded_file.name:
                    existing_idx = idx
                    break

            if existing_idx is not None:
                old_item = st.session_state['transcript_queue'][existing_idx]
                old_id = old_item.get('id')

                with open(file_path, 'wb') as f:
                    f.write(uploaded_file.getbuffer())

                updated_item = {
                    'id': old_id,
                    'filename': uploaded_file.name,
                    'path': str(file_path),
                    'status': 'new',
                    'selected_event': None,
                    'protocol': None,
                    'tasks': None,
                    'error': None,
                    'uploaded_at': datetime.now().isoformat(),
                    'workflow_step': 0
                }

                st.session_state['transcript_queue'][existing_idx] = updated_item
                updated_files.append(uploaded_file.name)
                save_wip_item(updated_item, wip_dir)
            else:
                with open(file_path, 'wb') as f:
                    f.write(uploaded_file.getbuffer())

                item_id = str(uuid.uuid4())[:8]

                new_item = {
                    'id': item_id,
                    'filename': uploaded_file.name,
                    'path': str(file_path),
                    'status': 'new',
                    'selected_event': None,
                    'protocol': None,
                    'tasks': None,
                    'error': None,
                    'uploaded_at': datetime.now().isoformat(),
                    'workflow_step': 0
                }
                st.session_state['transcript_queue'].append(new_item)
                newly_uploaded.append(uploaded_file.name)
                save_wip_item(new_item, wip_dir)

            st.session_state['processed_upload_ids'].add(uploaded_file.file_id)

        if newly_uploaded:
            st.success(f"✅ {len(newly_uploaded)} neue Transkript(e) hochgeladen!")
            st.rerun()
        elif updated_files:
            st.info(f"🔄 {len(updated_files)} bestehende(s) Transkript(e) aktualisiert: {', '.join(updated_files)}")

    # -------------------------------------------------------------------------
    # Background-Job Status
    # -------------------------------------------------------------------------
    with _bg_jobs_lock:
        done_ids = [k for k, v in _bg_protocol_jobs.items() if v['status'] == 'done']
        error_ids = [k for k, v in _bg_protocol_jobs.items() if v['status'] == 'error']

    for item_id in done_ids:
        with _bg_jobs_lock:
            job = _bg_protocol_jobs.pop(item_id)
        for i, item in enumerate(st.session_state['transcript_queue']):
            if item['id'] == item_id:
                if not st.session_state['transcript_queue'][i].get('protocol'):
                    st.session_state['transcript_queue'][i]['protocol'] = job['protocol']
                st.session_state['transcript_queue'][i]['status'] = 'processing'
                save_wip_item(st.session_state['transcript_queue'][i], wip_dir)
                break

    for item_id in error_ids:
        with _bg_jobs_lock:
            job = _bg_protocol_jobs.pop(item_id)
        for i, item in enumerate(st.session_state['transcript_queue']):
            if item['id'] == item_id:
                st.session_state['transcript_queue'][i]['error'] = job['error']
                st.session_state['transcript_queue'][i]['status'] = 'error'
                save_wip_item(st.session_state['transcript_queue'][i], wip_dir)
                break

    with _bg_jobs_lock:
        _running_jobs = [(k, v) for k, v in _bg_protocol_jobs.items() if v['status'] == 'running']

    if _running_jobs:
        for _, job in _running_jobs:
            st.info(f"⏳ Protokoll wird erstellt: **{job['filename'][:60]}** ({job['chunks']} Tokens) – du kannst währenddessen weiterarbeiten.")
        st.caption("💡 Die Ergebnisse werden beim nächsten Klick automatisch übernommen.")

    st.markdown("---")

    # -------------------------------------------------------------------------
    # 2. Transkript-Liste
    # -------------------------------------------------------------------------
    queue = st.session_state['transcript_queue']

    if not queue:
        st.info("📭 Keine Transkripte vorhanden. Lade Dateien hoch um zu beginnen.")
        return

    st.markdown("### 📋 Meine Transkripte")

    new_items = [(idx, item) for idx, item in enumerate(queue) if item['status'] == 'new']
    processing_items = [(idx, item) for idx, item in enumerate(queue) if item['status'] == 'processing']
    completed_items = [(idx, item) for idx, item in enumerate(queue) if item['status'] == 'completed']
    error_items = [(idx, item) for idx, item in enumerate(queue) if item['status'] == 'error']

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("🟡 Neu", len(new_items))
    with col2:
        st.metric("🟠 In Bearbeitung", len(processing_items))
    with col3:
        st.metric("🟢 Fertig", len(completed_items))
    with col4:
        st.metric("🔴 Fehler", len(error_items))

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
        if st.button(
            "📁 Archiv verbergen" if st.session_state['show_archive'] else "📁 Archiv anzeigen",
            disabled=(len(completed_items) == 0),
            use_container_width=True
        ):
            st.session_state['show_archive'] = not st.session_state['show_archive']
            st.rerun()

    st.markdown("---")

    # Neue Transkripte
    if new_items:
        st.markdown("#### 🟡 Neue Transkripte")
        for idx, item in new_items:
            selected_event = item.get('selected_event')
            col_name, col_termin, col_assign, col_edit, col_delete = st.columns([3, 2, 0.8, 1.2, 0.5])
            with col_name:
                st.write(f"📄 **{item['filename']}**")
                st.caption(f"Hochgeladen: {datetime.fromisoformat(item['uploaded_at']).strftime('%d.%m.%Y %H:%M')}")
            with col_termin:
                if selected_event:
                    ev_title = selected_event.get('title', 'Termin')[:35]
                    st.write(f"✅ **{ev_title}**")
                else:
                    st.caption("❌ Kein Termin")
            with col_assign:
                assign_label = "📅" if not selected_event else "📅✏️"
                if st.button(assign_label, key=f"assign_{idx}", help="Termin zuordnen", use_container_width=True):
                    current = st.session_state.get('assign_termin_idx')
                    st.session_state['assign_termin_idx'] = None if current == idx else idx
                    st.rerun()
            with col_edit:
                if st.button("▶️ Bearbeiten", key=f"edit_new_{idx}", use_container_width=True):
                    st.session_state['selected_transcript_idx'] = idx
                    st.session_state['transcript_queue'][idx]['status'] = 'processing'
                    if st.session_state['transcript_queue'][idx].get('workflow_step', 0) < 1:
                        st.session_state['transcript_queue'][idx]['workflow_step'] = 1
                    save_wip_item(st.session_state['transcript_queue'][idx], _get_user_ctx().wip_dir)
                    st.rerun()
            with col_delete:
                if st.button("🗑️", key=f"delete_new_{idx}", use_container_width=True, help="Löschen"):
                    deleted_item = st.session_state['transcript_queue'].pop(idx)
                    delete_wip_item(deleted_item, _get_user_ctx().wip_dir)
                    if st.session_state.get('selected_transcript_idx') == idx:
                        st.session_state['selected_transcript_idx'] = None
                    st.success(f"✅ '{deleted_item['filename']}' gelöscht")
                    st.rerun()

            if st.session_state.get('assign_termin_idx') == idx:
                render_inline_termin_assignment(idx)

    # In Bearbeitung
    if processing_items:
        st.markdown("#### 🟠 In Bearbeitung")
        for idx, item in processing_items:
            selected_event = item.get('selected_event')
            workflow_steps = ["Neu", "Umbenennen", "Protokoll erstellen", "Tasks extrahieren", "Finalisieren"]
            current_step = item.get('workflow_step', 0)
            step_label = workflow_steps[min(current_step, len(workflow_steps) - 1)]

            col_name, col_termin, col_assign, col_edit, col_delete = st.columns([3, 2, 0.8, 1.2, 0.5])
            with col_name:
                st.write(f"📄 **{item['filename']}**")
                st.caption(f"Schritt {current_step}/4: {step_label}")
            with col_termin:
                if selected_event:
                    ev_title = selected_event.get('title', 'Termin')[:35]
                    st.write(f"✅ **{ev_title}**")
                else:
                    st.caption("❌ Kein Termin")
            with col_assign:
                assign_label = "📅" if not selected_event else "📅✏️"
                if st.button(assign_label, key=f"assign_proc_{idx}", help="Termin zuordnen/ändern", use_container_width=True):
                    current = st.session_state.get('assign_termin_idx')
                    st.session_state['assign_termin_idx'] = None if current == idx else idx
                    st.rerun()
            with col_edit:
                if st.button("📝 Fortsetzen", key=f"edit_proc_{idx}", use_container_width=True):
                    st.session_state['selected_transcript_idx'] = idx
                    st.rerun()
            with col_delete:
                if st.button("🗑️", key=f"delete_proc_{idx}", use_container_width=True, help="Löschen"):
                    deleted_item = st.session_state['transcript_queue'].pop(idx)
                    delete_wip_item(deleted_item, _get_user_ctx().wip_dir)
                    if st.session_state.get('selected_transcript_idx') == idx:
                        st.session_state['selected_transcript_idx'] = None
                    st.success(f"✅ '{deleted_item['filename']}' gelöscht")
                    st.rerun()

            if st.session_state.get('assign_termin_idx') == idx:
                render_inline_termin_assignment(idx)

    # Fertig (Archiv)
    if completed_items and st.session_state['show_archive']:
        st.markdown("#### 🟢 Fertig (Archiv)")
        for idx, item in completed_items:
            with st.expander(f"✅ {item['filename']}", expanded=False):
                st.caption(f"Abgeschlossen: {datetime.fromisoformat(item['uploaded_at']).strftime('%d.%m.%Y %H:%M')}")

                col_view, col_reopen, col_delete = st.columns(3)
                with col_view:
                    if st.button("👁️ Ansehen", key=f"view_{idx}"):
                        st.session_state['selected_transcript_idx'] = idx
                        st.rerun()

                with col_reopen:
                    if st.button("🔄 Wieder öffnen", key=f"reopen_{idx}"):
                        st.session_state['transcript_queue'][idx]['status'] = 'processing'
                        st.session_state['selected_transcript_idx'] = idx
                        st.rerun()

                with col_delete:
                    if st.button("🗑️ Löschen", key=f"delete_completed_{idx}"):
                        deleted_item = st.session_state['transcript_queue'].pop(idx)
                        delete_wip_item(deleted_item, _get_user_ctx().wip_dir)
                        if st.session_state.get('selected_transcript_idx') == idx:
                            st.session_state['selected_transcript_idx'] = None
                        st.success(f"✅ '{deleted_item['filename']}' gelöscht")
                        st.rerun()

    # Fehler
    if error_items:
        st.markdown("#### 🔴 Fehler")
        for idx, item in error_items:
            with st.expander(f"❌ {item['filename']}", expanded=False):
                st.error(f"Fehler: {item.get('error', 'Unbekannter Fehler')}")

                col_retry, col_delete = st.columns(2)
                with col_retry:
                    if st.button("🔄 Erneut versuchen", key=f"retry_{idx}"):
                        st.session_state['transcript_queue'][idx]['status'] = 'new'
                        st.session_state['transcript_queue'][idx]['error'] = None
                        st.rerun()

                with col_delete:
                    if st.button("🗑️ Löschen", key=f"delete_error_{idx}"):
                        deleted_item = st.session_state['transcript_queue'].pop(idx)
                        delete_wip_item(deleted_item, _get_user_ctx().wip_dir)
                        if st.session_state.get('selected_transcript_idx') == idx:
                            st.session_state['selected_transcript_idx'] = None
                        st.success(f"✅ '{deleted_item['filename']}' gelöscht")
                        st.rerun()

    st.markdown("---")

    # -------------------------------------------------------------------------
    # 3. Detail-Ansicht
    # -------------------------------------------------------------------------
    selected_idx = st.session_state.get('selected_transcript_idx')

    if selected_idx is not None and selected_idx < len(queue):
        render_transcript_detail_view(selected_idx)
    else:
        st.info("💡 Wähle ein Transkript aus der Liste oben um zu beginnen")

