"""
Protokoll-Hilfsfunktionen: Namensextraktion, Task-Extraktion, PDF-Konvertierung,
Agenda-Template-Verwaltung und Asana-Protokoll-Erstellung.
"""
import shutil
import streamlit as st
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def extract_person_names_from_protocol_markdown(protocol_text: str) -> List[str]:
    """
    Extrahiert Personen-Namen aus dem Protokoll-Text im Markdown-Format.

    Sucht nach dem Muster: - **Name**: Aufgabenbeschreibung

    Args:
        protocol_text: Protokoll-Text im Markdown-Format

    Returns:
        Liste eindeutiger Namen
    """
    import re

    names = set()

    # Muster: - **Name**: Aufgabe (mit oder ohne Bindestriche vor **)
    pattern = r'-\s*\*\*([^*]+)\*\*\s*:'

    matches = re.findall(pattern, protocol_text)

    for match in matches:
        name = match.strip()
        # Filtere unerwünschte Matches
        if name and name not in ['?', 'TBD', 'TODO', 'Alle', 'Team', 'Datum', 'Ort', 'Zeit']:
            names.add(name)

    return sorted(list(names))


def extract_person_names_from_tasks(tasks: List[Dict[str, Any]]) -> List[str]:
    """
    Extrahiert alle eindeutigen Personen-Namen aus den Aufgaben.

    Args:
        tasks: Liste von Task-Dictionaries mit 'assignee' Feld

    Returns:
        Liste eindeutiger Namen (ohne [?], TBD, etc.)
    """
    names = set()

    for task in tasks:
        assignee = task.get('assignee', '')
        if assignee and isinstance(assignee, str):
            if assignee not in ['[?]', '?', 'TBD', 'TODO', '', 'Alle', 'Team', 'null']:
                names.add(assignee.strip())

    return sorted(list(names))


def extract_all_person_names(protocol_text: str, tasks: List[Dict[str, Any]]) -> List[str]:
    """
    Kombiniert Namen aus Protokoll-Text und Tasks.

    Args:
        protocol_text: Protokoll-Text
        tasks: Liste von Tasks

    Returns:
        Kombinierte, eindeutige Liste von Namen
    """
    names_from_protocol = extract_person_names_from_protocol_markdown(protocol_text)
    names_from_tasks = extract_person_names_from_tasks(tasks)

    all_names = set(names_from_protocol + names_from_tasks)

    return sorted(list(all_names))


def extract_tasks_from_protocol_text(protocol_text: str, llm) -> List[Dict[str, Any]]:
    """
    Extrahiert Aufgaben aus dem "Weitere Schritte" Abschnitt des Protokolls.

    Args:
        protocol_text: Vollständiger Protokoll-Text (editiert vom Nutzer)
        llm: LLM-Instanz für Parsing

    Returns:
        Liste von Dicts mit Aufgaben (title, description, due_date, assignee)
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    import json
    import re

    system_prompt = """Du bist ein Assistent, der aus Meeting-Protokollen Aufgaben extrahiert.

Analysiere den Protokoll-Text und extrahiere ALLE Aufgaben aus dem Protokoll.

Aufgaben können im Protokoll in mehreren Formen auftauchen:
- Inline-pro-TOP: unter `**Aufgaben:**`-Blöcken direkt unter einem Themenblock (mehrere solcher Blöcke pro Protokoll möglich) im Format `- Vorname Nachname: Aufgabenbeschreibung [Fälligkeitsdatum falls erwähnt]`
- Sammelblock am Ende (Legacy): unter `**Aufgaben & Nächste Schritte**` oder `**Weitere Schritte**` im Format `- **[Name oder [?]]**: [Aufgabenbeschreibung] - Fällig: [Datum oder [?]]`

Sammle ALLE Aufgaben aus ALLEN solcher Blöcke ein (nicht nur einen). Behandle Inline- und Sammelblock-Format gleichwertig.

Gib die Aufgaben im folgenden JSON-Format zurück:

