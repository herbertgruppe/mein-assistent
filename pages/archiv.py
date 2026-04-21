"""
Archiv-Tab: Berichte-Archiv mit Ordner-Verwaltung, E-Mail-Versand und Asana-Integration.
"""
import os
import re
import shutil
import streamlit as st
from datetime import datetime
from typing import List

from utils.api_cache import cached_get_asana_projects


def get_archive_subfolders(archive_dir: str = "newsletter_archiv") -> list:
    """Gibt Liste aller Unterordner im Archiv zurück"""
    subfolders = []
    if os.path.exists(archive_dir):
        for item in os.listdir(archive_dir):
            item_path = os.path.join(archive_dir, item)
            if os.path.isdir(item_path):
                subfolders.append(item)
    return sorted(subfolders)


def get_archive_files_grouped(archive_dir: str = "newsletter_archiv") -> dict:
    """Gibt Archiv-Dateien gruppiert nach Ordnern zurück"""
    grouped_files = {
        "root": []
    }

    if os.path.exists(archive_dir):
        for file in os.listdir(archive_dir):
            file_path = os.path.join(archive_dir, file)
            if os.path.isfile(file_path) and file.endswith('.md'):
                file_stat = os.stat(file_path)
                grouped_files["root"].append({
                    'name': file,
                    'path': file_path,
                    'folder': None,
                    'size': file_stat.st_size,
                    'modified': datetime.fromtimestamp(file_stat.st_mtime)
                })

        for folder in os.listdir(archive_dir):
            folder_path = os.path.join(archive_dir, folder)
            if os.path.isdir(folder_path):
                grouped_files[folder] = []
                for file in os.listdir(folder_path):
                    if file.endswith('.md'):
                        file_path = os.path.join(folder_path, file)
                        file_stat = os.stat(file_path)
                        grouped_files[folder].append({
                            'name': file,
                            'path': file_path,
                            'folder': folder,
                            'size': file_stat.st_size,
                            'modified': datetime.fromtimestamp(file_stat.st_mtime)
                        })

    for folder in grouped_files:
        grouped_files[folder].sort(key=lambda x: x['modified'], reverse=True)

    return grouped_files


def send_report_email(filename: str, content: str, idx: int):
    """Sendet einen Bericht per E-Mail"""
    email_receiver = os.getenv("EMAIL_RECEIVER", "")

    if not email_receiver:
        st.warning("⚠️ EMAIL_RECEIVER nicht in .env konfiguriert")
        return

    try:
        subject = f"Bericht: {filename.replace('.md', '').replace('-', ' ')}"

        with st.spinner("📤 Sende E-Mail..."):
            result = st.session_state.orchestrator.communication_agent.send_email(
                to=email_receiver,
                subject=subject,
                body=content
            )

        if result.get("status") == "success":
            st.success(f"✅ E-Mail erfolgreich an {email_receiver} gesendet!")
        else:
            st.error(f"❌ {result.get('result', 'E-Mail-Versand fehlgeschlagen')}")

    except Exception as e:
        st.error(f"❌ Fehler beim E-Mail-Versand: {e}")


