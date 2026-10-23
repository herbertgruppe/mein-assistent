"""
Tests fuer die Kennzahlen und Altersangaben der Uebersichtsseite.

Sven schickte einen Screenshot, auf dem drei Dinge nicht stimmten:

1. „9 warten auf Freigabe" — acht davon lagen aber noch bei Mara und waren
   gar keine fertigen Entwuerfe. Zusammengezaehlt sah unfertige Arbeit wie
   erledigte aus.
2. „0 Tage die aelteste" — waehrend in der Liste darunter Eintraege mit
   130 Tagen standen. Die Kennzahl zaehlte nur offene Zuordnungen, von
   denen es keine mehr gab.
3. Eine Besprechung vom 03.09. stand mit „0 Tage" da. Grund: Das Alter kam
   aus dem Anlagedatum, und dieser Vorgang war erst am selben Tag aus dem
   Altweg ueberfuehrt worden.
"""
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone
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
    spec = importlib.util.spec_from_file_location("api_kennzahlen_test", REPO_ROOT / "api.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, _BASE_ENV, clear=False):
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def api():
    return _load_api()


def _vor_tagen(tage):
    return (datetime.now(timezone.utc) - timedelta(days=tage)).isoformat()


def _p(status, besprechung_vor, angelegt_vor, **felder):
    basis = {
        "id": "p" + str(besprechung_vor),
        "status": status,
        "recording_title": "Titel",
        "meeting_name": "Termin",
        "meeting_datetime": _vor_tagen(besprechung_vor),
        "created_at": _vor_tagen(angelegt_vor),
        "reviewer_token": "t" + str(besprechung_vor),
        "reminder_count": 0,
        "expires_at": None,
    }
    basis.update(felder)
    return basis


def _uebersicht(api, offen=None, review=None):
    with mock.patch.object(api._protocols_db, "list_pending_assignments",
                           return_value=offen or []), \
         mock.patch.object(api._protocols_db, "list_awaiting_review",
                           return_value=review or []), \
         mock.patch.object(api._protocols_db, "list_recently_finalized", return_value=[]):
        return api.protocols_overview(_user="sven")


# ── „warten auf Freigabe" zaehlt nur echte Entwuerfe ────────────────────────

def test_bei_mara_wird_getrennt_gezaehlt(api):
    """Svens Screenshot: 9 angezeigt, davon 8 noch gar nicht fertig."""
    review = [_p("assigned", 20, 1) for _ in range(8)] + [_p("draft", 130, 130)]
    d = _uebersicht(api, review=review)
    k = d["kennzahlen"]
    assert k["zur_freigabe"] == 1, "nur der echte Entwurf"
    assert k["bei_mara"] == 8
    assert k["im_review"] == 9, "die Gesamtzahl bleibt als Listenlaenge erhalten"


def test_in_review_zaehlt_zur_freigabe(api):
    """Ein geoeffneter Editor ist weiterhin Svens Arbeit."""
    d = _uebersicht(api, review=[_p("in_review", 5, 5), _p("draft", 6, 6)])
    assert d["kennzahlen"]["zur_freigabe"] == 2
    assert d["kennzahlen"]["bei_mara"] == 0


# ── Das Alter bezieht sich auf die Besprechung ──────────────────────────────

def test_aelteste_besprechung_statt_aeltester_datensatz(api):
    """
    Der Fall aus dem Screenshot: keine offenen Zuordnungen mehr, aber
    Entwuerfe von vor Monaten. „0 Tage" war technisch richtig und trotzdem
    irrefuehrend.
    """
    d = _uebersicht(api, offen=[], review=[_p("draft", 130, 1)])
    assert d["kennzahlen"]["aeltestes_tage"] == 0, "kein offener Vorgang"
    assert d["kennzahlen"]["aelteste_besprechung_tage"] == 130


def test_beide_altersangaben_am_eintrag(api):
    """
    Eine am 03.09. gehaltene Besprechung, deren Vorgang erst heute aus dem
    Altweg ueberfuehrt wurde.
    """
    eintrag = api._uebersicht_eintrag(_p("assigned", 47, 0))
    assert eintrag["alter_tage"] == 0, "der Vorgang ist frisch"
    assert eintrag["alter_besprechung_tage"] == 47, "die Besprechung nicht"


def test_alter_vertraegt_fehlendes_datum(api):
    eintrag = api._uebersicht_eintrag(_p("draft", 5, 5, meeting_datetime=None))
    assert eintrag["alter_besprechung_tage"] is None


def test_ohne_vorgaenge_keine_fehler(api):
    d = _uebersicht(api)
    assert d["kennzahlen"]["aelteste_besprechung_tage"] == 0
    assert d["kennzahlen"]["zur_freigabe"] == 0
    assert d["kennzahlen"]["bei_mara"] == 0


# ── Das Frontend nutzt die richtigen Werte ──────────────────────────────────

def _js():
    return (REPO_ROOT / "static" / "overview.js").read_text(encoding="utf-8")


def test_frontend_zeigt_echte_entwuerfe():
    assert "k.zur_freigabe" in _js()


def test_frontend_zeigt_bei_mara_getrennt():
    js = _js()
    assert "kpi-mara" in js
    assert "k.bei_mara" in js


def test_frontend_nutzt_das_besprechungsalter():
    js = _js()
    assert "k.aelteste_besprechung_tage" in js
    assert "alter_besprechung_tage" in js


def test_freigabe_reiter_rechnet_ab_besprechung():
    """Beim Zuordnen zaehlt die Wartezeit, beim Freigeben das Datum."""
    js = _js()
    assert 'reiter === "freigabe"' in js


def test_altersmarkierung_folgt_demselben_wert():
    """
    Sonst stuende ein Eintrag mit 130 Tagen in Blau, weil die Farbe aus
    einem anderen Wert kaeme als die Zahl daneben.
    """
    js = _js()
    assert "altersKlasse(massgeblich)" in js


def test_kennzahl_hat_eine_klare_beschriftung():
    html = (REPO_ROOT / "templates" / "overview.html").read_text(encoding="utf-8")
    assert "Tage die älteste Besprechung" in html
    assert "noch bei Mara" in html
