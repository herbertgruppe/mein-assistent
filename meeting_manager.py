"""
Meeting Manager - Automatische Verarbeitung von Meeting-Transkripten

Dieses Modul überwacht den Ordner transcripts/incoming auf neue Transkript-Dateien,
ermittelt den zugehörigen Outlook-Termin über die Microsoft Graph API und benennt
die Dateien entsprechend um.

Funktionen:
- Ordnerüberwachung mit watchdog
- Metadaten-Extraktion (Erstellungsdatum/-zeit)
- Microsoft Graph API Integration für Kalenderzugriff
- LLM-basierte Titel-Generierung als Fallback
- Automatisches Verschieben und Umbenennen
"""

import os
import sys
import time
import shutil
import re
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
import logging
from difflib import SequenceMatcher

# Watchdog für Ordnerüberwachung
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# LangChain für LLM
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

# PDF-Extraktion
from langchain_community.document_loaders import PyPDFLoader

# PDF-Metadaten
try:
    from pypdf import PdfReader
except ImportError:
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        PdfReader = None

# Bestehende Tools importieren
from tools.outlook_graph_tool import OutlookGraphTool

# Logging konfigurieren
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class MeetingManager:
    """
    Hauptklasse für das Management von Meeting-Transkripten.

    Nutzt die bestehende MSAL-Authentifizierung über OutlookGraphTool
    und LLM-Integration analog zu anderen Agenten in der Codebase.
    """

    def __init__(
        self,
        incoming_dir: str = "transcripts/incoming",
        processed_dir: str = "transcripts/processed",
        llm_provider: Optional[str] = None,
        llm_model: Optional[str] = None
    ):
        """
        Initialisiert den Meeting Manager.

        Args:
            incoming_dir: Pfad zum Überwachungsordner
            processed_dir: Pfad zum Zielordner für verarbeitete Dateien
            llm_provider: LLM-Provider ('anthropic' oder 'openai'), Standard aus .env
            llm_model: LLM-Modell, Standard aus .env
        """
        self.incoming_dir = Path(incoming_dir)
        self.processed_dir = Path(processed_dir)

        # Ordner erstellen falls nicht vorhanden
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)

        # LLM-Provider aus .env oder Parameter
        self.llm_provider = llm_provider or os.getenv("LLM_PROVIDER", "anthropic")
        self.llm_model = llm_model or os.getenv("RESEARCH_MODEL", "claude-sonnet-4-5")

        # Outlook Graph Tool initialisieren (nutzt bestehende MSAL-Auth)
        logger.info("Initialisiere Microsoft Graph API Verbindung...")
        self.outlook_tool = OutlookGraphTool()

        # Asana Agent initialisieren
        try:
            from agents.asana_agent import AsanaAgent
            self.asana_agent = AsanaAgent()
            logger.info("Asana Agent initialisiert")
        except Exception as e:
            logger.warning(f"Asana Agent konnte nicht initialisiert werden: {e}")
            self.asana_agent = None

        # LLM initialisieren
        logger.info(f"Initialisiere LLM ({self.llm_provider}/{self.llm_model})...")
        self.llm = self._initialize_llm()

        # Mapping-Konfiguration laden
        self.mapping_config_path = Path("config/mapping_config.json")
        self.project_mappings = self._load_project_mappings()

        logger.info("Meeting Manager erfolgreich initialisiert")

    def _initialize_llm(self):
        """
        Initialisiert den LLM analog zu anderen Agenten in der Codebase.

        Returns:
            ChatAnthropic oder ChatOpenAI Instanz
        """
        temperature = float(os.getenv("TEMPERATURE", "0.7"))
        max_tokens = int(os.getenv("MAX_TOKENS", "4000"))

        if self.llm_provider == "anthropic":
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY nicht in .env gefunden")

            return ChatAnthropic(
                api_key=api_key,
                model=self.llm_model,
                temperature=temperature,
                max_tokens=max_tokens
            )
        elif self.llm_provider == "openai":
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY nicht in .env gefunden")

            return ChatOpenAI(
                api_key=api_key,
                model_name=self.llm_model,
                temperature=temperature,
                max_tokens=max_tokens
            )
        else:
            raise ValueError(f"Unbekannter LLM Provider: {self.llm_provider}")

    def _load_project_mappings(self) -> Dict[str, Any]:
        """
        Lädt die Projekt-Mappings aus der Konfigurations-Datei.

        Returns:
            Dict mit Projekt-Mappings oder leeres Dict bei Fehler
        """
        try:
            if not self.mapping_config_path.exists():
                logger.warning(f"Mapping-Konfiguration nicht gefunden: {self.mapping_config_path}")
                return {}

            with open(self.mapping_config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                logger.info(f"✓ Projekt-Mappings geladen: {len(config.get('project_mappings', {}))} Einträge")
                return config.get('project_mappings', {})

        except Exception as e:
            logger.error(f"Fehler beim Laden der Mapping-Konfiguration: {e}")
            return {}

    def _fuzzy_match_score(self, text1: str, text2: str) -> float:
        """
        Berechnet Ähnlichkeits-Score zwischen zwei Strings (0.0 - 1.0).

        Args:
            text1: Erster String
            text2: Zweiter String

        Returns:
            Ähnlichkeits-Score (0.0 = keine Übereinstimmung, 1.0 = perfekte Übereinstimmung)
        """
        return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()

    def get_asana_project_context(
        self,
        meeting_title: str,
        fuzzy_threshold: float = 0.6
    ) -> Optional[Dict[str, Any]]:
        """
        Ermittelt den passenden Asana-Projekt-Kontext für einen Meeting-Titel.

        Workflow:
        1. Suche in mapping_config.json nach Keywords
        2. Falls nicht gefunden: Fuzzy-Suche über alle Asana-Projekte
        3. Hole offene Tasks des gefundenen Projekts

        Args:
            meeting_title: Titel des Meetings
            fuzzy_threshold: Schwellwert für Fuzzy-Match (0.0 - 1.0)

        Returns:
            Dict mit:
            - project_gid: Asana-Projekt-GID
            - project_name: Projekt-Name
            - open_tasks: Liste offener Tasks
            oder None wenn kein Projekt gefunden
        """
        if not self.asana_agent:
            logger.warning("Asana Agent nicht verfügbar")
            return None

        logger.info(f"Suche Asana-Projekt-Kontext für Meeting: '{meeting_title}'")

        # 1. Suche in Mapping-Konfiguration
        meeting_title_lower = meeting_title.lower()

        for mapping_key, mapping_data in self.project_mappings.items():
            keywords = mapping_data.get('keywords', [])

            for keyword in keywords:
                if keyword.lower() in meeting_title_lower:
                    project_gid = mapping_data.get('asana_project_gid')

                    if project_gid:
                        logger.info(f"✓ Projekt via Mapping gefunden: {mapping_key} (GID: {project_gid})")

                        # Hole offene Tasks
                        try:
                            tasks = self.asana_agent.get_project_tasks(project_gid, limit=20)
                            # Filtere nur incomplete Tasks
                            open_tasks = [t for t in tasks if not t.get('completed', False)]

                            logger.info(f"✓ {len(open_tasks)} offene Tasks gefunden")

                            return {
                                'project_gid': project_gid,
                                'project_name': mapping_data.get('description', mapping_key),
                                'open_tasks': open_tasks,
                                'match_type': 'keyword'
                            }
                        except Exception as e:
                            logger.error(f"Fehler beim Laden der Tasks für Projekt {project_gid}: {e}")
                            return {
                                'project_gid': project_gid,
                                'project_name': mapping_data.get('description', mapping_key),
                                'open_tasks': [],
                                'match_type': 'keyword',
                                'error': str(e)
                            }

        # 2. Fuzzy-Suche über alle Asana-Projekte
        logger.info("Kein Keyword-Match - starte Fuzzy-Suche...")

        try:
            all_projects = self.asana_agent.list_projects()

            best_match = None
            best_score = 0.0

            for project in all_projects:
                project_name = project.get('name', '')
                score = self._fuzzy_match_score(meeting_title, project_name)

                if score > best_score:
                    best_score = score
                    best_match = project

            if best_match and best_score >= fuzzy_threshold:
                project_gid = best_match['gid']
                project_name = best_match['name']

                logger.info(f"✓ Projekt via Fuzzy-Match gefunden: {project_name} (Score: {best_score:.2f})")

                # Hole offene Tasks
                tasks = self.asana_agent.get_project_tasks(project_gid, limit=20)
                open_tasks = [t for t in tasks if not t.get('completed', False)]

                logger.info(f"✓ {len(open_tasks)} offene Tasks gefunden")

                return {
                    'project_gid': project_gid,
                    'project_name': project_name,
                    'open_tasks': open_tasks,
                    'match_type': 'fuzzy',
                    'match_score': best_score
                }
            else:
                logger.info(f"Kein passendes Projekt gefunden (bester Score: {best_score:.2f})")
                return None

        except Exception as e:
            logger.error(f"Fehler bei Fuzzy-Suche: {e}")
            return None

    def extract_text_from_pdf(self, file_path: Path) -> str:
        """
        Extrahiert Text aus einer PDF-Datei.

        Args:
            file_path: Pfad zur PDF-Datei

        Returns:
            Extrahierter Text
        """
        try:
            logger.info(f"Extrahiere Text aus PDF: {file_path.name}")
            loader = PyPDFLoader(str(file_path))
            pages = loader.load()

            # Kombiniere alle Seiten
            full_text = "\n\n".join([page.page_content for page in pages])
            logger.info(f"✓ {len(full_text)} Zeichen aus {len(pages)} Seite(n) extrahiert")

            return full_text

        except Exception as e:
            logger.error(f"Fehler beim Lesen der PDF: {e}")
            return ""

    def extract_datetime_from_content(self, content: str) -> Optional[datetime]:
        """
        Extrahiert Datum und Uhrzeit aus dem Transkript-Inhalt.

        Sucht nach Mustern wie:
        - 2026-01-26 10:03:15
        - 2026-01-26 10:03
        - 26.01.2026 10:03

        Args:
            content: Transkript-Inhalt (Text)

        Returns:
            datetime-Objekt oder None wenn nicht gefunden
        """
        # Regex-Muster für verschiedene Datums-/Zeitformate
        patterns = [
            # ISO-Format: 2026-01-26 10:03:15 oder 2026-01-26 10:03
            r'(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})(?::(\d{2}))?',
            # DE-Format: 26.01.2026 10:03:15 oder 26.01.2026 10:03
            r'(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})(?::(\d{2}))?',
            # US-Format: 01/26/2026 10:03:15
            r'(\d{2})/(\d{2})/(\d{4})\s+(\d{2}):(\d{2})(?::(\d{2}))?',
        ]

        # Suche in den ersten 1000 Zeichen (sollte am Anfang stehen)
        search_content = content[:1000]

        for pattern in patterns:
            match = re.search(pattern, search_content)
            if match:
                groups = match.groups()

                try:
                    # ISO-Format: YYYY-MM-DD HH:MM(:SS)?
                    if '-' in match.group(0):
                        year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
                        hour, minute = int(groups[3]), int(groups[4])
                        second = int(groups[5]) if groups[5] else 0
                    # DE-Format: DD.MM.YYYY HH:MM(:SS)?
                    elif '.' in match.group(0):
                        day, month, year = int(groups[0]), int(groups[1]), int(groups[2])
                        hour, minute = int(groups[3]), int(groups[4])
                        second = int(groups[5]) if groups[5] else 0
                    # US-Format: MM/DD/YYYY HH:MM(:SS)?
                    elif '/' in match.group(0):
                        month, day, year = int(groups[0]), int(groups[1]), int(groups[2])
                        hour, minute = int(groups[3]), int(groups[4])
                        second = int(groups[5]) if groups[5] else 0
                    else:
                        continue

                    dt = datetime(year, month, day, hour, minute, second)
                    logger.info(f"✓ Datum/Zeit aus Inhalt extrahiert: {dt}")
                    return dt

                except (ValueError, IndexError) as e:
                    logger.warning(f"Ungültiges Datum/Zeit-Format gefunden: {match.group(0)}")
                    continue

        logger.warning("Kein Datum/Zeit-Muster im Inhalt gefunden")
        return None

    def get_file_creation_time(self, file_path: Path) -> datetime:
        """
        Liest das Erstellungsdatum und die Uhrzeit einer Datei aus.

        Args:
            file_path: Pfad zur Datei

        Returns:
            datetime-Objekt mit Erstellungszeitpunkt
        """
        # Unter Linux: st_mtime (Änderungszeit) ist oft aussagekräftiger als st_ctime
        # Unter Windows: st_ctime ist Erstellungszeit
        stat = file_path.stat()

        # Nutze das ältere der beiden Timestamps
        timestamp = min(stat.st_ctime, stat.st_mtime)
        creation_time = datetime.fromtimestamp(timestamp)

        logger.info(f"Datei {file_path.name} erstellt am: {creation_time}")
        return creation_time

    def extract_pdf_creation_date(self, file_path: Path) -> Optional[datetime]:
        """
        Extrahiert das Erstellungsdatum aus den PDF-Metadaten.

        Args:
            file_path: Pfad zur PDF-Datei

        Returns:
            datetime-Objekt mit PDF-Erstellungsdatum oder None
        """
        if PdfReader is None:
            logger.warning("PyPDF2/pypdf nicht installiert, kann PDF-Metadaten nicht lesen")
            return None

        try:
            reader = PdfReader(str(file_path))
            metadata = reader.metadata

            if metadata and metadata.get('/CreationDate'):
                creation_date_str = metadata.get('/CreationDate')

                # PDF-Datum-Format: D:YYYYMMDDHHmmSSOHH'mm'
                # Beispiel: D:20260126100315+01'00'
                # Entferne das führende "D:" falls vorhanden
                if creation_date_str.startswith('D:'):
                    creation_date_str = creation_date_str[2:]

                # Parse das Datum
                # Format: YYYYMMDDHHmmSS mit optionalem Timezone-Suffix
                year = int(creation_date_str[0:4])
                month = int(creation_date_str[4:6])
                day = int(creation_date_str[6:8])
                hour = int(creation_date_str[8:10]) if len(creation_date_str) >= 10 else 0
                minute = int(creation_date_str[10:12]) if len(creation_date_str) >= 12 else 0
                second = int(creation_date_str[12:14]) if len(creation_date_str) >= 14 else 0

                pdf_datetime = datetime(year, month, day, hour, minute, second)
                logger.info(f"✓ PDF-Erstellungsdatum aus Metadaten: {pdf_datetime}")
                return pdf_datetime

        except Exception as e:
            logger.warning(f"Fehler beim Lesen der PDF-Metadaten: {e}")

        return None

    def get_transcript_datetime(
        self,
        file_path: Path,
        user_provided_datetime: Optional[datetime] = None
    ) -> Tuple[datetime, str]:
        """
        Ermittelt das Datum/Zeit für ein Transkript mit Fallback-Strategie.

        Priorität:
        1. User-provided datetime (aus UI)
        2. Datum/Zeit aus Transkript-Inhalt (Text-Suche) - zuverlässigste Quelle!
        3. PDF-Metadaten (CreationDate) - nur als Fallback, oft unzuverlässig
        4. Datei-Metadaten (Erstellungszeit/Ablagedatum)

        Args:
            file_path: Pfad zur Transkript-Datei
            user_provided_datetime: Optional vom User eingegebenes Datum/Zeit

        Returns:
            Tuple: (datetime, Quelle als String)
        """
        # 1. User-provided datetime hat höchste Priorität
        if user_provided_datetime:
            logger.info(f"Nutze user-provided datetime: {user_provided_datetime}")
            return user_provided_datetime, "user_input"

        # 2. Versuche Datum/Zeit aus Inhalt zu extrahieren (HÖCHSTE PRIORITÄT!)
        try:
            # Text extrahieren (PDF oder TXT)
            if file_path.suffix.lower() == '.pdf':
                content = self.extract_text_from_pdf(file_path)
            else:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read(1000)  # Nur Anfang lesen

            content_datetime = self.extract_datetime_from_content(content)
            if content_datetime:
                return content_datetime, "content"
        except Exception as e:
            logger.warning(f"Fehler beim Extrahieren von Datum/Zeit aus Inhalt: {e}")

        # 3. Fallback für PDFs: Versuche PDF-Metadaten auszulesen
        # Achtung: PDF-Metadaten enthalten oft das Generierungsdatum, nicht das Meeting-Datum!
        if file_path.suffix.lower() == '.pdf':
            pdf_datetime = self.extract_pdf_creation_date(file_path)
            if pdf_datetime:
                logger.warning(f"Fallback auf PDF-Metadaten (möglicherweise Generierungsdatum): {pdf_datetime}")
                return pdf_datetime, "pdf_metadata"

        # 4. Letzter Fallback: Datei-Metadaten (Ablagedatum)
        file_datetime = self.get_file_creation_time(file_path)
        logger.warning(f"Fallback auf Datei-Metadaten (Ablagedatum): {file_datetime}")
        return file_datetime, "file_metadata"

    def find_meeting_at_time(
        self,
        target_time: datetime,
        tolerance_minutes: int = 15
    ) -> Optional[Dict[str, Any]]:
        """
        Sucht in Outlook nach einem Meeting zum angegebenen Zeitpunkt.

        Nutzt die bestehende OutlookGraphTool mit Graph API Integration.

        Args:
            target_time: Zielzeitpunkt für die Suche
            tolerance_minutes: Toleranz in Minuten (Meeting darf ±X Minuten abweichen)

        Returns:
            Dict mit Meeting-Informationen oder None wenn kein Meeting gefunden
        """
        logger.info(f"Suche Meeting um {target_time} (±{tolerance_minutes} Minuten)...")

        try:
            # Erweiterten Zeitraum abrufen (kompletter Tag)
            start_of_day = target_time.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = target_time.replace(hour=23, minute=59, second=59, microsecond=999999)

            # Graph API Aufruf über bestehende OutlookGraphTool
            events = self.outlook_tool.get_events_for_date_range(
                start_date=start_of_day,
                end_date=end_of_day
            )

            if not events:
                logger.warning(f"Keine Events am {target_time.date()} gefunden")
                return None

            # Events nach zeitlicher Nähe zum target_time filtern
            tolerance = timedelta(minutes=tolerance_minutes)

            for event in events:
                event_start = event.get("start")
                event_end = event.get("end")

                # Prüfe ob target_time innerhalb des Meetings liegt oder in Toleranz
                if event_start and event_end:
                    # Zeitzone-aware Vergleich
                    if isinstance(event_start, str):
                        event_start = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
                    if isinstance(event_end, str):
                        event_end = datetime.fromisoformat(event_end.replace('Z', '+00:00'))

                    # Zeitzone entfernen für Vergleich (naive datetime)
                    if event_start.tzinfo:
                        event_start = event_start.replace(tzinfo=None)
                    if event_end.tzinfo:
                        event_end = event_end.replace(tzinfo=None)

                    # Prüfe ob target_time innerhalb des Meetings oder in Toleranz
                    if (event_start - tolerance <= target_time <= event_end + tolerance):
                        logger.info(f"Meeting gefunden: {event.get('title', 'Ohne Titel')}")
                        return event

            logger.warning(f"Kein Meeting in ±{tolerance_minutes} Minuten um {target_time} gefunden")
            return None

        except Exception as e:
            logger.error(f"Fehler beim Abrufen der Kalender-Events: {e}")
            return None

    def generate_title_from_transcript(
        self,
        transcript_path: Path,
        max_chars: int = 2000
    ) -> str:
        """
        Generiert einen aussagekräftigen Titel aus dem Transkript-Anfang per LLM.

        Args:
            transcript_path: Pfad zur Transkript-Datei
            max_chars: Maximale Anzahl Zeichen aus dem Transkript zu lesen

        Returns:
            Generierter Titel (max. 50 Zeichen)
        """
        logger.info(f"Generiere Titel für {transcript_path.name} mit LLM...")

        try:
            # Ersten Teil des Transkripts lesen
            with open(transcript_path, 'r', encoding='utf-8') as f:
                transcript_excerpt = f.read(max_chars)

            # LLM-Prompt für Titel-Generierung
            system_message = SystemMessage(content="""Du bist ein Assistent, der aus Meeting-Transkripten
prägnante Titel generiert. Analysiere den Anfang des Transkripts und erstelle einen kurzen,
aussagekräftigen Titel (maximal 50 Zeichen), der das Hauptthema zusammenfasst.

Regeln:
- Maximal 50 Zeichen
- Keine Sonderzeichen außer Bindestrich und Unterstrich
- Deutsch bevorzugt
- Fokus auf Hauptthema/Zweck des Meetings
- Keine Füllwörter

Beispiele:
- "Projektplanung_Q1_2024"
- "Kundenbesprechung_Firma_X"
- "Teammeeting_Produktentwicklung"
- "Budget_Review"
""")

            human_message = HumanMessage(content=f"""Transkript-Auszug:

{transcript_excerpt}

---
Generiere einen prägnanten Titel (max. 50 Zeichen):""")

            # LLM aufrufen
            response = self.llm.invoke([system_message, human_message])
            title = response.content.strip()

            # Titel bereinigen
            title = self._sanitize_filename(title, max_length=50)

            logger.info(f"Generierter Titel: {title}")
            return title

        except Exception as e:
            logger.error(f"Fehler bei Titel-Generierung: {e}")
            # Fallback: Nutze Dateinamen ohne Erweiterung
            return transcript_path.stem[:50]

    def _sanitize_filename(self, name: str, max_length: int = 100) -> str:
        """
        Bereinigt einen String für die Verwendung als Dateinamen.

        Args:
            name: Zu bereinigender String
            max_length: Maximale Länge

        Returns:
            Bereinigter String
        """
        # Entferne Anführungszeichen und problematische Zeichen
        invalid_chars = '<>:"/\\|?*\n\r\t'
        for char in invalid_chars:
            name = name.replace(char, '')

        # Ersetze Leerzeichen durch Unterstriche
        name = name.replace(' ', '_')

        # Entferne mehrfache Unterstriche
        while '__' in name:
            name = name.replace('__', '_')

        # Trimme auf maximale Länge
        name = name[:max_length].strip('_')

        return name

    def process_transcript(
        self,
        file_path: Path,
        user_provided_datetime: Optional[datetime] = None,
        user_selected_meeting: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Verarbeitet eine einzelne Transkript-Datei.

        Workflow:
        1. Datum/Zeit ermitteln (user input > content > file metadata)
        2. Meeting in Outlook suchen (oder user-selected verwenden)
        3. Falls nicht gefunden: LLM-Titel generieren
        4. Datei umbenennen und verschieben

        Args:
            file_path: Pfad zur zu verarbeitenden Datei
            user_provided_datetime: Optional vom User bereitgestelltes Datum/Zeit
            user_selected_meeting: Optional vom User ausgewähltes Meeting

        Returns:
            True bei Erfolg, False bei Fehler
        """
        logger.info(f"Verarbeite Transkript: {file_path.name}")

        metadata_file = None

        try:
            # Prüfe auf Metadaten-Datei vom Upload
            metadata_path = file_path.with_suffix(file_path.suffix + '.metadata.json')
            if metadata_path.exists():
                import json
                try:
                    with open(metadata_path, 'r', encoding='utf-8') as f:
                        metadata = json.load(f)
                        if not user_provided_datetime:
                            datetime_str = metadata.get('user_provided_datetime')
                            if datetime_str:
                                user_provided_datetime = datetime.fromisoformat(datetime_str)
                                logger.info(f"Lade user-provided datetime aus Metadaten: {user_provided_datetime}")
                        if not user_selected_meeting:
                            user_selected_meeting = metadata.get('selected_meeting')
                            if user_selected_meeting:
                                logger.info(f"Lade user-selected meeting aus Metadaten: {user_selected_meeting.get('title')}")
                        metadata_file = metadata_path
                except Exception as e:
                    logger.warning(f"Fehler beim Lesen der Metadaten: {e}")

            # 1. Datum/Zeit ermitteln (mit Fallback-Strategie)
            transcript_datetime, datetime_source = self.get_transcript_datetime(
                file_path,
                user_provided_datetime
            )
            logger.info(f"Datum/Zeit-Quelle: {datetime_source}")

            # 2. Meeting verwenden (Priorität: user-selected > automatisch gefunden)
            meeting = user_selected_meeting if user_selected_meeting else self.find_meeting_at_time(transcript_datetime)

            # 3. Titel bestimmen
            if meeting:
                # Meeting gefunden - nutze Meeting-Titel
                meeting_title = meeting.get('title', 'Unbekanntes_Meeting')
                title = self._sanitize_filename(meeting_title, max_length=100)
                logger.info(f"Nutze Meeting-Titel: {title}")
            else:
                # Kein Meeting gefunden - LLM-Generierung
                logger.info("Kein Meeting gefunden, generiere Titel mit LLM...")

                # Text für LLM extrahieren
                if file_path.suffix.lower() == '.pdf':
                    # Nutze bereits extrahierten Text wenn vorhanden
                    text_for_llm = self.extract_text_from_pdf(file_path)[:2000]
                else:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        text_for_llm = f.read(2000)

                # Temporäre TXT-Datei für LLM-Verarbeitung wenn PDF
                if file_path.suffix.lower() == '.pdf':
                    temp_txt = file_path.with_suffix('.tmp.txt')
                    with open(temp_txt, 'w', encoding='utf-8') as f:
                        f.write(text_for_llm)
                    title = self.generate_title_from_transcript(temp_txt)
                    temp_txt.unlink()  # Temp-Datei löschen
                else:
                    title = self.generate_title_from_transcript(file_path)

            # 4. Neuen Dateinamen erstellen: YYYY-MM-DD_Titel.ext
            date_str = transcript_datetime.strftime("%Y-%m-%d")
            file_extension = file_path.suffix
            new_filename = f"{date_str}_{title}{file_extension}"

            # 5. Datei verschieben
            destination = self.processed_dir / new_filename

            # Falls Datei bereits existiert, füge Nummer hinzu
            counter = 1
            while destination.exists():
                new_filename = f"{date_str}_{title}_{counter}{file_extension}"
                destination = self.processed_dir / new_filename
                counter += 1

            shutil.move(str(file_path), str(destination))
            logger.info(f"✓ Datei verschoben: {destination.name}")

            # Metadaten-Datei löschen falls vorhanden
            if metadata_file and metadata_file.exists():
                metadata_file.unlink()
                logger.info(f"✓ Metadaten-Datei gelöscht: {metadata_file.name}")

            return True

        except Exception as e:
            logger.error(f"Fehler bei Verarbeitung von {file_path.name}: {e}")
            # Metadaten-Datei auch bei Fehler löschen
            if metadata_file and metadata_file.exists():
                try:
                    metadata_file.unlink()
                except:
                    pass
            return False

    def process_existing_files(self):
        """
        Verarbeitet alle bereits vorhandenen Dateien im incoming-Ordner.
        """
        logger.info(f"Verarbeite vorhandene Dateien in {self.incoming_dir}...")

        files = list(self.incoming_dir.glob("*"))
        supported_files = [f for f in files if f.is_file() and f.suffix.lower() in ['.txt', '.md', '.text', '.pdf']]

        if not supported_files:
            logger.info("Keine Dateien zum Verarbeiten gefunden")
            return

        logger.info(f"Gefunden: {len(supported_files)} Datei(en)")

        for file_path in supported_files:
            self.process_transcript(file_path)
            # Kurze Pause zwischen Verarbeitungen
            time.sleep(1)

    def start_watching(self):
        """
        Startet die Ordnerüberwachung im Dauerbetrieb.

        Nutzt watchdog für Echtzeit-Überwachung.
        """
        logger.info(f"Starte Ordnerüberwachung: {self.incoming_dir}")

        # Erst vorhandene Dateien verarbeiten
        self.process_existing_files()

        # Event Handler erstellen
        event_handler = TranscriptEventHandler(self)

        # Observer erstellen und starten
        observer = Observer()
        observer.schedule(event_handler, str(self.incoming_dir), recursive=False)
        observer.start()

        logger.info("✓ Ordnerüberwachung aktiv. Drücke Ctrl+C zum Beenden.")

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Beende Ordnerüberwachung...")
            observer.stop()

        observer.join()
        logger.info("Ordnerüberwachung beendet")


class TranscriptEventHandler(FileSystemEventHandler):
    """
    Event Handler für watchdog - reagiert auf neue Dateien im Überwachungsordner.
    """

    def __init__(self, manager: MeetingManager):
        """
        Args:
            manager: MeetingManager-Instanz für die Verarbeitung
        """
        self.manager = manager
        super().__init__()

    def on_created(self, event):
        """
        Wird aufgerufen wenn eine neue Datei erstellt wird.

        Args:
            event: FileSystemEvent
        """
        if event.is_directory:
            return

        file_path = Path(event.src_path)

        # Nur unterstützte Dateien verarbeiten
        if file_path.suffix.lower() not in ['.txt', '.md', '.text', '.pdf']:
            logger.debug(f"Ignoriere nicht unterstützte Datei: {file_path.name}")
            return

        logger.info(f"Neue Datei erkannt: {file_path.name}")

        # Kurze Wartezeit, falls Datei noch geschrieben wird
        time.sleep(2)

        # Verarbeitung starten
        self.manager.process_transcript(file_path)


def main():
    """
    Hauptfunktion für direkten Aufruf des Skripts.
    """
    # .env laden (falls noch nicht geschehen)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        logger.warning("python-dotenv nicht installiert, verwende System-Umgebungsvariablen")

    # Manager erstellen und starten
    manager = MeetingManager()

    # Ordnerüberwachung starten
    manager.start_watching()


if __name__ == "__main__":
    main()
