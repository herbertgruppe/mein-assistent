"""
Tests fuer Vorgangs-Dedup, Asana-Dubletten und Svens Korrekturen (HBE-3052).

Alle drei Defekte wurden am 15.09.2026 im Echtbetrieb gefunden:
  * Zwei Mails eines Vorgangs erzeugten zwei Asana-Aufgaben.
  * Eine bereits vorhandene Aufgabe wurde ein zweites Mal angelegt.
  * Eine in Outlook geaenderte Kategorie blieb folgenlos.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
poller = pytest.importorskip("lena_mail_triage_poller")


def _mail(mid="m1", conv="c1", subject="Betreff", sender="a@b.de"):
    return {"message_id": mid, "conversation_id": conv, "subject": subject,
            "sender_name": "Name", "sender_email": sender,
            "body_preview": "Text", "received_at": "2026-09-15T08:00:00Z"}


@pytest.fixture
def aktiv(monkeypatch):
    monkeypatch.setattr(poller, "AKTIONEN_AKTIV", True)
    monkeypatch.setattr(poller, "_PERSONA_CONFIG", poller._load_persona_config())
    monkeypatch.setattr(poller, "_SYSTEM_SENDERS", None)


# ── Konsequenz pro Vorgang ────────────────────────────────────────────────

def test_zweite_mail_desselben_vorgangs_erzeugt_nichts(aktiv, monkeypatch):
    """Der Fall vom 15.09.: zwei Open-Master-Data-Mails, zwei Aufgaben."""
    angelegt = []
    monkeypatch.setattr(poller, "_asana_aufgabe",
                        lambda *a, **k: angelegt.append(1) or f"gid-{len(angelegt)}")
    state = {}

    r1 = poller.konsequenz_ausfuehren(_mail("m1", "vorgang-A"), 4, state=state)
    r2 = poller.konsequenz_ausfuehren(_mail("m2", "vorgang-A"), 4, state=state)

    assert r1["art"] == "asana"
    assert r2["art"] is None
    assert "bereits eine Konsequenz" in (r2["hinweis"] or "")
    assert len(angelegt) == 1, "Ein Vorgang, eine Aufgabe"


def test_anderer_vorgang_wird_normal_behandelt(aktiv, monkeypatch):
    angelegt = []
    monkeypatch.setattr(poller, "_asana_aufgabe",
                        lambda *a, **k: angelegt.append(1) or f"gid-{len(angelegt)}")
    state = {}
    poller.konsequenz_ausfuehren(_mail("m1", "vorgang-A"), 4, state=state)
    r = poller.konsequenz_ausfuehren(_mail("m2", "vorgang-B"), 4, state=state)
    assert r["art"] == "asana"
    assert len(angelegt) == 2


def test_ohne_state_kein_vorgangs_dedup(aktiv, monkeypatch):
    """Rueckwaertskompatibel — Aufrufer ohne state verhalten sich wie bisher."""
    monkeypatch.setattr(poller, "_asana_aufgabe", lambda *a, **k: "gid")
    assert poller.konsequenz_ausfuehren(_mail(), 4)["art"] == "asana"


def test_gescheiterte_konsequenz_sperrt_den_vorgang_nicht(aktiv, monkeypatch):
    """Wenn nichts entstand, darf der naechste Versuch nicht blockiert sein."""
    monkeypatch.setattr(poller, "_asana_aufgabe", lambda *a, **k: None)
    state = {}
    r1 = poller.konsequenz_ausfuehren(_mail("m1", "vorgang-C"), 4, state=state)
    assert r1["art"] is None
    monkeypatch.setattr(poller, "_asana_aufgabe", lambda *a, **k: "gid-spaeter")
    r2 = poller.konsequenz_ausfuehren(_mail("m2", "vorgang-C"), 4, state=state)
    assert r2["art"] == "asana"


def test_merker_waechst_nicht_unbegrenzt():
    state = {}
    for i in range(poller.MAX_VORGANGS_MERKER + 60):
        poller.vorgang_merken(state, f"c{i}", {"art": "asana", "id": i})
    assert len(state["konsequenz_vorgaenge"]) <= poller.MAX_VORGANGS_MERKER


# ── Asana-Dublettenpruefung ───────────────────────────────────────────────

@pytest.mark.parametrize("a,b", [
    ("Rueckfrage zum Angebot", "AW: Rueckfrage zum Angebot"),
    ("Projekt X", "WG: Projekt X"),
    ("Thema", "Re: Thema"),
    ("Budget  2027", "budget 2027"),
])
def test_titel_normalisierung_erkennt_gleiches(a, b):
    assert poller._titel_normalisieren(a) == poller._titel_normalisieren(b)


def test_titel_normalisierung_unterscheidet_verschiedenes():
    assert poller._titel_normalisieren("Angebot A") != poller._titel_normalisieren("Angebot B")


def test_vorhandene_aufgabe_wird_nicht_doppelt_angelegt(monkeypatch):
    """Der Fall vom 15.09.: die gestern angelegte Aufgabe wurde dupliziert."""
    monkeypatch.setattr(poller, "ASANA_TOKEN", "token")
    monkeypatch.setattr(poller, "_asana_offene_titel",
                        lambda *a, **k: {poller._titel_normalisieren("Open Master Data")})
    gerufen = []
    monkeypatch.setattr(poller.requests, "post",
                        lambda *a, **k: gerufen.append(1))
    assert poller._asana_aufgabe("AW: Open Master Data", "N", "n@x.de", "T", "2026-09-15") is None
    assert not gerufen, "Es darf kein Anlage-Aufruf erfolgen"


# ── Svens Korrekturen ─────────────────────────────────────────────────────

def test_kategorie_abbildung_ist_umkehrbar():
    for aktion, kat in poller.AKTION_ZU_KATEGORIE.items():
        assert poller.KATEGORIE_ZU_AKTION[kat] == aktion


def test_lena_kategorie_wird_erkannt():
    assert poller._lena_kategorie(["Rot", "Lena: Antworten"]) == "Lena: Antworten"
    assert poller._lena_kategorie(["Priorität: Hoch"]) is None
    assert poller._lena_kategorie([]) is None


def _inbox(monkeypatch, messages):
    class R:
        status_code = 200
        @staticmethod
        def json():
            return {"messages": messages}
    monkeypatch.setattr(poller.requests, "get", lambda *a, **k: R())


def test_unveraenderte_kategorie_ist_keine_korrektur(monkeypatch):
    monkeypatch.setattr(poller, "KORREKTUREN_AKTIV", True)
    monkeypatch.setattr(poller, "_get_learning_db", lambda: None)
    _inbox(monkeypatch, [{"message_id": "m1", "subject": "S", "from_email": "a@b.de",
                          "categories": ["Lena: Ablegen"]}])
    state = {"triage_results": {"m1": {"action": "ablegen", "priority": "niedrig"}}}
    z = poller.korrekturen_verarbeiten(state)
    assert z["korrekturen"] == 0


def test_geaenderte_kategorie_wird_als_korrektur_erkannt(monkeypatch):
    monkeypatch.setattr(poller, "KORREKTUREN_AKTIV", True)
    monkeypatch.setattr(poller, "AKTIONEN_AKTIV", True)
    monkeypatch.setattr(poller, "_get_learning_db", lambda: None)
    ausgefuehrt = []
    monkeypatch.setattr(poller, "konsequenz_ausfuehren",
                        lambda *a, **k: ausgefuehrt.append(a[1]) or {"art": "antwort"})
    _inbox(monkeypatch, [{"message_id": "m1", "subject": "S", "from_email": "a@b.de",
                          "from_name": "N", "body_preview": "T",
                          "categories": ["Lena: Antworten"]}])
    state = {"triage_results": {"m1": {"action": "ablegen", "priority": "niedrig"}}}
    z = poller.korrekturen_verarbeiten(state)
    assert z["korrekturen"] == 1
    assert z["konsequenzen"] == 1
    assert ausgefuehrt == [5], "Antworten entspricht Schritt 5"


def test_korrektur_wird_nur_einmal_verarbeitet(monkeypatch):
    monkeypatch.setattr(poller, "KORREKTUREN_AKTIV", True)
    monkeypatch.setattr(poller, "AKTIONEN_AKTIV", True)
    monkeypatch.setattr(poller, "_get_learning_db", lambda: None)
    monkeypatch.setattr(poller, "konsequenz_ausfuehren", lambda *a, **k: {"art": "antwort"})
    _inbox(monkeypatch, [{"message_id": "m1", "subject": "S", "from_email": "a@b.de",
                          "from_name": "N", "body_preview": "T",
                          "categories": ["Lena: Antworten"]}])
    state = {"triage_results": {"m1": {"action": "ablegen", "priority": "niedrig"}}}
    assert poller.korrekturen_verarbeiten(state)["korrekturen"] == 1
    assert poller.korrekturen_verarbeiten(state)["korrekturen"] == 0, "kein zweites Mal"


def test_mail_ohne_eigene_entscheidung_wird_ignoriert(monkeypatch):
    """Was Lena nie kategorisiert hat, kann auch keine Korrektur sein."""
    monkeypatch.setattr(poller, "KORREKTUREN_AKTIV", True)
    monkeypatch.setattr(poller, "_get_learning_db", lambda: None)
    _inbox(monkeypatch, [{"message_id": "fremd", "subject": "S", "from_email": "a@b.de",
                          "categories": ["Lena: Tun"]}])
    z = poller.korrekturen_verarbeiten({"triage_results": {}})
    assert z["korrekturen"] == 0 and z["geprueft"] == 0


def test_korrektur_wird_gelernt(monkeypatch):
    monkeypatch.setattr(poller, "KORREKTUREN_AKTIV", True)
    monkeypatch.setattr(poller, "AKTIONEN_AKTIV", False)
    erfasst = {}

    class DB:
        @staticmethod
        def record_override(**kw):
            erfasst.update(kw)
            return True
    monkeypatch.setattr(poller, "_get_learning_db", lambda: DB())
    _inbox(monkeypatch, [{"message_id": "m1", "subject": "AW: Budget",
                          "from_email": "kai@herbert.de", "from_name": "Kai",
                          "categories": ["Lena: Tun"]}])
    state = {"triage_results": {"m1": {"action": "ablegen", "priority": "niedrig"}}}
    z = poller.korrekturen_verarbeiten(state)
    assert z["gelernt"] == 1
    assert erfasst["original_action"] == "ablegen"
    assert erfasst["override_action"] == "tun"
    assert erfasst["sender_domain"] == "herbert.de"


def test_abgeschaltet_passiert_nichts(monkeypatch):
    monkeypatch.setattr(poller, "KORREKTUREN_AKTIV", False)
    assert poller.korrekturen_verarbeiten({})["korrekturen"] == 0
