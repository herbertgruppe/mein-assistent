"""
Tests fuer die Outlook-Eingeladenen mit Antwortstatus.

Sven wollte im Protokoll nicht nur sehen, wer gesprochen hat, sondern auch
wer eingeladen war und ob er zu- oder abgesagt hat — ausdruecklich als
Outlook-Angabe gekennzeichnet und getrennt von den Anwesenden.

Der Grund fuer die Trennung: Eine Zusage ist keine Anwesenheit. Wer zugesagt
hat und dann nicht kam, taucht in keinem Transkript auf; wer spontan dazukam,
stand nie im Termin. Die Vermischung beider Begriffe war jahrelang die
haeufigste Fehlerquelle bei den Teilnehmerlisten.

Nebenbefund vom 20.10.2026: Besprechungsraeume stehen in Outlook als
Teilnehmer (zwei bis drei pro Tag). Ein Raum hat nicht mitdiskutiert.
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
    spec = importlib.util.spec_from_file_location("api_eingeladene_test", REPO_ROOT / "api.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, _BASE_ENV, clear=False):
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def api():
    return _load_api()


# ── Status uebersetzen ──────────────────────────────────────────────────────

@pytest.mark.parametrize("roh,erwartet", [
    ("accepted", "zugesagt"),
    ("declined", "abgesagt"),
    ("tentativelyAccepted", "mit Vorbehalt zugesagt"),
    ("notResponded", "ohne Antwort"),
    ("none", "ohne Antwort"),
    ("organizer", "Organisator"),
])
def test_status_wird_uebersetzt(api, roh, erwartet):
    block = api._eingeladene_block([{"name": "Frank Herbert", "status": roh}])
    assert f"- Frank Herbert — {erwartet}" in block


def test_unbekannter_status_bleibt_ohne_angabe(api):
    """Lieber keine Angabe als eine erfundene."""
    block = api._eingeladene_block([{"name": "Frank Herbert", "status": "weissnicht"}])
    assert "- Frank Herbert" in block
    assert "—" not in block


def test_alte_schreibweise_mit_blossen_namen(api):
    """Bestehende Protokolle fuehren nur Namen — die duerfen nicht brechen."""
    block = api._eingeladene_block(["Frank Herbert", "Jan Herbert"])
    assert "- Frank Herbert" in block
    assert "- Jan Herbert" in block


def test_gemischte_schreibweisen(api):
    block = api._eingeladene_block([
        "Alt Ohne Status",
        {"name": "Neu Mit Status", "status": "declined"},
    ])
    assert "- Alt Ohne Status" in block
    assert "- Neu Mit Status — abgesagt" in block


# ── Besprechungsraeume ──────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "Besprechung 10 1. OG Neubau",
    "Raum 204",
    "Konferenzraum Mannheim",
])
def test_raeume_werden_erkannt(api, name):
    assert api._ist_raum(name) is True


@pytest.mark.parametrize("name", [
    "Frank Herbert",
    "Dr. Sven Herbert",
    "Nadia El Monjid",
])
def test_personen_bleiben_personen(api, name):
    assert api._ist_raum(name) is False


def test_raum_per_mailadresse(api):
    assert api._ist_raum("Grosser Saal", "raum-204@herbert.de") is True


def test_raeume_stehen_nicht_in_der_liste(api):
    """Ein Raum hat nicht mitdiskutiert."""
    block = api._eingeladene_block([
        {"name": "Sven Herbert", "status": "organizer"},
        {"name": "Besprechung 10 1. OG Neubau", "status": "accepted"},
    ])
    assert "- Sven Herbert" in block
    assert "- Besprechung 10" not in block
    # Aber nachvollziehbar, warum jemand fehlt
    assert "Raum und nicht Person" in block
    assert "Besprechung 10 1. OG Neubau" in block


# ── Randfaelle ──────────────────────────────────────────────────────────────

def test_ohne_termin_steht_das_auch_da(api):
    block = api._eingeladene_block([])
    assert "Kein Termin zugeordnet" in block


def test_nur_raeume_ergibt_keine_leere_liste(api):
    block = api._eingeladene_block([{"name": "Raum 204", "status": "accepted"}])
    assert "Keine Personen" in block


def test_eintrag_ohne_namen_faellt_raus(api):
    block = api._eingeladene_block([{"status": "accepted"}, {"name": "Frank Herbert"}])
    assert block.count("- ") == 1


def test_mailadresse_als_rueckfall(api):
    """Ohne Anzeigenamen ist die Adresse besser als nichts."""
    block = api._eingeladene_block([{"email": "extern@firma.de", "status": "accepted"}])
    assert "extern@firma.de — zugesagt" in block


# ── Der Auftrag an Mara ─────────────────────────────────────────────────────

def test_auftrag_erklaert_die_trennung(api):
    protocol = {
        "id": "p1", "recording_id": "of_x", "recording_title": "Titel",
        "meeting_name": "BL-Besprechung HRN", "meeting_datetime": "2026-10-01T09:00:00",
        "event_id": "ev", "asana_board_gid": "b", "asana_section_gid": "s",
        "eingeladene": [{"name": "Frank Herbert", "status": "declined"}],
    }
    with mock.patch.object(api, "_PC_API_KEY", "key"), \
         mock.patch.object(api._http, "post") as post:
        post.return_value = mock.Mock(ok=True, json=lambda: {"identifier": "HBE-1"})
        api._create_mara_issue(protocol)
    beschreibung = post.call_args.kwargs["json"]["description"]
    assert "Frank Herbert — abgesagt" in beschreibung
    assert "Eine Zusage ist keine Anwesenheit" in beschreibung
    assert "getrennt" in beschreibung


# ── Das Frontend schickt den Status mit ─────────────────────────────────────

def test_zuordnungsseite_schickt_den_status():
    js = (REPO_ROOT / "static" / "review_assign.js").read_text(encoding="utf-8")
    assert "status: a.response" in js, "ohne response kein Zu-/Absage-Status"
    assert "ev.attendees" in js


def test_zuordnungsseite_faellt_auf_namen_zurueck():
    """Ein Termin ohne Einzelangaben darf nicht zu einer leeren Liste fuehren."""
    js = (REPO_ROOT / "static" / "review_assign.js").read_text(encoding="utf-8")
    assert "ev.attendee_names" in js
