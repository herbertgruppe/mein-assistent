"""
Tests fuer die Kategorie "Lena: Automatisch abgelegt".

Sven meldete: Mails, die Lena ohne Rueckfrage ins Archiv verschiebt, sieht er
im Posteingang nie. Im Archiv war danach nicht mehr unterscheidbar, welche Post
er selbst zum Ablegen markiert hatte und welche an ihm vorbeigegangen ist.

Loesung: Ein gedecktes "ablegen" (skip_archive=False) setzt eine eigene
Kategorie. Ein "ablegen" als blosser Vorschlag (skip_archive=True) behaelt
"Lena: Ablegen", denn diese Mail bleibt im Posteingang und wartet auf Sven.

Der Sonderfall, der sonst still Falschauskunft gaebe: Schlaegt das Verschieben
fehl, liegt die Mail weiter im Posteingang — dann darf sie nicht als
"Automatisch abgelegt" markiert bleiben.
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
    api_path = REPO_ROOT / "api.py"
    spec = importlib.util.spec_from_file_location("api_auto_archive_test", api_path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, _BASE_ENV, clear=False):
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def api():
    return _load_api()


class _OutlookOk:
    access_token = "fake-ms-token"

    def is_authenticated(self):
        return True


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}

    def json(self):
        return self._body

    @property
    def text(self):
        return str(self._body)


def _categorize(api, action, skip_archive, bestehend=None, move_status=201):
    """
    Ruft den Endpunkt auf und liefert (antwort, patches, move_versucht).
    patches ist die Liste aller an Graph geschickten Kategorie-Listen,
    in der Reihenfolge ihres Auftretens.
    """
    patches = []
    moves = []

    def fake_get(url, headers=None, timeout=None):
        return _Resp(200, {"categories": list(bestehend or [])})

    def fake_patch(url, headers=None, json=None, timeout=None):
        patches.append(list(json.get("categories", [])))
        return _Resp(200, {})

    def fake_post(url, headers=None, json=None, timeout=None):
        moves.append(url)
        return _Resp(move_status, {"id": "neue-id"})

    req = api.LenaCategorizeRequest(
        message_id="AAMk-test", action=action, skip_archive=skip_archive
    )
    with mock.patch.object(api, "_get_outlook_tool", return_value=_OutlookOk()), \
         mock.patch.object(api, "_resolve_folder_id", return_value="archiv-id"), \
         mock.patch("requests.get", side_effect=fake_get), \
         mock.patch("requests.patch", side_effect=fake_patch), \
         mock.patch("requests.post", side_effect=fake_post):
        antwort = api.lena_mail_categorize(req, _key="test-key")
    return antwort, patches, moves


# ── Der Kern: still archiviert bekommt eine eigene Kategorie ────────────────

def test_gedecktes_ablegen_traegt_auto_kategorie(api):
    """Svens Anliegen: im Archiv erkennen, was er nie gesehen hat."""
    antwort, patches, moves = _categorize(api, "ablegen", skip_archive=False)
    assert patches[0] == [api.LENA_AUTO_ARCHIVE_CATEGORY]
    assert moves, "Ein gedecktes ablegen muss die Mail auch verschieben"
    assert antwort.moved_to_archive is True


def test_vorschlag_behaelt_normale_kategorie(api):
    """Diese Mail bleibt im Posteingang — sie wartet auf Svens Urteil."""
    antwort, patches, moves = _categorize(api, "ablegen", skip_archive=True)
    assert patches[0] == ["Lena: Ablegen"]
    assert not moves, "Ein Vorschlag darf die Mail nicht verschieben"
    assert antwort.moved_to_archive is False


def test_andere_aktionen_unveraendert(api):
    for aktion, erwartet in [
        ("antworten", "Lena: Antworten"),
        ("tun", "Lena: Tun"),
        ("weiterleiten", "Lena: Weiterleiten"),
    ]:
        _, patches, moves = _categorize(api, aktion, skip_archive=False)
        assert patches[0] == [erwartet]
        assert not moves


# ── Der Sonderfall, der sonst luegen wuerde ─────────────────────────────────

def test_fehlgeschlagenes_verschieben_setzt_kategorie_zurueck(api):
    """
    Graph lehnt das Verschieben ab. Die Mail liegt weiter im Posteingang —
    "Automatisch abgelegt" waere dort schlicht falsch.
    """
    antwort, patches, _ = _categorize(api, "ablegen", skip_archive=False, move_status=403)
    assert antwort.moved_to_archive is False
    assert patches[-1] == ["Lena: Ablegen"], \
        "Nach fehlgeschlagenem Verschieben muss die Kategorie zurueckgesetzt werden"


# ── Fremde Kategorien bleiben erhalten ──────────────────────────────────────

def test_fremde_kategorien_bleiben(api):
    _, patches, _ = _categorize(
        api, "ablegen", skip_archive=False, bestehend=["Privat", "Lena: Tun"]
    )
    assert patches[0] == ["Privat", api.LENA_AUTO_ARCHIVE_CATEGORY], \
        "Nicht-Lena-Kategorien bleiben, alte Lena-Kategorien werden ersetzt"


# ── Rueckabbildung ──────────────────────────────────────────────────────────

def test_rueckabbildung_auf_ablegen(api):
    """Eine zurueckgeholte Mail muss weiterhin als 'ablegen' gelesen werden."""
    assert api.LENA_CATEGORY_TO_ACTION[api.LENA_AUTO_ARCHIVE_CATEGORY] == "ablegen"
    assert api.LENA_CATEGORY_TO_ACTION["Lena: Ablegen"] == "ablegen"


def test_kategorie_ist_in_den_definitionen(api):
    """Sonst legt Outlook sie ohne Farbe an und sie faellt im Archiv nicht auf."""
    namen = [d["displayName"] for d in api.LENA_MASTER_CATEGORIES]
    assert api.LENA_AUTO_ARCHIVE_CATEGORY in namen


def test_poller_kennt_die_kategorie():
    """Der Poller liest Kategorien zurueck — er muss die neue mitfuehren."""
    import importlib
    spec = importlib.util.spec_from_file_location(
        "poller_auto_archive_test", REPO_ROOT / "lena_mail_triage_poller.py"
    )
    poller = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, _BASE_ENV, clear=False):
        spec.loader.exec_module(poller)
    assert poller.KATEGORIE_ZU_AKTION[poller.KATEGORIE_AUTO_ABGELEGT] == "ablegen"
