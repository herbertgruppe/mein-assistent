"""
Tests fuer den Nur-Ablegen-Modus der Mail-Triage (2026-10-27).

Anlass: Sven musste bei der Bearbeitung der Mails jedes Mal erst im
Weiterleiten-Stapel nachsehen, ob dort etwas liegt, das eigentlich anders
behandelt werden muss — bevor er antworten oder ablegen konnte. Das Etikett
kostete mehr Zeit, als es sparte. Nur "Ablegen" bleibt scharf; alles andere
bleibt unberuehrt im Posteingang, bis ein neuer Arbeitsablauf steht.

Zu sichern ist vor allem, dass NICHTS durchrutscht: weder eine Kategorie noch
ein Entwurf noch eine Asana-Aufgabe, auf keinem der vier Wege (neue Mail,
Korrektur, von Hand gesetzte Kategorie, rueckwirkende Regel).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
poller = pytest.importorskip("lena_mail_triage_poller")


MAIL = {"message_id": "m1", "conversation_id": "", "subject": "Frage zum Angebot",
        "sender_name": "Erika Kundin", "sender_email": "kundin@extern.de",
        "body_preview": "Koennen Sie bestaetigen?", "received_at": "2026-10-27T07:00:00Z",
        "to_emails": [], "cc_emails": []}


@pytest.fixture
def modus(monkeypatch):
    def setzen(an: bool):
        monkeypatch.setattr(poller, "NUR_ABLEGEN", an)
        monkeypatch.setattr(poller, "AKTIONEN_AKTIV", True)
    return setzen


# ── Die Entscheidungsfunktion ─────────────────────────────────────────────

@pytest.mark.parametrize("aktion,erwartet", [
    ("ablegen", False),
    ("weiterleiten", True),
    ("tun", True),
    ("antworten", True),
    ("warten", True),
    ("recherchieren", True),
])
def test_nur_ablegen_laesst_nur_ablegen_durch(modus, aktion, erwartet):
    modus(True)
    assert poller.nur_ablegen_ueberspringt(aktion) is erwartet


@pytest.mark.parametrize("aktion", ["ablegen", "weiterleiten", "tun", "antworten"])
def test_ohne_modus_wird_nichts_uebersprungen(modus, aktion):
    modus(False)
    assert poller.nur_ablegen_ueberspringt(aktion) is False


def test_standard_ist_aus():
    """Ein vergessener Schalter darf das Verhalten nicht still aendern."""
    quelle = open(poller.__file__, encoding="utf-8").read()
    assert 'os.getenv("LENA_MAIL_TRIAGE_NUR_ABLEGEN", "0")' in quelle


# ── Keine Konsequenzen: Entwurf, Aufgabe ──────────────────────────────────

@pytest.mark.parametrize("schritt", [3, 4, 5])
def test_keine_konsequenz_im_nur_ablegen_modus(modus, monkeypatch, schritt):
    modus(True)
    angefasst = []
    for name in ("_entwurf_anlegen", "_asana_aufgabe", "_llm_entwurf", "_llm_aufgabe"):
        monkeypatch.setattr(poller, name, lambda *a, _n=name, **k: angefasst.append(_n) or "x")
    r = poller.konsequenz_ausfuehren(MAIL, schritt, empfaenger="Frank Herbert", state={})
    assert r["art"] is None
    assert not angefasst, f"Darf nichts ausloesen, hat aber {angefasst} gerufen"
    assert "Nur-Ablegen" in (r["hinweis"] or ""), "Die Begruendung gehoert ins Protokoll"


def test_ohne_modus_gilt_die_konsequenz_weiter(modus, monkeypatch):
    """Gegenprobe: Die Sperre darf nicht schlicht immer greifen."""
    modus(False)
    monkeypatch.setattr(poller, "_llm_aufgabe", lambda *a, **k: ("Titel", None, "Kontext"))
    monkeypatch.setattr(poller, "_asana_aufgabe", lambda *a, **k: "gid-1")
    monkeypatch.setattr(poller, "_volltext", lambda *a, **k: "Text")
    monkeypatch.setattr(poller, "_mail_archivieren", lambda mid: True)
    monkeypatch.setattr(poller, "_rueckfrage", lambda *a, **k: True)
    r = poller.konsequenz_ausfuehren(MAIL, 4, state={})
    assert r["art"] == "asana"


def test_von_hand_gesetzte_kategorie_loest_nichts_aus(modus, monkeypatch):
    """Der Weg ueber _anweisung_ausfuehren: Sven setzt selbst 'Lena: Tun'."""
    modus(True)
    angelegt = []
    monkeypatch.setattr(poller, "_asana_aufgabe", lambda *a, **k: angelegt.append(1) or "g")
    monkeypatch.setattr(poller, "ANWEISUNGEN_AKTIV", True)
    m = dict(MAIL, from_name="E", from_email="kundin@extern.de",
             received_at=poller.datetime.now(poller.timezone.utc).isoformat(),
             categories=["Lena: Tun"])
    poller._anweisung_ausfuehren(m, "m1", "Lena: Tun", {}, {}, {}, {})
    assert not angelegt


# ── Rueckwirkende Regeln ──────────────────────────────────────────────────

def test_rueckwirkende_regel_mit_anderer_aktion_ruht(modus, monkeypatch, tmp_path):
    """Eine Regel 'Anthropic-Belege -> weiterleiten' haette sonst bei jeder
    Regelaenderung Kategorien auf vorhandene Mails gesetzt."""
    modus(True)
    regeln = tmp_path / "r.json"
    regeln.write_text('{"regeln": []}', encoding="utf-8")
    monkeypatch.setattr(poller, "LAUFZEIT_REGELN", str(regeln))
    state = {"regeln_stand": -1.0}             # erzwingt die Pruefung

    class Antwort:
        status_code = 200
        @staticmethod
        def json():
            return {"messages": [{"message_id": "x", "from_email": "beleg@anthropic.com",
                                  "subject": "Rechnung", "categories": []}]}
    monkeypatch.setattr(poller.requests, "get", lambda *a, **k: Antwort())
    monkeypatch.setattr(poller, "match_regel",
                        lambda *a, **k: {"aktion": "weiterleiten", "name": "beleg",
                                         "empfaenger": "Laura"})
    gesetzt = []
    monkeypatch.setattr(poller, "_categorize_mail", lambda *a, **k: gesetzt.append(a) or True)
    z = poller.regeln_rueckwirkend(state)
    assert not gesetzt
    assert z["angewendet"] == 0


def test_rueckwirkende_ablegen_regel_gilt_weiter(modus, monkeypatch, tmp_path):
    modus(True)
    regeln = tmp_path / "r.json"
    regeln.write_text('{"regeln": []}', encoding="utf-8")
    monkeypatch.setattr(poller, "LAUFZEIT_REGELN", str(regeln))
    state = {"regeln_stand": -1.0}

    class Antwort:
        status_code = 200
        @staticmethod
        def json():
            return {"messages": [{"message_id": "x", "from_email": "dmarc@x.de",
                                  "subject": "DMARC", "categories": []}]}
    monkeypatch.setattr(poller.requests, "get", lambda *a, **k: Antwort())
    monkeypatch.setattr(poller, "match_regel",
                        lambda *a, **k: {"aktion": "ablegen", "name": "dmarc", "empfaenger": None})
    gesetzt = []
    monkeypatch.setattr(poller, "_categorize_mail", lambda *a, **k: gesetzt.append(a) or True)
    z = poller.regeln_rueckwirkend(state)
    assert z["angewendet"] == 1


# ── Der Zyklus selbst ─────────────────────────────────────────────────────

@pytest.fixture
def zyklus(monkeypatch):
    """Ein Durchlauf von _poll_once mit abgeklemmter Aussenwelt."""
    kategorisiert, wichtigkeit = [], []
    monkeypatch.setattr(poller, "DRY_RUN", False)
    monkeypatch.setattr(poller, "RETRIAGE_ALL", False)
    monkeypatch.setattr(poller, "THREAD_GROUPING", False)
    monkeypatch.setattr(poller, "_categorize_mail",
                        lambda mid, aktion, skip_archive=False: kategorisiert.append((mid, aktion)) or True)
    monkeypatch.setattr(poller, "_set_importance", lambda mid, p: wichtigkeit.append(mid))
    monkeypatch.setattr(poller, "regeln_rueckwirkend", lambda s: {"geprueft": 0, "angewendet": 0})
    monkeypatch.setattr(poller, "korrekturen_verarbeiten",
                        lambda s: {"korrekturen": 0, "konsequenzen": 0, "gelernt": 0})
    monkeypatch.setattr(poller, "_save_state", lambda s: None)
    monkeypatch.setattr(poller, "_get_learning_db", lambda: None)
    monkeypatch.setattr(poller, "_record_categorization", lambda *a, **k: None)
    monkeypatch.setattr(poller, "_tg_alert", lambda *a, **k: None)
    monkeypatch.setattr(poller, "may_auto_archive", lambda *a, **k: True)
    monkeypatch.setattr(poller, "sender_verdict", lambda *a, **k: "neutral")
    monkeypatch.setattr(poller.time, "sleep", lambda s: None)

    def stellen(mails, entscheidung):
        monkeypatch.setattr(poller, "_fetch_inbox_for_triage", lambda: mails)
        monkeypatch.setattr(poller, "triage_mail", lambda *a, **k: entscheidung)
    return stellen, kategorisiert, wichtigkeit


def _entscheidung(aktion, schritt):
    return (aktion, "mittel", "llm:test", None, schritt, None)


def test_zyklus_ablegen_wird_kategorisiert(monkeypatch, zyklus):
    stellen, kategorisiert, _ = zyklus
    monkeypatch.setattr(poller, "NUR_ABLEGEN", True)
    stellen([dict(MAIL, message_id="a1")], _entscheidung("ablegen", 2))
    state = {}
    z = poller._poll_once(state)
    assert kategorisiert == [("a1", "ablegen")]
    assert z["categorized"] == 1
    assert z["nicht_ablegen_uebersprungen"] == 0


@pytest.mark.parametrize("aktion,schritt", [("weiterleiten", 3), ("tun", 4), ("antworten", 5)])
def test_zyklus_rest_bleibt_unberuehrt(monkeypatch, zyklus, aktion, schritt):
    stellen, kategorisiert, wichtigkeit = zyklus
    monkeypatch.setattr(poller, "NUR_ABLEGEN", True)
    stellen([dict(MAIL, message_id="x1")], _entscheidung(aktion, schritt))
    state = {}
    z = poller._poll_once(state)
    assert not kategorisiert, "Keine Kategorie auf Mails, die nicht abgelegt werden"
    assert not wichtigkeit, "Auch die Wichtigkeit bleibt, wie sie eingegangen ist"
    assert z["categorized"] == 0
    assert z["nicht_ablegen_uebersprungen"] == 1


def test_zyklus_uebersprungene_mail_gilt_als_gesehen(monkeypatch, zyklus):
    """Sonst bezahlt jeder Zyklus dieselbe Sprachmodell-Bewertung erneut."""
    stellen, _, _ = zyklus
    monkeypatch.setattr(poller, "NUR_ABLEGEN", True)
    stellen([dict(MAIL, message_id="x1")], _entscheidung("antworten", 5))
    state = {}
    poller._poll_once(state)
    assert "x1" in state["processed_message_ids"]


def test_zyklus_uebersprungene_mail_erzeugt_keine_phantomentscheidung(monkeypatch, zyklus):
    """Sonst wuerde die Korrektur-Erkennung spaeter gegen eine Entscheidung
    vergleichen, die nie umgesetzt wurde."""
    stellen, _, _ = zyklus
    monkeypatch.setattr(poller, "NUR_ABLEGEN", True)
    stellen([dict(MAIL, message_id="x1")], _entscheidung("tun", 4))
    state = {}
    poller._poll_once(state)
    assert "x1" not in (state.get("triage_results") or {})


def test_zyklus_ohne_modus_kategorisiert_alles(monkeypatch, zyklus):
    stellen, kategorisiert, _ = zyklus
    monkeypatch.setattr(poller, "NUR_ABLEGEN", False)
    monkeypatch.setattr(poller, "AKTIONEN_AKTIV", False)
    stellen([dict(MAIL, message_id="x1")], _entscheidung("antworten", 5))
    poller._poll_once({})
    assert kategorisiert == [("x1", "antworten")]


def test_zyklus_gemischter_posteingang(monkeypatch, zyklus):
    """Drei Mails, nur eine wird abgelegt."""
    stellen, kategorisiert, _ = zyklus
    monkeypatch.setattr(poller, "NUR_ABLEGEN", True)
    mails = [dict(MAIL, message_id=i) for i in ("a", "b", "c")]
    antworten = iter([_entscheidung("ablegen", 2), _entscheidung("tun", 4),
                      _entscheidung("weiterleiten", 3)])
    monkeypatch.setattr(poller, "_fetch_inbox_for_triage", lambda: mails)
    monkeypatch.setattr(poller, "triage_mail", lambda *a, **k: next(antworten))
    z = poller._poll_once({})
    assert kategorisiert == [("a", "ablegen")]
    assert z["nicht_ablegen_uebersprungen"] == 2