[
  {
    "title": "Kurzer Aufgabentitel (max 80 Zeichen)",
    "description": "Detaillierte Beschreibung",
    "due_date": "YYYY-MM-DD oder null",
    "assignee": "Name der zuständigen Person oder null"
  }
]"""

    user_prompt = f"""Extrahiere alle Aufgaben aus diesem Protokoll:

{protocol_text}"""

    try:
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ])

        content = response.content

        tasks_json = None
        start_match = re.search(r'\[\s*[\{\]]', content)
        if start_match:
            start = start_match.start()
            depth = 0
            in_string = False
            escape_next = False
            for i in range(start, len(content)):
                c = content[i]
                if escape_next:
                    escape_next = False
                    continue
                if c == '\\' and in_string:
                    escape_next = True
                    continue
                if c == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if c == '[':
                    depth += 1
                elif c == ']':
                    depth -= 1
                    if depth == 0:
                        tasks_json = content[start:i + 1]
                        break

        if tasks_json:
            tasks = json.loads(tasks_json)
            return tasks
        else:
            return []

    except Exception as e:
        raise RuntimeError(f"Fehler beim Extrahieren der Tasks aus Protokoll: {e}") from e


def sanitize_filename(name: str, max_length: int = 100) -> str:
    """
    Bereinigt einen String für die Verwendung als Dateinamen.

    Args:
        name: Zu bereinigender String
        max_length: Maximale Länge des Dateinamens

    Returns:
        Bereinigter String
    """
    import re

    invalid_chars = '<>:"/\\|?*\n\r\t'
    for char in invalid_chars:
        name = name.replace(char, '')

    name = name.replace(' ', '_')

    while '__' in name:
        name = name.replace('__', '_')

    name = name.strip('_')

    if len(name) > max_length:
        name = name[:max_length].strip('_')

    return name


def convert_markdown_to_pdf(markdown_file: Path, output_pdf: Path) -> bool:
    """
    Konvertiert eine Markdown-Datei zu PDF.

    Args:
        markdown_file: Pfad zur .md Datei
        output_pdf: Pfad für die Ausgabe-PDF

    Returns:
        True bei Erfolg, False bei Fehler
    """
    try:
        import markdown
        from weasyprint import HTML, CSS

        with open(markdown_file, 'r', encoding='utf-8') as f:
            md_content = f.read()

        html_content = markdown.markdown(md_content, extensions=['extra', 'nl2br'])

        full_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    line-height: 1.6;
                    max-width: 800px;
                    margin: 40px auto;
                    padding: 20px;
                    color: #333;
                }}
                h1 {{
                    color: #2c3e50;
                    border-bottom: 2px solid #3498db;
                    padding-bottom: 10px;
                }}
                h2 {{
                    color: #34495e;
                    margin-top: 30px;
                }}
                h3 {{
                    color: #7f8c8d;
                }}
                strong {{
                    color: #2c3e50;
                }}
                ul, ol {{
                    margin-left: 20px;
                }}
                hr {{
                    border: none;
                    border-top: 1px solid #ddd;
                    margin: 20px 0;
                }}
            </style>
        </head>
        <body>
            {html_content}
        </body>
        </html>
        """

        HTML(string=full_html).write_pdf(output_pdf)
        return True

    except Exception as e:
        st.error(f"Fehler bei PDF-Konvertierung: {e}")
        return False


