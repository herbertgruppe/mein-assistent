"""
Document Tool für lokalen Dateizugriff
"""

import os
from typing import List, Dict, Any
from pathlib import Path


class DocumentTool:
    """Tool zum Scannen und Extrahieren von Texten aus lokalen Dokumenten"""

    def __init__(self, docs_folder: str = "input_docs"):
        """
        Initialisiert das DocumentTool

        Args:
            docs_folder: Pfad zum Ordner mit den Dokumenten
        """
        self.docs_folder = docs_folder
        self.supported_extensions = [".pdf", ".docx", ".txt", ".csv", ".xlsx", ".xls"]

    def scan_documents(self) -> List[Dict[str, Any]]:
        """
        Scannt den input_docs Ordner und listet alle unterstützten Dokumente auf

        Returns:
            Liste von Dictionaries mit Dokumentinformationen
        """
        documents = []

        if not os.path.exists(self.docs_folder):
            print(f"⚠️ Ordner {self.docs_folder} existiert nicht")
            return documents

        for file_path in Path(self.docs_folder).rglob("*"):
            if file_path.is_file() and file_path.suffix.lower() in self.supported_extensions:
                documents.append({
                    "name": file_path.name,
                    "path": str(file_path),
                    "type": file_path.suffix.lower(),
                    "size": file_path.stat().st_size
                })

        return documents

    def count_documents(self) -> int:
        """
        Zählt die Anzahl der verfügbaren Dokumente

        Returns:
            Anzahl der Dokumente
        """
        return len(self.scan_documents())

    def extract_text_from_pdf(self, file_path: str, use_chunking: bool = True) -> str:
        """
        Extrahiert Text aus einer PDF-Datei mit optimiertem Chunking

        Args:
            file_path: Pfad zur PDF-Datei
            use_chunking: Ob Text mit Überlappungen gechunkt werden soll (Standard: True)

        Returns:
            Extrahierter Text
        """
        try:
            from langchain_community.document_loaders import PyPDFLoader
            from langchain_text_splitters import RecursiveCharacterTextSplitter

            print(f"[DocumentTool] Extrahiere Text aus PDF: {Path(file_path).name}")
            loader = PyPDFLoader(file_path)
            pages = loader.load()

            # Kombiniere alle Seiten mit Überlappung am Seitenende
            # Um Namen an Seitengrenzen nicht zu verlieren, fügen wir die letzten 200 Zeichen
            # der vorherigen Seite am Anfang der nächsten Seite hinzu
            if use_chunking and len(pages) > 1:
                enhanced_text = ""
                for i, page in enumerate(pages):
                    if i == 0:
                        enhanced_text = page.page_content
                    else:
                        # Füge die letzten 200 Zeichen der vorherigen Seite als Überlappung hinzu
                        prev_overlap = pages[i-1].page_content[-200:] if len(pages[i-1].page_content) > 200 else pages[i-1].page_content
                        enhanced_text += "\n\n" + prev_overlap + "\n" + page.page_content

                full_text = enhanced_text
            else:
                # Fallback: Einfache Konkatenation
                full_text = "\n\n".join([page.page_content for page in pages])

            print(f"[DocumentTool] ✓ Erfolgreich {len(full_text)} Zeichen aus {len(pages)} Seite(n) extrahiert")
            print(f"[DocumentTool] ℹ️ Text mit Seitenüberlappung erstellt für bessere Suche")
            print(f"DEBUG: PDF-Extraktion abgeschlossen - {len(full_text)} Zeichen aus {Path(file_path).name}")

            return full_text

        except ImportError:
            return "❌ PyPDFLoader nicht verfügbar. Installiere: pip install pypdf"
        except Exception as e:
            return f"❌ Fehler beim Lesen der PDF: {str(e)}"

    def extract_text_from_docx(self, file_path: str) -> str:
        """
        Extrahiert Text aus einer DOCX-Datei

        Args:
            file_path: Pfad zur DOCX-Datei

        Returns:
            Extrahierter Text
        """
        try:
            from langchain_community.document_loaders import Docx2txtLoader

            print(f"[DocumentTool] Extrahiere Text aus DOCX: {Path(file_path).name}")
            loader = Docx2txtLoader(file_path)
            documents = loader.load()

            # Kombiniere alle Dokumente
            full_text = "\n\n".join([doc.page_content for doc in documents])
            print(f"[DocumentTool] ✓ Erfolgreich {len(full_text)} Zeichen extrahiert")
            return full_text

        except ImportError:
            return "❌ Docx2txtLoader nicht verfügbar. Installiere: pip install docx2txt"
        except Exception as e:
            return f"❌ Fehler beim Lesen der DOCX: {str(e)}"

    def extract_text_from_txt(self, file_path: str) -> str:
        """
        Extrahiert Text aus einer TXT-Datei

        Args:
            file_path: Pfad zur TXT-Datei

        Returns:
            Extrahierter Text
        """
        try:
            from langchain_community.document_loaders import TextLoader

            print(f"[DocumentTool] Extrahiere Text aus TXT: {Path(file_path).name}")
            loader = TextLoader(file_path, encoding='utf-8')
            documents = loader.load()

            # Kombiniere alle Dokumente
            full_text = "\n\n".join([doc.page_content for doc in documents])
            print(f"[DocumentTool] ✓ Erfolgreich {len(full_text)} Zeichen extrahiert")
            return full_text

        except Exception as e:
            # Fallback: Direktes Lesen
            try:
                print(f"[DocumentTool] Fallback: Direktes Lesen der Datei")
                with open(file_path, 'r', encoding='utf-8') as f:
                    text = f.read()
                print(f"[DocumentTool] ✓ Erfolgreich {len(text)} Zeichen extrahiert")
                return text
            except Exception as e2:
                return f"❌ Fehler beim Lesen der TXT: {str(e2)}"

    def extract_text_from_csv(self, file_path: str) -> str:
        """
        Extrahiert Text aus einer CSV-Datei

        Args:
            file_path: Pfad zur CSV-Datei

        Returns:
            Extrahierter Text (formatiert als Tabelle)
        """
        try:
            import pandas as pd

            print(f"[DocumentTool] Extrahiere Text aus CSV: {Path(file_path).name}")

            # Versuche verschiedene Encodings
            for encoding in ['utf-8', 'latin-1', 'iso-8859-1', 'cp1252']:
                try:
                    df = pd.read_csv(file_path, encoding=encoding)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                return "❌ Fehler: Konnte Encoding nicht ermitteln"

            # Formatiere als Text
            output = f"CSV-Datei: {Path(file_path).name}\n"
            output += f"Anzahl Zeilen: {len(df)}\n"
            output += f"Anzahl Spalten: {len(df.columns)}\n"
            output += f"Spalten: {', '.join(df.columns)}\n\n"

            # Zeige die ersten Zeilen als Tabelle
            output += "Datenvorschau:\n"
            output += df.head(10).to_string(index=False)

            # Bei großen Dateien: Zusammenfassung
            if len(df) > 10:
                output += f"\n\n... und {len(df) - 10} weitere Zeilen"

            # Füge alle Daten als durchsuchbaren Text hinzu
            output += "\n\nVollständige Daten (durchsuchbar):\n"
            output += df.to_string(index=False)

            print(f"[DocumentTool] ✓ Erfolgreich {len(df)} Zeilen extrahiert")
            return output

        except ImportError:
            return "❌ Pandas nicht verfügbar. Installiere: pip install pandas"
        except Exception as e:
            return f"❌ Fehler beim Lesen der CSV: {str(e)}"

    def extract_text_from_excel(self, file_path: str) -> str:
        """
        Extrahiert Text aus einer Excel-Datei (.xlsx, .xls)

        Args:
            file_path: Pfad zur Excel-Datei

        Returns:
            Extrahierter Text (formatiert als Tabellen)
        """
        try:
            import pandas as pd

            print(f"[DocumentTool] Extrahiere Text aus Excel: {Path(file_path).name}")

            # Lese alle Sheets
            excel_file = pd.ExcelFile(file_path)
            output = f"Excel-Datei: {Path(file_path).name}\n"
            output += f"Anzahl Sheets: {len(excel_file.sheet_names)}\n"
            output += f"Sheet-Namen: {', '.join(excel_file.sheet_names)}\n\n"

            total_rows = 0
            # Verarbeite jedes Sheet
            for sheet_name in excel_file.sheet_names:
                df = pd.read_excel(file_path, sheet_name=sheet_name)
                total_rows += len(df)

                output += f"\n{'=' * 60}\n"
                output += f"SHEET: {sheet_name}\n"
                output += f"{'=' * 60}\n"
                output += f"Anzahl Zeilen: {len(df)}\n"
                output += f"Anzahl Spalten: {len(df.columns)}\n"
                output += f"Spalten: {', '.join(map(str, df.columns))}\n\n"

                # Zeige die ersten Zeilen
                output += "Datenvorschau:\n"
                output += df.head(10).to_string(index=False)

                if len(df) > 10:
                    output += f"\n\n... und {len(df) - 10} weitere Zeilen"

                # Füge alle Daten als durchsuchbaren Text hinzu
                output += "\n\nVollständige Daten (durchsuchbar):\n"
                output += df.to_string(index=False)
                output += "\n"

            print(f"[DocumentTool] ✓ Erfolgreich {total_rows} Zeilen aus {len(excel_file.sheet_names)} Sheet(s) extrahiert")
            return output

        except ImportError:
            return "❌ Pandas oder openpyxl nicht verfügbar. Installiere: pip install pandas openpyxl"
        except Exception as e:
            return f"❌ Fehler beim Lesen der Excel-Datei: {str(e)}"

    def extract_text(self, file_path: str) -> str:
        """
        Extrahiert Text aus einer Datei basierend auf ihrem Typ

        Args:
            file_path: Pfad zur Datei

        Returns:
            Extrahierter Text
        """
        file_ext = Path(file_path).suffix.lower()

        if file_ext == ".pdf":
            return self.extract_text_from_pdf(file_path)
        elif file_ext == ".docx":
            return self.extract_text_from_docx(file_path)
        elif file_ext == ".txt":
            return self.extract_text_from_txt(file_path)
        elif file_ext == ".csv":
            return self.extract_text_from_csv(file_path)
        elif file_ext in [".xlsx", ".xls"]:
            return self.extract_text_from_excel(file_path)
        else:
            return f"❌ Nicht unterstütztes Dateiformat: {file_ext}"

    def _fuzzy_match_filename(self, query: str, filename: str) -> bool:
        """
        Prüft ob ein Query-String mit einem Dateinamen übereinstimmt (fuzzy)

        Args:
            query: Suchbegriff
            filename: Dateiname

        Returns:
            True wenn Match gefunden wurde
        """
        query_lower = query.lower()
        filename_lower = filename.lower()

        # Entferne Sonderzeichen und Dateiendungen für Vergleich
        import re
        query_clean = re.sub(r'[^\w\s]', '', query_lower)
        filename_clean = re.sub(r'[^\w\s]', '', filename_lower.rsplit('.', 1)[0])

        # Verschiedene Match-Strategien
        # 1. Exakte Übereinstimmung
        if query_clean in filename_clean or filename_clean in query_clean:
            return True

        # 2. Wort-für-Wort Match
        query_words = query_clean.split()
        filename_words = filename_clean.split()

        # Wenn mindestens 50% der Query-Wörter im Dateinamen sind
        if query_words:
            matches = sum(1 for word in query_words if word in filename_clean)
            if matches >= len(query_words) * 0.5:
                return True

        # 3. Teilstring-Match für lange Dateinamen
        if len(query_clean) >= 3:
            if query_clean in filename_clean:
                return True

        return False

    def search_in_documents(self, query: str, force_full_scan: bool = False) -> List[Dict[str, Any]]:
        """
        Durchsucht alle Dokumente nach einem Query mit optimierter Chunking-Strategie

        Args:
            query: Suchbegriff oder Frage
            force_full_scan: Erzwingt vollständigen Scan aller Dokumente (für Namenssuchen)

        Returns:
            Liste von relevanten Dokumenten mit Textausschnitten
        """
        results = []
        documents = self.scan_documents()

        print(f"[DocumentTool] Suche nach: '{query}'")
        print(f"[DocumentTool] Durchsuche {len(documents)} Dokument(e)...")

        # Erkenne Namenssuchen (z.B. "Dr. Sven Herbert", "Herbert")
        is_name_search = self._is_name_search(query)
        if is_name_search:
            print(f"[DocumentTool] ⚠️ Namenssuche erkannt - erzwinge vollständigen Scan")
            force_full_scan = True

        for doc in documents:
            print(f"[DocumentTool] Scanne: {doc['name']}")

            # Prüfe ob der Query auf den Dateinamen passt (Fuzzy Match)
            filename_match = self._fuzzy_match_filename(query, doc["name"])

            if filename_match:
                print(f"[DocumentTool] ✓ Dateiname-Match gefunden: {doc['name']}")
                # Extrahiere den vollständigen Text
                text = self.extract_text(doc["path"])

                results.append({
                    "document": doc["name"],
                    "path": doc["path"],
                    "snippet": text[:500] + "..." if len(text) > 500 else text,
                    "full_text": text,
                    "match_type": "filename"
                })
            else:
                # Suche im Inhalt - IMMER den vollständigen Text durchsuchen
                text = self.extract_text(doc["path"])

                # Chunking-basierte Suche mit Überlappung für bessere Trefferquote
                matches = self._search_with_chunks(query, text, chunk_size=2000, overlap=200)

                if matches:
                    print(f"[DocumentTool] ✓ Inhalt-Match gefunden: {doc['name']} ({len(matches)} Treffer)")

                    # Nutze den besten/ersten Match
                    best_match = matches[0]

                    results.append({
                        "document": doc["name"],
                        "path": doc["path"],
                        "snippet": best_match["snippet"],
                        "full_text": text,
                        "match_type": "content",
                        "match_count": len(matches),
                        "all_matches": matches[:3]  # Speichere bis zu 3 Matches
                    })
                else:
                    print(f"[DocumentTool] ✗ Kein Match in: {doc['name']}")

        print(f"[DocumentTool] Gefunden: {len(results)} Ergebnis(se)")

        # Bei Namenssuchen: Wenn nichts gefunden wurde, gib detaillierte Info zurück
        if force_full_scan and len(results) == 0:
            print(f"[DocumentTool] ⚠️ WARNUNG: Keine Treffer trotz vollständigem Scan von {len(documents)} Dokumenten")

        return results

    def _is_name_search(self, query: str) -> bool:
        """
        Erkennt ob es sich um eine Namenssuche handelt

        Args:
            query: Suchanfrage

        Returns:
            True wenn es eine Namenssuche ist
        """
        # Erkenne Namensmuster: Titel + Vorname + Nachname, oder nur Name
        import re

        # Titel-Präfixe
        titles = ['dr', 'prof', 'herr', 'frau', 'mr', 'mrs', 'ms']
        query_lower = query.lower()

        # Hat Titel?
        has_title = any(query_lower.startswith(title) for title in titles)

        # Hat Großbuchstaben-Wörter (typisch für Namen)?
        words = query.split()
        capitalized_words = sum(1 for word in words if word and word[0].isupper())

        # Ist vermutlich Name wenn:
        # - Beginnt mit Titel ODER
        # - Hat mindestens 2 großgeschriebene Wörter
        return has_title or capitalized_words >= 2

    def _search_with_chunks(self, query: str, text: str, chunk_size: int = 2000, overlap: int = 200) -> List[Dict[str, str]]:
        """
        Durchsucht Text mit Chunking und Überlappung

        Args:
            query: Suchbegriff
            text: Zu durchsuchender Text
            chunk_size: Größe der Chunks in Zeichen
            overlap: Überlappung zwischen Chunks in Zeichen

        Returns:
            Liste von gefundenen Matches mit Kontext
        """
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        # Erstelle Text Splitter mit Überlappung
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=overlap,
            length_function=len,
            separators=["\n\n", "\n", ". ", ", ", " ", ""]
        )

        # Split den Text
        chunks = splitter.split_text(text)

        print(f"[DocumentTool] Text aufgeteilt in {len(chunks)} Chunks (Größe: {chunk_size}, Überlappung: {overlap})")

        matches = []
        query_lower = query.lower()

        # Durchsuche jeden Chunk
        for i, chunk in enumerate(chunks):
            chunk_lower = chunk.lower()

            if query_lower in chunk_lower:
                # Match gefunden!
                start_idx = chunk_lower.find(query_lower)

                # Erstelle Snippet mit Kontext
                snippet_start = max(0, start_idx - 200)
                snippet_end = min(len(chunk), start_idx + len(query) + 200)
                snippet = chunk[snippet_start:snippet_end]

                matches.append({
                    "chunk_index": i,
                    "snippet": snippet,
                    "position": start_idx
                })

                print(f"[DocumentTool] ✓ Match in Chunk {i+1}/{len(chunks)} gefunden")

        return matches

    def _get_document_overview(self) -> str:
        """
        Erstellt eine Übersicht über verfügbare Dokumente und extrahiert wichtige Schlagworte

        Returns:
            Formatierte Übersicht mit Schlagworten
        """
        documents = self.scan_documents()

        if not documents:
            return "❌ Keine Dokumente in input_docs/ gefunden."

        output = f"""⚠️ SUCHBEGRIFF FEHLT ODER ZU KURZ!

Ich benötige einen konkreten Suchbegriff, um die Dokumente zu durchsuchen.

VERFÜGBARE DOKUMENTE ({len(documents)}):
"""

        # Liste alle Dokumente mit Metadaten auf
        for i, doc in enumerate(documents, 1):
            output += f"\n{i}. {doc['name']} ({doc['type']}, {doc['size']:,} Bytes)"

            # Versuche, wichtige Schlagworte aus den ersten Zeilen zu extrahieren
            try:
                text = self.extract_text(doc['path'])
                if text and not text.startswith("❌"):
                    # Extrahiere die ersten 500 Zeichen als Vorschau
                    preview = text[:500].strip()

                    # Finde großgeschriebene Wörter (potenzielle Namen)
                    import re
                    capitalized_words = re.findall(r'\b[A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)*\b', preview)

                    # Entferne Duplikate und häufige Füllwörter
                    stopwords = {'Der', 'Die', 'Das', 'Ein', 'Eine', 'Und', 'Oder', 'Von', 'Für', 'Mit', 'Auf', 'Im', 'Am', 'Zum', 'Zur'}
                    keywords = [w for w in set(capitalized_words) if w not in stopwords][:10]

                    if keywords:
                        output += f"\n   Mögliche Schlagworte: {', '.join(keywords)}"
            except Exception as e:
                print(f"[DocumentTool] Fehler beim Extrahieren von Schlagworten aus {doc['name']}: {e}")

        output += "\n\nBITTE GEBE EINEN SPEZIFISCHEN SUCHBEGRIFF AN:"
        output += "\n- Bei Personensuche: z.B. 'Dr. Sven Herbert' oder 'Herbert'"
        output += "\n- Bei Dokumentensuche: z.B. 'KHS' oder 'Gesellschafterliste'"
        output += "\n- Bei Themensuche: z.B. relevante Fachbegriffe"

        return output

    def read_full_document(self, document_name: str, max_chars: int = 50000) -> str:
        """
        Liest den vollständigen Text eines Dokuments (bis max_chars Zeichen)

        Args:
            document_name: Name des Dokuments (kann auch Teil des Namens sein)
            max_chars: Maximale Anzahl an Zeichen (Standard: 50.000)

        Returns:
            Vollständiger Text des Dokuments oder Fehlermeldung
        """
        documents = self.scan_documents()

        # Suche das Dokument (mit Fuzzy-Match)
        matching_docs = []
        for doc in documents:
            if self._fuzzy_match_filename(document_name, doc["name"]):
                matching_docs.append(doc)

        if not matching_docs:
            return f"❌ Kein Dokument mit dem Namen '{document_name}' gefunden.\n\nVerfügbare Dokumente:\n" + \
                   "\n".join([f"  • {doc['name']}" for doc in documents])

        if len(matching_docs) > 1:
            return f"⚠️ Mehrere Dokumente gefunden für '{document_name}':\n" + \
                   "\n".join([f"  • {doc['name']}" for doc in matching_docs]) + \
                   "\n\nBitte spezifiziere den Dateinamen genauer."

        # Extrahiere den vollständigen Text
        doc = matching_docs[0]
        print(f"[DocumentTool] Lese vollständiges Dokument: {doc['name']}")

        full_text = self.extract_text(doc["path"])

        # Prüfe Textlänge
        if len(full_text) > max_chars:
            print(f"[DocumentTool] ⚠️ Dokument ist zu lang ({len(full_text)} Zeichen), kürze auf {max_chars} Zeichen")
            full_text = full_text[:max_chars] + f"\n\n... [Text gekürzt nach {max_chars} Zeichen]"

        print(f"[DocumentTool] ✓ Vollständiges Dokument gelesen: {len(full_text)} Zeichen")

        return f"""VOLLSTÄNDIGES DOKUMENT: {doc['name']}
Typ: {doc['type']}
Größe: {doc['size']:,} Bytes
Text-Länge: {len(full_text)} Zeichen

{'=' * 80}

{full_text}"""

    def get_all_documents_text(self) -> str:
        """
        Holt den Text aus allen Dokumenten

        Returns:
            Kombinierter Text aller Dokumente
        """
        documents = self.scan_documents()
        all_texts = []

        for doc in documents:
            text = self.extract_text(doc["path"])
            all_texts.append(f"\n\n=== {doc['name']} ===\n\n{text}")

        return "\n".join(all_texts)

    def _run(self, query: str = None, **kwargs) -> str:
        """
        LangChain Tool _run Methode (für direkte Tool-Aufrufe)

        Args:
            query: Suchbegriff oder Frage
            **kwargs: Zusätzliche Parameter (unterstützt __arg1 als Fallback)

        Returns:
            Formatierte Suchergebnisse
        """
        # Fix für das __arg1 Problem - flexibles Argumenthandling
        search_term = query or kwargs.get('__arg1') or kwargs.get('input') or ""
        search_term = search_term.strip() if search_term else ""

        print(f"DEBUG: Tool empfängt Suche nach: '{search_term}'")
        print(f"DEBUG: Ursprüngliche Parameter - query: '{query}', kwargs: {kwargs}")

        # Delegiere an invoke mit dem bereinigten Suchbegriff
        return self.invoke(query=search_term, **kwargs)

    def invoke(self, query: str = None, **kwargs) -> str:
        """
        LangChain-Tool-kompatible Invoke-Methode

        Args:
            query: Suchbegriff oder Frage
            **kwargs: Zusätzliche Parameter (unterstützt __arg1 als Fallback)

        Returns:
            Formatierte Suchergebnisse
        """
        # WICHTIG: Unterstütze sowohl 'query' als auch '__arg1' (vom Modell verwendet)
        # Das Modell übergibt manchmal den Suchbegriff als __arg1 statt query
        actual_query = query or kwargs.get('__arg1', '') or kwargs.get('input', '')
        search_query = actual_query.strip() if actual_query else ''

        print(f"[DocumentTool] invoke() aufgerufen mit query='{query}', kwargs={kwargs}")
        print(f"[DocumentTool] Verwende actual_query='{actual_query}' -> search_query='{search_query}'")

        # FALLBACK-LOGIK: Wenn query leer oder zu kurz ist
        if not search_query or len(search_query.strip()) < 2:
            print(f"[DocumentTool] ⚠️ WARNUNG: Suchbegriff ist leer oder zu kurz!")
            return self._get_document_overview()

        # Suche mit automatischer Namenserkennung
        results = self.search_in_documents(search_query, force_full_scan=False)

        if not results:
            # Bei Namenssuchen: Bestätige den vollständigen Scan
            if self._is_name_search(search_query):
                return f"VOLLSTÄNDIGER SCAN ABGESCHLOSSEN: Keine Treffer für '{search_query}' in {self.count_documents()} Dokument(en). Der Name wurde weder in Dateinamen noch im Inhalt gefunden. Die Suche erfolgte mit Chunk-Überlappung, sodass auch Namen an Seitengrenzen erkannt werden."
            else:
                return f"Keine Ergebnisse in lokalen Dokumenten für: {search_query}"

        # Formatiere Ergebnisse
        output = f"Gefunden in {len(results)} Dokument(en):\n\n"

        for result in results:
            output += f"📄 **{result['document']}**\n"
            output += f"Match-Typ: {result['match_type']}\n"

            if result.get('match_count', 0) > 1:
                output += f"Anzahl Treffer: {result['match_count']}\n"

            output += f"Ausschnitt: ...{result['snippet']}...\n\n"

            # Bei mehreren Matches: Zeige auch die anderen
            if result.get('all_matches') and len(result['all_matches']) > 1:
                output += "Weitere Fundstellen:\n"
                for i, match in enumerate(result['all_matches'][1:], start=2):
                    output += f"  {i}. ...{match['snippet'][:100]}...\n"
                output += "\n"

        return output
