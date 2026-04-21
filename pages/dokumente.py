"""
Dokumente-Tab: Upload und Verwaltung von Dokumenten.
"""
import os
import streamlit as st

from utils.state import _get_user_ctx


@st.fragment
def render_documents_tab():
    """Rendert den Dokumente-Tab mit Upload-Funktion.

    Performance: @st.fragment verhindert, dass Interaktionen in diesem Tab
    ein Re-Rendering der übrigen 5 Tabs auslösen.
    """
    st.header("📁 Dokumente verwalten")

    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader("📤 Dokumente hochladen")

        uploaded_files = st.file_uploader(
            "Laden Sie Dokumente hoch (PDF, DOCX, TXT, CSV, XLSX)",
            accept_multiple_files=True,
            type=["pdf", "docx", "txt", "csv", "xlsx"],
            help="Wählen Sie eine oder mehrere Dateien zum Hochladen"
        )

        if uploaded_files:
            if st.button("📥 Dateien speichern", type="primary", use_container_width=True):
                os.makedirs(str(_get_user_ctx().input_docs), exist_ok=True)

                success_count = 0
                error_count = 0

                for uploaded_file in uploaded_files:
                    try:
                        file_path = os.path.join(str(_get_user_ctx().input_docs), uploaded_file.name)
                        with open(file_path, "wb") as f:
                            f.write(uploaded_file.getbuffer())
                        success_count += 1
                    except Exception as e:
                        st.error(f"❌ Fehler bei {uploaded_file.name}: {e}")
                        error_count += 1

                if success_count > 0:
                    st.success(f"✅ {success_count} Datei(en) erfolgreich hochgeladen!")
                    st.rerun()

                if error_count > 0:
                    st.warning(f"⚠️ {error_count} Datei(en) konnten nicht hochgeladen werden")

    with col2:
        st.subheader("📊 Statistik")
        doc_tool = st.session_state.orchestrator.document_tool
        doc_count = doc_tool.count_documents()

        if doc_count > 0:
            documents = doc_tool.scan_documents()
            total_size = sum(doc['size'] for doc in documents)
            total_size_mb = total_size / (1024 * 1024)

            st.metric("Anzahl Dokumente", doc_count)
            st.metric("Gesamtgröße", f"{total_size_mb:.2f} MB")

            type_counts = {}
            for doc in documents:
                doc_type = doc['type']
                type_counts[doc_type] = type_counts.get(doc_type, 0) + 1

            st.write("**Dateitypen:**")
            for doc_type, count in type_counts.items():
                st.write(f"- {doc_type}: {count}")
        else:
            st.info("Noch keine Dokumente vorhanden")

    st.markdown("---")

    st.subheader("📋 Verfügbare Dokumente")

    doc_tool = st.session_state.orchestrator.document_tool
    documents = doc_tool.scan_documents()

    if documents:
        cols = st.columns([3, 1.5, 1, 1])
        cols[0].markdown("**Dateiname**")
        cols[1].markdown("**Typ**")
        cols[2].markdown("**Größe**")
        cols[3].markdown("**Aktion**")

        st.markdown("---")

        for idx, doc in enumerate(documents):
            cols = st.columns([3, 1.5, 1, 1])

            size_kb = doc['size'] / 1024
            size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.2f} MB"

            cols[0].write(doc['name'])
            cols[1].write(doc['type'])
            cols[2].write(size_str)

            if cols[3].button("🗑️ Löschen", key=f"del_doc_{idx}"):
                try:
                    file_path = os.path.join(str(_get_user_ctx().input_docs), doc['name'])
                    os.remove(file_path)
                    st.success(f"✓ {doc['name']} gelöscht")
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Fehler beim Löschen: {e}")
    else:
        st.info("📭 Keine Dokumente vorhanden. Laden Sie Dateien hoch, um zu beginnen.")