def generate_agenda_with_asana_context(
    asana_agent,
    project_gid: str,
    meeting_title: str,
    meeting_description: str = ""
) -> str:
    """
    Generiert eine Agenda mit Kontext aus Asana (Agenda-Items und offene Protokollpunkte).

    Args:
        asana_agent: AsanaAgent-Instanz
        project_gid: Asana-Projekt-GID
        meeting_title: Titel des Meetings
        meeting_description: Optionale Meeting-Beschreibung

    Returns:
        Formatierte Agenda als Markdown-String
    """
    date_str = datetime.now().strftime("%d.%m.%Y")

    agenda_section_gid = asana_agent.ensure_section_exists(project_gid, "Agenda")
    protocol_section_gid = asana_agent.ensure_section_exists(project_gid, "Protokolle")

    agenda_content = f"""# Agenda: {meeting_title}
**Datum:** {date_str}

⚠️ **Keine Besprechung ohne Protokoll - Aufzeichnung aktivieren!**

---

"""

    if meeting_description:
        agenda_content += f"""## Kontext
{meeting_description}

---

"""

    top_number = 1

    if protocol_section_gid:
        open_protocols = asana_agent.find_protocol_tasks_with_open_items(
            project_gid=project_gid,
            protocol_section_name="Protokolle"
        )

        if open_protocols:
            agenda_content += f"""## TOP {top_number}: Rückblick - Offene Punkte aus vorherigen Besprechungen

"""
            for protocol in open_protocols:
                protocol_name_short = protocol['protocol_name'].replace('📄 Protokoll ', '')
                for item in protocol['open_items']:
                    assignee_str = f" - Zuständig: {item['assignee']}" if item['assignee'] else ""
                    due_str = f" - Fällig: {item['due_on']}" if item['due_on'] else ""
                    agenda_content += f"""- [ ] {item['name']}{assignee_str}{due_str} *(aus {protocol_name_short})*
"""

            agenda_content += "\n---\n\n"
            top_number += 1

    if agenda_section_gid:
        agenda_items = asana_agent.get_tasks_from_section(
            section_gid=agenda_section_gid,
            limit=50,
            include_completed=False
        )

        if agenda_items:
            agenda_content += """## 📝 Tagesordnungspunkte

"""
            for item in agenda_items:
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

    return agenda_content


def extract_protocol_from_transcript_streaming(
    transcript_text: str,
    meeting_title: str,
    llm,
    attendees: Optional[List[str]] = None,
    meeting_date: Optional[str] = None,
    agenda_text: Optional[str] = None,
):
    """Generiert ein Besprechungsprotokoll aus einem Transkript via LLM-Streaming.

    Yields einzelne Text-Chunks (str) des Protokolls zur Live-Anzeige.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    attendees_str = ""
    if attendees:
        attendees_str = f"\n**Teilnehmer:** {', '.join(attendees)}"

    date_str = ""
    if meeting_date:
        date_str = f"\n**Datum:** {meeting_date}"

    agenda_section = ""
    if agenda_text:
        agenda_section = f"\n\n## Tagesordnung\n{agenda_text}"

    system_prompt = """Du bist ein professioneller Protokollant für die Herbert Gruppe.
Erstelle aus dem folgenden Meeting-Transkript ein strukturiertes Besprechungsprotokoll auf Deutsch.

Struktur:
1. Kopfdaten (Titel, Datum, Teilnehmer)
2. Zusammenfassung (2-4 Sätze, Kernaussage des Meetings)
3. Besprochene Themen — pro Themenblock:
   - Kurzer Kontext (so viel Detail wie nötig für das Verständnis der Entscheidung — alle Fakten, Zahlen und Beispiele aus dem Transkript)
   - **Entscheidungen:** direkt unter dem Thema (Bullet-Liste, nur wenn Beschlüsse gefallen)
   - **Aufgaben:** direkt unter dem Thema im Format: - Vorname Nachname: Aufgabe [Fälligkeitsdatum falls erwähnt]
4. Offene Punkte (Sammelblock am Ende, nur falls vorhanden)

Regeln:
- Vollständig — kein Längen-Limit, die Länge folgt dem Inhalt
- Inline-pro-TOP-Stil: KEINE separaten Sammelblöcke "Entscheidungen" oder "Aufgaben" am Ende — alles steht direkt unter dem jeweiligen Thema
- Smalltalk, Begrüßung und Verabschiedung weglassen
- Diskussionsdetails dürfen rein, wenn sie für das Verständnis der Entscheidung wichtig sind
- Personen immer mit Vor- und Nachname nennen (auch in Aufgaben)
- Keine Zeitstempel oder Sprecher-Labels aus dem Transkript wiedergeben
- Fachbegriffe und Zahlen 1:1 aus dem Transkript übernehmen
- Markdown-Formatierung
- Sprache: Deutsch"""

    user_content = f"""# Meeting: {meeting_title}{date_str}{attendees_str}{agenda_section}

## Transkript
{transcript_text}