def render_folder_section(folder_name: str, reports: list, folder_key: str,
                          available_folders: list, archive_dir: str):
    """Rendert einen Ordner mit seinen Berichten"""
    report_count = len(reports)

    with st.expander(f"{folder_name} ({report_count} Berichte)", expanded=False):
        for idx, report in enumerate(reports):
            unique_key = f"{folder_key}_{idx}_{report['name'][:20]}"

            with st.expander(f"📄 {report['name']}", expanded=False):
                st.caption(f"📅 {report['modified'].strftime('%d.%m.%Y %H:%M')} | 📦 {report['size']/1024:.1f} KB")

                try:
                    with open(report['path'], 'r', encoding='utf-8') as f:
                        content = f.read()

                    st.markdown(content)
                    st.markdown("---")

                    col1, col2, col3, col4, col5 = st.columns(5)

                    with col1:
                        st.download_button(
                            "📥 Download",
                            data=content,
                            file_name=report['name'],
                            mime="text/markdown",
                            key=f"dl_{unique_key}",
                            use_container_width=True
                        )

                    with col2:
                        if st.button("📧 E-Mail", key=f"mail_{unique_key}", use_container_width=True):
                            send_report_email(report['name'], content, unique_key)

                    with col3:
                        asana_enabled = st.session_state.orchestrator.asana_tool.is_configured
                        if st.button("✅ Asana", key=f"asana_{unique_key}", use_container_width=True,
                                     disabled=not asana_enabled,
                                     help="Asana nicht konfiguriert" if not asana_enabled else "Als Asana-Aufgabe anlegen"):
                            st.session_state[f"show_asana_{unique_key}"] = True
                            st.rerun()

                    with col4:
                        can_move = len(available_folders) > 1
                        if st.button("📦 Verschieben", key=f"move_{unique_key}",
                                     disabled=not can_move, use_container_width=True,
                                     help="Erstellen Sie zuerst Ordner" if not can_move else None):
                            st.session_state[f"show_move_{unique_key}"] = True
                            st.rerun()

                    with col5:
                        if st.button("🗑️ Löschen", key=f"del_{unique_key}", use_container_width=True):
                            st.session_state[f"confirm_del_{unique_key}"] = True
                            st.rerun()

                    # Asana-Dialog
                    if st.session_state.get(f"show_asana_{unique_key}", False):
                        st.markdown("---")
                        st.write("**✅ Als Asana-Aufgabe anlegen:**")

                        suggested_title = report['name'].replace('.md', '').replace('-', ' ').replace('_', ' ')

                        task_title = st.text_input("Aufgabentitel", value=suggested_title, key=f"asana_title_{unique_key}")

                        asana_projects = cached_get_asana_projects(st.session_state.orchestrator.asana_agent)

                        if asana_projects:
                            project_options = {p['name']: p['gid'] for p in asana_projects}
                            selected_project_name = st.selectbox(
                                "Projekt *",
                                options=list(project_options.keys()),
                                key=f"asana_project_{unique_key}"
                            )
                            selected_project_gid = project_options[selected_project_name]
                        else:
                            st.warning("⚠️ Keine Asana-Projekte verfügbar")
                            selected_project_gid = None

                        task_description = st.text_area(
                            "Beschreibung (optional)",
                            value=content[:500] + "..." if len(content) > 500 else content,
                            height=100,
                            key=f"asana_desc_{unique_key}"
                        )

                        task_due = st.date_input("Fälligkeitsdatum (optional)", value=None, key=f"asana_due_{unique_key}")

                        col1, col2 = st.columns(2)
                        with col1:
                            if st.button("✓ Aufgabe erstellen", key=f"conf_asana_{unique_key}",
                                         type="primary", use_container_width=True):
                                if not selected_project_gid:
                                    st.error("❌ Bitte wählen Sie ein Projekt aus")
                                elif not task_title.strip():
                                    st.error("❌ Bitte geben Sie einen Aufgabentitel ein")
                                else:
                                    try:
                                        due_date_str = task_due.strftime('%Y-%m-%d') if task_due else None

                                        result = st.session_state.orchestrator.asana_agent.create_task(
                                            name=task_title.strip(),
                                            notes=task_description,
                                            due_on=due_date_str,
                                            project_gid=selected_project_gid,
                                            assignee_gid="me"
                                        )

                                        if result.get('success'):
                                            permalink = result.get('permalink_url', '')
                                            success_msg = f"✅ Asana-Aufgabe '{task_title}' erstellt!"
                                            if permalink:
                                                st.success(success_msg)
                                                st.markdown(f"🔗 [Aufgabe in Asana öffnen]({permalink})")
                                            else:
                                                st.success(success_msg)

                                            del st.session_state[f"show_asana_{unique_key}"]
                                            if f'asana_projects_{unique_key}' in st.session_state:
                                                del st.session_state[f'asana_projects_{unique_key}']
                                            st.rerun()
                                        else:
                                            st.error(f"❌ Fehler: {result.get('error', 'Unbekannter Fehler')}")
                                    except Exception as e:
                                        st.error(f"❌ Fehler: {e}")

                        with col2:
                            if st.button("✗ Abbrechen", key=f"canc_asana_{unique_key}", use_container_width=True):
                                del st.session_state[f"show_asana_{unique_key}"]
                                if f'asana_projects_{unique_key}' in st.session_state:
                                    del st.session_state[f'asana_projects_{unique_key}']
                                st.rerun()

                    # Verschieben-Dialog
                    if st.session_state.get(f"show_move_{unique_key}", False):
                        st.markdown("---")
                        st.write("**📦 Verschieben nach:**")

                        current_folder_display = f"📂 Hauptverzeichnis" if folder_key is None else f"📁 {folder_key}"
                        target_options = [f for f in available_folders if f != current_folder_display]

                        if target_options:
                            target = st.selectbox("Zielordner", target_options, key=f"sel_{unique_key}")

                            col1, col2 = st.columns(2)
                            with col1:
                                if st.button("✓ Verschieben", key=f"conf_move_{unique_key}",
                                             type="primary", use_container_width=True):
                                    if target == "📂 Hauptverzeichnis":
                                        target_path = os.path.join(archive_dir, report['name'])
                                    else:
                                        folder_name_clean = target.replace("📁 ", "")
                                        target_path = os.path.join(archive_dir, folder_name_clean, report['name'])

                                    try:
                                        if os.path.exists(target_path):
                                            st.error("❌ Datei existiert bereits am Zielort!")
                                        else:
                                            shutil.move(report['path'], target_path)
                                            st.success(f"✅ Nach {target} verschoben!")
                                            del st.session_state[f"show_move_{unique_key}"]
                                            st.rerun()
                                    except Exception as e:
                                        st.error(f"❌ Fehler: {e}")

                            with col2:
                                if st.button("✗ Abbrechen", key=f"canc_move_{unique_key}", use_container_width=True):
                                    del st.session_state[f"show_move_{unique_key}"]
                                    st.rerun()
                        else:
                            st.warning("Keine anderen Zielordner verfügbar")
                            if st.button("✗ Schließen", key=f"close_{unique_key}", use_container_width=True):
                                del st.session_state[f"show_move_{unique_key}"]
                                st.rerun()

                    # Löschen-Dialog
                    if st.session_state.get(f"confirm_del_{unique_key}", False):
                        st.markdown("---")
                        st.warning(f"⚠️ **'{report['name']}' wirklich löschen?**")
                        st.caption("Diese Aktion kann nicht rückgängig gemacht werden!")

                        col1, col2 = st.columns(2)
                        with col1:
                            if st.button("✓ Ja, löschen", key=f"conf_del_{unique_key}",
                                         type="primary", use_container_width=True):
                                try:
                                    os.remove(report['path'])
                                    st.success("✓ Gelöscht!")
                                    del st.session_state[f"confirm_del_{unique_key}"]
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"❌ Fehler: {e}")

                        with col2:
                            if st.button("✗ Abbrechen", key=f"canc_del_{unique_key}", use_container_width=True):
                                del st.session_state[f"confirm_del_{unique_key}"]
                                st.rerun()

                except Exception as e:
                    st.error(f"❌ Fehler beim Laden: {e}")


