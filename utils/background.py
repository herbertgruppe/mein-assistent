"""
Background Protocol Generation (Thread-basiert).

_bg_protocol_jobs und _bg_jobs_lock sind Modul-Level-Globals,
die von Background-Threads und dem Streamlit-UI-Thread geteilt werden.
"""
import threading
from pathlib import Path
from typing import Any, Dict, Optional


# Modul-Level: wird von Background-Threads und Streamlit-Thread geteilt
_bg_protocol_jobs: Dict[str, Any] = {}   # {item_id: {status, protocol, chunks, filename, error}}
_bg_jobs_lock = threading.Lock()


def _run_protocol_generation_bg(item_id: str, file_path_str: str, meeting_title: str, llm,
                                 attendees=None, meeting_date=None, agenda_text=None,
                                 protocol_cache_dir: str = None,
                                 wip_dir_str: str = None):
    """Läuft in einem Background-Thread. Erzeugt das Protokoll ohne den UI-Thread zu blockieren.

    Persistenz-Strategie (wichtig! — darf Browser-Schließen überleben):
    - Disk-Cache unter STABILEM Key `item_id` (nicht file_path.stem, der sich beim Rename ändert)
    - Zusätzlich: Protokoll direkt in die WIP-JSON schreiben, falls ein wip_dir_str übergeben wurde.
      Damit sieht Schritt 2 das Protokoll auch, wenn der UI-Thread nicht mehr läuft (geschlossener Browser).
    """
    try:
        # Lazy import um Circular Imports zu vermeiden
        from agents import extract_protocol_from_transcript_streaming

        file_path = Path(file_path_str)

        if file_path.suffix.lower() == '.pdf':
            from langchain_community.document_loaders import PyPDFLoader
            loader = PyPDFLoader(str(file_path))
            pages = loader.load()
            transcript_text = "\n\n".join([p.page_content for p in pages])
        else:
            transcript_text = file_path.read_text(encoding='utf-8')

        protocol_parts = []
        for chunk in extract_protocol_from_transcript_streaming(
            transcript_text, meeting_title, llm,
            attendees=attendees, meeting_date=meeting_date, agenda_text=agenda_text
        ):
            protocol_parts.append(chunk)
            with _bg_jobs_lock:
                _bg_protocol_jobs[item_id]['chunks'] = len(protocol_parts)

        protocol = ''.join(protocol_parts)

        # Cache auf Disk schreiben — STABILER Key (item_id), unabhängig von Rename
        cache_dir = Path(protocol_cache_dir) if protocol_cache_dir else Path("transcripts/protocol_cache")
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"{item_id}_protocol.md").write_text(protocol, encoding='utf-8')
        # Zusätzlich unter altem Stem-Key schreiben (Rückwärtskompat, falls UI sie so erwartet)
        try:
            (cache_dir / f"{file_path.stem}_protocol.md").write_text(protocol, encoding='utf-8')
        except Exception:
            pass

        # WIP-JSON direkt updaten — überlebt geschlossenen Browser
        if wip_dir_str:
            try:
                import json as _json
                wip_file = Path(wip_dir_str) / f"item_{item_id}.json"
                if wip_file.exists():
                    with open(wip_file, 'r', encoding='utf-8') as f:
                        wip_item = _json.load(f)
                    if not wip_item.get('protocol'):
                        wip_item['protocol'] = protocol
                        wip_item['status'] = 'processing'
                        with open(wip_file, 'w', encoding='utf-8') as f:
                            _json.dump(wip_item, f, indent=2, ensure_ascii=False, default=str)
            except Exception as _e:
                print(f"[BG-Protocol] WIP-Update fehlgeschlagen: {_e}")

        with _bg_jobs_lock:
            _bg_protocol_jobs[item_id]['status'] = 'done'
            _bg_protocol_jobs[item_id]['protocol'] = protocol

    except Exception as e:
        with _bg_jobs_lock:
            _bg_protocol_jobs[item_id]['status'] = 'error'
            _bg_protocol_jobs[item_id]['error'] = str(e)


def start_bg_protocol_generation(item_id: str, file_path_str: str, meeting_title: str, llm, filename: str,
                                   attendees=None, meeting_date=None, agenda_text=None,
                                   protocol_cache_dir: str = None,
                                   wip_dir_str: str = None):
    """Startet die Protokoll-Erstellung in einem Background-Thread."""
    with _bg_jobs_lock:
        _bg_protocol_jobs[item_id] = {
            'status': 'running',
            'protocol': '',
            'chunks': 0,
            'filename': filename,
            'error': ''
        }
    t = threading.Thread(
        target=_run_protocol_generation_bg,
        args=(item_id, file_path_str, meeting_title, llm),
        kwargs={
            'attendees': attendees,
            'meeting_date': meeting_date,
            'agenda_text': agenda_text,
            'protocol_cache_dir': protocol_cache_dir,
            'wip_dir_str': wip_dir_str,
        },
        daemon=True
    )
    t.start()
