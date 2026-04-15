"""
UserContext: Zentralisiert alle benutzerspezifischen Pfade und Credentials.

Jeder angemeldete User bekommt eine eigene Instanz, die in st.session_state['user_ctx'] gespeichert wird.
"""

from pathlib import Path
from typing import Optional
import json
import os


class UserContext:
    """Kapselt alle pfad- und credential-bezogenen Informationen eines Users."""

    def __init__(self, username: str):
        self.username = username
        self.base = Path(f"users/{username}")

        # Transkript-Verzeichnisse
        self.transcripts_incoming = self.base / "transcripts" / "incoming"
        self.transcripts_processed = self.base / "transcripts" / "processed"
        self.transcripts_archive = self.base / "transcripts" / "archive"
        self.protocols_dir = self.base / "transcripts" / "protocols"
        self.protocols_final = self.base / "transcripts" / "protocols_final"
        self.meeting_prep = self.base / "transcripts" / "meeting_prep"
        self.wip_dir = self.base / "transcripts" / "wip"
        self.protocol_cache = self.base / "transcripts" / "protocol_cache"

        # Andere Verzeichnisse
        self.input_docs = self.base / "input_docs"
        self.data_dir = self.base / "data"
        self.auth_dir = self.base / "auth"
        self.meetings_dir = self.base / "meetings"

    def ensure_dirs(self):
        """Erstellt alle notwendigen Verzeichnisse für den User."""
        dirs = [
            self.transcripts_incoming,
            self.transcripts_processed,
            self.transcripts_archive,
            self.protocols_dir,
            self.protocols_final,
            self.meeting_prep,
            self.wip_dir,
            self.protocol_cache,
            self.input_docs,
            self.data_dir,
            self.data_dir / "agendas",
            self.auth_dir,
            self.meetings_dir,
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

    # ---- Credential Properties ----

    @property
    def outlook_token_file(self) -> Path:
        """Pfad zur Outlook-Token-Datei des Users."""
        return self.auth_dir / "outlook_token.json"

    @property
    def asana_credentials_file(self) -> Path:
        """Pfad zur Asana-Credentials-Datei des Users."""
        return self.auth_dir / "asana_credentials.json"

    def get_asana_token(self) -> Optional[str]:
        """Lädt den Asana PAT des Users. Fallback auf .env."""
        if self.asana_credentials_file.exists():
            try:
                data = json.loads(self.asana_credentials_file.read_text(encoding='utf-8'))
                token = data.get('access_token', '').strip()
                if token:
                    return token
            except Exception:
                pass
        # Fallback: globaler .env-Wert
        return os.getenv("ASANA_ACCESS_TOKEN", "")

    def save_asana_token(self, token: str):
        """Speichert den Asana PAT des Users."""
        self.auth_dir.mkdir(parents=True, exist_ok=True)
        self.asana_credentials_file.write_text(
            json.dumps({"access_token": token}, indent=2),
            encoding='utf-8'
        )

    def has_outlook_token(self) -> bool:
        """Prüft ob ein Outlook-Token vorhanden ist."""
        return self.outlook_token_file.exists()

    def has_asana_token(self) -> bool:
        """Prüft ob ein Asana-Token vorhanden ist (per-user oder .env)."""
        return bool(self.get_asana_token())