@st.fragment
def render_archive_tab():
    """Rendert den Archiv-Tab mit Berichten und Ordnerverwaltung.

    Performance: @st.fragment isoliert Re-Renders auf diesen Tab.
    """
    st.header("📚 Berichte-Archiv")

    archive_dir = "newsletter_archiv"

    os.makedirs(archive_dir, exist_ok=True)

    with st.expander("📁 Ordner-Verwaltung", expanded=False):
        st.subheader("Neuen Ordner erstellen")

        col1, col2 = st.columns([3, 1])

        with col1:
            new_folder_name = st.text_input(
                "Ordnername",
                placeholder="z.B. Wärmepumpen, Projekte, Analysen...",
                help="Erstellen Sie Unterordner, um Ihre Berichte zu organisieren"
            )

        with col2:
            st.write("")
            st.write("")
            if st.button("📁 Erstellen", use_container_width=True):
                if new_folder_name:
                    safe_folder_name = re.sub(r'[^\w\s-]', '', new_folder_name)
                    safe_folder_name = re.sub(r'[-\s]+', '-', safe_folder_name).strip('-')

                    if safe_folder_name:
                        folder_path = os.path.join(archive_dir, safe_folder_name)
                        try:
                            os.makedirs(folder_path, exist_ok=True)
                            st.success(f"✅ Ordner '{safe_folder_name}' erstellt!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ Fehler beim Erstellen: {e}")
                    else:
                        st.warning("⚠️ Ungültiger Ordnername")
                else:
                    st.warning("⚠️ Bitte Ordnernamen eingeben")

        subfolders = get_archive_subfolders(archive_dir)
        if subfolders:
            st.markdown("---")
            st.write("**Vorhandene Ordner:**")
            for folder in subfolders:
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.write(f"📁 {folder}")
                with col2:
                    if st.button("🗑️", key=f"del_folder_{folder}", help=f"Ordner '{folder}' löschen"):
                        folder_path = os.path.join(archive_dir, folder)
                        try:
                            if len(os.listdir(folder_path)) == 0:
                                os.rmdir(folder_path)
                                st.success(f"✓ Ordner '{folder}' gelöscht")
                                st.rerun()
                            else:
                                st.warning(f"⚠️ Ordner '{folder}' ist nicht leer!")
                        except Exception as e:
                            st.error(f"❌ Fehler: {e}")

    st.markdown("---")

    # Hierarchische Anzeige
    folders_data = {}

    main_reports = []
    try:
        for file in os.listdir(archive_dir):
            file_path = os.path.join(archive_dir, file)
            if os.path.isfile(file_path) and file.endswith('.md'):
                try:
                    file_stat = os.stat(file_path)
                    main_reports.append({
                        'name': file,
                        'path': file_path,
                        'folder': None,
                        'size': file_stat.st_size,
                        'modified': datetime.fromtimestamp(file_stat.st_mtime)
                    })
                except Exception as e:
                    print(f"Fehler beim Lesen von {file}: {e}")
    except Exception as e:
        print(f"Fehler beim Scannen des Archivs: {e}")

    folders_data['Hauptverzeichnis'] = sorted(main_reports, key=lambda x: x['modified'], reverse=True)

    subfolders = get_archive_subfolders(archive_dir)
    for folder in subfolders:
        folder_reports = []
        folder_path = os.path.join(archive_dir, folder)
        try:
            for file in os.listdir(folder_path):
                if file.endswith('.md'):
                    file_path = os.path.join(folder_path, file)
                    try:
                        file_stat = os.stat(file_path)
                        folder_reports.append({
                            'name': file,
                            'path': file_path,
                            'folder': folder,
                            'size': file_stat.st_size,
                            'modified': datetime.fromtimestamp(file_stat.st_mtime)
                        })
                    except Exception as e:
                        print(f"Fehler beim Lesen von {file}: {e}")
        except Exception as e:
            print(f"Fehler beim Scannen des Ordners {folder}: {e}")

        folders_data[folder] = sorted(folder_reports, key=lambda x: x['modified'], reverse=True)

    total_reports = sum(len(reports) for reports in folders_data.values())

    col1, col2 = st.columns([3, 1])
    with col1:
        st.subheader(f"📄 {total_reports} Berichte gefunden")
    with col2:
        st.metric("Gesamt", total_reports)

    st.markdown("---")

    if total_reports > 0:
        available_folders = ["📂 Hauptverzeichnis"] + [f"📁 {folder}" for folder in subfolders]

        if folders_data['Hauptverzeichnis']:
            render_folder_section("📂 Hauptverzeichnis", folders_data['Hauptverzeichnis'],
                                  None, available_folders, archive_dir)

        for folder in sorted(subfolders):
            if folders_data[folder]:
                render_folder_section(f"📁 {folder}", folders_data[folder],
                                      folder, available_folders, archive_dir)

    else:
        st.info("📭 Noch keine Berichte im Archiv vorhanden.")
        st.markdown("""
        Berichte werden automatisch erstellt und hier gespeichert, wenn Sie im Chat-Tab
        Anfragen stellen, die zu Recherche-Ergebnissen oder generierten Inhalten führen.
        """)
