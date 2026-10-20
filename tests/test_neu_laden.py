"""
Tests fuer die „neu laden"-Knoepfe bei Terminen und Asana-Boards.

Sven legte ein Asana-Board an und fand es nicht in der Auswahl. Grund: Die
Board-Liste liegt 15 Minuten im Cache (Asanas Rate-Limit ist 150 Anfragen
pro Minute). Der einzige Ausweg war bis dahin ein Neustart des Dienstes —
also entweder eine Viertelstunde warten oder jemanden darum bitten.

Dasselbe gilt fuer einen Termin, der erst nach dem Oeffnen der Seite in
Outlook angelegt wurde. Outlook wird zwar nicht zwischengespeichert, aber
die Seite laedt die Liste nur einmal.
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
    spec = importlib.util.spec_from_file_location("api_neuladen_test", REPO_ROOT / "api.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, _BASE_ENV, clear=False):
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def api():
    return _load_api()


@pytest.fixture(autouse=True)
def leerer_cache(api):
    api._asana_cache.clear()
    yield
    api._asana_cache.clear()


# ── Der Cache selbst ────────────────────────────────────────────────────────

def test_zweiter_aufruf_kommt_aus_dem_cache(api):
    aufrufe = []

    def lader():
        aufrufe.append(1)
        return ["a"]

    api._asana_cached("boards", lader)
    api._asana_cached("boards", lader)
    assert len(aufrufe) == 1, "Der Cache schont Asanas Rate-Limit"


def test_refresh_umgeht_den_cache(api):
    """Svens Fall: Das neue Board muss sofort sichtbar werden."""
    stand = [["altes Board"]]

    def lader():
        return list(stand[0])

    assert api._asana_cached("boards", lader) == ["altes Board"]
    stand[0] = ["altes Board", "neues Board"]

    # Ohne refresh bleibt es beim alten Stand
    assert api._asana_cached("boards", lader) == ["altes Board"]
    # Mit refresh kommt das neue dazu
    assert api._asana_cached("boards", lader, refresh=True) == ["altes Board", "neues Board"]


def test_refresh_schreibt_den_cache_neu(api):
    """Nach dem Neuladen darf der naechste Aufruf nicht wieder alt sein."""
    stand = [["alt"]]
    lader = lambda: list(stand[0])

    api._asana_cached("boards", lader)
    stand[0] = ["alt", "neu"]
    api._asana_cached("boards", lader, refresh=True)
    assert api._asana_cached("boards", lader) == ["alt", "neu"]


def test_refresh_betrifft_nur_den_eigenen_schluessel(api):
    """Die Abschnitte eines anderen Boards bleiben unberuehrt."""
    api._asana_cached("boards", lambda: ["b"])
    api._asana_cached("sections:111", lambda: ["s"])
    aufrufe = []
    api._asana_cached("boards", lambda: aufrufe.append(1) or ["b2"], refresh=True)
    # sections unveraendert, ohne erneuten Lader-Aufruf
    assert api._asana_cached("sections:111", lambda: ["ANDERS"]) == ["s"]


# ── Die Endpunkte ───────────────────────────────────────────────────────────

class _Agent:
    def __init__(self, projekte):
        self._p = projekte

    def is_connected(self):
        return True

    def list_projects(self):
        return self._p

    def get_project_sections(self, gid):
        return [{"gid": "s1", "name": "Protokolle"}]


def test_boards_endpunkt_reicht_refresh_durch(api):
    stand = [[{"gid": "1", "name": "Alt"}]]
    with mock.patch.object(api, "_get_asana_agent", side_effect=lambda: _Agent(stand[0])):
        erste = api.get_asana_boards(_user="sven")
        stand[0] = [{"gid": "1", "name": "Alt"}, {"gid": "2", "name": "Neu"}]
        ohne = api.get_asana_boards(_user="sven")
        mit = api.get_asana_boards(refresh=True, _user="sven")
    assert len(erste) == 1
    assert len(ohne) == 1, "ohne refresh bleibt der Cache gueltig"
    assert len(mit) == 2, "mit refresh kommt das neue Board"


def test_sections_endpunkt_reicht_refresh_durch(api):
    aufrufe = []

    class _A(_Agent):
        def get_project_sections(self, gid):
            aufrufe.append(gid)
            return [{"gid": "s1", "name": "Protokolle"}]

    with mock.patch.object(api, "_get_asana_agent", side_effect=lambda: _A([])):
        api.get_asana_board_sections("111", _user="sven")
        api.get_asana_board_sections("111", _user="sven")
        assert len(aufrufe) == 1
        api.get_asana_board_sections("111", refresh=True, _user="sven")
    assert len(aufrufe) == 2


# ── Das Frontend ────────────────────────────────────────────────────────────

def _js(name):
    return (REPO_ROOT / "static" / name).read_text(encoding="utf-8")


def _html(name):
    return (REPO_ROOT / "templates" / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("datei", ["review.html", "review_assign.html"])
def test_beide_knoepfe_sind_da(datei):
    html = _html(datei)
    assert 'id="event-reload"' in html, "Termine ohne Neu-laden-Knopf"
    assert 'id="board-reload"' in html, "Boards ohne Neu-laden-Knopf"


@pytest.mark.parametrize("datei", ["review.js", "review_assign.js"])
def test_board_knopf_erzwingt_frische_daten(datei):
    js = _js(datei)
    assert "refresh=true" in js, "ohne refresh bliebe der Cache stehen"
    assert "loadBoards(true)" in js


@pytest.mark.parametrize("datei", ["review.js", "review_assign.js"])
def test_getroffene_wahl_ueberlebt_das_neuladen(datei):
    """
    Sonst verliert man beim Klick die bereits gewaehlte Zuordnung — und
    merkt es erst beim Absenden.
    """
    js = _js(datei)
    assert "var gewaehlt = boardSelect.value;" in js
    assert "boardSelect.value = gewaehlt;" in js


@pytest.mark.parametrize("datei", ["review.js", "review_assign.js"])
def test_knopf_zeigt_waehrend_des_ladens_rueckmeldung(datei):
    """Ohne sie klickt man zweimal, weil nichts zu passieren scheint."""
    js = _js(datei)
    assert "function mitLadeanzeige" in js
    assert "knopf.disabled = true;" in js


@pytest.mark.parametrize("datei", ["review.js", "review_assign.js"])
def test_ladefunktionen_geben_ein_versprechen_zurueck(datei):
    """Sonst kann die Ladeanzeige nicht wissen, wann sie enden darf."""
    js = _js(datei)
    assert "return fetch(url)" in js, "loadEvents ohne return"
    assert "return fetch('/api/asana/boards?'" in js, "loadBoards ohne return"
