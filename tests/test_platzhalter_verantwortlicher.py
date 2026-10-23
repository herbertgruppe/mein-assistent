"""
Tests fuer Aufgaben ohne benannten Verantwortlichen.

Hintergrund: Der Asana-Parser erkennt nur Zeilen der Form

    - Vorname Nachname: Was zu tun ist [2026-12-15]

Fehlt der Doppelpunkt, faellt die Zeile ersatzlos weg. Deshalb schreiben
Mara und die Live-Mitschrift auch bei unbekanntem Verantwortlichen einen
Platzhalter ("Offen:", "(unklar):"), damit die Aufgabe in Asana ankommt.

Stand ohne diesen Fix entstand daraus eine **Person namens "Offen"**, der
Aufgaben zugewiesen wurden. Ein erfundener Verantwortlicher ist schlimmer
als gar keiner: Niemand fuehlt sich zustaendig, aber die Aufgabe sieht
zugewiesen aus.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# utils/protocol.py importiert streamlit, das hier nicht gebraucht wird.
sys.modules.setdefault("streamlit", types.ModuleType("streamlit"))

from utils.protocol import extract_tasks_from_protocol_text  # noqa: E402


def _protokoll(*zeilen):
    return "# Besprechung\n\n## 1. Thema\n\n**Aufgaben:**\n" + "\n".join(zeilen) + "\n"


class TestPlatzhalterWirdVerworfen:
    """Platzhalter duerfen nicht als Person in Asana landen."""

    def test_offen_erzeugt_keine_person(self):
        aufgaben = extract_tasks_from_protocol_text(
            _protokoll("- Offen: Brand-Account anlegen")
        )
        assert len(aufgaben) == 1, "Die Aufgabe muss ankommen"
        assert aufgaben[0]["assignee"] is None, "aber ohne erfundenen Verantwortlichen"
        assert "Brand-Account" in aufgaben[0]["title"]

    def test_unklar_in_klammern(self):
        """Genau diese Schreibweise verwendet Mara."""
        aufgaben = extract_tasks_from_protocol_text(
            _protokoll("- (unklar): Konzept erstellen")
        )
        assert len(aufgaben) == 1
        assert aufgaben[0]["assignee"] is None

    def test_weitere_platzhalter(self):
        for platzhalter in ("offen", "Noch offen", "unklar", "Unbekannt",
                            "niemand", "N.N.", "TBD"):
            aufgaben = extract_tasks_from_protocol_text(
                _protokoll(f"- {platzhalter}: Etwas tun")
            )
            assert len(aufgaben) == 1, f"{platzhalter!r} verschluckt die Aufgabe"
            assert aufgaben[0]["assignee"] is None, (
                f"{platzhalter!r} wurde zur Person"
            )

    def test_echter_name_bleibt(self):
        """Die Sperrliste darf keine echten Namen schlucken."""
        aufgaben = extract_tasks_from_protocol_text(
            _protokoll("- Daniel Grauthammer: Zeiterfassung ausrollen [2026-12-15]")
        )
        assert aufgaben[0]["assignee"] == "Daniel Grauthammer"
        assert aufgaben[0]["due_date"] == "2026-12-15"

    def test_platzhalter_mit_frist(self):
        """Platzhalter und Frist zusammen — beides muss greifen."""
        aufgaben = extract_tasks_from_protocol_text(
            _protokoll("- Offen: Lagerkonzept erstellen [2026-11-30]")
        )
        assert aufgaben[0]["assignee"] is None
        assert aufgaben[0]["due_date"] == "2026-11-30"


class TestBulletOhneDoppelpunkt:
    """
    Dokumentiert eine Grenze, die bewusst NICHT geaendert wurde.

    Eine Bullet-Zeile ohne Doppelpunkt wird nicht als Aufgabe erkannt —
    sie verschwindet still. Das zu aendern haette das Verhalten beim
    Finalisieren der wartenden Entwuerfe veraendert, deshalb bleibt es
    vorerst so. Wer das Format erzeugt, MUSS also immer einen
    Verantwortlichen oder einen Platzhalter schreiben.

    Dieser Test schlaegt fehl, sobald jemand das Verhalten aendert — dann
    ist die Entscheidung bewusst zu treffen statt versehentlich.
    """

    def test_ohne_doppelpunkt_faellt_weg(self):
        aufgaben = extract_tasks_from_protocol_text(
            _protokoll("- Stahlbau-Punkte klaeren")
        )
        assert aufgaben == [], (
            "Verhalten geaendert: Bullets ohne Doppelpunkt werden jetzt erkannt. "
            "Das ist moeglicherweise richtig — dann diesen Test anpassen und "
            "pruefen, was es fuer wartende Entwuerfe bedeutet."
        )


class TestNichtAufgabenBleibenDraussen:
    """Entscheidungen und offene Punkte duerfen keine Asana-Aufgaben werden."""

    def test_offene_punkte_erzeugen_keine_aufgaben(self):
        text = (
            "# Besprechung\n\n## 1. Thema\n\n"
            "**Entscheidung:** Keine Urlaubssperre im Dezember.\n\n"
            "**Aufgaben:**\n"
            "- Felix Mueller: Stahlbau klaeren\n\n"
            "## Offene Punkte\n\n"
            "- Wer pflegt den Kanal kuenftig?\n"
            "- Fuer das Lagerkonzept ist niemand benannt.\n"
        )
        aufgaben = extract_tasks_from_protocol_text(text)
        assert len(aufgaben) == 1
        assert aufgaben[0]["assignee"] == "Felix Mueller"
