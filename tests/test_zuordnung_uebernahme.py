"""
Tests fuer zwei Stellen, an denen Sven bisher doppelt arbeiten musste.

**Uebernahme der Zuordnung.** In Stufe 1 waehlt Sven Termin und Asana-Board.
Der Review-Editor bekam diese Werte nie zu sehen — `REVIEW_CONTEXT` enthielt
sie schlicht nicht. Also stand er dort erneut vor leeren Feldern und traf
dieselbe Entscheidung ein zweites Mal. Bei 36 offenen Entwuerfen 36 Mal.

**Board-Vorschlag ueber den Namen.** Sven benennt Besprechungen nach
Moeglichkeit wie das zugehoerige Asana-Board. Beim ersten Mal gibt es noch
keine Historie, aus der sich das Board ableiten liesse — dann hilft der
Namensvergleich.
"""
import importlib.util
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

_BASE_ENV = {
    "TELEGRAM_BOT_TOKEN": "test-token",
    "TELEGRAM_WEBHOOK_SECRET": "test-secret",
    "API_SECRET_KEY": "test-key",
}


def _load_api():
    spec = importlib.util.spec_from_file_location("api_zuordnung_test", REPO_ROOT / "api.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, _BASE_ENV, clear=False):
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def api():
    return _load_api()


def _lies(pfad):
    return pfad.read_text(encoding="utf-8")


# ── Die Werte erreichen den Editor ──────────────────────────────────────────

def test_review_route_reicht_die_zuordnung_durch(api):
    import inspect
    quelle = inspect.getsource(api.review_page)
    for feld in ("event_id", "asana_board_gid", "asana_section_gid"):
        assert f'"{feld}"' in quelle, f"{feld} fehlt im Template-Kontext"


def test_template_gibt_sie_an_das_skript():
    html = _lies(REPO_ROOT / "templates" / "review.html")
    for feld in ("eventId", "asanaBoardGid", "asanaSectionGid"):
        assert feld in html, f"{feld} fehlt in REVIEW_CONTEXT"


def test_skript_wertet_sie_aus():
    js = _lies(REPO_ROOT / "static" / "review.js")
    assert "ctx.eventId" in js
    assert "ctx.asanaBoardGid" in js
    assert "ctx.asanaSectionGid" in js


def test_getroffene_wahl_schlaegt_den_ratevorgang():
    """
    Die Vorauswahl per Titelaehnlichkeit darf eine bereits getroffene
    Entscheidung nicht ueberschreiben — sonst waere die Uebernahme wertlos.
    """
    js = _lies(REPO_ROOT / "static" / "review.js")
    pos_uebernahme = js.index("ctx.eventId")
    pos_raten = js.index("titleSimilarity(ev.title")
    assert pos_uebernahme < pos_raten, (
        "Die Uebernahme muss vor dem Raten stehen und mit return abbrechen"
    )


def test_fehlender_termin_faellt_auf_das_raten_zurueck():
    """
    Ein verschobener oder abgesagter Termin steht nicht mehr in der Liste.
    Dann soll das Feld nicht leer bleiben, sondern geraten werden.
    """
    js = _lies(REPO_ROOT / "static" / "review.js")
    assert "eventSelect.value === ctx.eventId" in js


# ── Namensvergleich ─────────────────────────────────────────────────────────

def test_gleicher_name_trifft(api):
    assert api._namensaehnlichkeit("1:1 LK / SH", "1:1 LK / SH") == 1.0


def test_fuellwoerter_erzeugen_keinen_treffer(api):
    """
    "Besprechung" steht in fast jedem Namen. Ohne Filter traefe jede
    Besprechung auf jedes Board mit diesem Wort.
    """
    assert api._namensaehnlichkeit(
        "Besprechung Vertrieb", "Besprechung Einkauf"
    ) == 0.0


def test_teilweise_uebereinstimmung_wird_erkannt(api):
    wert = api._namensaehnlichkeit(
        "BL-Besprechung HRN", "HRN Herbert Rhein-Neckar"
    )
    assert wert > 0


def test_voellig_verschiedene_namen(api):
    assert api._namensaehnlichkeit("Zeiterfassung myTGA", "Volksbank AR") == 0.0


def test_leere_namen_stuerzen_nicht(api):
    assert api._namensaehnlichkeit("", "Board") == 0.0
    assert api._namensaehnlichkeit(None, None) == 0.0


# ── Der Vorschlag ───────────────────────────────────────────────────────────

def _protokoll(name="Kundenbefragung", titel=None):
    return {
        "id": "p1",
        "meeting_name": name,
        "recording_title": titel,
        "reviewer_token": "t",
        "expires_at": None,
    }


def test_historie_schlaegt_den_namen(api):
    """
    Eine bestaetigte frühere Zuordnung ist staerker als eine Namensaehnlichkeit
    — sie ist eine Entscheidung, keine Vermutung.
    """
    treffer = {
        "asana_board_gid": "111", "asana_section_gid": "222",
        "meeting_name": "1:1 LK / SH", "meeting_datetime": "2026-09-01", "status": "finalized",
    }
    with mock.patch.object(api, "_get_protocol_for_token", return_value=_protokoll()), \
         mock.patch.object(api._protocols_db, "find_previous_board", return_value=treffer), \
         mock.patch.object(api, "_get_asana_agent") as agent:
        d = api.suggest_asana_board("p1", _user="sven")
    assert d["grund"] == "historie"
    assert d["asana_board_gid"] == "111"
    agent.assert_not_called()


def test_ohne_historie_wird_der_name_verglichen(api):
    boards = [
        {"gid": "1", "name": "01 HS Anfragen Privatkunden"},
        {"gid": "2", "name": "Kundenbefragung 2026"},
        {"gid": "3", "name": "Lieferanten"},
    ]
    with mock.patch.object(api, "_get_protocol_for_token", return_value=_protokoll()), \
         mock.patch.object(api._protocols_db, "find_previous_board", return_value=None), \
         mock.patch.object(api, "_get_asana_agent"), \
         mock.patch.object(api, "_asana_cached", return_value=boards):
        d = api.suggest_asana_board("p1", _user="sven")
    assert d["gefunden"] is True
    assert d["grund"] == "name"
    assert d["asana_board_gid"] == "2"


def test_schwacher_treffer_wird_nicht_vorgeschlagen(api):
    """Unter einem Drittel Ueberschneidung ist es Zufall."""
    boards = [{"gid": "9", "name": "Lieferanten Einkauf Logistik Zentrale"}]
    with mock.patch.object(api, "_get_protocol_for_token",
                           return_value=_protokoll(name="Kundenbefragung")), \
         mock.patch.object(api._protocols_db, "find_previous_board", return_value=None), \
         mock.patch.object(api, "_get_asana_agent"), \
         mock.patch.object(api, "_asana_cached", return_value=boards):
        d = api.suggest_asana_board("p1", _user="sven")
    assert d["gefunden"] is False


def test_asana_ausfall_bleibt_folgenlos(api):
    """Ohne Asana gibt es keinen Vorschlag — aber auch keinen Fehler."""
    with mock.patch.object(api, "_get_protocol_for_token", return_value=_protokoll()), \
         mock.patch.object(api._protocols_db, "find_previous_board", return_value=None), \
         mock.patch.object(api, "_get_asana_agent", side_effect=RuntimeError("weg")):
        d = api.suggest_asana_board("p1", _user="sven")
    assert d["gefunden"] is False


def test_aufnahmetitel_wird_mitverglichen(api):
    """
    Der Plaud-Titel traegt oft den sprechenden Namen, waehrend meeting_name
    noch allgemein ist.
    """
    boards = [{"gid": "7", "name": "Standardisierungszirkel"}]
    protokoll = _protokoll(name="Abstimmung", titel="09-03 Standardisierungszirkel Vorfertigung")
    with mock.patch.object(api, "_get_protocol_for_token", return_value=protokoll), \
         mock.patch.object(api._protocols_db, "find_previous_board", return_value=None), \
         mock.patch.object(api, "_get_asana_agent"), \
         mock.patch.object(api, "_asana_cached", return_value=boards):
        d = api.suggest_asana_board("p1", _user="sven")
    assert d["gefunden"] is True
    assert d["asana_board_gid"] == "7"
