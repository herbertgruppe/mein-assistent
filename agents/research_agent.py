"""
Research Agent für Informationsbeschaffung
"""

import os
from typing import Dict, Any, List
from ._tool_allowlist import assert_tools_allowlisted
from .base_agent import BaseAgent
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from tools import DocumentTool


class ResearchAgent(BaseAgent):
    """Agent für Recherche und Informationsbeschaffung"""

    def __init__(self, api_key: str = None, llm_provider: str = None):
        super().__init__("ResearchAgent")

        # LLM Provider bestimmen
        self.llm_provider = llm_provider or os.getenv("LLM_PROVIDER", "anthropic")

        # API-Key laden
        if self.llm_provider == "anthropic":
            self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        else:
            self.api_key = api_key or os.getenv("OPENAI_API_KEY")

        # Tavily API-Key für Websuche
        self.tavily_api_key = os.getenv("TAVILY_API_KEY")

        # DEBUG: Prüfe ob Tavily Key vorhanden ist
        print(f"DEBUG: Tavily Key vorhanden: {bool(self.tavily_api_key)}")
        if self.tavily_api_key:
            print(f"DEBUG: Tavily Key beginnt mit: {self.tavily_api_key[:10]}...")

        # LLM und Tools initialisieren
        self.llm = self._initialize_llm()
        self.tavily_tool = self._initialize_tavily()
        self.document_tool = self._initialize_document_tool()

        # DEBUG: Prüfe ob Tools initialisiert wurden
        print(f"DEBUG: Tavily Tool initialisiert: {self.tavily_tool is not None}")
        print(f"DEBUG: Document Tool initialisiert: {self.document_tool is not None}")

    def _initialize_llm(self):
        """Initialisiert das LLM basierend auf dem Provider"""
        # Prüfe ob API-Key vorhanden ist
        if not self.api_key:
            api_key_name = "ANTHROPIC_API_KEY" if self.llm_provider == "anthropic" else "OPENAI_API_KEY"
            print(f"\n❌ FEHLER: {api_key_name} fehlt in der .env-Datei!")
            print(f"Bitte füge deinen API-Key in die .env-Datei ein.")
            return None

        try:
            if self.llm_provider == "anthropic":
                from langchain_anthropic import ChatAnthropic
                model = os.getenv("RESEARCH_MODEL", "claude-3-5-sonnet-latest")
                return ChatAnthropic(
                    api_key=self.api_key,
                    model=model,
                    temperature=float(os.getenv("TEMPERATURE", "0.7")),
                    max_tokens=int(os.getenv("MAX_TOKENS", "8192"))
                )
            else:
                from langchain_openai import ChatOpenAI
                model = os.getenv("RESEARCH_MODEL", "gpt-4")
                return ChatOpenAI(
                    api_key=self.api_key,
                    model=model,
                    temperature=float(os.getenv("TEMPERATURE", "0.7")),
                    max_tokens=int(os.getenv("MAX_TOKENS", "8192"))
                )
        except ImportError as e:
            print(f"\n⚠️ Warnung: LLM-Provider nicht verfügbar ({e})")
            print("Installiere: pip install langchain-anthropic oder langchain-openai")
            return None
        except Exception as e:
            print(f"\n⚠️ Fehler bei LLM-Initialisierung: {e}")
            return None

    def _initialize_tavily(self):
        """Initialisiert das Tavily Search Tool für Websuchen"""
        if not self.tavily_api_key:
            print(f"\n⚠️ Warnung: TAVILY_API_KEY fehlt - Websuche nicht verfügbar")
            print("Bitte füge deinen Tavily API-Key in die .env-Datei ein: https://tavily.com/")
            return None

        try:
            from langchain_community.tools.tavily_search import TavilySearchResults

            return TavilySearchResults(
                api_key=self.tavily_api_key,
                max_results=5
            )
        except ImportError as e:
            print(f"\n⚠️ Warnung: Tavily-Tool nicht verfügbar ({e})")
            print("Installiere: pip install langchain-community")
            return None
        except Exception as e:
            print(f"\n⚠️ Fehler bei Tavily-Initialisierung: {e}")
            return None

    def _initialize_document_tool(self):
        """Initialisiert das Document Tool für lokale Dokumentensuche"""
        try:
            doc_tool = DocumentTool()
            doc_count = doc_tool.count_documents()
            print(f"DEBUG: {doc_count} Dokument(e) in input_docs/ gefunden")
            return doc_tool
        except Exception as e:
            print(f"\n⚠️ Fehler bei Document-Tool-Initialisierung: {e}")
            return None

    def process(self, input_data: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Führt Recherche basierend auf der Eingabe durch

        Args:
            input_data: Die Suchanfrage oder das Recherche-Thema
            context: Zusätzlicher Kontext

        Returns:
            Dict mit Recherche-Ergebnissen
        """
        print(f"\n[{self.name}] Führe Recherche durch...")
        print(f"[{self.name}] Provider: {self.llm_provider}")

        if not self.llm:
            return {
                "agent": self.name,
                "query": input_data,
                "findings": "LLM nicht verfügbar - bitte API-Key konfigurieren",
                "status": "error",
                "context": context or {}
            }

        try:
            # Sammle verfügbare Tools
            available_tools = []
            if self.tavily_tool:
                available_tools.append(self.tavily_tool)
            if self.document_tool:
                # Erstelle ein LangChain-kompatibles Tool aus dem DocumentTool
                from langchain_core.tools import Tool
                doc_search_tool = Tool(
                    name="search_local_documents",
                    description="""Durchsucht lokale Dokumente im input_docs/ Ordner nach einem spezifischen Suchbegriff.

⚠️ KRITISCH - Der Parameter 'query' MUSS als expliziter String übergeben werden:
- RICHTIG: search_local_documents(query="Dr. Sven Herbert")
- FALSCH: search_local_documents("Dr. Sven Herbert") ohne query=
- FALSCH: search_local_documents() ohne Parameter

PARAMETER-VERWENDUNG:
- Bei Personensuche: query="Dr. Sven Herbert" oder query="Herbert"
- Bei Dokumentensuche: query="KHS" oder query="Gesellschafterliste"
- Bei Themensuche: query="Relevante Schlagwörter"
- NIEMALS einen leeren String oder keinen Parameter übergeben!

Das Tool scannt ALLE Dokumente vollständig mit Chunk-Überlappung (2000 Zeichen pro Chunk, 200 Zeichen Überlappung), sodass keine Treffer übersehen werden.""",
                    func=self.document_tool.invoke
                )
                available_tools.append(doc_search_tool)

            # Binde Tools an das LLM
            if available_tools:
                assert_tools_allowlisted(available_tools, self.name)
                llm_with_tools = self.llm.bind_tools(available_tools)
            else:
                # Kein Tool verfügbar, nutze normales LLM
                llm_with_tools = self.llm
                print(f"[{self.name}] ⚠️ Keine Tools verfügbar - nutze nur LLM-Wissen")

            # Erstelle System-Prompt
            system_prompt = self._create_system_prompt()

            # Initialisiere Nachrichtenliste
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=self._create_user_prompt(input_data, context))
            ]

            # Agent-Schleife: Maximal 5 Iterationen
            max_iterations = 5
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
                    findings = response.content
                    break

                # Führe Tool-Calls aus
                tool_calls_made = True
                for tool_call in response.tool_calls:
                    tool_name = tool_call.get("name", "unknown")
                    tool_args = tool_call.get("args", {})
                    print(f"[{self.name}] 🔍 Führe Tool aus: {tool_name}")
                    print(f"[{self.name}] 📥 Tool-Argumente: {tool_args}")

                    try:
                        # Führe das Tool aus
                        if self.tavily_tool and tool_name == "tavily_search_results_json":
                            tool_result = self.tavily_tool.invoke(tool_args)
                        elif self.document_tool and tool_name == "search_local_documents":
                            # Extrahiere query und validiere - unterstütze sowohl 'query' als auch '__arg1'
                            query_param = tool_args.get("query") or tool_args.get("__arg1", "")
                            query_param = query_param.strip() if query_param else ""

                            print(f"[{self.name}] 🔎 Suche nach: '{query_param}'")

                            # KRITISCH: Verhindere leere Suchbegriffe
                            if not query_param or len(query_param) < 2:
                                tool_result = f"""❌ FEHLER: Suchbegriff ist leer oder zu kurz!

Du MUSST einen spezifischen Suchbegriff angeben. Extrahiere aus der Benutzeranfrage:
- Bei Personensuche: Den vollständigen Namen (z.B. "Dr. Sven Herbert") oder mindestens den Nachnamen (z.B. "Herbert")
- Bei Dokumentensuche: Den Dateinamen oder relevante Schlagwörter
- Bei allgemeiner Suche: Die wichtigsten Suchbegriffe

BENUTZERANFRAGE WAR: {input_data}

Bitte rufe das Tool ERNEUT auf mit einem KONKRETEN Suchbegriff aus der Anfrage."""
                            else:
                                # Prüfe ob es eine Zähl-Anfrage ist
                                is_counting_query = any(keyword in input_data.lower() for keyword in ["wie viele", "anzahl", "zähle", "zählen", "wie viel"])

                                # Normale Suche
                                results_obj = self.document_tool.search_in_documents(query_param)

                                # Bei Zähl-Anfragen: Gib den vollständigen Text zurück
                                if is_counting_query and results_obj and len(results_obj) > 0:
                                    first_result = results_obj[0]
                                    if 'full_text' in first_result:
                                        tool_result = f"""LOKALER DOKUMENTEN-INHALT: {first_result['document']}

{first_result['full_text']}

⚠️ WICHTIG: Dies ist der VOLLSTÄNDIGE Dokumenttext. Zähle die Einträge/Firmen/Personen im Text und gib das Ergebnis direkt zurück. Rufe KEINE weiteren Tools auf!"""
                                        print(f"[{self.name}] ✓ Zähl-Anfrage erkannt - gebe vollständigen Text zurück ({len(first_result['full_text'])} Zeichen)")
                                    else:
                                        # Fallback: Normale formatierte Ausgabe
                                        tool_result = self.document_tool.invoke(query_param)
                                else:
                                    # Normale Suche ohne Zähl-Absicht
                                    tool_result = self.document_tool.invoke(query_param)

                                print(f"[{self.name}] 📤 Tool-Rückgabe ({len(str(tool_result))} Zeichen):\n{str(tool_result)[:500]}...")
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
                findings = messages[-1].content if isinstance(messages[-1], AIMessage) else "Maximale Iterationen erreicht"

            result = {
                "agent": self.name,
                "query": input_data,
                "findings": findings,
                "web_search_used": tool_calls_made and self.tavily_tool is not None,
                "status": "success",
                "context": context or {}
            }

            print(f"[{self.name}] ✓ Recherche abgeschlossen")

        except Exception as e:
            print(f"[{self.name}] ✗ Fehler: {e}")
            import traceback
            traceback.print_exc()
            result = {
                "agent": self.name,
                "query": input_data,
                "findings": f"Fehler bei der Recherche: {str(e)}",
                "status": "error",
                "context": context or {}
            }

        self.add_to_memory(result)
        return result

    def _create_system_prompt(self) -> str:
        """Erstellt den System-Prompt für den Research-Agent"""
        current_year = 2025  # Aktuelles Jahr
        current_date = "Januar 2025"

        doc_count = self.document_tool.count_documents() if self.document_tool else 0

        # Liste verfügbare Dokumente
        doc_list = ""
        if self.document_tool:
            documents = self.document_tool.scan_documents()
            if documents:
                doc_list = "\n\nVERFÜGBARE LOKALE DOKUMENTE:\n"
                for doc in documents:
                    doc_list += f"   • {doc['name']}\n"

        return f"""Du bist ein Research-Agent in einem Multi-Agenten-System. Dein Wissensstand ist von Januar 2025.

⚠️ KRITISCH - KONTEXT-VERSCHMUTZUNG VERMEIDEN:
Wenn du Kontext aus vorherigen Konversationen erhältst:
- Prüfe KRITISCH, ob die Informationen DIREKT relevant für die aktuelle Anfrage sind
- Verwende NUR Informationen, die einen klaren Bezug zur aktuellen Frage haben
- Ignoriere veraltete oder thematisch irrelevante Informationen aus der Historie
- Bei Zweifeln: Führe eine NEUE Suche durch statt auf alte Informationen zu vertrauen
- Bevorzuge IMMER aktuelle Tool-Ergebnisse über historische Kontext-Informationen

VERFÜGBARE TOOLS:

1. TAVILY WEB-SUCHE (tavily_search_results_json)
   - Für aktuelle Informationen aus dem Internet
   - Für Ereignisse nach Januar 2025
   - Für aktuelle Nachrichten und Daten

2. LOKALE DOKUMENTE (search_local_documents)
   - Durchsucht {doc_count} lokale Dokument(e) im input_docs/ Ordner
   - Gibt bei Dokumenten-Match den VOLLSTÄNDIGEN Text zurück (nicht nur Snippets!)
   - Für Informationen aus den eigenen Dokumenten des Nutzers
   - Für unternehmensspezifische oder persönliche Informationen{doc_list}

⚠️ SPEZIAL-BEHANDLUNG BEI ZÄHL-ANFRAGEN:
Wenn der Nutzer nach der ANZAHL fragt ("Wie viele?", "Zähle", "Wie viele Personen/Namen/Firmen?"):
1. Nutze search_local_documents mit dem Dokumentnamen (z.B. "KHS")
2. Das Tool gibt dir den VOLLSTÄNDIGEN Dokumenttext zurück
3. Gib in deiner Antwort den kompletten Text zurück mit dem Marker:
   "LOKALER DOKUMENTEN-INHALT: [Dokumentname]

   [Vollständiger Text hier]"
4. Rufe KEINE weiteren Tools auf - eine Suche reicht!
5. Der TaskAgent wird dann automatisch den Text analysieren und zählen

KRITISCHE REGELN FÜR LOKALE DOKUMENTE:
⚠️ WICHTIG: Wenn der Nutzer nach einem Dokument fragt, dessen Name in der obigen Liste erscheint:
   - Nutze SOFORT das search_local_documents Tool
   - Suche NICHT im Internet nach diesem Dateinamen
   - Das Tool findet Dokumente auch bei ungenauen Namen (z.B. "KHS" findet "KHS-Gesellschafterliste")
   - Nach dem Tool-Aufruf: Fasse die Ergebnisse zusammen und antworte dem Nutzer
   - Rufe KEIN weiteres Tool auf, außer der Nutzer fragt explizit danach

⚠️ NAMENSSUCHE IN DOKUMENTEN - KRITISCHE ANLEITUNG:
   - Bei Personensuchen (z.B. "Dr. Sven Herbert", "Herbert", "Müller"):
     * SCHRITT 1: Extrahiere den vollständigen Namen aus der Benutzeranfrage
     * SCHRITT 2: Übergebe den Namen EXPLIZIT als query-Parameter als STRING:
       RICHTIG: search_local_documents(query="Dr. Sven Herbert")
       FALSCH: search_local_documents("Dr. Sven Herbert")
     * SCHRITT 3: Wenn kein Treffer: Versuche NUR den Nachnamen: query="Herbert"
     * Das Tool scannt AUTOMATISCH alle Dokumente vollständig
     * Es nutzt Chunks von 2000 Zeichen mit 200 Zeichen Überlappung
     * Namen an Seitengrenzen werden NICHT übersehen
     * NIEMALS einen leeren query-String oder keinen Parameter übergeben!

   - LANGE DOKUMENTE (z.B. KHS-Gesellschafterliste):
     * Diese Dokumente können hunderte Einträge enthalten
     * Suche GEZIELT nach dem Nachnamen, nicht nach dem ganzen Dokument
     * Beispiel: Bei "Suche Dr. Sven Herbert in der KHS-Liste"
       → Nutze query="Herbert" (nicht query="KHS" oder query="")
     * Das Tool findet dann den spezifischen Eintrag im Dokument

   - Gib NIEMALS voreilig "nicht gefunden" zurück - warte auf das Tool-Ergebnis
   - Wenn das Tool nichts findet, bestätige: "Der vollständige Scan aller Dokumente hat keine Treffer für [Name] ergeben"

TOOL-AUSWAHL-STRATEGIE:
- Analysiere die Anfrage sorgfältig und wähle das RICHTIGE Tool:

- search_local_documents verwenden wenn:
  * Der Nutzer nach einem Dokument fragt (auch bei "Wie viele Einträge in Dokument X?")
  * Der Nutzer nach einem SPEZIFISCHEN Begriff oder Namen sucht
  * Der Nutzer nach "meinen Dokumenten", "lokalen Dateien" fragt
  * Bei ZÄHL-ANFRAGEN: Suche nach dem Dokumentnamen, das Tool gibt dir den vollen Text zurück!
  * Firmennamen, Mitarbeiternamen oder andere spezifische Daten gesucht werden

- TAVILY verwenden wenn:
  * Aktuelle Web-Informationen oder Nachrichten benötigt werden
  * Der Nutzer explizit nach "aktuellen", "neuesten" Informationen fragt
  * Allgemeines Weltwissen aus dem Internet gefragt ist

- KEIN TOOL verwenden wenn:
  * Allgemeines historisches Wissen ausreicht (vor 2025)
  * Theoretische Fragen gestellt werden

ARBEITSWEISE:
1. Analysiere die Anfrage: Welche Informationsquelle wird benötigt?
2. Wähle das passende Tool (NIEMALS beide gleichzeitig, NIEMALS mehrere Aufrufe desselben Tools!)
3. Warte auf die Ergebnisse
4. ⚠️ WICHTIG - Nach Tool-Aufruf:
   - Bei search_local_documents mit Zähl-Anfrage: Gib den VOLLSTÄNDIGEN Text mit "LOKALER DOKUMENTEN-INHALT:" Marker zurück
   - Bei search_local_documents mit Namenssuche: Nutze die Snippets zur Beantwortung
   - Bei tavily_search: Nutze die Web-Ergebnisse zur Beantwortung
   - NIEMALS weitere Tools nach dem ersten erfolgreichen Tool-Aufruf!
5. Fasse die Ergebnisse zusammen und antworte dem Nutzer SOFORT
6. STOPPE nach der Antwort - rufe kein weiteres Tool auf

Deine Antworten sollten:
- Gut strukturiert und informativ sein
- Klar kennzeichnen, woher die Informationen stammen (Web vs. lokale Dokumente)
- Die wichtigsten Informationen zuerst nennen
- Quellen angeben
- Faktenbasiert und objektiv sein
- DIREKT antworten ohne weitere Tool-Aufrufe nach erfolgreicher Suche"""

    def _create_user_prompt(self, query: str, context: Dict[str, Any] = None) -> str:
        """Erstellt den User-Prompt für die Recherche-Anfrage"""
        prompt = ""

        # Füge Nutzer-Kontext hinzu falls vorhanden
        if context and "user_context" in context:
            user_context = context["user_context"]
            if user_context:
                prompt += f"""{user_context}

---

"""

        # Füge relevante frühere Erkenntnisse hinzu
        if context and "memory" in context and context["memory"].get("relevant_insights"):
            prompt += "RELEVANTE FRÜHERE ERKENNTNISSE:\n"
            for insight in context["memory"]["relevant_insights"][:3]:  # Max 3 Erkenntnisse
                prompt += f"- {insight['insight'][:200]}...\n"
            prompt += "\n---\n\n"

        prompt += f"""Bitte recherchiere zum folgenden Thema:

{query}"""

        if context and not ("user_context" in context or "memory" in context):
            # Legacy: Falls Kontext ohne neue Struktur übergeben wird
            prompt += f"""

Zusätzlicher Kontext: {context}"""

        prompt += """

Liefere eine umfassende Antwort mit:
1. Zusammenfassung der wichtigsten Informationen
2. Relevante Fakten und Details
3. Verschiedene Perspektiven (wenn anwendbar)
4. Quellen oder Referenzen"""

        return prompt

    def search_web(self, query: str) -> str:
        """Führt eine echte Web-Suche mit Tavily durch"""
        if not self.tavily_tool:
            return "Websuche nicht verfügbar - TAVILY_API_KEY fehlt"

        try:
            results = self.tavily_tool.invoke({"query": query})
            return results
        except Exception as e:
            return f"Fehler bei Websuche: {str(e)}"

    def analyze_data(self, data: str) -> Dict[str, Any]:
        """Analysiert gegebene Daten"""
        return {
            "analysis": f"Analyse von: {data}",
            "insights": ["Insight 1", "Insight 2", "Insight 3"]
        }
