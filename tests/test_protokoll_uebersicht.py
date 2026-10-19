"""
Tests fuer die Protokoll-Uebersicht und die Tagesmeldung.

Ausgangslage: Je offener Zuordnung ging eine eigene Telegram-Nachricht raus.
Bei 21 offenen Aufnahmen waren 99 Erinnerungen verschickt, eine davon neun Mal
fuer dieselbe Aufnahme. Der Kanal wurde dadurch unbenutzbar — und damit die
Erinnerung wirkungslos.

Neu: eine Meldung je Werktag mit den drei aeltesten namentlich und einem Link
auf die Uebersichtsseite. Die Entscheidung, ob gesendet wird, faellt in der
API, weil der Poller den Endpunkt alle zehn Minuten aufruft.
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
    spec = importlib.util.spec_from_file_location(
        "api_uebersicht_test", REPO_ROOT / "api.py"
    )
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, _BASE_ENV, clear=False):
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def api():
    return _load_api()


def _protokoll(**felder):
    basis = {
        "id": "p1",
        "recording_title": "BL-Besprechung HRN",
        "meeting_name": "BL-Besprechung HRN",
        "meeting_datetime": "2026-10-01T09:00:00",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reviewer_token": "tok-1",
        "reminder_count": 0,
        "status": "pending_assignment",
        "transcript": None,
        "recording_duration": "",
        "expires_at": None,
    }
    basis.update(felder)
    return basis


def _vor_tagen(tage):
    return (datetime.now(timezone.utc) - timedelta(days=tage)).isoformat()


# ── Faelligkeit: Werktag, Uhrzeit, einmal am Tag ────────────────────────────

def _jetzt(api, jahr, monat, tag, stunde):
    return datetime(jahr, monat, tag, stunde, 0, tzinfo=api.DIGEST_TZ)


def test_samstag_und_sonntag_bleiben_still(api):
    """Svens Wunsch: am Wochenende keine Meldung."""
    for tag in (17, 18):  # 2026-10-17 Sa, 2026-10-18 So
        faellig, grund = api._digest_faellig(_jetzt(api, 2026, 10, tag, 9), None)
        assert faellig is False
        assert grund == "Wochenende"


def test_werktag_nach_der_stunde_ist_faellig(api):
    faellig, grund = api._digest_faellig(_jetzt(api, 2026, 10, 19, 9), None)
    assert faellig is True
    assert grund == "faellig"


def test_vor_der_eingestellten_stunde_noch_nicht(api):
    faellig, grund = api._digest_faellig(_jetzt(api, 2026, 10, 19, 5), None)
    assert faellig is False
    assert "vor" in grund


def test_zweimal_am_selben_tag_nicht(api):
    """Der Poller ruft alle zehn Minuten auf — das darf nichts ausloesen."""
    heute = _jetzt(api, 2026, 10, 19, 9)
    schon = heute.replace(hour=7).astimezone(timezone.utc).isoformat()
    faellig, grund = api._digest_faellig(heute, schon)
    assert faellig is False
    assert grund == "heute bereits verschickt"


def test_am_naechsten_tag_wieder(api):
    gestern = _jetzt(api, 2026, 10, 19, 7).astimezone(timezone.utc).isoformat()
    faellig, _ = api._digest_faellig(_jetzt(api, 2026, 10, 20, 9), gestern)
    assert faellig is True


def test_unbrauchbarer_zeitstempel_blockiert_nicht(api):
    """Ein kaputter Wert in der Spalte darf die Meldung nicht dauerhaft stoppen."""
    faellig, _ = api._digest_faellig(_jetzt(api, 2026, 10, 19, 9), "kein-datum")
    assert faellig is True


# ── Der Text ────────────────────────────────────────────────────────────────

def test_text_nennt_anzahl_und_aeltestes(api):
    offen = [_protokoll(id="a", created_at=_vor_tagen(28)),
             _protokoll(id="b", created_at=_vor_tagen(3))]
    t = api._digest_text(offen, [])
    assert "2 Aufnahmen warten auf Zuordnung" in t
    assert "28 Tagen" in t


def test_text_nennt_hoechstens_drei_namentlich(api):
    offen = [_protokoll(id=str(i), recording_title="Termin %d" % i,
                        created_at=_vor_tagen(30 - i)) for i in range(10)]
    t = api._digest_text(offen, [])
    assert t.count("   • ") == 3, "mehr als drei Eintraege liest niemand"
    assert "und 7 weitere" in t


def test_text_zeigt_die_aeltesten_zuerst(api):
    offen = [_protokoll(id="neu", recording_title="Neu", created_at=_vor_tagen(1)),
             _protokoll(id="alt", recording_title="Uralt", created_at=_vor_tagen(40))]
    t = api._digest_text(offen, [])
    assert t.index("Uralt") < t.index("Neu")


def test_einzahl_ist_korrekt(api):
    t = api._digest_text([_protokoll(created_at=_vor_tagen(2))], [])
    assert "1 Aufnahme wartet auf Zuordnung" in t
    assert "Aufnahmen" not in t


def test_text_enthaelt_den_uebersichts_link(api):
    t = api._digest_text([_protokoll()], [])
    assert "/protokolle" in t


def test_freigaben_werden_mitgezaehlt(api):
    t = api._digest_text([], [_protokoll(status="draft") for _ in range(4)])
    assert "4 Protokollentwuerfe warten auf Freigabe" in t


def test_eine_freigabe_in_einzahl(api):
    t = api._digest_text([], [_protokoll(status="draft")])
    assert "1 Protokollentwurf wartet auf Freigabe" in t


def test_sprecherhinweis_nur_bei_offenen_zuordnungen(api):
    assert "Sprecher in Plaud" in api._digest_text([_protokoll()], [])
    assert "Sprecher in Plaud" not in api._digest_text([], [_protokoll(status="draft")])


# ── Zustellung: Paperclip zuerst, Telegram als Rueckfall ────────────────────

class _Antwort:
    def __init__(self, ok=True, status=201):
        self.ok = ok
        self.status_code = status
        self.text = "{}"


def test_paperclip_ist_der_erste_weg(api):
    with mock.patch.object(api, "_PC_API_URL", "https://pc.test"), \
         mock.patch.object(api, "_PC_BOARD_KEY", "key"), \
         mock.patch.object(api, "_MARA_CHAT_ENDPOINT", "ep"), \
         mock.patch.object(api, "_MARA_CHAT_CONVERSATION", "conv"), \
         mock.patch.object(api._http, "post", return_value=_Antwort()) as post:
        erfolg, weg = api._an_mara_zustellen("Text", "schluessel")
    assert erfolg is True
    assert weg == "paperclip"
    assert post.call_args.kwargs["json"]["idempotencyKey"] == "schluessel"


def test_telegram_faengt_paperclip_ausfall_auf(api):
    """Eine Stoerung in Paperclip darf nicht heissen, dass Sven nichts erfaehrt."""
    with mock.patch.object(api, "_PC_API_URL", "https://pc.test"), \
         mock.patch.object(api, "_PC_BOARD_KEY", "key"), \
         mock.patch.object(api, "_MARA_CHAT_ENDPOINT", "ep"), \
         mock.patch.object(api, "_MARA_CHAT_CONVERSATION", "conv"), \
         mock.patch.object(api._http, "post", side_effect=RuntimeError("weg")), \
         mock.patch.object(api, "_TG_MARA_BOT_TOKEN", "mara-token"), \
         mock.patch.object(api, "_TG_ADMIN_CHAT_ID", "123"), \
         mock.patch.object(api, "_tg_mara_send_message", return_value=42):
        erfolg, weg = api._an_mara_zustellen("Text", "schluessel")
    assert erfolg is True
    assert weg == "telegram_mara"


def test_ohne_jeden_weg_kein_stiller_erfolg(api):
    with mock.patch.object(api, "_PC_BOARD_KEY", ""), \
         mock.patch.object(api, "_TG_MARA_BOT_TOKEN", ""), \
         mock.patch.object(api, "_TG_BOT_TOKEN", ""):
        erfolg, weg = api._an_mara_zustellen("Text", "schluessel")
    assert erfolg is False
    assert weg == "kein_weg"


# ── Der Endpunkt ────────────────────────────────────────────────────────────

def test_ohne_erfolgreiche_zustellung_wird_nicht_markiert(api):
    """
    Sonst gilt der Tag als erledigt, obwohl Sven nichts bekommen hat — und
    die Meldung kommt erst am naechsten Tag wieder.
    """
    offen = [_protokoll(id="a")]
    with mock.patch.object(api._protocols_db, "list_pending_assignments", return_value=offen), \
         mock.patch.object(api._protocols_db, "list_awaiting_review", return_value=[]), \
         mock.patch.object(api._protocols_db, "last_reminder_timestamp", return_value=None), \
         mock.patch.object(api, "_an_mara_zustellen", return_value=(False, "kein_weg")), \
         mock.patch.object(api._protocols_db, "mark_reminder_sent") as markiere:
        with pytest.raises(api.HTTPException) as fehler:
            api.send_assignment_reminders(force=True, _key="test-key")
    assert fehler.value.status_code == 503
    markiere.assert_not_called()


def test_erfolgreiche_zustellung_markiert_alle(api):
    offen = [_protokoll(id="a"), _protokoll(id="b")]
    with mock.patch.object(api._protocols_db, "list_pending_assignments", return_value=offen), \
         mock.patch.object(api._protocols_db, "list_awaiting_review", return_value=[]), \
         mock.patch.object(api._protocols_db, "last_reminder_timestamp", return_value=None), \
         mock.patch.object(api, "_an_mara_zustellen", return_value=(True, "paperclip")), \
         mock.patch.object(api._protocols_db, "mark_reminder_sent") as markiere:
        ergebnis = api.send_assignment_reminders(force=True, _key="test-key")
    assert ergebnis["gesendet"] is True
    assert ergebnis["offen"] == 2
    assert markiere.call_count == 2


def test_eine_meldung_statt_einer_je_aufnahme(api):
    """Der Kern der Aenderung: 21 offene Vorgaenge ergeben genau eine Nachricht."""
    offen = [_protokoll(id=str(i), created_at=_vor_tagen(i + 1)) for i in range(21)]
    with mock.patch.object(api._protocols_db, "list_pending_assignments", return_value=offen), \
         mock.patch.object(api._protocols_db, "list_awaiting_review", return_value=[]), \
         mock.patch.object(api._protocols_db, "last_reminder_timestamp", return_value=None), \
         mock.patch.object(api._protocols_db, "mark_reminder_sent"), \
         mock.patch.object(api, "_an_mara_zustellen", return_value=(True, "paperclip")) as zustellen:
        api.send_assignment_reminders(force=True, _key="test-key")
    assert zustellen.call_count == 1


def test_nichts_offen_ergibt_keine_meldung(api):
    with mock.patch.object(api._protocols_db, "list_pending_assignments", return_value=[]), \
         mock.patch.object(api._protocols_db, "list_awaiting_review", return_value=[]), \
         mock.patch.object(api, "_an_mara_zustellen") as zustellen:
        ergebnis = api.send_assignment_reminders(_key="test-key")
    assert ergebnis["gesendet"] is False
    zustellen.assert_not_called()


# ── Uebersicht ──────────────────────────────────────────────────────────────

def test_eintrag_erkennt_fehlenden_titel(api):
    """Plaud vergibt oft nur einen Zeitstempel — das muss sichtbar sein."""
    e = api._uebersicht_eintrag(_protokoll(recording_title="10-19 09:30"))
    assert e["ohne_namen"] is True
    e2 = api._uebersicht_eintrag(_protokoll(recording_title="BL-Besprechung HRN"))
    assert e2["ohne_namen"] is False


def test_eintrag_enthaelt_den_review_link(api):
    e = api._uebersicht_eintrag(_protokoll(reviewer_token="abc123"))
    assert e["link"] == "/review/abc123"


def test_uebersicht_zaehlt_richtig(api):
    offen = [_protokoll(id="a", created_at=_vor_tagen(28)),
             _protokoll(id="b", recording_title="10-19 09:30", created_at=_vor_tagen(2))]
    with mock.patch.object(api._protocols_db, "list_pending_assignments", return_value=offen), \
         mock.patch.object(api._protocols_db, "list_awaiting_review", return_value=[_protokoll(status="draft")]), \
         mock.patch.object(api._protocols_db, "list_recently_finalized", return_value=[]):
        d = api.protocols_overview(_user="sven")
    assert d["kennzahlen"]["offen"] == 2
    assert d["kennzahlen"]["im_review"] == 1
    assert d["kennzahlen"]["aeltestes_tage"] == 28
    assert d["kennzahlen"]["ohne_namen"] == 1


def test_alter_in_tagen_vertraegt_muell(api):
    assert api._alter_in_tagen(None) is None
    assert api._alter_in_tagen("kein-datum") is None
    assert api._alter_in_tagen(_vor_tagen(5)) == 5
