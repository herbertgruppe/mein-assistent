"""
Tests fuer Rueckfragen, Aufgaben-Workflow und rueckwirkende Regeln (HBE-3061).

Alle Faelle stammen aus dem Betrieb vom 15./16.09.2026:
  * Eine Korrektur auf "Antworten" erzeugte keinen Entwurf — und Sven erfuhr
    nicht warum.
  * Drei Mail-Aufgaben landeten in der Section "🔴 Heute", weil Asana Aufgaben
    ohne Section in die erste einsortiert.
  * Der Weiterleitungstext lautete "Hallo, kannst du das bitte uebernehmen?" —
    ohne Anrede, ohne Kontext, ohne Umlaute.
  * Zwei Mails, auf die neue Regeln gepasst haetten, blieben unberuehrt.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
poller = pytest.importorskip("lena_mail_triage_poller")


MAIL = {"message_id": "m1", "conversation_id": "", "subject": "Zahlungserinnerung 7193",
        "sender_name": "Valerija Kozubskaja", "sender_email": "v@coglas.de",
        "body_preview": "Sehr geehrter Herr Herbert, anbei die Zahlungserinnerung.",
        "received_at": "2026-09-16T07:00:00Z"}


@pytest.fixture
def aktiv(monkeypatch):
    monkeypatch.setattr(poller, "AKTIONEN_AKTIV", True)
    monkeypatch.setattr(poller, "RUECKFRAGEN_AKTIV", True)
    monkeypatch.setattr(poller, "TG_ADMIN_CHAT", "12345")
    monkeypatch.setattr(poller, "_PERSONA_CONFIG", poller._load_persona_config())
    monkeypatch.setattr(poller, "_SYSTEM_SENDERS", None)


@pytest.fixture
def gesendet(monkeypatch):
    """Faengt Rueckfragen ab."""
    raus = []
    monkeypatch.setattr(poller, "_rueckfrage",
                        lambda text, state: raus.append(text) or True)
    return raus


# ── Rueckfrage bei unklarem Empfaenger ────────────────────────────────────

def test_unklarer_empfaenger_loest_rueckfrage_aus(aktiv, gesendet, monkeypatch):
    monkeypatch.setattr(poller, "_entwurf_anlegen", lambda *a, **k: "darf-nicht")
    r = poller.konsequenz_ausfuehren(MAIL, 3, empfaenger="Erika Mustermann", state={})
    assert r["art"] is None
    assert len(gesendet) == 1
    assert "An wen weiterleiten" in gesendet[0]
    assert "Erika Mustermann" in gesendet[0]
    assert "andere Kategorie" in gesendet[0], "Die Alternative muss angeboten werden"


def test_aufloesbarer_empfaenger_ohne_rueckfrage(aktiv, gesendet, monkeypatch):
    monkeypatch.setattr(poller, "_entwurf_anlegen", lambda *a, **k: "draft-1")
    monkeypatch.setattr(poller, "_weiterleitungstext", lambda *a, **k: "Text")
    r = poller.konsequenz_ausfuehren(MAIL, 3, empfaenger="Frank Herbert", state={})
    assert r["art"] == "weiterleitung"
    assert not gesendet


# ── Rueckfrage, wenn kein Entwurf moeglich ────────────────────────────────

def test_kein_entwurf_loest_rueckfrage_mit_optionen_aus(aktiv, gesendet, monkeypatch):
    """Der NetJets-Fall: Verweigerung ist richtig, Schweigen nicht."""
    frage = ("Theodora Evangelaki von NetJets fragt, ob Geschaeftsreisen ein Thema "
             "sind. Wie antworten — Interesse, ablehnen, oder kein Thema?")
    monkeypatch.setattr(poller, "_llm_entwurf", lambda *a, **k: ("nein", "", frage))
    r = poller.konsequenz_ausfuehren(MAIL, 5, state={})
    assert r["art"] is None
    assert r["stufe"] == "nein"
    assert len(gesendet) == 1
    assert "Wie soll ich antworten" in gesendet[0]
    assert "Interesse" in gesendet[0], "Die Optionen des Modells muessen mit"


def test_gelungener_entwurf_ohne_rueckfrage(aktiv, gesendet, monkeypatch):
    monkeypatch.setattr(poller, "_llm_entwurf", lambda *a, **k: ("voll", "Guten Tag,\n\npasst.", ""))
    monkeypatch.setattr(poller, "_entwurf_anlegen", lambda *a, **k: "draft-2")
    r = poller.konsequenz_ausfuehren(MAIL, 5, state={})
    assert r["art"] == "antwort"
    assert not gesendet


# ── Aufgabe: Mail archivieren und melden ──────────────────────────────────

def test_aufgabe_archiviert_die_mail_und_meldet(aktiv, gesendet, monkeypatch):
    monkeypatch.setattr(poller, "TUN_MAIL_ARCHIVIEREN", True)
    monkeypatch.setattr(poller, "_llm_aufgabe",
                        lambda *a, **k: ("Lageplan pruefen und zurueckmelden", None, "Kontext"))
    monkeypatch.setattr(poller, "_asana_aufgabe", lambda *a, **k: "gid-1")
    monkeypatch.setattr(poller, "_volltext", lambda *a, **k: "Volltext")
    archiviert = []
    monkeypatch.setattr(poller, "_mail_archivieren", lambda mid: archiviert.append(mid) or True)

    r = poller.konsequenz_ausfuehren(MAIL, 4, state={})
    assert r["art"] == "asana"
    assert archiviert == ["m1"]
    assert "archiviert" in (r["hinweis"] or "")
    assert len(gesendet) == 1
    assert "Aufgabe angelegt" in gesendet[0]
    assert "Lageplan pruefen" in gesendet[0]
    assert "abgelegt" in gesendet[0]


def test_ohne_archivierung_bleibt_die_mail(aktiv, gesendet, monkeypatch):
    monkeypatch.setattr(poller, "TUN_MAIL_ARCHIVIEREN", False)
    monkeypatch.setattr(poller, "_llm_aufgabe", lambda *a, **k: ("Titel", None, ""))
    monkeypatch.setattr(poller, "_asana_aufgabe", lambda *a, **k: "gid-1")
    monkeypatch.setattr(poller, "_volltext", lambda *a, **k: "x")
    archiviert = []
    monkeypatch.setattr(poller, "_mail_archivieren", lambda mid: archiviert.append(mid) or True)
    r = poller.konsequenz_ausfuehren(MAIL, 4, state={})
    assert not archiviert
    assert "bleibt liegen" in (r["hinweis"] or "")


def test_ohne_aufgabe_wird_nichts_archiviert(aktiv, gesendet, monkeypatch):
    """Scheitert das Anlegen, darf die Mail keinesfalls verschwinden."""
    monkeypatch.setattr(poller, "_llm_aufgabe", lambda *a, **k: ("Titel", None, ""))
    monkeypatch.setattr(poller, "_asana_aufgabe", lambda *a, **k: None)
    monkeypatch.setattr(poller, "_volltext", lambda *a, **k: "x")
    archiviert = []
    monkeypatch.setattr(poller, "_mail_archivieren", lambda mid: archiviert.append(mid) or True)
    r = poller.konsequenz_ausfuehren(MAIL, 4, state={})
    assert r["art"] is None
    assert not archiviert, "Ohne Aufgabe keine Archivierung"


# ── Faelligkeit nur aus der Mail ──────────────────────────────────────────

def test_frist_wird_uebernommen(monkeypatch):
    gesendete_daten = {}

    class R:
        status_code = 201
        @staticmethod
        def json():
            return {"data": {"gid": "g1"}}
    monkeypatch.setattr(poller, "ASANA_TOKEN", "t")
    monkeypatch.setattr(poller, "_asana_offene_titel", lambda *a, **k: set())
    monkeypatch.setattr(poller, "_asana_section_gid", lambda: None)

    def fake_post(url, **kw):
        gesendete_daten.update(kw.get("json", {}).get("data", {}))
        return R()
    monkeypatch.setattr(poller.requests, "post", fake_post)

    poller._asana_aufgabe("Betreff", "N", "n@x.de", "T", "2026-09-16",
                          titel="Tu was", frist="2026-09-30", kontext="K")
    assert gesendete_daten.get("due_on") == "2026-09-30"
    assert gesendete_daten.get("name") == "Tu was"


def test_ohne_frist_kein_datum(monkeypatch):
    daten = {}

    class R:
        status_code = 201
        @staticmethod
        def json():
            return {"data": {"gid": "g1"}}
    monkeypatch.setattr(poller, "ASANA_TOKEN", "t")
    monkeypatch.setattr(poller, "_asana_offene_titel", lambda *a, **k: set())
    monkeypatch.setattr(poller, "_asana_section_gid", lambda: None)
    monkeypatch.setattr(poller.requests, "post",
                        lambda url, **kw: daten.update(kw.get("json", {}).get("data", {})) or R())
    poller._asana_aufgabe("Betreff", "N", "n@x.de", "T", "2026-09-16", titel="Tu was")
    assert "due_on" not in daten, "Kein erfundenes Datum"


# ── Weiterleitungstext ────────────────────────────────────────────────────

def test_weiterleitungstext_ohne_llm_hat_anrede_und_umlaute(monkeypatch):
    monkeypatch.setattr(poller, "_get_llm_client", lambda: None)
    t = poller._weiterleitungstext("Frank", "Zahlungserinnerung 7193", "Coglas", "Text")
    assert t.startswith("Hallo Frank,")
    assert "Viele Grüße" in t, "Umlaute muessen korrekt sein"
    assert "Gruesse" not in t
    assert "Zahlungserinnerung" in t


def test_weiterleitungstext_ohne_namen():
    t = poller._weiterleitungstext("", "Betreff", "Absender", "Text")
    assert t.startswith("Hallo,")


# ── Kuerzen an der Wortgrenze ─────────────────────────────────────────────

def test_kuerzen_schneidet_am_wort():
    """Ein Titel endete mit 'Anforderungen (Snapshot-Modell, Histo'."""
    t = poller._kuerzen("Open Master Data Loesung evaluieren und Anforderungen klaeren", 30)
    assert not t.rstrip("…").endswith("Anford")
    assert t.endswith("…")
    assert " " not in t[-2:], "Kein Leerzeichen vor dem Auslassungszeichen"


def test_kuerzen_laesst_kurzes_unveraendert():
    assert poller._kuerzen("Kurz", 50) == "Kurz"


def test_kuerzen_entfernt_haengende_satzzeichen():
    t = poller._kuerzen("Titel mit Aufzaehlung, noch mehr Text", 18)
    assert not t.rstrip("…").endswith(",")


def test_kuerzen_vertraegt_leeren_text():
    assert poller._kuerzen("", 10) == ""


# ── Rueckwirkende Regeln ──────────────────────────────────────────────────

def test_erster_lauf_wendet_nichts_rueckwirkend_an(monkeypatch, tmp_path):
    monkeypatch.setattr(poller, "LAUFZEIT_REGELN", str(tmp_path / "r.json"))
    state = {}
    z = poller.regeln_rueckwirkend(state)
    assert z["angewendet"] == 0
    assert "regeln_stand" in state, "Stand muss gemerkt werden"


def test_unveraenderte_regeln_loesen_nichts_aus(monkeypatch, tmp_path):
    p = tmp_path / "r.json"
    p.write_text('{"regeln": []}', encoding="utf-8")
    monkeypatch.setattr(poller, "LAUFZEIT_REGELN", str(p))
    state = {}
    poller.regeln_rueckwirkend(state)          # erster Lauf: merken
    gerufen = []
    monkeypatch.setattr(poller.requests, "get", lambda *a, **k: gerufen.append(1))
    z = poller.regeln_rueckwirkend(state)      # zweiter Lauf: unveraendert
    assert z["angewendet"] == 0
    assert not gerufen, "Ohne Aenderung darf kein Abruf erfolgen"
