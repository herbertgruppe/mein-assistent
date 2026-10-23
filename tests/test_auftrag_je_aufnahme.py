"""
Tests dafuer, dass jede Aufnahme einen eigenen Auftrag bekommt.

Sven meldete drei Vorgaenge, die als „bei Mara" in der Liste standen und
sich nicht bewegten. Es gab zu ihnen **gar keinen Auftrag**.

Ursache: Paperclip gibt bei gleichem Issue-Titel das bestehende Issue
zurueck — mit gueltiger Nummer und HTTP 200, also wie ein Erfolg. Zu einem
Termin gibt es aber mitunter zwei Aufnahmen, etwa wenn der Recorder
zwischendurch gestoppt wurde. Beide erzeugten denselben Titel
(„BL-Besprechung Volpp (03.09.2026)"), der zweite Vorgang bekam das Issue
des ersten zurueck — und wartete still. Einer davon vier Wochen.

Zwei Absicherungen: die Uhrzeit im Titel, und die Gegenprobe, ob das
zurueckgegebene Issue ueberhaupt zu diesem Vorgang gehoert.
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
    spec = importlib.util.spec_from_file_location("api_auftrag_test", REPO_ROOT / "api.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, _BASE_ENV, clear=False):
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def api():
    return _load_api()


def _protokoll(pid="draft-1", zeit="2026-09-03T08:36:30"):
    return {
        "id": pid,
        "recording_id": "of_" + "a" * 32,
        "recording_title": "09-03 Besprechung: WMS-Prozessdefinition",
        "meeting_name": "BL-Besprechung Volpp",
        "meeting_datetime": zeit,
        "event_id": "ev", "asana_board_gid": "b", "asana_section_gid": "s",
        "eingeladene": [],
    }


class _Antwort:
    def __init__(self, status=201, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body

    @property
    def text(self):
        return str(self._body)


# ── Die Uhrzeit macht den Titel eindeutig ───────────────────────────────────

def test_kurzdatum_ohne_uhrzeit(api):
    assert api._kurzdatum("2026-09-03T08:36:30") == "03.09.2026"


def test_kurzdatum_mit_uhrzeit(api):
    assert api._kurzdatum("2026-09-03T08:36:30", mit_uhrzeit=True) == "03.09.2026, 08:36"


def test_zwei_aufnahmen_ergeben_zwei_titel(api):
    """Der Kern: Beide Aufnahmen gehoeren zum selben Termin am selben Tag."""
    erste = api._kurzdatum("2026-09-03T08:36:30", mit_uhrzeit=True)
    zweite = api._kurzdatum("2026-09-03T11:27:21", mit_uhrzeit=True)
    assert erste != zweite


def test_kurzdatum_vertraegt_muell(api):
    assert api._kurzdatum(None) == "ohne Datum"
    assert api._kurzdatum("kein-datum", mit_uhrzeit=True) == "kein-datum"


def test_titel_enthaelt_die_uhrzeit(api):
    gesendet = {}

    def fake_post(url, **kw):
        gesendet.update(kw.get("json") or {})
        return _Antwort(body={"identifier": "HBE-1", "description": "draft-1"})

    with mock.patch.object(api, "_PC_API_KEY", "key"), \
         mock.patch.object(api._http, "post", side_effect=fake_post):
        api._create_mara_issue(_protokoll())
    assert "08:36" in gesendet["title"]


# ── Die Gegenprobe ──────────────────────────────────────────────────────────

def test_fremdes_issue_wird_abgewiesen(api):
    """
    Paperclip liefert das bestehende Issue mit HTTP 200 — das sah aus wie
    ein Erfolg und war der eigentliche Fehler.
    """
    fremd = {"identifier": "HBE-3440", "description": "Draft-ID: ein-anderer-vorgang"}
    with mock.patch.object(api, "_PC_API_KEY", "key"), \
         mock.patch.object(api._http, "post", return_value=_Antwort(body=fremd)):
        ergebnis = api._create_mara_issue(_protokoll())
    assert ergebnis is None, "ein fremdes Issue ist kein Auftrag"


def test_eigenes_issue_wird_angenommen(api):
    eigen = {"identifier": "HBE-3450", "description": "**Draft-ID:** `draft-1`"}
    with mock.patch.object(api, "_PC_API_KEY", "key"), \
         mock.patch.object(api._http, "post", return_value=_Antwort(body=eigen)):
        ergebnis = api._create_mara_issue(_protokoll())
    assert ergebnis == "HBE-3450"


def test_antwort_ohne_beschreibung_gilt_als_fremd(api):
    """Im Zweifel kein Auftrag — besser ein sichtbarer Fehler als Stille."""
    with mock.patch.object(api, "_PC_API_KEY", "key"), \
         mock.patch.object(api._http, "post",
                           return_value=_Antwort(body={"identifier": "HBE-9"})):
        assert api._create_mara_issue(_protokoll()) is None


# ── Der Vorgang bleibt offen, wenn niemand beauftragt wurde ─────────────────

def test_ohne_auftrag_kein_stilles_assigned(api):
    """
    Vorher stand der Vorgang auf `assigned`, trug in der Uebersicht
    „bei Mara" — und wartete auf niemanden.
    """
    p = _protokoll()
    p["status"] = "pending_assignment"
    req = api.ProtocolAssignRequest(event_id="ev", create_asana_task=False)

    with mock.patch.object(api, "_get_protocol_for_token", return_value=p), \
         mock.patch.object(api._protocols_db, "get_by_id", return_value=p), \
         mock.patch.object(api._protocols_db, "confirm_assignment"), \
         mock.patch.object(api, "_create_mara_issue", return_value=None), \
         mock.patch.object(api._protocols_db, "set_status") as zuruecksetzen:
        with pytest.raises(api.HTTPException) as fehler:
            api.assign_protocol("draft-1", req, token=None, _user="sven")

    assert fehler.value.status_code == 502
    zuruecksetzen.assert_called_once_with("draft-1", "pending_assignment")


def test_mit_auftrag_bleibt_alles_wie_bisher(api):
    p = _protokoll()
    p["status"] = "pending_assignment"
    req = api.ProtocolAssignRequest(event_id="ev", create_asana_task=False)

    with mock.patch.object(api, "_get_protocol_for_token", return_value=p), \
         mock.patch.object(api._protocols_db, "get_by_id", return_value=p), \
         mock.patch.object(api._protocols_db, "confirm_assignment"), \
         mock.patch.object(api, "_create_mara_issue", return_value="HBE-3450"), \
         mock.patch.object(api._protocols_db, "set_status") as zuruecksetzen:
        d = api.assign_protocol("draft-1", req, token=None, _user="sven")

    assert d["mara_issue"] == "HBE-3450"
    zuruecksetzen.assert_not_called()


def test_zweiter_klick_beauftragt_nicht_erneut(api):
    """Bestehendes Verhalten — darf durch die Absicherung nicht kippen."""
    p = _protokoll()
    p["status"] = "assigned"
    req = api.ProtocolAssignRequest(event_id="ev", create_asana_task=False)

    with mock.patch.object(api, "_get_protocol_for_token", return_value=p), \
         mock.patch.object(api._protocols_db, "get_by_id", return_value=p), \
         mock.patch.object(api._protocols_db, "confirm_assignment"), \
         mock.patch.object(api, "_create_mara_issue") as beauftragen, \
         mock.patch.object(api._protocols_db, "set_status") as zuruecksetzen:
        d = api.assign_protocol("draft-1", req, token=None, _user="sven")

    beauftragen.assert_not_called()
    zuruecksetzen.assert_not_called()
    assert d["status"] == "assigned"
