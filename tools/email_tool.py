"""
Email Tool für Outlook-Integration
"""

import os
import smtplib
from typing import Dict, Any, Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


class EmailTool:
    """Tool zum Versenden von E-Mails über Outlook SMTP oder Microsoft Graph API"""

    def __init__(self, email_address: str = None, password: str = None):
        """
        Initialisiert das EmailTool

        Args:
            email_address: Outlook E-Mail-Adresse (z.B. user@herbert.de)
            password: App-Passwort oder reguläres Passwort
        """
        # Stelle sicher dass .env geladen ist
        self._ensure_env_loaded()

        self.email_address = email_address or os.getenv("OUTLOOK_EMAIL")
        self.password = password or os.getenv("OUTLOOK_PASSWORD")
        self.smtp_server = "smtp.office365.com"
        self.smtp_port = 587

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

    def send_email_smtp(
        self,
        to: str,
        subject: str,
        body: str,
        cc: Optional[str] = None,
        bcc: Optional[str] = None,
        html: bool = False
    ) -> Dict[str, Any]:
        """
        Versendet eine E-Mail über SMTP

        Args:
            to: Empfänger-Adresse(n) (komma-separiert)
            subject: Betreff
            body: E-Mail-Inhalt
            cc: CC-Empfänger (optional)
            bcc: BCC-Empfänger (optional)
            html: Ob der Body HTML enthält

        Returns:
            Dict mit Status und Nachricht
        """
        if not self.email_address:
            return {
                "status": "error",
                "message": "E-Mail-Adresse nicht konfiguriert. Setze OUTLOOK_EMAIL in .env"
            }

        if not self.password:
            return self._get_graph_api_instructions()

        try:
            # Erstelle MIME-Nachricht
            msg = MIMEMultipart("alternative") if html else MIMEMultipart()
            msg["From"] = self.email_address
            msg["To"] = to
            msg["Subject"] = subject

            if cc:
                msg["Cc"] = cc
            if bcc:
                msg["Bcc"] = bcc

            # Füge Body hinzu
            if html:
                msg.attach(MIMEText(body, "html"))
            else:
                msg.attach(MIMEText(body, "plain"))

            # Verbinde mit SMTP-Server
            print(f"[EmailTool] Verbinde mit {self.smtp_server}:{self.smtp_port}...")
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                print(f"[EmailTool] Authentifiziere als {self.email_address}...")
                server.login(self.email_address, self.password)

                # Sende E-Mail
                recipients = [to]
                if cc:
                    recipients.extend(cc.split(","))
                if bcc:
                    recipients.extend(bcc.split(","))

                print(f"[EmailTool] Sende E-Mail an {len(recipients)} Empfänger...")
                server.sendmail(self.email_address, recipients, msg.as_string())

            print(f"[EmailTool] ✓ E-Mail erfolgreich versendet!")
            return {
                "status": "success",
                "message": f"E-Mail erfolgreich an {to} versendet",
                "method": "SMTP"
            }

        except smtplib.SMTPAuthenticationError:
            print(f"[EmailTool] ✗ SMTP-Authentifizierung fehlgeschlagen")
            return self._get_graph_api_instructions()

        except Exception as e:
            print(f"[EmailTool] ✗ Fehler beim E-Mail-Versand: {e}")
            return {
                "status": "error",
                "message": f"Fehler beim E-Mail-Versand: {str(e)}",
                "fallback": "Versuche Microsoft Graph API (siehe Anleitung)"
            }

    def _get_graph_api_instructions(self) -> Dict[str, Any]:
        """
        Gibt Anweisungen für die Microsoft Graph API Konfiguration zurück

        Returns:
            Dict mit Status und Anleitung
        """
        instructions = """
📧 OUTLOOK E-MAIL KONFIGURATION

⚠️ SMTP-Authentifizierung nicht verfügbar oder fehlgeschlagen.

Für Business-Konten (@herbert.de) gibt es zwei Optionen:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OPTION 1: APP-PASSWORT (Einfacher)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Gehe zu https://account.microsoft.com/security
2. Unter "Erweiterte Sicherheitsoptionen" → "App-Kennwörter"
3. Erstelle ein neues App-Passwort
4. Füge es in die .env Datei ein:

   OUTLOOK_EMAIL=deine-email@herbert.de
   OUTLOOK_PASSWORD=dein-app-passwort

5. Starte das Programm neu

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OPTION 2: MICROSOFT GRAPH API (Für Business-Konten)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SCHRITT 1: App registrieren in Azure
1. Gehe zu https://portal.azure.com
2. Suche "App registrations" (App-Registrierungen)
3. Klicke "New registration"
   - Name: "Mein Assistent Email Tool"
   - Supported account types: "Accounts in any organizational directory"
   - Redirect URI: "http://localhost:8000" (Web)
4. Notiere die "Application (client) ID"
5. Notiere die "Directory (tenant) ID"

SCHRITT 2: Client Secret erstellen
1. In deiner App → "Certificates & secrets"
2. "New client secret"
3. Beschreibung: "Email Tool Secret"
4. Ablaufdatum: 24 Monate
5. Notiere den "Value" (wird nur einmal angezeigt!)

SCHRITT 3: API-Berechtigungen setzen
1. In deiner App → "API permissions"
2. "Add a permission" → "Microsoft Graph" → "Application permissions"
3. Wähle aus:
   - Mail.Send (E-Mails senden)
   - User.Read.All (optional, für Kalenderzugriff)
4. Klicke "Grant admin consent for [Your Organization]"

SCHRITT 4: Konfiguration in .env
Füge folgende Zeilen in deine .env Datei ein:

OUTLOOK_EMAIL=deine-email@herbert.de
GRAPH_CLIENT_ID=deine-application-id
GRAPH_CLIENT_SECRET=dein-client-secret
GRAPH_TENANT_ID=deine-tenant-id

SCHRITT 5: Installiere Graph API Library
pip install msgraph-core requests

SCHRITT 6: Programm neu starten
Das Tool erkennt automatisch die Graph API Konfiguration.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Bei Fragen oder Problemen siehe:
https://learn.microsoft.com/en-us/graph/auth-v2-service
"""

        return {
            "status": "configuration_required",
            "message": instructions,
            "method": "Graph API Setup Required"
        }

    def send_email_graph(
        self,
        to: str,
        subject: str,
        body: str,
        cc: Optional[str] = None,
        html: bool = False
    ) -> Dict[str, Any]:
        """
        Versendet eine E-Mail über Microsoft Graph API

        Args:
            to: Empfänger-Adresse
            subject: Betreff
            body: E-Mail-Inhalt
            cc: CC-Empfänger (optional)
            html: Ob der Body HTML enthält

        Returns:
            Dict mit Status und Nachricht
        """
        client_id = os.getenv("GRAPH_CLIENT_ID")
        client_secret = os.getenv("GRAPH_CLIENT_SECRET")
        tenant_id = os.getenv("GRAPH_TENANT_ID")

        if not all([client_id, client_secret, tenant_id]):
            return self._get_graph_api_instructions()

        try:
            import requests

            # Hole Access Token
            token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
            token_data = {
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials"
            }

            print("[EmailTool] Authentifiziere mit Microsoft Graph API...")
            token_response = requests.post(token_url, data=token_data)
            token_response.raise_for_status()
            access_token = token_response.json()["access_token"]

            # Erstelle E-Mail
            email_msg = {
                "message": {
                    "subject": subject,
                    "body": {
                        "contentType": "HTML" if html else "Text",
                        "content": body
                    },
                    "toRecipients": [
                        {"emailAddress": {"address": to}}
                    ]
                },
                "saveToSentItems": "true"
            }

            if cc:
                email_msg["message"]["ccRecipients"] = [
                    {"emailAddress": {"address": addr.strip()}}
                    for addr in cc.split(",")
                ]

            # Sende E-Mail
            send_url = f"https://graph.microsoft.com/v1.0/users/{self.email_address}/sendMail"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }

            print(f"[EmailTool] Sende E-Mail via Graph API an {to}...")
            send_response = requests.post(send_url, json=email_msg, headers=headers)
            send_response.raise_for_status()

            print(f"[EmailTool] ✓ E-Mail erfolgreich versendet via Graph API!")
            return {
                "status": "success",
                "message": f"E-Mail erfolgreich an {to} versendet",
                "method": "Microsoft Graph API"
            }

        except ImportError:
            return {
                "status": "error",
                "message": "Microsoft Graph API Library nicht installiert. Führe aus: pip install msgraph-core requests"
            }
        except Exception as e:
            print(f"[EmailTool] ✗ Graph API Fehler: {e}")
            return {
                "status": "error",
                "message": f"Fehler beim E-Mail-Versand via Graph API: {str(e)}"
            }

    def invoke(self, to: str, subject: str, body: str, **kwargs) -> str:
        """
        LangChain-Tool-kompatible Invoke-Methode

        Args:
            to: Empfänger-Adresse
            subject: Betreff
            body: E-Mail-Inhalt
            **kwargs: Zusätzliche Parameter (cc, bcc, html)

        Returns:
            Formatiertes Ergebnis
        """
        cc = kwargs.get("cc")
        bcc = kwargs.get("bcc")
        html = kwargs.get("html", False)

        print(f"[EmailTool] invoke() aufgerufen: to={to}, subject={subject}")

        # Validiere Parameter
        if not to or not subject or not body:
            return "❌ FEHLER: 'to', 'subject' und 'body' sind erforderliche Parameter!"

        # Versuche zuerst SMTP
        result = self.send_email_smtp(to, subject, body, cc, bcc, html)

        # Bei SMTP-Fehler: Versuche Graph API
        if result["status"] == "error" and os.getenv("GRAPH_CLIENT_ID"):
            print("[EmailTool] SMTP fehlgeschlagen, versuche Graph API...")
            result = self.send_email_graph(to, subject, body, cc, html)

        # Formatiere Ausgabe
        if result["status"] == "success":
            return f"✓ E-Mail erfolgreich versendet an {to} via {result['method']}"
        elif result["status"] == "configuration_required":
            return result["message"]
        else:
            return f"❌ {result['message']}"
