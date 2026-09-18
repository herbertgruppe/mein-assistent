"""
Tests fuer Kategorien als Anweisung (HBE-3118).

Der Fall aus dem Betrieb vom 18.09.2026: Sven setzte eine Mail auf "Lena: Tun",
es entstand keine Asana-Aufgabe. Die Kategorie war richtig — die
Korrektur-Erkennung uebergeht sie aber, weil keine eigene Entscheidung zum
Vergleich vorliegt.

Drei Wege fuehren dorthin:
  * Sven kategorisiert schneller als der 10-Minuten-Takt
  * die Entscheidung ist aus dem 500er-Cache gefallen
  * die Mail wurde verschoben und hat dabei eine neue message_id bekommen
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
poller = pytest.importorskip("lena_mail_triage_poller")


def _vor(tagen: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=tagen)).isoformat().replace("+00:00", "Z")


def _mail(kategorie="Lena: Tun", tage_alt=0, mid="neu-1"):
    return {
        "message_id": mid,
        "subject": "Saldenliste Controlling Dashboard",
        "from_name": "Dominik Prestel",
        "from_email": "d.prestel@herbert.de",
        "body_preview": "Koennten Sie mir die Anforderungen nennen?",
        "received_at": _vor(tage_alt),
        "categories": [kategorie],
    }


@pytest.fixture
def aktiv(monkeypatch):
    monkeypatch.setattr(poller, "KORREKTUREN_AKTIV", True)
    monkeypatch.setattr(poller, "ANWEISUNGEN_AKTIV", True)
    monkeypatch.setattr(poller, "ANWEISUNG_MAX_ALTER_TAGE", 7)


@pytest.fixture
def ausgefuehrt(monkeypatch):
    """Faengt die Konsequenz ab, statt Asana oder Outlook anzufassen."""
    protokoll = []

    def fake(mail, schritt, empfaenger=None, state=None):
        protokoll.append({"schritt": schritt, "mail": mail, "empfaenger": empfaenger})
        return {"art": "asana", "id": "gid-1", "stufe": None, "hinweis": None}
    monkeypatch.setattr(poller, "konsequenz_ausfuehren", fake)
    return protokoll


# ── Der Kern: Kategorie ohne eigene Entscheidung wirkt ────────────────────

def test_kategorie_ohne_eigene_entscheidung_wird_ausgefuehrt(aktiv, ausgefuehrt):
    z = {"geprueft": 0, "korrekturen": 0, "konsequenzen": 0, "gelernt": 0,
         "anweisungen": 0, "anweisungen_uebergangen": 0}
    erledigt, eigene, state = {}, {}, {}
    poller._anweisung_ausfuehren(_mail(), "neu-1", "Lena: Tun", state, erledigt, eigene, z)

    assert z["anweisungen"] == 1
    assert z["konsequenzen"] == 1
    assert len(ausgefuehrt) == 1
    assert ausgefuehrt[0]["schritt"] == 4, "'Tun' ist Schritt 4 — Asana"
    assert erledigt["neu-1"] == "tun"
    assert eigene["neu-1"]["action"] == "tun", "Damit gilt sie kuenftig als eigene Entscheidung"


def test_zweiter_durchlauf_wiederholt_nichts(aktiv, ausgefuehrt):
    z = {"konsequenzen": 0}
    erledigt, eigene, state = {}, {}, {}
    for _ in range(3):
        poller._anweisung_ausfuehren(_mail(), "neu-1", "Lena: Tun", state, erledigt, eigene, z)
    assert len(ausgefuehrt) == 1, "Jeder Zyklus wuerde sonst eine neue Aufgabe anlegen"


def test_ohne_konsequenz_trotzdem_vermerkt(aktiv, monkeypatch):
    """Sonst laeuft der Fall alle zehn Minuten neu — samt LLM-Aufruf."""
    monkeypatch.setattr(poller, "konsequenz_ausfuehren",
                        lambda *a, **k: {"art": None, "hinweis": "nichts zu tun"})
    erledigt, eigene, z = {}, {}, {}
    poller._anweisung_ausfuehren(_mail(), "neu-1", "Lena: Tun", {}, erledigt, eigene, z)
    assert erledigt.get("neu-1") == "tun"


# ── Altbestand wird nicht rueckwirkend abgearbeitet ───────────────────────

def test_alte_mail_wird_nur_gemeldet(aktiv, ausgefuehrt):
    z = {}
    state, erledigt, eigene = {}, {}, {}
    poller._anweisung_ausfuehren(_mail(tage_alt=40), "alt-1", "Lena: Tun",
                                 state, erledigt, eigene, z)
    assert not ausgefuehrt, "Altbestand darf keinen Schwall Aufgaben ausloesen"
    assert z.get("anweisungen_uebergangen") == 1
    assert "alt-1" in state["anweisungen_uebergangen"]
    assert "alt-1" not in erledigt, "Nicht als erledigt markieren — sonst nie nachholbar"


def test_alte_mail_wird_nur_einmal_gemeldet(aktiv, ausgefuehrt):
    z = {}
    state, erledigt, eigene = {}, {}, {}
    for _ in range(4):
        poller._anweisung_ausfuehren(_mail(tage_alt=40), "alt-1", "Lena: Tun",
                                     state, erledigt, eigene, z)
    assert z.get("anweisungen_uebergangen") == 1, "Sonst spammt jeder Zyklus das Log"


def test_mail_ohne_datum_gilt_als_alt(aktiv, ausgefuehrt):
    m = _mail()
    m["received_at"] = ""
    poller._anweisung_ausfuehren(m, "x", "Lena: Tun", {}, {}, {}, {})
    assert not ausgefuehrt, "Ohne Datum im Zweifel nicht ausfuehren"


def test_unlesbares_datum_gilt_als_alt(aktiv, ausgefuehrt):
    m = _mail()
    m["received_at"] = "gestern irgendwann"
    poller._anweisung_ausfuehren(m, "x", "Lena: Tun", {}, {}, {}, {})
    assert not ausgefuehrt


@pytest.mark.parametrize("tage,erwartet", [(0, True), (7, True), (8, False)])
def test_altersgrenze(aktiv, ausgefuehrt, tage, erwartet):
    poller._anweisung_ausfuehren(_mail(tage_alt=tage), f"m{tage}", "Lena: Tun",
                                 {}, {}, {}, {})
    assert bool(ausgefuehrt) is erwartet


# ── Abschaltbar ───────────────────────────────────────────────────────────

def test_schalter_aus_bewirkt_nichts(monkeypatch, ausgefuehrt):
    monkeypatch.setattr(poller, "ANWEISUNGEN_AKTIV", False)
    erledigt = {}
    poller._anweisung_ausfuehren(_mail(), "neu-1", "Lena: Tun", {}, erledigt, {}, {})
    assert not ausgefuehrt
    assert not erledigt


# ── Alle Kategorien landen beim richtigen Schritt ─────────────────────────

@pytest.mark.parametrize("kategorie,schritt", [
    ("Lena: Ablegen", 2),
    ("Lena: Weiterleiten", 3),
    ("Lena: Tun", 4),
    ("Lena: Antworten", 5),
])
def test_kategorie_trifft_den_richtigen_schritt(aktiv, ausgefuehrt, monkeypatch,
                                                kategorie, schritt):
    monkeypatch.setattr(poller, "triage_mail",
                        lambda *a, **k: ("weiterleiten", "mittel", "llm:x", None, 3, "Laura"))
    poller._anweisung_ausfuehren(_mail(kategorie=kategorie), "m1", kategorie,
                                 {}, {}, {}, {})
    assert ausgefuehrt[0]["schritt"] == schritt


def test_weiterleiten_bestimmt_den_empfaenger(aktiv, ausgefuehrt, monkeypatch):
    monkeypatch.setattr(poller, "triage_mail",
                        lambda *a, **k: ("weiterleiten", "mittel", "llm:x", None, 3, "Laura"))
    poller._anweisung_ausfuehren(_mail(kategorie="Lena: Weiterleiten"), "m1",
                                 "Lena: Weiterleiten", {}, {}, {}, {})
    assert ausgefuehrt[0]["empfaenger"] == "Laura"


def test_fehler_bei_empfaengersuche_bricht_nicht_ab(aktiv, ausgefuehrt, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("LLM weg")
    monkeypatch.setattr(poller, "triage_mail", boom)
    poller._anweisung_ausfuehren(_mail(kategorie="Lena: Weiterleiten"), "m1",
                                 "Lena: Weiterleiten", {}, {}, {}, {})
    assert len(ausgefuehrt) == 1
    assert ausgefuehrt[0]["empfaenger"] is None, "Ohne Empfaenger folgt die Rueckfrage"


# ── Keine erfundenen Lernsignale ──────────────────────────────────────────

def test_anweisung_erzeugt_kein_lernsignal(aktiv, ausgefuehrt, monkeypatch):
    """Es gibt keine eigene Entscheidung, der die Kategorie widersprechen koennte.
    Ein Override waere eine erfundene Gegenueberstellung."""
    class DB:
        def __init__(self):
            self.aufrufe = []
        def record_override(self, **kw):
            self.aufrufe.append(kw)
    db = DB()
    monkeypatch.setattr(poller, "_get_learning_db", lambda: db)
    poller._anweisung_ausfuehren(_mail(), "neu-1", "Lena: Tun", {}, {}, {}, {})
    assert not db.aufrufe


# ── Die Datums-Hilfsfunktion ──────────────────────────────────────────────

def test_ist_zu_alt_grenzwerte(monkeypatch):
    monkeypatch.setattr(poller, "ANWEISUNG_MAX_ALTER_TAGE", 7)
    assert poller._ist_zu_alt_fuer_anweisung(_vor(0)) is False
    assert poller._ist_zu_alt_fuer_anweisung(_vor(7)) is False
    assert poller._ist_zu_alt_fuer_anweisung(_vor(9)) is True
    assert poller._ist_zu_alt_fuer_anweisung("") is True
