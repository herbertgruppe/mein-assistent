"""
Tests fuer die Zeitzonen-Darstellung.

Sven meldete, Outlook-Termine wuerden zwei Stunden zu frueh angezeigt. Grund:
Microsoft Graph liefert UTC und kennzeichnet das nur im separaten Feld
`timeZone` — der Zeitstempel selbst traegt keinen Offset
("2026-10-19T07:00:00.0000000"). Dasselbe gilt fuer Plauds `start_at`.

Wer so einen Wert unbesehen anzeigt, ist im Sommer zwei Stunden zu frueh:
Ein Termin um 09:00 stand als 07:00 in der Liste.

Besonders geprueft wird der Wechsel zur Winterzeit am 25.10.2026 — ein
fester Offset von zwei Stunden waere ab dann wieder falsch.
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
    spec = importlib.util.spec_from_file_location("api_zeitzone_test", REPO_ROOT / "api.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, _BASE_ENV, clear=False):
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def api():
    return _load_api()


# ── Sommerzeit (MESZ, UTC+2) ────────────────────────────────────────────────

def test_sommerzeit_wird_um_zwei_stunden_versetzt(api):
    """Svens Fall: Ein Termin um 09:00 stand als 07:00 da."""
    assert api._als_ortszeit("2026-10-19T07:00:00") == "19.10.2026 09:00"


def test_mit_z_gekennzeichnet(api):
    assert api._als_ortszeit("2026-10-19T07:00:00Z") == "19.10.2026 09:00"


def test_mit_sekundenbruchteilen(api):
    """So liefert Graph es tatsaechlich."""
    assert api._als_ortszeit("2026-10-19T07:00:00.000000") == "19.10.2026 09:00"


def test_mit_explizitem_offset(api):
    """Traegt der Wert bereits einen Offset, wird er respektiert."""
    assert api._als_ortszeit("2026-10-19T09:00:00+02:00") == "19.10.2026 09:00"


# ── Winterzeit (MEZ, UTC+1) ─────────────────────────────────────────────────

def test_nach_der_zeitumstellung_nur_eine_stunde(api):
    """
    Am 25.10.2026 endet die Sommerzeit. Ein fester Offset von zwei Stunden
    waere ab dann falsch — deshalb echte Zeitzonenrechnung statt Addition.
    """
    assert api._als_ortszeit("2026-11-15T07:00:00") == "15.11.2026 08:00"


def test_umstellungstag_selbst(api):
    """Vormittags des 25.10. gilt bereits Winterzeit (Umstellung um 03:00)."""
    assert api._als_ortszeit("2026-10-25T07:00:00") == "25.10.2026 08:00"


def test_kurz_vor_der_umstellung_noch_sommerzeit(api):
    assert api._als_ortszeit("2026-10-24T07:00:00") == "24.10.2026 09:00"


# ── Tagesgrenzen ────────────────────────────────────────────────────────────

def test_datum_springt_korrekt(api):
    """23:30 UTC ist in Ortszeit bereits der Folgetag."""
    assert api._als_ortszeit("2026-10-19T23:30:00") == "20.10.2026 01:30"


# ── Unbrauchbare Eingaben ───────────────────────────────────────────────────

@pytest.mark.parametrize("wert", ["", None, "kein-datum", "19.10.2026"])
def test_muell_wird_unveraendert_zurueckgegeben(api, wert):
    """Lieber der Rohwert als ein Absturz der ganzen Seite."""
    ergebnis = api._als_ortszeit(wert)
    assert ergebnis == (wert or "")


# ── Das Frontend ────────────────────────────────────────────────────────────

def _lies(name):
    return (REPO_ROOT / "static" / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("datei", ["review.js", "review_assign.js"])
def test_frontend_rechnet_um(datei):
    js = _lies(datei)
    assert "function ortszeit" in js, f"{datei} ohne Umrechnung"
    assert "toLocaleTimeString" in js


@pytest.mark.parametrize("datei", ["review.js", "review_assign.js"])
def test_frontend_kennzeichnet_den_wert_als_utc(datei):
    """
    Ohne angehaengtes Z liest JavaScript den Zeitstempel als Ortszeit —
    genau der Fehler, um den es geht.
    """
    js = _lies(datei)
    assert "slice(0, 19) + 'Z'" in js


@pytest.mark.parametrize("datei", ["review.js", "review_assign.js"])
def test_kein_roher_zeitschnitt_mehr_im_dropdown(datei):
    """
    `(ev.start || '').slice(11, 16)` war die fehlerhafte Stelle. Als
    Rueckfall innerhalb von ortszeit() ist der Schnitt in Ordnung, im
    Dropdown selbst nicht mehr.
    """
    js = _lies(datei)
    assert "var time = ortszeit(ev.start);" in js
    assert "var time = (ev.start || '').slice(11, 16);" not in js
