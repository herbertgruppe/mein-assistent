"""
Communication Agent für E-Mail-Versand und Kommunikation
"""

import os
from typing import Dict, Any
from .base_agent import BaseAgent
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from tools import EmailTool


class CommunicationAgent(BaseAgent):
    """Agent für E-Mail-Versand und externe Kommunikation"""

    def __init__(self, api_key: str = None, llm_provider: str = None):
        super().__init__("CommunicationAgent")

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

        print(f"DEBUG: Email Tool initialisiert: {self.email_tool is not None}")

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
                model = os.getenv("COMMUNICATION_MODEL", "claude-3-5-sonnet-latest")
                return ChatAnthropic(
                    api_key=self.api_key,
                    model=model,
                    temperature=float(os.getenv("TEMPERATURE", "0.7"))
                )
            else:
                from langchain_openai import ChatOpenAI
                model = os.getenv("COMMUNICATION_MODEL", "gpt-4")
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
        """Initialisiert das Email Tool für Outlook"""
        try:
            email_tool = EmailTool()
            print(f"DEBUG: Email-Tool konfiguriert für: {email_tool.email_address or 'Nicht konfiguriert'}")
            return email_tool
        except Exception as e:
            print(f"\n⚠️ Fehler bei Email-Tool-Initialisierung: {e}")
            return None

    def process(self, input_data: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Verarbeitet Kommunikationsanfragen (E-Mails versenden, etc.)

        Args:
            input_data: Die Kommunikationsanfrage
            context: Zusätzlicher Kontext

        Returns:
            Dict mit Kommunikations-Ergebnissen
        """
        print(f"\n[{self.name}] Verarbeite Kommunikationsanfrage...")
        print(f"[{self.name}] Provider: {self.llm_provider}")

        if not self.llm:
            return {
                "agent": self.name,
                "request": input_data,
                "result": "LLM nicht verfügbar - bitte API-Key konfigurieren",
                "status": "error",
                "context": context or {}
            }

        if not self.email_tool:
            return {
                "agent": self.name,
                "request": input_data,
                "result": "Email-Tool nicht verfügbar",
                "status": "error",
                "context": context or {}
            }

        try:
            # Erstelle LangChain-kompatibles Email-Tool
            from langchain_core.tools import Tool
            email_send_tool = Tool(
                name="send_email",
                description="""Versendet eine E-Mail über Outlook (Business-Konto @herbert.de).

PARAMETER (alle erforderlich):
- to: Empfänger-E-Mail-Adresse (String)
- subject: Betreff der E-Mail (String)
- body: Inhalt der E-Mail (String)

OPTIONALE PARAMETER:
- cc: CC-Empfänger (String, komma-separiert)
- html: True wenn Body HTML enthält (Boolean, default: False)

AUTHENTIFIZIERUNG:
Das Tool versucht automatisch:
1. SMTP-Authentifizierung (smtp.office365.com:587)
2. Falls SMTP fehlschlägt: Microsoft Graph API (falls konfiguriert)
3. Falls beide fehlschlagen: Ausgabe der Setup-Anleitung

BEISPIELE:
- send_email(to="empfaenger@example.com", subject="Meeting", body="Hallo...")
- send_email(to="person@firma.de", subject="Angebot", body="<h1>Angebot</h1>...", html=True)

Nutze dieses Tool nur, wenn der Nutzer explizit eine E-Mail versenden möchte.""",
                func=self.email_tool.invoke
            )

            # Binde Tool an LLM
            llm_with_tools = self.llm.bind_tools([email_send_tool])

            # Erstelle System-Prompt
            system_prompt = self._create_system_prompt()

            # Initialisiere Nachrichtenliste
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=self._create_user_prompt(input_data, context))
            ]

            # Agent-Schleife: Maximal 3 Iterationen (E-Mail braucht weniger)
            max_iterations = 3
            tool_calls_made = False

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
                tool_calls_made = True
                for tool_call in response.tool_calls:
                    tool_name = tool_call.get("name", "unknown")
                    tool_args = tool_call.get("args", {})
                    print(f"[{self.name}] 📧 Führe Tool aus: {tool_name}")
                    print(f"[{self.name}] 📥 Tool-Argumente: {tool_args}")

                    try:
                        # Führe das Email-Tool aus
                        if tool_name == "send_email":
                            # Validiere Pflichtparameter
                            to = tool_args.get("to", "").strip()
                            subject = tool_args.get("subject", "").strip()
                            body = tool_args.get("body", "").strip()

                            if not all([to, subject, body]):
                                tool_result = "❌ FEHLER: 'to', 'subject' und 'body' sind erforderlich!"
                            else:
                                tool_result = self.email_tool.invoke(
                                    to=to,
                                    subject=subject,
                                    body=body,
                                    cc=tool_args.get("cc"),
                                    html=tool_args.get("html", False)
                                )
                                print(f"[{self.name}] 📤 E-Mail-Ergebnis:\n{tool_result[:300]}...")
                        else:
                            tool_result = f"Tool {tool_name} nicht verfügbar"

                        # Erstelle Tool-Message
                        tool_message = ToolMessage(
                            content=str(tool_result),
                            tool_call_id=tool_call["id"]
                        )
                        messages.append(tool_message)
                        print(f"[{self.name}] ✓ Tool-Ergebnis übergeben")

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
                "email_sent": tool_calls_made,
                "status": "success",
                "context": context or {}
            }

            print(f"[{self.name}] ✓ Kommunikation abgeschlossen")

        except Exception as e:
            print(f"[{self.name}] ✗ Fehler: {e}")
            import traceback
            traceback.print_exc()
            result = {
                "agent": self.name,
                "request": input_data,
                "result": f"Fehler bei der Kommunikation: {str(e)}",
                "status": "error",
                "context": context or {}
            }

        self.add_to_memory(result)
        return result

    def _create_system_prompt(self) -> str:
        """Erstellt den System-Prompt für den Communication-Agent"""
        email_configured = bool(self.email_tool and self.email_tool.email_address)
        email_status = f"Konfiguriert für: {self.email_tool.email_address}" if email_configured else "Nicht konfiguriert"

        return f"""Du bist ein Communication-Agent in einem Multi-Agenten-System.
Deine Hauptaufgabe ist es, E-Mails zu verfassen und zu versenden.

VERFÜGBARES TOOL:

📧 EMAIL-VERSAND (send_email)
   - Status: {email_status}
   - Methode: SMTP (smtp.office365.com:587) oder Microsoft Graph API
   - Für: Business-Konto @herbert.de

ARBEITSWEISE:

1. ANFRAGE ANALYSIEREN
   - Prüfe ob der Nutzer wirklich eine E-Mail versenden möchte
   - Identifiziere: Empfänger, Betreff, Inhalt
   - Falls Informationen fehlen: Frage NICHT nach, sondern gib eine höfliche Fehlermeldung

2. E-MAIL VERFASSEN
   - Formuliere professionelle, höfliche E-Mails
   - Achte auf korrekte Anrede und Grußformel
   - Passe den Ton an den Kontext an (formell/informell)
   - Strukturiere längere E-Mails mit Absätzen

3. E-MAIL VERSENDEN
   - Nutze send_email Tool mit allen erforderlichen Parametern
   - to: Empfänger-Adresse
   - subject: Aussagekräftiger Betreff
   - body: Vollständiger E-Mail-Text

4. ERGEBNIS MITTEILEN
   - Bei Erfolg: Bestätige den Versand
   - Bei Fehler (nicht konfiguriert): Zeige die Setup-Anleitung
   - Bei SMTP-Fehler: Das Tool versucht automatisch Graph API

WICHTIGE REGELN:
- Versende NIEMALS E-Mails ohne explizite Nutzeranfrage
- Bestätige IMMER vor dem Versand, was versendet wird (im Tool-Call)
- Bei fehlender Konfiguration: Gib die vollständige Anleitung aus
- Frage NICHT nach Bestätigung - versende direkt wenn alle Infos da sind
- Bei unklaren Anfragen: Erkläre was benötigt wird

BEISPIEL-ABLAUF:
Nutzer: "Schreibe eine E-Mail an max@example.com, dass das Meeting auf morgen verschoben wird"
→ 1. Analysiere: Empfänger (max@example.com), Inhalt (Meeting-Verschiebung)
→ 2. Verfasse höfliche E-Mail mit Betreff "Meeting-Verschiebung" und passender Nachricht
→ 3. Nutze send_email Tool
→ 4. Bestätige: "E-Mail erfolgreich an max@example.com versendet"

FEHLERBEHANDLUNG:
- Wenn Email-Tool nicht konfiguriert ist: Zeige die Setup-Anleitung
- Wenn SMTP fehlschlägt: Tool versucht automatisch Graph API
- Wenn Graph API auch fehlschlägt: Zeige die vollständige Setup-Anleitung"""

    def _create_user_prompt(self, request: str, context: Dict[str, Any] = None) -> str:
        """Erstellt den User-Prompt für die Kommunikationsanfrage"""
        prompt = ""

        # Füge Nutzer-Kontext hinzu falls vorhanden
        if context and "user_context" in context:
            user_context = context["user_context"]
            if user_context:
                prompt += f"""{user_context}

---

"""

        prompt += f"""Kommunikationsanfrage:

{request}"""

        if context and not "user_context" in context:
            # Legacy: Falls Kontext ohne neue Struktur übergeben wird
            prompt += f"""

Zusätzlicher Kontext: {context}"""

        return prompt

    def send_email(self, to: str, subject: str, body: str, **kwargs) -> Dict[str, Any]:
        """
        Direkte Methode zum E-Mail-Versand (ohne LLM)

        Args:
            to: Empfänger
            subject: Betreff
            body: Inhalt
            **kwargs: Zusätzliche Parameter

        Returns:
            Dict mit Ergebnis
        """
        if not self.email_tool:
            return {
                "status": "error",
                "message": "Email-Tool nicht verfügbar"
            }

        result_text = self.email_tool.invoke(to=to, subject=subject, body=body, **kwargs)

        return {
            "agent": self.name,
            "status": "success",
            "result": result_text
        }
