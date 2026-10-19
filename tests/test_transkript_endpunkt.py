"""
Tests fuer POST /api/protocols/{id}/fetch-transcript.

Vorgeschichte: Mara hatte keinen funktionierenden Weg zum Transkript. In
ihrem Gedaechtnis stand seit Juni ein CLI-Aufruf samt eigener Token-Datei im
Paperclip-Container — beides existiert dort nicht (mehr). Der Versuch
scheiterte mit `invalid auth header`, woraus sie auf einen abgelaufenen Token
schloss und den Auftrag blockierte.

Siebzehn Auftraege hingen seit August an dieser Fehldiagnose, waehrend der
Token durchgehend gueltig war und sich taeglich selbst erneuerte.

Dieser Endpunkt gibt ihr einen Dienst statt eines Werkzeugs, das sie nicht
hat. Die Tests sichern vor allem die Faelle, die damals falsch gedeutet
wurden: Was heisst "kein Zugang", was heisst "noch nichts da", was heisst
"Aufnahme weg"?
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

_BASE_ENV = {
    "TELEGRAM_BOT_TOKEN": "test-token",
    "TELEGRAM_WEBHOOK_SECRET": "test-secret",
    "API_SECRET_KEY": "test-key",
}


def _load_api():
    spec = importlib.util.spec_from_file_location("api_transkript_test", REPO_ROOT / "api.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, _BASE_ENV, clear=False):
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def api():
    return _load_api()


class _Antwort:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body

    @property
    def text(self):
        return json.dumps(self._body)


def _plaud_datei(segmente=None, quelle="transaction_polish", name="09-29 Abstimmung"):
    if segmente is None:
        segmente = [
            {"content": "Guten Morgen.", "speaker": "Sven Herbert", "start_time": 1500},
            {"content": "Moin.", "speaker": "Lev Keimes", "start_time": 4200},
            {"content": "  ", "speaker": "Sven Herbert", "start_time": 5000},
            {"content": "Zum ersten Punkt.", "speaker": "", "start_time": 65000},
        ]
    return {
        "name": name,
        "source_list": [
            {"data_type": quelle, "data_content": json.dumps(segmente)},
            {"data_type": "outline", "data_content": "[]"},
        ],
    }


def _protokoll(**felder):
    basis = {
        "id": "p1",
        "recording_id": "of_" + "a" * 32,
        "recording_title": "09-29 Abstimmung",
        "transcript": None,
        "transcript_saved_at": None,
    }
    basis.update(felder)
    return basis


# ── Formatierung ────────────────────────────────────────────────────────────

def test_transkript_traegt_zeit_und_sprecher(api):
    segmente = [{"content": "Guten Morgen.", "speaker": "Sven Herbert", "start_time": 1500}]
    assert api._transkript_formatieren(segmente) == "[00:01] Sven Herbert: Guten Morgen."


def test_minuten_werden_korrekt_gerechnet(api):
    segmente = [{"content": "Text", "speaker": "A", "start_time": 125000}]
    assert "[02:05]" in api._transkript_formatieren(segmente)


def test_leere_segmente_fallen_raus(api):
    segmente = [
        {"content": "Da.", "speaker": "A", "start_time": 0},
        {"content": "   ", "speaker": "A", "start_time": 1000},
        {"content": "", "speaker": "B", "start_time": 2000},
    ]
    assert api._transkript_formatieren(segmente).count("\n") == 0


def test_segment_ohne_sprecher_bleibt_erhalten(api):
    """Lieber ein Segment ohne Namen als ein fehlendes Stueck Gespraech."""
    zeile = api._transkript_formatieren([{"content": "Text", "start_time": 0}])
    assert "Text" in zeile
    assert ":" not in zeile.replace("[00:00]", "")


def test_original_speaker_als_rueckfall(api):
    segmente = [{"content": "Text", "original_speaker": "Speaker 3", "start_time": 0}]
    assert "Speaker 3:" in api._transkript_formatieren(segmente)


# ── Quellenwahl ─────────────────────────────────────────────────────────────

def test_nachbearbeitete_fassung_wird_bevorzugt(api):
    datei = {
        "source_list": [
            {"data_type": "transaction", "data_content": json.dumps([{"content": "roh"}])},
            {"data_type": "transaction_polish", "data_content": json.dumps([{"content": "fein"}])},
        ]
    }
    segmente, quelle = api._plaud_segmente(datei)
    assert quelle == "transaction_polish"
    assert segmente[0]["content"] == "fein"


def test_rohfassung_wenn_keine_nachbearbeitung(api):
    datei = {"source_list": [
        {"data_type": "transaction", "data_content": json.dumps([{"content": "roh"}])}
    ]}
    _segmente, quelle = api._plaud_segmente(datei)
    assert quelle == "transaction"


def test_kaputtes_json_wird_uebergangen(api):
    datei = {"source_list": [
        {"data_type": "transaction_polish", "data_content": "{kaputt"},
        {"data_type": "transaction", "data_content": json.dumps([{"content": "ok"}])},
    ]}
    segmente, quelle = api._plaud_segmente(datei)
    assert quelle == "transaction"
    assert segmente[0]["content"] == "ok"


# ── Die drei Zustaende, die damals verwechselt wurden ───────────────────────

def test_fehlender_zugang_meldet_503(api):
    with mock.patch.object(api, "_read_plaud_tokens", return_value={}):
        with pytest.raises(api.HTTPException) as f:
            api._plaud_datei_holen("of_abc")
    assert f.value.status_code == 503


def test_abgelehnter_token_meldet_503_mit_klarem_hinweis(api):
    """
    Das ist der Fall, den Mara monatelang faelschlich gemeldet hat. Wenn er
    wirklich eintritt, muss die Meldung eindeutig sein.
    """
    with mock.patch.object(api, "_read_plaud_tokens", return_value={"access_token": "x"}), \
         mock.patch.object(api._http, "get", return_value=_Antwort(status=401)):
        with pytest.raises(api.HTTPException) as f:
            api._plaud_datei_holen("of_abc")
    assert f.value.status_code == 503
    assert "erneuern" in f.value.detail.lower()


def test_geloeschte_aufnahme_meldet_404(api):
    with mock.patch.object(api, "_read_plaud_tokens", return_value={"access_token": "x"}), \
         mock.patch.object(api._http, "get", return_value=_Antwort(status=404)):
        with pytest.raises(api.HTTPException) as f:
            api._plaud_datei_holen("of_abc")
    assert f.value.status_code == 404


def test_noch_kein_transkript_ist_kein_fehler_sondern_409(api):
    """
    Plaud braucht nach einer Aufnahme einige Minuten. Wer das als Fehler
    meldet, erzeugt Fehlalarme — und Fehlalarme werden irgendwann ignoriert.
    """
    with mock.patch.object(api._protocols_db, "get_by_id", return_value=_protokoll()), \
         mock.patch.object(api, "_plaud_datei_holen", return_value={"source_list": []}):
        with pytest.raises(api.HTTPException) as f:
            api.fetch_transcript("p1", _key="test-key")
    assert f.value.status_code == 409


# ── Der Endpunkt ────────────────────────────────────────────────────────────

def test_holt_und_archiviert(api):
    with mock.patch.object(api._protocols_db, "get_by_id", return_value=_protokoll()), \
         mock.patch.object(api, "_plaud_datei_holen", return_value=_plaud_datei()), \
         mock.patch.object(api._protocols_db, "save_transcript") as speichern:
        d = api.fetch_transcript("p1", _key="test-key")
    assert "Sven Herbert: Guten Morgen." in d["transcript"]
    assert d["quelle"] == "transaction_polish"
    assert d["segmente"] == 4
    speichern.assert_called_once()
    # Das Archiv ist der Zweck: ohne es waere das Protokoll spaeter nicht
    # mehr gegen die Quelle pruefbar.
    assert "Guten Morgen." in speichern.call_args[0][1]


def test_zweiter_aufruf_nutzt_das_archiv(api):
    """Plaud nicht ohne Not behelligen — und schneller ist es auch."""
    vorhanden = _protokoll(transcript="[00:00] A: Schon da.", transcript_saved_at="2026-10-01")
    with mock.patch.object(api._protocols_db, "get_by_id", return_value=vorhanden), \
         mock.patch.object(api, "_plaud_datei_holen") as holen:
        d = api.fetch_transcript("p1", _key="test-key")
    assert d["quelle"] == "archiv"
    assert d["transcript"] == "[00:00] A: Schon da."
    holen.assert_not_called()


def test_erneut_erzwingt_frischen_abruf(api):
    """Nach einer Sprecherkorrektur traegt das Archiv die alte Zuordnung."""
    vorhanden = _protokoll(transcript="[00:00] Speaker 1: Alt.")
    with mock.patch.object(api._protocols_db, "get_by_id", return_value=vorhanden), \
         mock.patch.object(api, "_plaud_datei_holen", return_value=_plaud_datei()) as holen, \
         mock.patch.object(api._protocols_db, "save_transcript"):
        d = api.fetch_transcript("p1", erneut=True, _key="test-key")
    holen.assert_called_once()
    assert "Sven Herbert" in d["transcript"]


def test_unbekanntes_protokoll_meldet_404(api):
    with mock.patch.object(api._protocols_db, "get_by_id", return_value=None):
        with pytest.raises(api.HTTPException) as f:
            api.fetch_transcript("gibtsnicht", _key="test-key")
    assert f.value.status_code == 404


def test_ohne_aufnahme_meldet_404(api):
    with mock.patch.object(api._protocols_db, "get_by_id",
                           return_value=_protokoll(recording_id=None)):
        with pytest.raises(api.HTTPException) as f:
            api.fetch_transcript("p1", _key="test-key")
    assert f.value.status_code == 404


# ── Beide Schreibweisen der Aufnahme-Kennung ────────────────────────────────

def test_beide_id_schreibweisen_werden_versucht(api):
    """
    Seit 09/2026 tragen die Kennungen ein `of_`-Praefix. Aeltere Eintraege in
    der Datenbank haben es nicht — beide muessen auffindbar bleiben.
    """
    assert api._plaud_id_kandidaten("abc123") == ["abc123", "of_abc123"]
    assert api._plaud_id_kandidaten("of_abc123") == ["of_abc123"]


def test_blankes_hex_wird_mit_praefix_nachgeschlagen(api):
    versuche = []

    def fake_get(url, **kw):
        versuche.append(url)
        return _Antwort(status=200 if url.endswith("of_" + "b" * 32) else 404,
                        body=_plaud_datei())

    with mock.patch.object(api, "_read_plaud_tokens", return_value={"access_token": "x"}), \
         mock.patch.object(api._http, "get", side_effect=fake_get):
        datei = api._plaud_datei_holen("b" * 32)
    assert len(versuche) == 2
    assert datei["name"]