---
Erstelle jetzt das strukturierte Besprechungsprotokoll:"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]

    for chunk in llm.stream(messages):
        if hasattr(chunk, "content") and chunk.content:
            yield chunk.content


# ---------------------------------------------------------------------------
# Asana-Integration
# ---------------------------------------------------------------------------


def create_protocol_task_in_asana(
    asana_agent,
    project_gid: str,
    meeting_title: str,
    protocol_text: str,
    protocol_file_path: Optional[Path] = None,
    pdf_file_path: Optional[Path] = None,
    outlook_event_id: Optional[str] = None,
    outlook_tool=None
) -> Dict[str, Any]:
    """
    Erstellt eine zentrale Protokoll-Aufgabe in Asana mit optionalem PDF-Anhang
    und verschiebt sie automatisch in den "Protokolle"-Section.

    Args:
        asana_agent: AsanaAgent-Instanz
        project_gid: Asana-Projekt-GID
        meeting_title: Titel des Meetings
        protocol_text: Vollständiger Protokoll-Text
        protocol_file_path: Optional - Pfad zur .md Datei
        pdf_file_path: Optional - Pfad zur PDF-Datei zum Anhängen
        outlook_event_id: Optional - Outlook Event-ID für Kategorie-Zuweisung
        outlook_tool: Optional - OutlookGraphTool-Instanz

    Returns:
        Result-Dict mit success, task_gid, permalink_url und ggf. error
    """
    try:
        date_str = datetime.now().strftime("%Y-%m-%d")
        task_title = f"📄 Protokoll {date_str} - {meeting_title}"

        protocol_section_gid = asana_agent.ensure_section_exists(project_gid, "Protokolle")

        result = asana_agent.create_task(
            name=task_title,
            notes=protocol_text,
            project_gid=project_gid
        )

        if not result.get('success'):
            return result

        task_gid = result.get('task_gid')

        if protocol_section_gid:
            section_result = asana_agent.add_task_to_section(
                task_gid=task_gid,
                section_gid=protocol_section_gid
            )
            if section_result.get('success'):
                print(f"[create_protocol_task_in_asana] ✓ Aufgabe in 'Protokolle'-Section verschoben")
            else:
                print(f"[create_protocol_task_in_asana] ⚠️ Konnte Aufgabe nicht in Section verschieben: {section_result.get('error')}")

        if pdf_file_path and pdf_file_path.exists():
            attachment_result = asana_agent.attach_file_to_task(
                task_gid=task_gid,
                file_path=str(pdf_file_path)
            )
            if not attachment_result.get('success'):
                print(f"[create_protocol_task_in_asana] ⚠️ PDF-Anhang fehlgeschlagen: {attachment_result.get('error')}")

        if outlook_event_id and outlook_tool:
            try:
                category_result = outlook_tool.add_category_to_event(
                    event_id=outlook_event_id,
                    category="Protokoll"
                )
                if category_result.get('success'):
                    print(f"[create_protocol_task_in_asana] ✓ Kategorie 'Protokoll' zum Termin hinzugefügt")
                else:
                    print(f"[create_protocol_task_in_asana] ⚠️ Kategorie-Zuweisung fehlgeschlagen: {category_result.get('error')}")
            except Exception as e:
                print(f"[create_protocol_task_in_asana] ⚠️ Fehler beim Setzen der Kategorie: {e}")

            try:
                prefix_result = outlook_tool.add_protocol_subject_prefix(event_id=outlook_event_id)
                if prefix_result.get('success'):
                    print(f"[create_protocol_task_in_asana] ✓ Betreff-Prefix '📄 ' gesetzt")
                else:
                    print(f"[create_protocol_task_in_asana] ⚠️ Betreff-Prefix fehlgeschlagen: {prefix_result.get('error')}")
            except Exception as e:
                print(f"[create_protocol_task_in_asana] ⚠️ Fehler beim Setzen des Betreff-Prefix: {e}")

        return {
            'success': True,
            'task_gid': task_gid,
            'task_name': result.get('task_name'),
            'permalink_url': result.get('permalink_url', ''),
            'message': f'Protokoll-Aufgabe erstellt: {task_title}'
        }

    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


