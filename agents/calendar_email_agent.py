"""
Calendar & Email Agent für Kalender- und E-Mail-Operationen
"""

import os
from typing import Dict, Any, List
from datetime import datetime, timedelta
from ._tool_allowlist import assert_tools_allowlisted
from .base_agent import BaseAgent
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from tools import EmailTool, OutlookGraphTool


class CalendarEmailAgent(BaseAgent):
    """Agent für Kalender- und E-Mail-Operationen (CRUD)"""

    def __init__(self, api_key: str = None, llm_provider: str = None, outlook_tool=None):
        super().__init__("CalendarEmailAgent")

        # LLM Provider bestimmen
        self.llm_provider = llm_provider or os.getenv("LLM_PROVIDER", "anthropic")

        # API-Key laden
        if self.llm_provider == "anthropic":
            self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        else:
            self.api_key = api_key or os.getenv("OPENAI_API_KEY")

        # LLM und Tools initialisieren
        self.llm = self._initialize_llm()
        self.email_tool = self._initialize_email_tool()
        self.outlook_tool = outlook_tool if outlook_tool else self._initialize_outlook_tool()

        print(f"DEBUG: CalendarEmailAgent initialisiert")
        print(f"DEBUG: Email Tool: {self.email_tool is not None}")
        print(f"DEBUG: Outlook Tool: {self.outlook_tool is not None}")

    def _initialize_llm(self):
        """Initialisiert das LLM basierend auf dem Provider"""
        if not self.api_key:
            api_key_name = "ANTHROPIC_API_KEY" if self.llm_provider == "anthropic" else "OPENAI_API_KEY"
            print(f"\n❌ FEHLER: {api_key_name} fehlt in der .env-Datei!")
            print(f"Bitte füge deinen API-Key in die .env-Datei ein.")
            return None

        try:
            if self.llm_provider == "anthropic":
                from langchain_anthropic import ChatAnthropic
                model = os.getenv("CALENDAR_EMAIL_MODEL", os.getenv("TASK_MODEL", "claude-sonnet-4-5"))
                return ChatAnthropic(
                    api_key=self.api_key,
                    model=model,
                    temperature=float(os.getenv("TEMPERATURE", "0.7"))
                )
            else:
                from langchain_openai import ChatOpenAI
                model = os.getenv("CALENDAR_EMAIL_MODEL", "gpt-4")
                return ChatOpenAI(
                    api_key=self.api_key,
                    model=model,
                    temperature=float(os.getenv("TEMPERATURE", "0.7"))
                )
        except ImportError as e:
            print(f"\n⚠️ Warnung: LLM-Provider nicht verfügbar ({e})")
            print("Installiere: pip install langchain-anthropic oder langchain-openai")
            return None
        except Exception as e:
            print(f"\n⚠️ Fehler bei LLM-Initialisierung: {e}")
            return None

    def _initialize_email_tool(self):
        """Initialisiert das Email Tool"""
        try:
            email_tool = EmailTool()
            return email_tool
        except Exception as e:
            print(f"\n⚠️ Fehler bei Email-Tool-Initialisierung: {e}")
            return None

    def _initialize_outlook_tool(self):
        """Initialisiert das Outlook Graph Tool"""
        try:
            outlook_tool = OutlookGraphTool()
            return outlook_tool
        except Exception as e:
            print(f"\n⚠️ Fehler bei Outlook-Tool-Initialisierung: {e}")
            return None

    def process(self, input_data: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Verarbeitet Kalender- und E-Mail-Anfragen

        Args:
            input_data: Die Anfrage (z.B. "Zeige mir meine Termine heute")
            context: Zusätzlicher Kontext

        Returns:
            Dict mit Ergebnissen
        """
        print(f"\n[{self.name}] Verarbeite Anfrage...")
        print(f"[{self.name}] Provider: {self.llm_provider}")

        if not self.llm:
            return {
                "agent": self.name,
                "request": input_data,
                "result": "LLM nicht verfügbar - bitte API-Key konfigurieren",
                "status": "error",
                "context": context or {}
            }

        if not self.email_tool or not self.outlook_tool:
            return {
                "agent": self.name,
                "request": input_data,
                "result": "Email-Tool oder Outlook-Tool nicht verfügbar",
                "status": "error",
                "context": context or {}
            }

        try:
            # Erstelle LangChain-kompatible Tools mit strukturierten Inputs
            from langchain_core.tools import StructuredTool
            from pydantic import BaseModel, Field
            from typing import Optional

            # Pydantic-Modelle für Tool-Inputs
            class ListCalendarEventsInput(BaseModel):
                start_date: Optional[str] = Field(None, description="Start-Datum im Format YYYY-MM-DD")
                end_date: Optional[str] = Field(None, description="End-Datum im Format YYYY-MM-DD")

            class SearchEmailsInput(BaseModel):
                search_query: str = Field(..., description="Suchbegriff für E-Mails")
                max_results: int = Field(10, description="Maximale Anzahl Ergebnisse")
                days_back: int = Field(30, description="Wie viele Tage zurück suchen")

            class SendEmailInput(BaseModel):
                to: str = Field(..., description="Empfänger-E-Mail-Adresse")
                subject: str = Field(..., description="Betreff der E-Mail")
                body: str = Field(..., description="Inhalt der E-Mail")
                cc: Optional[str] = Field(None, description="CC-Empfänger (komma-separiert)")
                html: bool = Field(False, description="True wenn Body HTML enthält")

            class CreateEmailDraftInput(BaseModel):
                subject: str = Field(..., description="Betreff der E-Mail")
                body: str = Field(..., description="Inhalt der E-Mail")
                to_recipients: str = Field(..., description="Empfänger (komma-separiert)")
                cc_recipients: Optional[str] = Field(None, description="CC-Empfänger (komma-separiert)")

            class AddEventAttachmentInput(BaseModel):
                event_id: str = Field(..., description="ID des Events")
                file_path: str = Field(..., description="Pfad zur Datei")
                file_name: Optional[str] = Field(None, description="Name der Datei")

            class SearchContactsInput(BaseModel):
                search_query: str = Field(..., description="Suchbegriff für Kontakte (Name oder E-Mail)")
                max_results: int = Field(10, description="Maximale Anzahl Ergebnisse")

            tools = [
                StructuredTool(
                    name="list_calendar_events",
                    description="""Listet Kalender-Events für einen bestimmten Zeitraum auf.

BEISPIELE:
- list_calendar_events() → Events von heute
- list_calendar_events(start_date="2026-01-29", end_date="2026-01-31") → Events für 29.-31. Januar

Nutze dieses Tool wenn der Nutzer nach Terminen, Meetings oder Kalender-Events fragt.""",
                    func=self._list_calendar_events_wrapper,
                    args_schema=ListCalendarEventsInput
                ),
                StructuredTool(
                    name="search_emails",
                    description="""Sucht nach E-Mails basierend auf einem Suchbegriff.

BEISPIELE:
- search_emails(search_query="Meeting")
- search_emails(search_query="Projekt X", max_results=5, days_back=60)

Nutze dieses Tool wenn der Nutzer nach E-Mails suchen möchte.""",
                    func=self._search_emails_wrapper,
                    args_schema=SearchEmailsInput
                ),
                StructuredTool(
                    name="send_email",
                    description="""Versendet eine E-Mail über Outlook.

BEISPIELE:
- send_email(to="empfaenger@example.com", subject="Meeting", body="Hallo...")
- send_email(to="person@firma.de", subject="Angebot", body="<h1>Angebot</h1>...", html=True)

Nutze dieses Tool nur, wenn der Nutzer explizit eine E-Mail versenden möchte.""",
                    func=self._send_email_wrapper,
                    args_schema=SendEmailInput
                ),
                StructuredTool(
                    name="create_email_draft",
                    description="""Erstellt einen E-Mail-Entwurf in Outlook (ohne zu versenden).

BEISPIELE:
- create_email_draft(subject="Meeting", body="Hallo...", to_recipients="person@example.com")
- create_email_draft(subject="Bericht", body="<h1>Bericht</h1>...", to_recipients="team@firma.de", cc_recipients="boss@firma.de")

Nutze dieses Tool wenn der Nutzer einen Entwurf erstellen möchte.""",
                    func=self._create_email_draft_wrapper,
                    args_schema=CreateEmailDraftInput
                ),
                StructuredTool(
                    name="add_event_attachment",
                    description="""Fügt eine Datei als Anhang zu einem Kalender-Event hinzu.

BEISPIEL:
- add_event_attachment(event_id="ABC123", file_path="/pfad/zur/datei.pdf", file_name="Meeting_Agenda.pdf")

Nutze dieses Tool um Dokumente an einen Termin anzuhängen.""",
                    func=self._add_event_attachment_wrapper,
                    args_schema=AddEventAttachmentInput
                ),
                StructuredTool(
                    name="search_contacts",
                    description="""Sucht Kontakte im Adressbuch basierend auf einem Suchbegriff.

BEISPIELE:
- search_contacts(search_query="Max Mustermann") → Sucht nach Namen
- search_contacts(search_query="max@example.com") → Sucht nach E-Mail
- search_contacts(search_query="Herbert") → Sucht nach Teil des Namens

WICHTIG: Nutze dieses Tool wenn der Nutzer eine E-Mail an eine Person schreiben möchte,
aber keine E-Mail-Adresse angegeben hat. Das Tool findet die E-Mail-Adresse im Adressbuch.""",
                    func=self._search_contacts_wrapper,
                    args_schema=SearchContactsInput
                )
            ]

            # Binde Tools an LLM
            assert_tools_allowlisted(tools, self.name)
            llm_with_tools = self.llm.bind_tools(tools)

            # Erstelle System-Prompt
            system_prompt = self._create_system_prompt()

            # Initialisiere Nachrichtenliste
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=self._create_user_prompt(input_data, context))
            ]

            # Agent-Schleife: Maximal 5 Iterationen
            max_iterations = 5
            tool_calls_made = []

            for iteration in range(max_iterations):
                print(f"[{self.name}] Iteration {iteration + 1}/{max_iterations}")

                # Rufe LLM auf
                response = llm_with_tools.invoke(messages)
                messages.append(response)

                # Prüfe ob Tool-Calls vorhanden sind
                if not response.tool_calls:
                    # Keine weiteren Tool-Calls, wir haben die finale Antwort
                    print(f"[{self.name}] ✓ Antwort generiert")
                    result_text = response.content
                    break

                # Führe Tool-Calls aus
                for tool_call in response.tool_calls:
                    tool_name = tool_call.get("name", "unknown")
                    tool_args = tool_call.get("args", {})
                    print(f"[{self.name}] 🔧 Führe Tool aus: {tool_name}")
                    print(f"[{self.name}] 📥 Tool-Argumente: {tool_args}")

                    try:
                        # Finde und führe das entsprechende Tool aus
                        tool_func = None
                        for tool in tools:
                            if tool.name == tool_name:
                                tool_func = tool.func
                                break

                        if tool_func:
                            tool_result = tool_func(**tool_args)
                            tool_calls_made.append(tool_name)
                            print(f"[{self.name}] ✓ Tool-Ergebnis erhalten")
                        else:
                            tool_result = f"Tool {tool_name} nicht gefunden"

                        # Erstelle Tool-Message
                        tool_message = ToolMessage(
                            content=str(tool_result),
                            tool_call_id=tool_call["id"]
                        )
                        messages.append(tool_message)

                    except Exception as e:
                        print(f"[{self.name}] ⚠️ Tool-Fehler: {e}")
                        tool_message = ToolMessage(
                            content=f"Fehler beim Tool-Aufruf: {str(e)}",
                            tool_call_id=tool_call["id"]
                        )
                        messages.append(tool_message)
            else:
                # Max Iterationen erreicht
                result_text = messages[-1].content if isinstance(messages[-1], AIMessage) else "Maximale Iterationen erreicht"

            result = {
                "agent": self.name,
                "request": input_data,
                "result": result_text,
                "tools_used": list(set(tool_calls_made)),
                "status": "success",
                "context": context or {}
            }

            print(f"[{self.name}] ✓ Anfrage abgeschlossen")

        except Exception as e:
            print(f"[{self.name}] ✗ Fehler: {e}")
            import traceback
            traceback.print_exc()
            result = {
                "agent": self.name,
                "request": input_data,
                "result": f"Fehler bei der Verarbeitung: {str(e)}",
                "status": "error",
                "context": context or {}
            }

        self.add_to_memory(result)
        return result

    def _create_system_prompt(self) -> str:
        """Erstellt den System-Prompt für den CalendarEmail-Agent"""
        smtp_configured = bool(self.email_tool and self.email_tool.email_address)
        outlook_authenticated = bool(self.outlook_tool and self.outlook_tool.access_token)
        outlook_configured = bool(self.outlook_tool and self.outlook_tool.is_configured)

        # Status-Messages
        calendar_status = "✓ Authentifiziert" if outlook_authenticated else "⚠️ Authentifizierung erforderlich"
        draft_status = "✓ Verfügbar (Graph API)" if outlook_authenticated else "⚠️ Authentifizierung erforderlich"
        send_status = f"✓ Verfügbar (SMTP: {self.email_tool.email_address})" if smtp_configured else draft_status
        search_status = "✓ Verfügbar (Graph API)" if outlook_authenticated else "⚠️ Authentifizierung erforderlich"

        return f"""Du bist ein CalendarEmail-Agent in einem Multi-Agenten-System.
Deine Hauptaufgabe ist es, Kalender- und E-Mail-Operationen durchzuführen.

VERFÜGBARE TOOLS & STATUS:

📅 KALENDER-OPERATIONEN:
   - list_calendar_events: Termine auflisten | {calendar_status}
   - add_event_attachment: Dokumente an Termin anhängen | {calendar_status}

📧 E-MAIL-OPERATIONEN:
   - search_emails: E-Mails suchen | {search_status}
   - send_email: E-Mail direkt versenden | {send_status}
   - create_email_draft: Entwurf in Outlook erstellen | {draft_status}

📇 KONTAKT-OPERATIONEN:
   - search_contacts: Kontakte im Adressbuch suchen | {search_status}

WICHTIGE HINWEISE:
- Kalender & E-Mail-Entwürfe benötigen Microsoft Graph API Authentifizierung
- E-Mail-Versand kann auch via SMTP erfolgen (wenn OUTLOOK_EMAIL konfiguriert)
- Falls nicht authentifiziert: Nutzer muss sich anmelden

ARBEITSWEISE:

1. ANFRAGE ANALYSIEREN
   - Verstehe was der Nutzer möchte
   - Identifiziere welche Tools benötigt werden
   - Prüfe ob alle erforderlichen Informationen vorhanden sind

2. TOOLS NUTZEN
   - Nutze die passenden Tools für die Anfrage
   - Bei Kalender-Anfragen: list_calendar_events
   - Bei E-Mail-Suche: search_emails
   - Bei E-Mail-Versand: send_email oder create_email_draft
   - Bei Dokument-Anhängen: add_event_attachment
   - Bei fehlender E-Mail-Adresse: search_contacts um Person im Adressbuch zu finden

3. ERGEBNISSE PRÄSENTIEREN
   - Fasse die Ergebnisse klar und strukturiert zusammen
   - Bei Listen: Zeige die wichtigsten Informationen
   - Bei Erfolg: Bestätige die Aktion
   - Bei Fehler: Erkläre was schiefgelaufen ist

WICHTIGE REGELN:
- Nutze IMMER die Tools um Informationen abzurufen
- RATE NICHT - wenn du keine Informationen hast, nutze die Tools
- Bei Datumsangaben: Verwende das Format "YYYY-MM-DD"
- Bei fehlenden Informationen: Frage den Nutzer
- Versende E-Mails NUR wenn explizit gefordert
- Sei präzise und professionell

BEISPIEL-ABLÄUFE:

1. Termine auflisten:
   Nutzer: "Zeige mir meine Termine heute"
   → Tool: list_calendar_events()
   → Antwort: Liste der heutigen Termine mit Zeit, Titel, Ort

2. E-Mails suchen:
   Nutzer: "Suche E-Mails zum Thema Projekt X"
   → Tool: search_emails(search_query="Projekt X")
   → Antwort: Liste relevanter E-Mails mit Betreff, Absender, Datum

3. E-Mail versenden:
   Nutzer: "Schreibe eine E-Mail an max@example.com dass das Meeting verschoben wird"
   → Tool: send_email(to="max@example.com", subject="Meeting-Verschiebung", body="...")
   → Antwort: Bestätigung des Versands

4. Entwurf erstellen:
   Nutzer: "Erstelle einen Entwurf für die Statusmeldung"
   → Tool: create_email_draft(...)
   → Antwort: Bestätigung dass Entwurf erstellt wurde

5. Kontakte suchen:
   Nutzer: "Erstelle eine E-Mail an Max Mustermann"
   → Tool: search_contacts(search_query="Max Mustermann")
   → Ergebnis: max.mustermann@firma.de
   → Tool: create_email_draft(to_recipients="max.mustermann@firma.de", ...)
   → Antwort: Entwurf erstellt mit E-Mail-Adresse aus dem Adressbuch"""

    def _create_user_prompt(self, request: str, context: Dict[str, Any] = None) -> str:
        """Erstellt den User-Prompt für die Anfrage"""
        prompt = ""

        # Füge Kontext hinzu falls vorhanden
        if context:
            prompt += f"""Kontext:
{context}

---

"""

        prompt += f"""Anfrage:

{request}"""

        return prompt

    # Tool-Wrapper-Methoden

    def _list_calendar_events_wrapper(self, start_date: str = None, end_date: str = None) -> str:
        """Wrapper für list_calendar_events"""
        try:
            # Prüfe ob Outlook authentifiziert ist
            if not self.outlook_tool.access_token:
                return """❌ Microsoft Graph API nicht authentifiziert!

Um Kalender-Termine abzurufen, müssen Sie sich zuerst mit Ihrem Microsoft-Konto anmelden.

**So authentifizieren Sie sich:**

1. Öffnen Sie ein Terminal im Projektverzeichnis
2. Führen Sie aus: `python authenticate_outlook.py`
3. Folgen Sie den Anweisungen auf dem Bildschirm
4. Nach erfolgreicher Anmeldung können Sie diesen Agent nutzen"""

            # Parse Datumsangaben
            if not start_date:
                start_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                start_dt = datetime.strptime(start_date, "%Y-%m-%d")

            if not end_date:
                end_dt = start_dt.replace(hour=23, minute=59, second=59)
            else:
                end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)

            # Hole Events
            events = self.outlook_tool.get_events_for_date_range(start_dt, end_dt)

            if not events:
                date_str = start_dt.strftime("%d.%m.%Y")
                if start_date != end_date and end_date:
                    date_str += f" - {end_dt.strftime('%d.%m.%Y')}"
                return f"📅 Keine Termine gefunden für {date_str}"

            # Formatiere Ergebnisse
            date_str = start_dt.strftime("%d.%m.%Y")
            if start_date != end_date and end_date:
                date_str += f" - {end_dt.strftime('%d.%m.%Y')}"

            result = f"📅 **Termine für {date_str}** ({len(events)} gefunden)\n\n"
            for idx, event in enumerate(events, 1):
                result += f"**{idx}. {event.get('title', 'Kein Titel')}**\n"
                result += f"   🕐 {event.get('start', '')} - {event.get('end', '')}\n"
                if event.get('location'):
                    result += f"   📍 {event.get('location')}\n"
                if event.get('attendees'):
                    result += f"   👥 {', '.join(event.get('attendees', []))}\n"
                result += f"   🆔 ID: `{event.get('id')}`\n\n"

            return result

        except Exception as e:
            import traceback
            return f"❌ Fehler beim Abrufen der Kalender-Events:\n{str(e)}\n\nDetails:\n{traceback.format_exc()}"

    def _search_emails_wrapper(self, search_query: str, max_results: int = 10, days_back: int = 30) -> str:
        """Wrapper für search_emails"""
        try:
            # Prüfe ob Outlook authentifiziert ist
            if not self.outlook_tool.access_token:
                return """❌ Microsoft Graph API nicht authentifiziert!

Um E-Mails zu durchsuchen, müssen Sie sich zuerst mit Ihrem Microsoft-Konto anmelden.

**So authentifizieren Sie sich:**

1. Öffnen Sie ein Terminal im Projektverzeichnis
2. Führen Sie aus: `python authenticate_outlook.py`
3. Folgen Sie den Anweisungen auf dem Bildschirm
4. Nach erfolgreicher Anmeldung können Sie diesen Agent nutzen"""

            emails = self.outlook_tool.search_emails(search_query, max_results, days_back)

            if not emails:
                return f"📧 Keine E-Mails gefunden für: **{search_query}**\n\nZeitraum: Letzte {days_back} Tage"

            # Formatiere Ergebnisse
            result = f"📧 **E-Mail-Suche: {search_query}** ({len(emails)} gefunden)\n\n"
            for idx, email in enumerate(emails, 1):
                result += f"**{idx}. {email.get('subject', 'Kein Betreff')}**\n"
                result += f"   📤 Von: {email.get('from', 'Unbekannt')}\n"
                result += f"   📅 {email.get('received', '')}\n"
                if email.get('preview'):
                    preview = email.get('preview')[:150]
                    result += f"   💬 {preview}{'...' if len(email.get('preview', '')) > 150 else ''}\n"
                if email.get('web_link'):
                    result += f"   🔗 [In Outlook öffnen]({email.get('web_link')})\n"
                result += "\n"

            return result

        except Exception as e:
            import traceback
            return f"❌ Fehler bei der E-Mail-Suche:\n{str(e)}\n\nDetails:\n{traceback.format_exc()}"

    def _send_email_wrapper(self, to: str, subject: str, body: str, cc: str = None, html: bool = False) -> str:
        """Wrapper für send_email"""
        try:
            result = self.email_tool.invoke(
                to=to,
                subject=subject,
                body=body,
                cc=cc,
                html=html
            )
            return result
        except Exception as e:
            return f"Fehler beim E-Mail-Versand: {str(e)}"

    def _create_email_draft_wrapper(self, subject: str, body: str, to_recipients: str, cc_recipients: str = None) -> str:
        """Wrapper für create_email_draft"""
        try:
            # Prüfe ob Outlook authentifiziert ist
            if not self.outlook_tool.access_token:
                return """❌ Microsoft Graph API nicht authentifiziert!

Um E-Mail-Entwürfe zu erstellen, müssen Sie sich zuerst mit Ihrem Microsoft-Konto anmelden.

**So authentifizieren Sie sich:**

1. Öffnen Sie ein Terminal im Projektverzeichnis
2. Führen Sie aus: `python authenticate_outlook.py`
3. Folgen Sie den Anweisungen auf dem Bildschirm
4. Nach erfolgreicher Anmeldung können Sie diesen Agent nutzen

Ihre Konfiguration in .env:
- MICROSOFT_CLIENT_ID: ✓ Vorhanden
- MICROSOFT_TENANT_ID: ✓ Vorhanden
- Access Token: ✗ Fehlend (bitte authentifizieren)"""

            # Konvertiere komma-separierte Strings zu Listen
            to_list = [r.strip() for r in to_recipients.split(",")]
            cc_list = [r.strip() for r in cc_recipients.split(",")] if cc_recipients else []

            result = self.outlook_tool.create_email_draft(
                subject=subject,
                body=body,
                to_recipients=to_list,
                cc_recipients=cc_list
            )

            if result.get("success"):
                draft_id = result.get("draft_id", "")
                return f"""✓ E-Mail-Entwurf erfolgreich erstellt!

📧 Betreff: {subject}
📤 An: {to_recipients}
{f'📋 CC: {cc_recipients}' if cc_recipients else ''}
🆔 Draft-ID: {draft_id}

Der Entwurf ist jetzt in Ihrem Outlook verfügbar und kann dort bearbeitet und versendet werden."""
            else:
                error = result.get('error', 'Unbekannter Fehler')
                return f"✗ Fehler beim Erstellen des Entwurfs: {error}"

        except Exception as e:
            return f"Fehler beim Erstellen des E-Mail-Entwurfs: {str(e)}"

    def _add_event_attachment_wrapper(self, event_id: str, file_path: str, file_name: str = None) -> str:
        """Wrapper für add_event_attachment"""
        try:
            result = self.outlook_tool.add_attachment_to_event(
                event_id=event_id,
                file_path=file_path,
                file_name=file_name
            )

            if result.get("success"):
                return f"✓ Datei erfolgreich an Event angehängt: {file_name or file_path}"
            else:
                return f"✗ Fehler beim Anhängen: {result.get('error', 'Unbekannter Fehler')}"

        except Exception as e:
            return f"Fehler beim Anhängen der Datei: {str(e)}"

    def _search_contacts_wrapper(self, search_query: str, max_results: int = 10) -> str:
        """Wrapper für search_contacts"""
        try:
            # Prüfe ob Outlook authentifiziert ist
            if not self.outlook_tool.access_token:
                return """❌ Microsoft Graph API nicht authentifiziert!

Um Kontakte zu durchsuchen, müssen Sie sich zuerst mit Ihrem Microsoft-Konto anmelden.

**So authentifizieren Sie sich:**

1. Öffnen Sie ein Terminal im Projektverzeichnis
2. Führen Sie aus: `python authenticate_outlook.py`
3. Folgen Sie den Anweisungen auf dem Bildschirm
4. Nach erfolgreicher Anmeldung können Sie diesen Agent nutzen"""

            contacts = self.outlook_tool.search_contacts(search_query, max_results)

            if not contacts:
                return f"📇 Keine Kontakte gefunden für: **{search_query}**"

            # Formatiere Ergebnisse
            result = f"📇 **Kontakte gefunden: {search_query}** ({len(contacts)} Treffer)\n\n"
            for idx, contact in enumerate(contacts, 1):
                result += f"**{idx}. {contact.get('name', 'Kein Name')}**\n"

                # E-Mail-Adressen
                if contact.get('primary_email'):
                    result += f"   📧 {contact.get('primary_email')}\n"
                elif contact.get('emails'):
                    result += f"   📧 {', '.join(contact.get('emails', []))}\n"

                # Firma und Position
                if contact.get('company'):
                    result += f"   🏢 {contact.get('company')}"
                    if contact.get('job_title'):
                        result += f" - {contact.get('job_title')}"
                    result += "\n"

                # Telefon
                if contact.get('phones'):
                    result += f"   📞 {', '.join(contact.get('phones', []))}\n"

                result += "\n"

            return result

        except Exception as e:
            import traceback
            return f"❌ Fehler bei der Kontaktsuche:\n{str(e)}\n\nDetails:\n{traceback.format_exc()}"
