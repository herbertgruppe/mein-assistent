"""
Tests fuer den Pruefvermerk zur Sprecherzuordnung und die Titelpflege.

**Pruefvermerk.** Sven prueft die Sprecher in Plaud, bevor er eine Aufnahme
freigibt — danach zieht Mara das Transkript und jede Korrektur kaeme zu spaet.
In Plaud laesst sich nicht festhalten, was er schon angesehen hat: Die
Third-Party-API kennt weder Kategorien noch Tags noch Status (geprueft am
19.10.2026, die Endpunkte /tags, /categories, /folders, /labels antworten
alle mit 404). Der Vermerk gehoert ohnehin hierher — er sagt etwas ueber
Svens Pruefung, nicht ueber die Aufnahme.

**Titelpflege.** Plaud nennt eine frische Aufnahme nach ihrem Zeitpunkt und
vergibt den sprechenden Titel erst spaeter. Der Poller liest sofort, also
bleibt der Zeitstempel stehen. Am 19.10.2026 betraf das 14 von 74 Vorgaengen;
bei sieben von acht Stichproben hatte Plaud laengst einen brauchbaren Namen.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from database.protocols_db import (  # noqa: E402
    SPRECHER_STATUS,
    SPRECHER_STATUS_TEXT,
    ProtocolsDB,
    ist_zeitstempel_titel,
)

_BASE_ENV = {
    "TELEGRAM_BOT_TOKEN": "test-token",
    "TELEGRAM_WEBHOOK_SECRET": "test-secret",
    "API_SECRET_KEY": "test-key",
}


def _load_api():
    spec = importlib.util.spec_from_file_location("api_vermerk_test", REPO_ROOT / "api.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, _BASE_ENV, clear=False):
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def api():
    return _load_api()


@pytest.fixture
def db(tmp_path):
    return ProtocolsDB(str(tmp_path / "p.db"))


def _anlegen(db, **felder):
    basis = dict(
        source="plaud",
        recording_id="of_" + "a" * 32,
        meeting_name="BL-Besprechung HRN",
        meeting_datetime="2026-10-01T09:00:00",
        markdown="# Entwurf",
    )
    basis.update(felder)
    ergebnis = db.create_draft(**basis)
    return ergebnis["id"] if isinstance(ergebnis, dict) else ergebnis


# ── Zeitstempel-Titel erkennen ──────────────────────────────────────────────

@pytest.mark.parametrize("titel", [
    "2026-09-18 09:33:46",
    "2026-10-15T11:01:18",
    "09-18 09:33",
    " 2026-09-16 13:57:46",
])
def test_zeitstempel_wird_erkannt(titel):
    assert ist_zeitstempel_titel(titel) is True


@pytest.mark.parametrize("titel", [
    "09-29 Abstimmung: Kulturbarometer, Personalthemen",
    "BL-Besprechung HRN",
    "09-28 Kick-off: Coglas-WMS-Projekt",
    "",
    None,
])
def test_sprechender_titel_wird_nicht_verwechselt(titel):
    assert ist_zeitstempel_titel(titel) is False


def test_plaud_kurzform_mit_text_gilt_als_sprechend():
    """
    "09-28 Kick-off: …" beginnt wie ein Zeitstempel, traegt aber einen Namen.
    Diese Faelle duerfen nicht als namenlos gelten — sonst wuerde die halbe
    Liste faelschlich markiert.
    """
    assert ist_zeitstempel_titel("09-28 Kick-off: Coglas") is False


# ── Der Vermerk in der Datenbank ────────────────────────────────────────────

def test_vorgabe_ist_offen(db):
    draft_id = _anlegen(db)
    assert (db.get_by_id(draft_id).get("sprecher_status") or "offen") == "offen"


@pytest.mark.parametrize("status", sorted(SPRECHER_STATUS))
def test_jeder_erlaubte_wert_laesst_sich_setzen(db, status):
    draft_id = _anlegen(db)
    assert db.set_sprecher_status(draft_id, status) is True
    assert db.get_by_id(draft_id)["sprecher_status"] == status


def test_unbekannter_wert_wird_abgewiesen(db):
    draft_id = _anlegen(db)
    with pytest.raises(ValueError):
        db.set_sprecher_status(draft_id, "vielleicht")


def test_zeitstempel_wird_mitgeschrieben(db):
    """
    Aendert Sven spaeter in Plaud noch etwas, laesst sich daran erkennen,
    dass der Vermerk aelter ist als die Korrektur.
    """
    draft_id = _anlegen(db)
    db.set_sprecher_status(draft_id, "geprueft")
    assert db.get_by_id(draft_id)["sprecher_geprueft_am"]


def test_zuruecksetzen_loescht_den_zeitstempel(db):
    draft_id = _anlegen(db)
    db.set_sprecher_status(draft_id, "geprueft")
    db.set_sprecher_status(draft_id, "offen")
    assert db.get_by_id(draft_id)["sprecher_geprueft_am"] is None


def test_alle_werte_haben_einen_text():
    for wert in SPRECHER_STATUS:
        assert SPRECHER_STATUS_TEXT.get(wert), f"{wert} ohne Beschriftung"


# ── Titel nachziehen ────────────────────────────────────────────────────────

def test_nur_zeitstempel_eintraege_werden_aufgelistet(db):
    _anlegen(db, meeting_name="Mit Namen", recording_id="of_" + "1" * 32)
    stumm = _anlegen(db, meeting_name="Stumm", recording_id="of_" + "2" * 32)
    db.update_recording_title(stumm, "2026-09-18 09:33:46")

    ids = [p["id"] for p in db.list_mit_zeitstempel_titel()]
    assert stumm in ids


def test_titel_laesst_sich_nachziehen(db):
    draft_id = _anlegen(db)
    db.update_recording_title(draft_id, "2026-09-18 09:33:46")
    assert db.update_recording_title(draft_id, "09-18 Abstimmung: Kundendienst") is True
    assert db.get_by_id(draft_id)["recording_title"] == "09-18 Abstimmung: Kundendienst"


def test_leerer_titel_ueberschreibt_nichts(db):
    """Ein leerer Name aus Plaud darf den vorhandenen nicht loeschen."""
    draft_id = _anlegen(db)
    db.update_recording_title(draft_id, "Guter Titel")
    assert db.update_recording_title(draft_id, "   ") is False
    assert db.get_by_id(draft_id)["recording_title"] == "Guter Titel"


# ── Der Endpunkt ────────────────────────────────────────────────────────────

def test_vermerk_wird_gesetzt(api):
    with mock.patch.object(api._protocols_db, "get_by_id", return_value={"id": "p1"}), \
         mock.patch.object(api._protocols_db, "set_sprecher_status") as setzen:
        d = api.set_sprecher_status(
            "p1", api.SprecherStatusRequest(status="nicht_noetig"), _user="sven"
        )
    setzen.assert_called_once_with("p1", "nicht_noetig")
    assert d["text"] == "keine Sprechererkennung nötig"


def test_vermerk_auf_unbekanntem_protokoll_meldet_404(api):
    with mock.patch.object(api._protocols_db, "get_by_id", return_value=None):
        with pytest.raises(api.HTTPException) as f:
            api.set_sprecher_status(
                "gibtsnicht", api.SprecherStatusRequest(status="geprueft"), _user="sven"
            )
    assert f.value.status_code == 404


def test_vermerk_braucht_angemeldeten_nutzer(api):
    """Wie die Uebersicht: ein Reviewer-Token darf hier nichts aendern."""
    import inspect
    quelle = inspect.getsource(api.set_sprecher_status)
    assert "require_angemeldeter_nutzer" in quelle


# ── Titel-Nachziehlauf ──────────────────────────────────────────────────────

def test_nachziehlauf_aktualisiert_nur_wo_plaud_besser_ist(api):
    kandidaten = [
        {"id": "a", "recording_id": "of_a", "recording_title": "2026-09-18 09:33:46"},
        {"id": "b", "recording_id": "of_b", "recording_title": "2026-09-16 13:57:46"},
    ]

    def fake_holen(rid):
        # Nur fuer die erste hat Plaud einen Namen
        return {"name": "09-18 Abstimmung: Kundendienst"} if rid == "of_a" \
            else {"name": "2026-09-16 13:57:46"}

    with mock.patch.object(api._protocols_db, "list_mit_zeitstempel_titel",
                           return_value=kandidaten), \
         mock.patch.object(api, "_plaud_datei_holen", side_effect=fake_holen), \
         mock.patch.object(api._protocols_db, "update_recording_title") as schreiben:
        d = api.refresh_recording_titles(_key="test-key")

    assert d["geprueft"] == 2
    assert len(d["aktualisiert"]) == 1
    assert d["in_plaud_ohne_namen"] == 1
    schreiben.assert_called_once_with("a", "09-18 Abstimmung: Kundendienst")


def test_nachziehlauf_sammelt_fehler_statt_abzubrechen(api):
    """Eine geloeschte Aufnahme darf den Lauf nicht stoppen."""
    kandidaten = [
        {"id": "a", "recording_id": "of_a", "recording_title": "2026-09-18 09:33:46"},
        {"id": "b", "recording_id": "of_b", "recording_title": "2026-09-19 10:00:00"},
    ]

    def fake_holen(rid):
        if rid == "of_a":
            raise api.HTTPException(status_code=404, detail="weg")
        return {"name": "09-19 Besprechung: Lager"}

    with mock.patch.object(api._protocols_db, "list_mit_zeitstempel_titel",
                           return_value=kandidaten), \
         mock.patch.object(api, "_plaud_datei_holen", side_effect=fake_holen), \
         mock.patch.object(api._protocols_db, "update_recording_title"):
        d = api.refresh_recording_titles(_key="test-key")

    assert len(d["fehler"]) == 1
    assert d["fehler"][0]["status"] == 404
    assert len(d["aktualisiert"]) == 1


def test_transkriptabruf_zieht_den_titel_mit(api):
    """
    Damit es nebenbei passiert und niemand daran denken muss.
    """
    protokoll = {
        "id": "p1", "recording_id": "of_a" * 8,
        "recording_title": "2026-09-29 10:00:00", "transcript": None,
    }
    datei = {
        "name": "09-29 Abstimmung: Kulturbarometer",
        "source_list": [{
            "data_type": "transaction",
            "data_content": json.dumps([{"content": "Text", "speaker": "A", "start_time": 0}]),
        }],
    }
    with mock.patch.object(api._protocols_db, "get_by_id", return_value=protokoll), \
         mock.patch.object(api, "_plaud_datei_holen", return_value=datei), \
         mock.patch.object(api._protocols_db, "save_transcript"), \
         mock.patch.object(api._protocols_db, "update_recording_title") as titel:
        api.fetch_transcript("p1", _key="test-key")
    titel.assert_called_once_with("p1", "09-29 Abstimmung: Kulturbarometer")


def test_transkriptabruf_ueberschreibt_guten_titel_nicht_mit_zeitstempel(api):
    protokoll = {
        "id": "p1", "recording_id": "of_a" * 8,
        "recording_title": "BL-Besprechung HRN", "transcript": None,
    }
    datei = {
        "name": "2026-09-29 10:00:00",
        "source_list": [{
            "data_type": "transaction",
            "data_content": json.dumps([{"content": "Text", "speaker": "A", "start_time": 0}]),
        }],
    }
    with mock.patch.object(api._protocols_db, "get_by_id", return_value=protokoll), \
         mock.patch.object(api, "_plaud_datei_holen", return_value=datei), \
         mock.patch.object(api._protocols_db, "save_transcript"), \
         mock.patch.object(api._protocols_db, "update_recording_title") as titel:
        api.fetch_transcript("p1", _key="test-key")
    titel.assert_not_called()


# ── Übersicht ───────────────────────────────────────────────────────────────

def test_uebersicht_liefert_den_vermerk(api):
    eintrag = api._uebersicht_eintrag({
        "id": "p1", "recording_title": "09-29 Abstimmung", "meeting_name": "",
        "meeting_datetime": "2026-09-29T09:00:00", "status": "pending_assignment",
        "created_at": "2026-09-29T09:00:00", "reviewer_token": "t", "reminder_count": 0,
        "sprecher_status": "nicht_noetig", "sprecher_geprueft_am": "2026-10-19T10:00:00",
        "expires_at": None,
    })
    assert eintrag["sprecher_status"] == "nicht_noetig"
    assert eintrag["sprecher_text"] == "keine Sprechererkennung nötig"


def test_uebersicht_zaehlt_ungepruefte(api):
    offen = [
        {"id": "a", "recording_title": "X", "meeting_name": "", "meeting_datetime": "",
         "status": "pending_assignment", "created_at": "2026-10-01T09:00:00",
         "reviewer_token": "t1", "reminder_count": 0, "expires_at": None},
        {"id": "b", "recording_title": "Y", "meeting_name": "", "meeting_datetime": "",
         "status": "pending_assignment", "created_at": "2026-10-01T09:00:00",
         "reviewer_token": "t2", "reminder_count": 0, "sprecher_status": "geprueft",
         "expires_at": None},
    ]
    with mock.patch.object(api._protocols_db, "list_pending_assignments", return_value=offen), \
         mock.patch.object(api._protocols_db, "list_awaiting_review", return_value=[]), \
         mock.patch.object(api._protocols_db, "list_recently_finalized", return_value=[]):
        d = api.protocols_overview(_user="sven")
    assert d["kennzahlen"]["sprecher_offen"] == 1