def load_agenda_templates() -> List[Dict[str, Any]]:
    """
    Lädt Agenda-Vorlagen aus JSON-Datei.

    Returns:
        Liste von Template-Dictionaries
    """
    from utils.state import _get_user_ctx
    template_file = _get_user_ctx().data_dir / "agenda_templates.json"

    if not template_file.exists():
        template_file.parent.mkdir(parents=True, exist_ok=True)
        with open(template_file, 'w', encoding='utf-8') as f:
            import json
            json.dump({"templates": []}, f, ensure_ascii=False, indent=2)
        return []

    try:
        with open(template_file, 'r', encoding='utf-8') as f:
            import json
            data = json.load(f)
            return data.get('templates', [])
    except Exception as e:
        print(f"[load_agenda_templates] Fehler beim Laden: {e}")
        return []


def save_agenda_templates(templates: List[Dict[str, Any]]) -> bool:
    """
    Speichert Agenda-Vorlagen in JSON-Datei.

    Args:
        templates: Liste von Template-Dictionaries

    Returns:
        True bei Erfolg, False bei Fehler
    """
    from utils.state import _get_user_ctx
    template_file = _get_user_ctx().data_dir / "agenda_templates.json"
    template_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        if template_file.exists():
            backup_file = template_file.with_suffix('.json.backup')
            shutil.copy2(template_file, backup_file)

        with open(template_file, 'w', encoding='utf-8') as f:
            import json
            json.dump({"templates": templates}, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[save_agenda_templates] Fehler beim Speichern: {e}")
        return False


def create_agenda_from_template(
    template: Dict[str, Any],
    meeting_title: str,
    asana_data: Optional[Dict[str, Any]] = None,
    combine_with_asana: bool = False
) -> str:
    """
    Erstellt eine Agenda aus einer Vorlage.

    Args:
        template: Template-Dictionary mit Sections
        meeting_title: Titel des Meetings
        asana_data: Optional - Daten aus Asana (open_protocols, agenda_items)
        combine_with_asana: Ob Asana-Daten integriert werden sollen

    Returns:
        Agenda als Markdown-String
    """
    date_str = datetime.now().strftime("%d.%m.%Y")

    agenda_content = f"""# Agenda: {meeting_title}
**Datum:** {date_str}

⚠️ **Keine Besprechung ohne Protokoll - Aufzeichnung aktivieren!**

---

"""

    top_number = 1

    if combine_with_asana and asana_data and asana_data.get('open_protocols'):
        agenda_content += f"""## TOP {top_number}: Rückblick - Offene Punkte aus vorherigen Besprechungen

"""
        for protocol in asana_data['open_protocols']:
            protocol_name_short = protocol['protocol_name'].replace('📄 Protokoll ', '')
            for item in protocol['open_items']:
                assignee_str = f" - Zuständig: {item['assignee']}" if item['assignee'] else ""
                due_str = f" - Fällig: {item['due_on']}" if item['due_on'] else ""
                agenda_content += f"""- [ ] {item['name']}{assignee_str}{due_str} *(aus {protocol_name_short})*
"""

        agenda_content += "\n---\n\n"
        top_number += 1

    if template.get('sections'):
        agenda_content += """## 📝 Tagesordnungspunkte

"""
        for section in template['sections']:
            agenda_content += f"""### TOP {top_number}: {section['title']}
"""
            if section.get('content'):
                agenda_content += f"""{section['content']}

"""
            agenda_content += "\n"
            top_number += 1

        agenda_content += "---\n\n"

    if combine_with_asana and asana_data and asana_data.get('agenda_items'):
        for item in asana_data['agenda_items']:
            assignee_str = f" (Themenverantwortlich: {item['assignee']})" if item['assignee'] else ""
            agenda_content += f"""### TOP {top_number}: {item['name']}{assignee_str}
"""
            if item['notes']:
                agenda_content += f"""{item['notes']}

"""
            agenda_content += "\n"
            top_number += 1

        agenda_content += "---\n\n"

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

    return agenda_content
