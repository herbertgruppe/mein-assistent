"""
Microsoft Graph API Tool für Outlook-Kalender Integration
"""

import os
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta


class OutlookGraphTool:
    """Tool für Microsoft Graph API - Outlook Kalender"""

    def __init__(self, token_file: str = None):
        """Initialisiert das Outlook Graph Tool.

        Args:
            token_file: Optionaler Pfad zur Token-Datei. Falls None, wird der
                        Default-Pfad 'auth/outlook_token.json' verwendet.
        """
        # Stelle sicher dass .env geladen ist
        self._ensure_env_loaded()

        # Lade Konfiguration aus .env
        self.client_id = os.getenv("MICROSOFT_CLIENT_ID")
        self.tenant_id = os.getenv("MICROSOFT_TENANT_ID")
        self.client_secret = os.getenv("MICROSOFT_CLIENT_SECRET")  # Optional für Server-Flow

        # Status
        self.is_configured = bool(self.client_id) and bool(self.tenant_id)
        self.access_token = None
        self.refresh_token = None

        if token_file:
            # Multi-User: Token-Pfad explizit übergeben
            os.makedirs(os.path.dirname(token_file), exist_ok=True)
            self.token_file = token_file
        else:
            # Legacy: Token in persistentem Verzeichnis speichern
            auth_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'auth')
            os.makedirs(auth_dir, exist_ok=True)
            self.token_file = os.path.join(auth_dir, "outlook_token.json")

        # Fallback: altes Token migrieren
        old_token = ".outlook_token.json"
        if os.path.exists(old_token) and not os.path.exists(self.token_file):
            import shutil
            shutil.move(old_token, self.token_file)

        # Versuche gespeichertes Token zu laden
        self._load_token_from_file()

        if not self.is_configured:
            print("[OutlookGraphTool] ⚠️ Microsoft Graph API nicht konfiguriert")
            print("[OutlookGraphTool] Benötigt: MICROSOFT_CLIENT_ID und MICROSOFT_TENANT_ID in .env")
        else:
            print(f"[OutlookGraphTool] ✓ Konfiguration gefunden")
            print(f"[OutlookGraphTool]   Client ID: {self.client_id[:8]}...")
            print(f"[OutlookGraphTool]   Tenant ID: {self.tenant_id[:8]}...")
            if self.access_token:
                print(f"[OutlookGraphTool] ✓ Token aus Datei geladen")

    def _ensure_env_loaded(self):
        """Stellt sicher dass .env-Variablen geladen sind"""
        # Versuche zuerst dotenv
        try:
            from dotenv import load_dotenv
            load_dotenv()
            return
        except ImportError:
            pass

        # Fallback: Lade .env manuell
        from pathlib import Path
        env_file = Path('.env')
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    # Überspringe Kommentare und leere Zeilen
                    if not line or line.startswith('#'):
                        continue
                    # Parse key=value
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        # Setze nur wenn noch nicht gesetzt
                        if key and not os.getenv(key):
                            os.environ[key] = value

    def _load_token_from_file(self):
        """Lädt gespeichertes Token aus Datei"""
        import json
        try:
            if os.path.exists(self.token_file):
                with open(self.token_file, 'r') as f:
                    data = json.load(f)
                    self.access_token = data.get('access_token')
                    self.refresh_token = data.get('refresh_token')
                    self.token_expires_at = data.get('expires_at', 0)

                    # Prüfe ob Token abgelaufen ist
                    from datetime import datetime
                    if self.token_expires_at < datetime.now().timestamp():
                        print("[OutlookGraphTool] Token abgelaufen, versuche automatischen Refresh...")
                        # Versuche automatischen Token-Refresh
                        if self.refresh_token:
                            success = self._refresh_access_token()
                            if not success:
                                print("[OutlookGraphTool] Token-Refresh fehlgeschlagen, erneute Authentifizierung erforderlich")
                                self.access_token = None
                        else:
                            print("[OutlookGraphTool] Kein Refresh-Token verfügbar, erneute Authentifizierung erforderlich")
                            self.access_token = None
        except Exception as e:
            print(f"[OutlookGraphTool] Fehler beim Laden des Tokens: {e}")

    def _save_token_to_file(self, token_response: Dict[str, Any]):
        """Speichert Token in Datei"""
        import json
        from datetime import datetime, timedelta

        try:
            # Berechne Ablaufzeit (normalerweise 1 Stunde)
            expires_in = token_response.get('expires_in', 3600)
            expires_at = (datetime.now() + timedelta(seconds=expires_in)).timestamp()

            data = {
                'access_token': token_response.get('access_token'),
                'refresh_token': token_response.get('refresh_token'),
                'expires_at': expires_at
            }

            with open(self.token_file, 'w') as f:
                json.dump(data, f)

            print("[OutlookGraphTool] Token gespeichert")
        except Exception as e:
            print(f"[OutlookGraphTool] Fehler beim Speichern des Tokens: {e}")

    def _refresh_access_token(self) -> bool:
        """
        Versucht das Access Token mit dem Refresh Token zu erneuern.

        Returns:
            True bei Erfolg, False bei Fehler
        """
        if not self.refresh_token:
            print("[OutlookGraphTool] Kein Refresh-Token verfügbar")
            return False

        try:
            import msal

            # Erstelle MSAL App
            if self.client_secret:
                app = msal.ConfidentialClientApplication(
                    self.client_id,
                    authority=f"https://login.microsoftonline.com/{self.tenant_id}",
                    client_credential=self.client_secret
                )
            else:
                app = msal.PublicClientApplication(
                    self.client_id,
                    authority=f"https://login.microsoftonline.com/{self.tenant_id}"
                )

            # Versuche Token-Refresh
            scopes = [
                "User.Read",
                "Calendars.ReadWrite",
                "Mail.ReadWrite",
                "Mail.Send"
            ]

            result = app.acquire_token_by_refresh_token(
                self.refresh_token,
                scopes=scopes
            )

            if "access_token" in result:
                print("[OutlookGraphTool] ✓ Token erfolgreich erneuert")
                self.access_token = result["access_token"]
                self.refresh_token = result.get("refresh_token", self.refresh_token)

                # Speichere neues Token
                self._save_token_to_file(result)
                return True
            else:
                error = result.get("error_description", result.get("error", "Unbekannter Fehler"))
                print(f"[OutlookGraphTool] ✗ Token-Refresh fehlgeschlagen: {error}")
                return False

        except Exception as e:
            print(f"[OutlookGraphTool] ✗ Fehler beim Token-Refresh: {e}")
            return False

    def _ensure_valid_token(self) -> bool:
        """
        Stellt sicher, dass ein gültiges Access Token vorhanden ist.
        Refresht das Token automatisch wenn es abgelaufen ist.

        Returns:
            True wenn Token gültig ist, False sonst
        """
        if not self.access_token:
            return False

        # Prüfe ob Token abgelaufen ist
        from datetime import datetime
        if hasattr(self, 'token_expires_at'):
            # Refresh 5 Minuten vor Ablauf
            if self.token_expires_at < (datetime.now().timestamp() + 300):
                print("[OutlookGraphTool] Token läuft bald ab, refreshe...")
                return self._refresh_access_token()

        return True

    def initiate_device_flow(self) -> Dict[str, Any]:
        """
        Initiiert den Device Code Flow und gibt die Informationen zurück

        Returns:
            Dict mit device_info oder error
        """
        if not self.is_configured:
            return {
                "success": False,
                "error": "Nicht konfiguriert. Client-ID und Tenant-ID fehlen in .env"
            }

        try:
            import msal

            # Erstelle MSAL App - verwende Confidential wenn Secret vorhanden
            if self.client_secret:
                print("[OutlookGraphTool] Verwende ConfidentialClientApplication (mit Secret)")
                app = msal.ConfidentialClientApplication(
                    client_id=self.client_id,
                    client_credential=self.client_secret,
                    authority=f"https://login.microsoftonline.com/{self.tenant_id}"
                )
            else:
                print("[OutlookGraphTool] Verwende PublicClientApplication (ohne Secret)")
                app = msal.PublicClientApplication(
                    client_id=self.client_id,
                    authority=f"https://login.microsoftonline.com/{self.tenant_id}"
                )

            # Scopes
            scopes = ["Calendars.Read", "Calendars.ReadWrite", "User.Read", "Mail.Send", "Contacts.Read", "Mail.Read", "Mail.ReadWrite"]

            # Initiiere Flow
            flow = app.initiate_device_flow(scopes=scopes)

            if "user_code" not in flow:
                return {
                    "success": False,
                    "error": "Device Flow konnte nicht initiiert werden"
                }

            # Speichere temporär für complete_flow
            self._temp_app = app
            self._temp_flow = flow

            return {
                "success": True,
                "device_info": {
                    "verification_uri": flow.get("verification_uri"),
                    "user_code": flow.get("user_code"),
                    "message": flow.get("message", ""),
                    "expires_in": flow.get("expires_in", 300)
                }
            }

        except Exception as e:
            print(f"[OutlookGraphTool] ❌ Fehler: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": f"Fehler: {str(e)}"
            }

    def complete_device_flow_wait(self) -> Dict[str, Any]:
        """
        Wartet auf Abschluss des Device Code Flows

        Returns:
            Dict mit success oder error
        """
        if not hasattr(self, '_temp_app') or not hasattr(self, '_temp_flow'):
            return {
                "success": False,
                "error": "Kein aktiver Device Flow gefunden"
            }

        try:
            print("[OutlookGraphTool] ⏳ Warte auf Authentifizierung...")

            # Warte auf Token
            result = self._temp_app.acquire_token_by_device_flow(self._temp_flow)

            # Cleanup
            delattr(self, '_temp_app')
            delattr(self, '_temp_flow')

            if "access_token" in result:
                self.access_token = result["access_token"]
                self.refresh_token = result.get("refresh_token")

                # Speichere Token
                self._save_token_to_file(result)

                print("[OutlookGraphTool] ✅ Erfolgreich authentifiziert!")
                return {
                    "success": True,
                    "message": "Erfolgreich authentifiziert"
                }
            else:
                error_desc = result.get("error_description", "Unbekannter Fehler")
                print(f"[OutlookGraphTool] ❌ Fehlgeschlagen: {error_desc}")
                return {
                    "success": False,
                    "error": error_desc
                }

        except Exception as e:
            print(f"[OutlookGraphTool] ❌ Fehler: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": f"Fehler: {str(e)}"
            }

    def authenticate_with_msal(self, use_device_flow: bool = True, timeout: int = 300) -> Dict[str, Any]:
        """
        Authentifiziert mit Microsoft Graph API über OAuth 2.0 Device Code Flow

        Der komplette Flow läuft in dieser Funktion ab und wartet auf die Anmeldung.

        Args:
            use_device_flow: Ob Device Code Flow verwendet werden soll (Standard: True)
            timeout: Timeout in Sekunden (Standard: 300 = 5 Minuten)

        Returns:
            Dict mit Status und Ergebnis
        """
        if not self.is_configured:
            print("[OutlookGraphTool] ❌ Kann nicht authentifizieren - nicht konfiguriert")
            return {
                "success": False,
                "error": "Nicht konfiguriert. Client-ID und Tenant-ID fehlen in .env"
            }

        try:
            import msal

            print("[OutlookGraphTool] 🔐 Starte Device Code Flow...")

            # Erstelle MSAL App - verwende Confidential wenn Secret vorhanden
            if self.client_secret:
                print("[OutlookGraphTool] Verwende ConfidentialClientApplication (mit Secret)")
                app = msal.ConfidentialClientApplication(
                    client_id=self.client_id,
                    client_credential=self.client_secret,
                    authority=f"https://login.microsoftonline.com/{self.tenant_id}"
                )
            else:
                print("[OutlookGraphTool] Verwende PublicClientApplication (ohne Secret)")
                app = msal.PublicClientApplication(
                    client_id=self.client_id,
                    authority=f"https://login.microsoftonline.com/{self.tenant_id}"
                )

            # Scopes für Kalender-Zugriff, Mail-Versand und Kontakte
            scopes = ["Calendars.Read", "Calendars.ReadWrite", "User.Read", "Mail.Send", "Contacts.Read", "Mail.Read", "Mail.ReadWrite"]

            # Device Code Flow initiieren
            flow = app.initiate_device_flow(scopes=scopes)

            if "user_code" not in flow:
                error_msg = "Device Flow konnte nicht initiiert werden"
                print(f"[OutlookGraphTool] ❌ {error_msg}")
                return {
                    "success": False,
                    "error": error_msg
                }

            # Gebe Flow-Informationen zurück für Anzeige
            device_info = {
                "verification_uri": flow.get("verification_uri"),
                "user_code": flow.get("user_code"),
                "message": flow.get("message", ""),
                "expires_in": flow.get("expires_in", timeout)
            }

            print(f"\n{'='*60}")
            print(f"MICROSOFT LOGIN ERFORDERLICH")
            print(f"{'='*60}")
            print(f"\n1. Öffnen Sie: {device_info['verification_uri']}")
            print(f"2. Geben Sie diesen Code ein: {device_info['user_code']}")
            print(f"\n⏳ Warte auf Authentifizierung (max. {timeout}s)...")
            print(f"{'='*60}\n")

            # Warte auf Authentifizierung mit automatischem Polling
            result = app.acquire_token_by_device_flow(flow)

            # Prüfe Ergebnis
            if "access_token" in result:
                self.access_token = result["access_token"]
                self.refresh_token = result.get("refresh_token")

                # Speichere Token
                self._save_token_to_file(result)

                print("[OutlookGraphTool] ✅ Erfolgreich authentifiziert!")
                print(f"[OutlookGraphTool]    Token-Typ: {result.get('token_type')}")
                print(f"[OutlookGraphTool]    Gültig für: {result.get('expires_in')} Sekunden")

                return {
                    "success": True,
                    "message": "Erfolgreich authentifiziert",
                    "device_info": device_info
                }
            else:
                error = result.get("error", "unknown_error")
                error_desc = result.get("error_description", "Unbekannter Fehler")

                print(f"[OutlookGraphTool] ❌ Authentifizierung fehlgeschlagen")
                print(f"[OutlookGraphTool]    Error: {error}")
                print(f"[OutlookGraphTool]    Description: {error_desc}")

                return {
                    "success": False,
                    "error": error_desc,
                    "error_code": error,
                    "device_info": device_info
                }

        except ImportError:
            error_msg = "msal nicht installiert. Führen Sie aus: pip install msal"
            print(f"[OutlookGraphTool] ❌ {error_msg}")
            return {
                "success": False,
                "error": error_msg
            }
        except Exception as e:
            print(f"[OutlookGraphTool] ❌ Fehler bei Authentifizierung: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": f"Technischer Fehler: {str(e)}"
            }

    def get_todays_events(self) -> List[Dict[str, Any]]:
        """
        Holt die heutigen Termine aus dem Outlook-Kalender

        Returns:
            Liste von Event-Dictionaries
        """
        if not self.access_token:
            print("[OutlookGraphTool] ⚠️ Nicht authentifiziert - rufe authenticate_with_msal() zuerst auf")
            return []

        try:
            import requests

            # Graph API Endpoint für Kalender-Events
            endpoint = "https://graph.microsoft.com/v1.0/me/calendar/calendarView"

            # Zeitraum: Heute 00:00 bis 23:59
            today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=1)

            params = {
                "startDateTime": today_start.isoformat() + "Z",
                "endDateTime": today_end.isoformat() + "Z",
                "$select": "subject,start,end,location,attendees,bodyPreview",
                "$orderby": "start/dateTime",
                "$top": 50
            }

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }

            response = requests.get(endpoint, params=params, headers=headers)
            response.raise_for_status()

            data = response.json()
            events = data.get("value", [])

            print(f"[OutlookGraphTool] ✓ {len(events)} Termin(e) für heute gefunden")

            # Formatiere Events
            formatted_events = []
            for event in events:
                start = event.get("start", {})
                end = event.get("end", {})
                location_obj = event.get("location", {})

                formatted_event = {
                    "id": event.get("id"),
                    "title": event.get("subject", "Kein Titel"),
                    "start": start.get("dateTime", ""),
                    "end": end.get("dateTime", ""),
                    "location": location_obj.get("displayName", ""),
                    "attendees": [
                        attendee.get("emailAddress", {}).get("name", "")
                        for attendee in event.get("attendees", [])
                    ],
                    "preview": event.get("bodyPreview", "")[:200]
                }
                formatted_events.append(formatted_event)

            return formatted_events

        except ImportError:
            print("[OutlookGraphTool] ❌ requests nicht installiert")
            print("[OutlookGraphTool] Installieren Sie: pip install requests")
            return []
        except Exception as e:
            print(f"[OutlookGraphTool] ❌ Fehler beim Laden der Termine: {e}")
            import traceback
            traceback.print_exc()
            return []

    def get_events_for_date_range(self, start_date: datetime, end_date: datetime) -> List[Dict[str, Any]]:
        """
        Holt Termine für einen bestimmten Zeitraum

        Args:
            start_date: Start-Datum
            end_date: End-Datum

        Returns:
            Liste von Event-Dictionaries
        """
        if not self.access_token:
            print("[OutlookGraphTool] ⚠️ Nicht authentifiziert")
            return []

        try:
            import requests

            endpoint = "https://graph.microsoft.com/v1.0/me/calendar/calendarView"

            params = {
                "startDateTime": start_date.isoformat() + "Z",
                "endDateTime": end_date.isoformat() + "Z",
                "$select": "subject,start,end,location,attendees,bodyPreview",
                "$orderby": "start/dateTime",
                "$top": 100
            }

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }

            response = requests.get(endpoint, params=params, headers=headers)
            response.raise_for_status()

            data = response.json()
            events = data.get("value", [])

            print(f"[OutlookGraphTool] ✓ {len(events)} Termin(e) gefunden")

            # Formatiere Events
            formatted_events = []
            for event in events:
                start = event.get("start", {})
                end = event.get("end", {})
                location_obj = event.get("location", {})

                formatted_event = {
                    "id": event.get("id"),
                    "title": event.get("subject", "Kein Titel"),
                    "start": start.get("dateTime", ""),
                    "end": end.get("dateTime", ""),
                    "location": location_obj.get("displayName", ""),
                    "attendees": [
                        attendee.get("emailAddress", {}).get("name", "")
                        for attendee in event.get("attendees", [])
                    ],
                    "preview": event.get("bodyPreview", "")[:200]
                }
                formatted_events.append(formatted_event)

            return formatted_events

        except Exception as e:
            print(f"[OutlookGraphTool] ❌ Fehler beim Laden der Termine: {e}")
            return []

    def check_khs_attendees(self, attendees: List[str], khs_list: List[str]) -> List[str]:
        """
        Prüft ob Teilnehmer in der KHS-Gesellschafterliste sind

        Args:
            attendees: Liste von Teilnehmer-Namen
            khs_list: Liste von KHS-Gesellschaftern

        Returns:
            Liste der gefundenen KHS-Gesellschafter
        """
        found_khs = []

        for attendee in attendees:
            for khs_member in khs_list:
                # Einfacher String-Match (case-insensitive)
                if khs_member.lower() in attendee.lower() or attendee.lower() in khs_member.lower():
                    found_khs.append(khs_member)
                    break

        return found_khs

    def is_authenticated(self) -> bool:
        """Prüft ob das Tool authentifiziert ist"""
        return self.access_token is not None

    def logout(self):
        """Löscht gespeicherte Tokens und meldet ab"""
        self.access_token = None
        self.refresh_token = None
        if os.path.exists(self.token_file):
            try:
                os.remove(self.token_file)
                print("[OutlookGraphTool] Token gelöscht")
            except Exception as e:
                print(f"[OutlookGraphTool] Fehler beim Löschen des Tokens: {e}")

    def create_email_draft(
        self,
        subject: str,
        body: str,
        to_recipients: Optional[List[str]] = None,
        cc_recipients: Optional[List[str]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Erstellt einen Email-Entwurf in Outlook.

        Args:
            subject: Email-Betreff
            body: Email-Inhalt (HTML oder Text)
            to_recipients: Liste von Email-Adressen (To)
            cc_recipients: Liste von Email-Adressen (CC)
            attachments: Liste von Anhängen [{name, path}]

        Returns:
            Dict mit success, draft_id und ggf. error
        """
        if not self.access_token:
            return {"success": False, "error": "Nicht authentifiziert"}

        try:
            import requests
            import base64

            # Baue Empfänger-Liste
            to_list = []
            if to_recipients:
                for email in to_recipients:
                    to_list.append({
                        "emailAddress": {"address": email}
                    })

            cc_list = []
            if cc_recipients:
                for email in cc_recipients:
                    cc_list.append({
                        "emailAddress": {"address": email}
                    })

            # Baue Email-Body
            message_data = {
                "subject": subject,
                "body": {
                    "contentType": "HTML",
                    "content": body
                },
                "toRecipients": to_list,
                "ccRecipients": cc_list
            }

            # Erstelle Entwurf
            url = "https://graph.microsoft.com/v1.0/me/messages"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }

            response = requests.post(url, headers=headers, json=message_data)

            if response.status_code in [200, 201]:
                draft = response.json()
                draft_id = draft.get("id")

                # Füge Anhänge hinzu wenn vorhanden
                if attachments and draft_id:
                    for attachment in attachments:
                        att_name = attachment.get('name')
                        att_path = attachment.get('path')

                        if att_path and os.path.exists(att_path):
                            with open(att_path, 'rb') as f:
                                file_content = f.read()
                                file_b64 = base64.b64encode(file_content).decode('utf-8')

                            att_data = {
                                "@odata.type": "#microsoft.graph.fileAttachment",
                                "name": att_name,
                                "contentBytes": file_b64
                            }

                            att_url = f"https://graph.microsoft.com/v1.0/me/messages/{draft_id}/attachments"
                            att_response = requests.post(att_url, headers=headers, json=att_data)

                            if att_response.status_code not in [200, 201]:
                                print(f"Warnung: Anhang {att_name} konnte nicht hinzugefügt werden")

                return {
                    "success": True,
                    "draft_id": draft_id,
                    "message": "Email-Entwurf erstellt"
                }
            else:
                return {
                    "success": False,
                    "error": f"API Fehler {response.status_code}: {response.text}"
                }

        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_email(
        self,
        to_email: str,
        subject: str,
        body: str,
        cc_emails: Optional[List[str]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Sendet eine Email direkt über Outlook.

        Args:
            to_email: Empfänger-Email-Adresse
            subject: Email-Betreff
            body: Email-Inhalt (HTML oder Text)
            cc_emails: Liste von CC-Empfänger-Emails (optional)
            attachments: Liste von Anhängen [{name, path}] (optional)

        Returns:
            Dict mit success und ggf. error
        """
        if not self.access_token:
            return {"success": False, "error": "Nicht authentifiziert"}

        try:
            import requests
            import base64

            # Baue Empfänger-Liste
            to_recipients = [{
                "emailAddress": {"address": to_email}
            }]

            cc_recipients = []
            if cc_emails:
                for email in cc_emails:
                    cc_recipients.append({
                        "emailAddress": {"address": email}
                    })

            # Baue Email-Body
            message_data = {
                "message": {
                    "subject": subject,
                    "body": {
                        "contentType": "HTML",
                        "content": body
                    },
                    "toRecipients": to_recipients,
                    "ccRecipients": cc_recipients
                },
                "saveToSentItems": "true"
            }

            # Füge Anhänge hinzu wenn vorhanden
            if attachments:
                attachments_data = []
                for attachment in attachments:
                    att_name = attachment.get('name')
                    att_path = attachment.get('path')

                    if att_path and os.path.exists(att_path):
                        with open(att_path, 'rb') as f:
                            file_content = f.read()
                            file_b64 = base64.b64encode(file_content).decode('utf-8')

                        attachments_data.append({
                            "@odata.type": "#microsoft.graph.fileAttachment",
                            "name": att_name,
                            "contentBytes": file_b64
                        })

                if attachments_data:
                    message_data["message"]["attachments"] = attachments_data

            # Sende Email
            url = "https://graph.microsoft.com/v1.0/me/sendMail"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }

            response = requests.post(url, headers=headers, json=message_data)

            if response.status_code == 202:  # Accepted
                return {
                    "success": True,
                    "message": "Email erfolgreich gesendet"
                }
            else:
                return {
                    "success": False,
                    "error": f"API Fehler {response.status_code}: {response.text}"
                }

        except Exception as e:
            import traceback
            return {
                "success": False,
                "error": f"{str(e)}\n{traceback.format_exc()}"
            }

    def add_attachment_to_event(
        self,
        event_id: str,
        file_path: str,
        file_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Fügt einen Anhang zu einem Outlook-Termin hinzu.

        Args:
            event_id: ID des Outlook-Events
            file_path: Pfad zur anzuhängenden Datei
            file_name: Optional - Name der Datei (default: basename von file_path)

        Returns:
            Dict mit success und ggf. error
        """
        # Stelle sicher dass Token gültig ist (auto-refresh wenn nötig)
        if not self._ensure_valid_token():
            return {"success": False, "error": "Nicht authentifiziert oder Token-Refresh fehlgeschlagen"}

        try:
            import requests
            import base64
            from pathlib import Path

            # Prüfe ob Datei existiert
            path = Path(file_path)
            if not path.exists():
                return {"success": False, "error": f"Datei nicht gefunden: {file_path}"}

            # Lese Datei
            with open(path, 'rb') as f:
                file_content = f.read()

            # Base64-Kodierung
            file_b64 = base64.b64encode(file_content).decode('utf-8')

            # Dateiname
            if not file_name:
                file_name = path.name

            # Erstelle Anhang
            attachment_data = {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": file_name,
                "contentBytes": file_b64
            }

            # Graph API Call
            url = f"https://graph.microsoft.com/v1.0/me/events/{event_id}/attachments"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }

            response = requests.post(url, headers=headers, json=attachment_data)

            # Bei 401 Unauthorized: Versuche Token-Refresh und Retry
            if response.status_code == 401:
                print("[OutlookGraphTool] 401 Unauthorized - versuche Token-Refresh und Retry...")
                if self._refresh_access_token():
                    # Retry mit neuem Token
                    headers["Authorization"] = f"Bearer {self.access_token}"
                    response = requests.post(url, headers=headers, json=attachment_data)

            if response.status_code in [200, 201]:
                return {
                    "success": True,
                    "message": f"Anhang '{file_name}' zum Event hinzugefügt"
                }
            else:
                return {
                    "success": False,
                    "error": f"API Fehler {response.status_code}: {response.text}"
                }

        except Exception as e:
            return {"success": False, "error": str(e)}

    def add_category_to_event(
        self,
        event_id: str,
        category: str
    ) -> Dict[str, Any]:
        """
        Fügt eine Kategorie zu einem Outlook-Termin hinzu.
        Behält bestehende Kategorien bei und fügt die neue hinzu (falls noch nicht vorhanden).

        Args:
            event_id: ID des Outlook-Events
            category: Kategorie-Name (z.B. "Protokoll")

        Returns:
            Dict mit success und ggf. error
        """
        # Stelle sicher dass Token gültig ist (auto-refresh wenn nötig)
        if not self._ensure_valid_token():
            return {"success": False, "error": "Nicht authentifiziert oder Token-Refresh fehlgeschlagen"}

        try:
            import requests

            print(f"[OutlookGraphTool] add_category_to_event: event_id={event_id}, category={category}")

            # Schritt 1: Hole aktuellen Event um bestehende Kategorien zu lesen
            url = f"https://graph.microsoft.com/v1.0/me/events/{event_id}"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }

            params = {
                "$select": "categories,subject"
            }

            print(f"[OutlookGraphTool] GET {url}")
            response = requests.get(url, headers=headers, params=params)
            print(f"[OutlookGraphTool] GET Response: {response.status_code}")

            # Bei 401 Unauthorized: Versuche Token-Refresh und Retry
            if response.status_code == 401:
                print("[OutlookGraphTool] 401 Unauthorized - versuche Token-Refresh und Retry...")
                if self._refresh_access_token():
                    headers["Authorization"] = f"Bearer {self.access_token}"
                    response = requests.get(url, headers=headers, params=params)
                    print(f"[OutlookGraphTool] GET Retry Response: {response.status_code}")

            if response.status_code != 200:
                error_detail = response.text[:500]  # Begrenzt für Lesbarkeit
                print(f"[OutlookGraphTool] ❌ Fehler: {response.status_code} - {error_detail}")
                return {
                    "success": False,
                    "error": f"Fehler beim Laden des Events: {response.status_code} - {error_detail}"
                }

            event_data = response.json()
            event_subject = event_data.get('subject', 'Unbekannt')
            existing_categories = event_data.get('categories', [])
            print(f"[OutlookGraphTool] Event '{event_subject}' hat Kategorien: {existing_categories}")

            # Schritt 2: Füge neue Kategorie hinzu (falls noch nicht vorhanden)
            if category in existing_categories:
                print(f"[OutlookGraphTool] ✓ Kategorie '{category}' war bereits gesetzt")
                return {
                    "success": True,
                    "message": f"Kategorie '{category}' war bereits gesetzt"
                }

            updated_categories = existing_categories + [category]
            print(f"[OutlookGraphTool] Setze neue Kategorien: {updated_categories}")

            # Schritt 3: Aktualisiere Event mit neuen Kategorien
            update_data = {
                "categories": updated_categories
            }

            print(f"[OutlookGraphTool] PATCH {url}")
            response = requests.patch(url, headers=headers, json=update_data)
            print(f"[OutlookGraphTool] PATCH Response: {response.status_code}")

            # Bei 401 Unauthorized: Versuche Token-Refresh und Retry
            if response.status_code == 401:
                print("[OutlookGraphTool] 401 Unauthorized - versuche Token-Refresh und Retry...")
                if self._refresh_access_token():
                    headers["Authorization"] = f"Bearer {self.access_token}"
                    response = requests.patch(url, headers=headers, json=update_data)
                    print(f"[OutlookGraphTool] PATCH Retry Response: {response.status_code}")

            if response.status_code == 200:
                print(f"[OutlookGraphTool] ✅ Kategorie '{category}' erfolgreich hinzugefügt zu '{event_subject}'")
                return {
                    "success": True,
                    "message": f"Kategorie '{category}' zum Termin '{event_subject}' hinzugefügt"
                }
            else:
                error_detail = response.text[:500]
                print(f"[OutlookGraphTool] ❌ PATCH Fehler: {response.status_code} - {error_detail}")
                return {
                    "success": False,
                    "error": f"API Fehler {response.status_code}: {error_detail}"
                }

        except Exception as e:
            return {"success": False, "error": str(e)}

    def add_protocol_subject_prefix(
        self,
        event_id: str,
        prefix: str = "📄 "
    ) -> Dict[str, Any]:
        """
        Fügt ein Protokoll-Prefix zum Betreff eines einzelnen Outlook-Termins hinzu.
        Funktioniert auch bei einzelnen Serienterminen.

        Args:
            event_id: ID des Outlook-Events
            prefix: Prefix-String (Standard: "📄 ")

        Returns:
            Dict mit success und ggf. error/message
        """
        if not self._ensure_valid_token():
            return {"success": False, "error": "Nicht authentifiziert oder Token-Refresh fehlgeschlagen"}

        try:
            import requests

            url = f"https://graph.microsoft.com/v1.0/me/events/{event_id}"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }

            # Hole aktuellen Betreff
            response = requests.get(url, headers=headers, params={"$select": "subject"})
            if response.status_code == 401:
                if self._refresh_access_token():
                    headers["Authorization"] = f"Bearer {self.access_token}"
                    response = requests.get(url, headers=headers, params={"$select": "subject"})

            if response.status_code != 200:
                return {"success": False, "error": f"Fehler beim Laden des Events: {response.status_code}"}

            current_subject = response.json().get('subject', '')
            print(f"[OutlookGraphTool] add_protocol_subject_prefix: aktueller Betreff='{current_subject}'")

            # Prefix nur hinzufügen wenn noch nicht vorhanden
            if current_subject.startswith(prefix):
                print(f"[OutlookGraphTool] ✓ Prefix bereits vorhanden")
                return {"success": True, "message": "Prefix war bereits gesetzt"}

            new_subject = prefix + current_subject
            response = requests.patch(url, headers=headers, json={"subject": new_subject})
            if response.status_code == 401:
                if self._refresh_access_token():
                    headers["Authorization"] = f"Bearer {self.access_token}"
                    response = requests.patch(url, headers=headers, json={"subject": new_subject})

            if response.status_code == 200:
                print(f"[OutlookGraphTool] ✅ Betreff aktualisiert: '{new_subject}'")
                return {"success": True, "message": f"Betreff auf '{new_subject}' gesetzt"}
            else:
                return {"success": False, "error": f"API Fehler {response.status_code}: {response.text[:300]}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def search_emails(self, search_query: str, max_results: int = 10,
                      days_back: int = 30) -> List[Dict[str, Any]]:
        """
        Sucht E-Mails basierend auf einem Suchbegriff

        Args:
            search_query: Suchbegriff (wird in Betreff und Body gesucht)
            max_results: Maximale Anzahl Ergebnisse
            days_back: Wie viele Tage zurück suchen (Standard: 30)

        Returns:
            Liste von E-Mail-Dictionaries
        """
        if not self.access_token:
            print("[OutlookGraphTool] ⚠️ Keine Authentifizierung vorhanden")
            return []

        try:
            import requests

            # Berechne Zeitraum
            from datetime import datetime, timedelta, timezone
            start_time = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()

            # Microsoft Graph API: Suche in E-Mails
            # Verwende $search für Volltextsuche in Betreff und Body
            url = "https://graph.microsoft.com/v1.0/me/messages"

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }

            # Parameter für die Suche
            params = {
                "$search": f'"{search_query}"',  # Suche in Betreff und Body
                "$top": max_results,
                "$orderby": "receivedDateTime desc",
                "$select": "id,subject,from,receivedDateTime,bodyPreview,hasAttachments,webLink"
            }

            response = requests.get(url, headers=headers, params=params)

            if response.status_code == 200:
                data = response.json()
                emails = data.get('value', [])

                # Formatiere E-Mails
                formatted_emails = []
                for email in emails:
                    # Parse receivedDateTime
                    received_dt = email.get('receivedDateTime', '')
                    try:
                        received_datetime = datetime.fromisoformat(received_dt.replace('Z', '+00:00'))
                        received_str = received_datetime.strftime('%d.%m.%Y %H:%M')
                    except:
                        received_str = received_dt

                    formatted_emails.append({
                        'id': email.get('id'),
                        'subject': email.get('subject', 'Kein Betreff'),
                        'from': email.get('from', {}).get('emailAddress', {}).get('name', 'Unbekannt'),
                        'from_email': email.get('from', {}).get('emailAddress', {}).get('address', ''),
                        'received': received_str,
                        'preview': email.get('bodyPreview', '')[:200],
                        'has_attachments': email.get('hasAttachments', False),
                        'web_link': email.get('webLink', '')
                    })

                print(f"[OutlookGraphTool] ✓ {len(formatted_emails)} E-Mails gefunden für '{search_query}'")
                return formatted_emails

            else:
                print(f"[OutlookGraphTool] ❌ Fehler beim Suchen: {response.status_code}")
                print(f"[OutlookGraphTool] Response: {response.text}")
                return []

        except Exception as e:
            print(f"[OutlookGraphTool] ❌ Fehler beim Suchen von E-Mails: {e}")
            import traceback
            traceback.print_exc()
            return []

    def search_contacts(self, search_query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        """
        Sucht Kontakte im Adressbuch basierend auf einem Suchbegriff

        Verbesserte Suchlogik:
        - Holt mehr Ergebnisse als angefordert (max_results * 3)
        - Filtert und rankt clientseitig nach Relevanz
        - Priorisiert exakte Übereinstimmungen
        - Extrahiert SMTP-Adressen aus Exchange-Pfaden

        Args:
            search_query: Suchbegriff (Name oder E-Mail)
            max_results: Maximale Anzahl Ergebnisse

        Returns:
            Liste von Kontakt-Dictionaries (sortiert nach Relevanz)
        """
        if not self.access_token:
            print("[OutlookGraphTool] ⚠️ Keine Authentifizierung vorhanden")
            return []

        try:
            import requests
            import re

            # Microsoft Graph API: Kontakte durchsuchen
            url = "https://graph.microsoft.com/v1.0/me/contacts"

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }

            # Hole mehr Ergebnisse als nötig für besseres clientseitiges Ranking
            # Verwende $search für Volltextsuche
            # Inkludiere proxyAddresses für SMTP-Adressen
            params = {
                "$search": f'"{search_query}"',
                "$top": max_results * 3,  # 3x mehr für besseres Ranking
                "$select": "id,displayName,emailAddresses,companyName,jobTitle,mobilePhone,businessPhones"
            }

            response = requests.get(url, headers=headers, params=params)

            if response.status_code == 200:
                data = response.json()
                contacts = data.get('value', [])

                # Formatiere und ranke Kontakte
                formatted_contacts = []
                search_lower = search_query.lower()

                for contact in contacts:
                    display_name = contact.get('displayName', '')

                    # Sammle alle E-Mail-Adressen und extrahiere SMTP
                    email_addresses = []
                    seen_emails = set()  # Vermeide Duplikate

                    for email_obj in contact.get('emailAddresses', []):
                        email_addr = email_obj.get('address', '')

                        # Prüfe ob es ein Exchange-Pfad ist
                        if email_addr.startswith('/o=') or email_addr.startswith('/O='):
                            # Exchange-Pfad - hole den tatsächlichen Kontakt nochmal mit mehr Details
                            # Das ist ein Fallback, wenn die primäre Suche keinen SMTP liefert
                            continue
                        elif '@' in email_addr and email_addr.lower() not in seen_emails:
                            # Normale SMTP-Adresse
                            email_addresses.append(email_addr)
                            seen_emails.add(email_addr.lower())
                        elif email_obj.get('name'):
                            # Fallback: nutze "name" Feld falls vorhanden
                            name_field = email_obj.get('name')
                            if '@' in name_field and name_field.lower() not in seen_emails:
                                email_addresses.append(name_field)
                                seen_emails.add(name_field.lower())

                    # Wenn keine E-Mail gefunden wurde, versuche die E-Mail über eine separate Abfrage zu holen
                    if not email_addresses and contact.get('id'):
                        try:
                            # Hole Kontakt-Details nochmal, diesmal mit allen Feldern
                            detail_url = f"https://graph.microsoft.com/v1.0/me/contacts/{contact.get('id')}"
                            detail_params = {"$select": "emailAddresses"}
                            detail_response = requests.get(detail_url, headers=headers, params=detail_params)

                            if detail_response.status_code == 200:
                                detail_data = detail_response.json()
                                for email_obj in detail_data.get('emailAddresses', []):
                                    email_addr = email_obj.get('address', '')
                                    # Versuche intelligente E-Mail-Generierung aus Namen
                                    if not email_addr or email_addr.startswith('/'):
                                        # Generiere aus displayName
                                        if display_name and ' ' in display_name:
                                            name_parts = display_name.split()
                                            first_name = name_parts[0].lower()
                                            last_name = name_parts[-1].lower()

                                            # Versuche mehrere Formate
                                            possible_formats = [
                                                f"{first_name[0]}.{last_name}",  # f.herbert
                                                f"{first_name}.{last_name}",      # frank.herbert
                                                f"{first_name}_{last_name}",      # frank_herbert
                                                f"{first_name}{last_name}",       # frankherbert
                                            ]

                                            # Versuche gängige Domains
                                            for format_str in possible_formats:
                                                # Nutze herbert.de als Primary-Domain
                                                potential = f"{format_str}@herbert.de"
                                                if potential.lower() not in seen_emails:
                                                    email_addresses.append(f"{potential} (geschätzt)")
                                                    seen_emails.add(potential.lower())
                                                    break
                                    elif '@' in email_addr and email_addr.lower() not in seen_emails:
                                        email_addresses.append(email_addr)
                                        seen_emails.add(email_addr.lower())
                        except Exception as detail_error:
                            print(f"[OutlookGraphTool] Warnung: Konnte Details für Kontakt nicht laden: {detail_error}")

                    # Sammle Telefonnummern
                    phones = []
                    if contact.get('mobilePhone'):
                        phones.append(f"Mobil: {contact.get('mobilePhone')}")
                    for phone in contact.get('businessPhones', []):
                        phones.append(f"Geschäftlich: {phone}")

                    # Berechne Relevanz-Score für Ranking
                    # Höherer Score = relevanter
                    relevance = 0

                    # Exakte Übereinstimmung (case-insensitive) = höchste Priorität
                    if display_name.lower() == search_lower:
                        relevance += 1000

                    # Übereinstimmung am Anfang
                    elif display_name.lower().startswith(search_lower):
                        relevance += 500

                    # Alle Suchbegriff-Wörter im Namen enthalten
                    search_words = search_lower.split()
                    name_lower = display_name.lower()
                    if all(word in name_lower for word in search_words):
                        relevance += 300

                    # Teilübereinstimmung
                    elif search_lower in name_lower:
                        relevance += 100

                    # Bonus wenn E-Mail vorhanden
                    if email_addresses:
                        relevance += 50

                    # Nur Kontakte mit mindestens etwas Relevanz
                    if relevance > 0:
                        formatted_contacts.append({
                            'id': contact.get('id'),
                            'name': display_name,
                            'emails': email_addresses,
                            'primary_email': email_addresses[0] if email_addresses else None,
                            'company': contact.get('companyName', ''),
                            'job_title': contact.get('jobTitle', ''),
                            'phones': phones,
                            '_relevance': relevance  # Für Sortierung
                        })

                # Sortiere nach Relevanz (höchste zuerst)
                formatted_contacts.sort(key=lambda x: x['_relevance'], reverse=True)

                # Entferne Relevanz-Score aus Ausgabe
                for contact in formatted_contacts:
                    del contact['_relevance']

                # Begrenze auf max_results
                formatted_contacts = formatted_contacts[:max_results]

                print(f"[OutlookGraphTool] ✓ {len(formatted_contacts)} Kontakt(e) gefunden für '{search_query}'")
                return formatted_contacts

            else:
                print(f"[OutlookGraphTool] ❌ Fehler beim Suchen: {response.status_code}")
                print(f"[OutlookGraphTool] Response: {response.text}")
                return []

        except Exception as e:
            print(f"[OutlookGraphTool] ❌ Fehler beim Suchen von Kontakten: {e}")
            import traceback
            traceback.print_exc()
            return []

    def get_configuration_status(self) -> Dict[str, Any]:
        """
        Gibt den aktuellen Konfigurationsstatus zurück

        Returns:
            Dictionary mit Konfigurationsinformationen
        """
        return {
            "configured": self.is_configured,
            "client_id": f"{self.client_id[:8]}..." if self.client_id else "Nicht gesetzt",
            "tenant_id": f"{self.tenant_id[:8]}..." if self.tenant_id else "Nicht gesetzt",
            "authenticated": self.access_token is not None,
            "setup_instructions": """
1. Gehen Sie zum Azure Portal: https://portal.azure.com
2. Registrieren Sie eine neue App unter "Azure Active Directory" > "App registrations"
3. Notieren Sie sich Client-ID und Tenant-ID
4. Fügen Sie diese in die .env-Datei ein:
   MICROSOFT_CLIENT_ID=ihre_client_id
   MICROSOFT_TENANT_ID=ihre_tenant_id
5. Starten Sie die App neu
            """
        }

    def get_unread_emails(self, max_results: int = 20, folder: str = "inbox") -> List[Dict[str, Any]]:
        """
        Holt ungelesene E-Mails aus dem Posteingang

        Args:
            max_results: Maximale Anzahl Ergebnisse
            folder: Ordner-Name (Standard: "inbox")

        Returns:
            Liste von E-Mail-Dictionaries im Graph API Format
        """
        if not self.access_token:
            print("[OutlookGraphTool] ⚠️ Keine Authentifizierung vorhanden")
            return []

        try:
            import requests

            # Microsoft Graph API: Ungelesene E-Mails
            url = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages"

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }

            # Parameter für ungelesene E-Mails
            params = {
                "$filter": "isRead eq false",
                "$top": max_results,
                "$orderby": "receivedDateTime desc",
                "$select": "id,subject,from,receivedDateTime,bodyPreview,body,hasAttachments,webLink,importance,categories"
            }

            response = requests.get(url, headers=headers, params=params)

            if response.status_code == 200:
                data = response.json()
                emails = data.get('value', [])
                print(f"[OutlookGraphTool] ✓ {len(emails)} ungelesene E-Mails gefunden")
                return emails
            else:
                print(f"[OutlookGraphTool] ❌ Fehler beim Abrufen: {response.status_code}")
                print(f"[OutlookGraphTool] Response: {response.text}")
                return []

        except Exception as e:
            print(f"[OutlookGraphTool] ❌ Fehler beim Abrufen ungelesener E-Mails: {e}")
            import traceback
            traceback.print_exc()
            return []

    def get_email_attachments(self, message_id: str) -> Dict[str, Any]:
        """
        Holt Anhang-Metadaten für eine Email

        Args:
            message_id: Email ID

        Returns:
            Dict mit 'success' und 'attachments' Liste
        """
        if not self.access_token:
            return {'success': False, 'error': 'Keine Authentifizierung'}

        try:
            import requests

            url = f"https://graph.microsoft.com/v1.0/me/messages/{message_id}/attachments"

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }

            # Hole nur Metadaten, nicht den Content
            params = {
                "$select": "id,name,size,contentType"
            }

            response = requests.get(url, headers=headers, params=params)

            if response.status_code == 200:
                data = response.json()
                attachments = data.get('value', [])
                print(f"[OutlookGraphTool] ✓ {len(attachments)} Anhänge gefunden")
                return {'success': True, 'attachments': attachments}
            else:
                error_msg = f"HTTP {response.status_code}: {response.text[:200]}"
                print(f"[OutlookGraphTool] ❌ Fehler beim Abrufen der Anhänge: {error_msg}")
                return {'success': False, 'error': error_msg}

        except Exception as e:
            error_msg = str(e)
            print(f"[OutlookGraphTool] ❌ Fehler beim Abrufen der Anhänge: {error_msg}")
            return {'success': False, 'error': error_msg}

    def mark_as_read(self, email_id: str) -> Dict[str, Any]:
        """
        Markiert E-Mail als gelesen

        Args:
            email_id: Die ID der E-Mail

        Returns:
            Dictionary mit 'success' (bool) und optional 'error' (str)
        """
        if not self.access_token:
            return {'success': False, 'error': 'Keine Authentifizierung vorhanden'}

        try:
            import requests

            url = f"https://graph.microsoft.com/v1.0/me/messages/{email_id}"

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }

            body = {"isRead": True}

            response = requests.patch(url, headers=headers, json=body)

            if response.status_code == 200:
                print(f"[OutlookGraphTool] ✓ E-Mail als gelesen markiert")
                return {'success': True}
            else:
                error_msg = f"HTTP {response.status_code}: {response.text}"
                print(f"[OutlookGraphTool] ❌ Fehler beim Markieren: {error_msg}")
                return {'success': False, 'error': error_msg}

        except Exception as e:
            error_msg = str(e)
            print(f"[OutlookGraphTool] ❌ Fehler beim Markieren als gelesen: {error_msg}")
            import traceback
            traceback.print_exc()
            return {'success': False, 'error': error_msg}

    def move_to_folder(self, email_id: str, folder_name: str = "Posteingang erledigt 2026") -> Dict[str, Any]:
        """
        Verschiebt E-Mail in einen Ordner

        Args:
            email_id: Die ID der E-Mail
            folder_name: Name des Zielordners (Standard: "Posteingang erledigt 2026")

        Returns:
            Dictionary mit 'success' (bool) und optional 'error' (str)
        """
        if not self.access_token:
            return {'success': False, 'error': 'Keine Authentifizierung vorhanden'}

        try:
            import requests

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }

            # Schritt 1: Nutze "inbox" Well-Known Folder für Posteingang
            url_inbox_folders = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/childFolders"
            response = requests.get(url_inbox_folders, headers=headers)

            target_folder_id = None
            inbox_child_folders = []

            if response.status_code == 200:
                inbox_child_folders = response.json().get('value', [])
                # Suche Zielordner in Posteingang-Unterordnern
                for folder in inbox_child_folders:
                    if folder.get('displayName') == folder_name:
                        target_folder_id = folder.get('id')
                        print(f"[OutlookGraphTool] ✓ Zielordner '{folder_name}' in Posteingang gefunden")
                        break

            # Schritt 2: Falls nicht gefunden, suche in allen Root-Ordnern
            if not target_folder_id:
                url_folders = "https://graph.microsoft.com/v1.0/me/mailFolders"
                response = requests.get(url_folders, headers=headers)

                if response.status_code != 200:
                    return {'success': False, 'error': f"Ordner-Abruf fehlgeschlagen: {response.status_code}"}

                folders = response.json().get('value', [])

                for folder in folders:
                    if folder.get('displayName') == folder_name:
                        target_folder_id = folder.get('id')
                        print(f"[OutlookGraphTool] ✓ Zielordner '{folder_name}' in Root-Ebene gefunden")
                        break

                # Schritt 3: Falls immer noch nicht gefunden, durchsuche Unterordner aller Root-Ordner
                if not target_folder_id:
                    for folder in folders:
                        folder_id = folder.get('id')
                        url_child = f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder_id}/childFolders"
                        response = requests.get(url_child, headers=headers)

                        if response.status_code == 200:
                            child_folders = response.json().get('value', [])
                            for child in child_folders:
                                if child.get('displayName') == folder_name:
                                    target_folder_id = child.get('id')
                                    print(f"[OutlookGraphTool] ✓ Zielordner '{folder_name}' in '{folder.get('displayName')}' gefunden")
                                    break

                        if target_folder_id:
                            break

            # Falls Ordner nicht gefunden wurde
            if not target_folder_id:
                # Debug: Zeige alle verfügbaren Ordner
                inbox_folder_names = [f['displayName'] for f in inbox_child_folders]
                print(f"[OutlookGraphTool] ⚠️ Verfügbare Posteingang-Unterordner: {inbox_folder_names}")
                return {
                    'success': False,
                    'error': f'Ordner "{folder_name}" nicht gefunden. Verfügbare Posteingang-Unterordner: {", ".join(inbox_folder_names)}'
                }

            # Schritt 4: Verschiebe E-Mail
            url_move = f"https://graph.microsoft.com/v1.0/me/messages/{email_id}/move"
            body = {"destinationId": target_folder_id}

            response = requests.post(url_move, headers=headers, json=body)

            if response.status_code == 201 or response.status_code == 200:
                print(f"[OutlookGraphTool] ✓ E-Mail in '{folder_name}' verschoben")
                return {'success': True}
            else:
                error_msg = f"HTTP {response.status_code}: {response.text}"
                print(f"[OutlookGraphTool] ❌ Fehler beim Verschieben: {error_msg}")
                return {'success': False, 'error': error_msg}

        except Exception as e:
            error_msg = str(e)
            print(f"[OutlookGraphTool] ❌ Fehler beim Verschieben: {error_msg}")
            import traceback
            traceback.print_exc()
            return {'success': False, 'error': error_msg}

    def save_email_analysis(self, email_id: str, analysis: Dict[str, Any]) -> Dict[str, Any]:
        """
        Speichert E-Mail-Analyse als Outlook Categories

        Args:
            email_id: Die ID der E-Mail
            analysis: Analysis-Dict mit priority, category, sentiment

        Returns:
            Dictionary mit 'success' (bool) und optional 'error' (str)
        """
        if not self.access_token:
            return {'success': False, 'error': 'Keine Authentifizierung vorhanden'}

        try:
            import requests

            # Erstelle Categories basierend auf Analyse
            categories = [
                "AI_Analyzed",
                f"AI_Priority_{analysis.get('priority', 3)}",
                f"AI_Category_{analysis.get('category', 'Sonstiges')}",
                f"AI_Sentiment_{analysis.get('sentiment', 'neutral')}"
            ]

            url = f"https://graph.microsoft.com/v1.0/me/messages/{email_id}"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }

            body = {"categories": categories}

            response = requests.patch(url, headers=headers, json=body)

            if response.status_code == 200:
                print(f"[OutlookGraphTool] ✓ Analyse in E-Mail-Categories gespeichert")
                return {'success': True}
            else:
                error_msg = f"HTTP {response.status_code}: {response.text}"
                print(f"[OutlookGraphTool] ❌ Fehler beim Speichern: {error_msg}")
                return {'success': False, 'error': error_msg}

        except Exception as e:
            error_msg = str(e)
            print(f"[OutlookGraphTool] ❌ Fehler beim Speichern der Analyse: {error_msg}")
            return {'success': False, 'error': error_msg}

    def get_email_analysis_from_categories(self, email: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Liest E-Mail-Analyse aus Outlook Categories

        Args:
            email: E-Mail-Dict mit 'categories' Property

        Returns:
            Analysis-Dict oder None falls nicht analysiert
        """
        categories = email.get('categories', [])

        # Prüfe ob E-Mail bereits analysiert wurde
        if "AI_Analyzed" not in categories:
            return None

        # Extrahiere Analyse-Daten aus Categories
        analysis = {
            'priority': 3,
            'category': 'Sonstiges',
            'sentiment': 'neutral',
            'summary': '',  # Nicht in Categories gespeichert
            'action_items': [],  # Nicht in Categories gespeichert
            'deadline': None  # Nicht in Categories gespeichert
        }

        for category in categories:
            if category.startswith('AI_Priority_'):
                try:
                    analysis['priority'] = int(category.replace('AI_Priority_', ''))
                except:
                    pass
            elif category.startswith('AI_Category_'):
                analysis['category'] = category.replace('AI_Category_', '')
            elif category.startswith('AI_Sentiment_'):
                analysis['sentiment'] = category.replace('AI_Sentiment_', '')

        print(f"[OutlookGraphTool] ✓ Analyse aus Categories gelesen: Priority={analysis['priority']}, Category={analysis['category']}")
        return analysis

    def list_all_folders(self) -> List[Dict[str, Any]]:
        """
        Listet alle verfügbaren E-Mail-Ordner auf (für Debugging)

        Returns:
            Liste von Ordner-Dictionaries mit 'id', 'displayName', 'parentFolderId'
        """
        if not self.access_token:
            print("[OutlookGraphTool] ⚠️ Keine Authentifizierung vorhanden")
            return []

        try:
            import requests

            url = "https://graph.microsoft.com/v1.0/me/mailFolders"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }

            response = requests.get(url, headers=headers)

            if response.status_code == 200:
                folders = response.json().get('value', [])
                print(f"[OutlookGraphTool] ✓ {len(folders)} Root-Ordner gefunden:")
                for folder in folders:
                    print(f"  - {folder.get('displayName')} (ID: {folder.get('id')})")
                return folders
            else:
                print(f"[OutlookGraphTool] ❌ Fehler beim Abrufen der Ordner: {response.status_code}")
                return []

        except Exception as e:
            print(f"[OutlookGraphTool] ❌ Fehler beim Auflisten der Ordner: {e}")
            import traceback
            traceback.print_exc()
            return []

    def forward_email(self, email_id: str, to_recipients: List[str], comment: str = "") -> Dict[str, Any]:
        """
        Leitet E-Mail weiter

        Args:
            email_id: Die ID der E-Mail
            to_recipients: Liste von E-Mail-Adressen
            comment: Optional: Kommentar/Nachricht bei Weiterleitung

        Returns:
            Dictionary mit 'success' (bool) und optional 'error' (str)
        """
        if not self.access_token:
            return {'success': False, 'error': 'Keine Authentifizierung vorhanden'}

        try:
            import requests

            url = f"https://graph.microsoft.com/v1.0/me/messages/{email_id}/forward"

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }

            # Formatiere Empfänger
            recipients = [{"emailAddress": {"address": addr}} for addr in to_recipients]

            body = {
                "toRecipients": recipients,
                "comment": comment
            }

            response = requests.post(url, headers=headers, json=body)

            if response.status_code == 202 or response.status_code == 200:
                print(f"[OutlookGraphTool] ✓ E-Mail weitergeleitet an {', '.join(to_recipients)}")
                return {'success': True}
            else:
                error_msg = f"HTTP {response.status_code}: {response.text}"
                print(f"[OutlookGraphTool] ❌ Fehler beim Weiterleiten: {error_msg}")
                return {'success': False, 'error': error_msg}

        except Exception as e:
            error_msg = str(e)
            print(f"[OutlookGraphTool] ❌ Fehler beim Weiterleiten: {error_msg}")
            import traceback
            traceback.print_exc()
            return {'success': False, 'error': error_msg}

    def reply_email(self, email_id: str, comment: str, reply_all: bool = False) -> Dict[str, Any]:
        """
        Antwortet auf eine E-Mail

        Args:
            email_id: Die ID der E-Mail
            comment: Der Antworttext
            reply_all: Falls True, wird an alle geantwortet (Reply All)

        Returns:
            Dictionary mit 'success' (bool) und optional 'error' (str)
        """
        if not self.access_token:
            return {'success': False, 'error': 'Keine Authentifizierung vorhanden'}

        try:
            import requests

            # Wähle den richtigen Endpoint
            action = "replyAll" if reply_all else "reply"
            url = f"https://graph.microsoft.com/v1.0/me/messages/{email_id}/{action}"

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }

            body = {
                "comment": comment
            }

            response = requests.post(url, headers=headers, json=body)

            if response.status_code == 202 or response.status_code == 200:
                reply_type = "allen" if reply_all else "Absender"
                print(f"[OutlookGraphTool] ✓ E-Mail-Antwort an {reply_type} gesendet")
                return {'success': True}
            else:
                error_msg = f"HTTP {response.status_code}: {response.text}"
                print(f"[OutlookGraphTool] ❌ Fehler beim Antworten: {error_msg}")
                return {'success': False, 'error': error_msg}

        except Exception as e:
            error_msg = str(e)
            print(f"[OutlookGraphTool] ❌ Fehler beim Antworten: {error_msg}")
            import traceback
            traceback.print_exc()
            return {'success': False, 'error': error_msg}
