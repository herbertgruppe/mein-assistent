"""
Tests fuer die Konsequenzen je Schritt (HBE-3048).

Leitgedanke: Eine Kategorie ohne Konsequenz ist farbig markierter Posteingang.
Schritt 3 erzeugt einen Weiterleitungs-Entwurf, Schritt 4 eine Asana-Aufgabe,
Schritt 5 einen Antwort-Entwurf. Nichts wird gesendet oder zugewiesen.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
poller = pytest.importorskip("lena_mail_triage_poller")


MAIL = {
    "message_id": "AAMk-test",
    "subject": "Rueckfrage zum Angebot",
    "sender_name": "Erika Kundin",
    "sender_email": "kundin@extern.de",
    "body_preview": "Guten Tag Herr Herbert, koennen Sie den Termin am Freitag bestaetigen?",
    "received_at": "2026-09-15T08:00:00Z",
}


@pytest.fixture
def aktiv(monkeypatch):
    monkeypatch.setattr(poller, "AKTIONEN_AKTIV", True)
    monkeypatch.setattr(poller, "_PERSONA_CONFIG", poller._load_persona_config())
    monkeypatch.setattr(poller, "_SYSTEM_SENDERS", None)


# ── Schalter ───────────────────────────────────────────────────────────────

def test_ohne_schalter_passiert_nichts(monkeypatch):
    monkeypatch.setattr(poller, "AKTIONEN_AKTIV", False)
    r = poller.konsequenz_ausfuehren(MAIL, 5)
    assert r["art"] is None


@pytest.mark.parametrize("schritt", [1, 2])
def test_ablegen_schritte_loesen_nichts_aus(aktiv, schritt):
    """Loeschen und Ablegen brauchen keinen Entwurf."""
    assert poller.konsequenz_ausfuehren(MAIL, schritt)["art"] is None


# ── Schritt 5: Antwort-Entwurf, dreistufig ────────────────────────────────

def test_voller_entwurf_wird_angelegt(aktiv, monkeypatch):
    erfasst = {}
    monkeypatch.setattr(poller, "_llm_entwurf",
                        lambda *a, **k: ("voll", "Guten Tag,\n\nder Termin passt.\n\nSven", ""))

    def fake_draft(mid, betreff, text, modus="reply", to=None):
        erfasst.update(mid=mid, betreff=betreff, text=text, modus=modus)
        return "draft-1"
    monkeypatch.setattr(poller, "_entwurf_anlegen", fake_draft)

    r = poller.konsequenz_ausfuehren(MAIL, 5)
    assert r["art"] == "antwort"
    assert r["id"] == "draft-1"
    assert r["stufe"] == "voll"
    assert erfasst["modus"] == "reply"
    assert erfasst["betreff"].startswith("AW: ")
    assert "Hinweis von Lena" not in erfasst["text"], "Voller Entwurf braucht keinen Zusatz"


def test_geruest_bekommt_einen_hinweis(aktiv, monkeypatch):
    erfasst = {}
    monkeypatch.setattr(poller, "_llm_entwurf",
                        lambda *a, **k: ("geruest", "Guten Tag,\n\nder Termin am [[Datum]] passt.\n\nSven", ""))
    monkeypatch.setattr(poller, "_entwurf_anlegen",
                        lambda mid, b, t, modus="reply", to=None: erfasst.update(text=t) or "draft-2")

    r = poller.konsequenz_ausfuehren(MAIL, 5)
    assert r["stufe"] == "geruest"
    assert "[[Datum]]" in erfasst["text"]
    assert "Hinweis von Lena" in erfasst["text"], "Luecke muss erklaert werden"


def test_bei_nein_entsteht_kein_entwurf(aktiv, monkeypatch):
    """Der wichtigste Fall: lieber nichts als etwas Erfundenes."""
    angelegt = []
    monkeypatch.setattr(poller, "_llm_entwurf",
                        lambda *a, **k: ("nein", "", "Soll das Angebot nachverhandelt werden?"))
    monkeypatch.setattr(poller, "_entwurf_anlegen",
                        lambda *a, **k: angelegt.append(1) or "darf-nicht-passieren")

    r = poller.konsequenz_ausfuehren(MAIL, 5)
    assert r["art"] is None
    assert r["stufe"] == "nein"
    assert r["hinweis"] == "Soll das Angebot nachverhandelt werden?"
    assert not angelegt, "Bei 'nein' darf kein Entwurf angelegt werden"


def test_leerer_text_erzeugt_keinen_entwurf(aktiv, monkeypatch):
    angelegt = []
    monkeypatch.setattr(poller, "_llm_entwurf", lambda *a, **k: ("voll", "   ", ""))
    monkeypatch.setattr(poller, "_entwurf_anlegen", lambda *a, **k: angelegt.append(1) or "x")
    r = poller.konsequenz_ausfuehren(MAIL, 5)
    assert r["art"] is None
    assert not angelegt


def test_llm_fehler_wird_abgefangen(aktiv, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("LLM weg")
    monkeypatch.setattr(poller, "_llm_entwurf", boom)
    r = poller.konsequenz_ausfuehren(MAIL, 5)
    assert r["art"] is None
    assert "Fehler" in (r["hinweis"] or "")


# ── Schritt 3: Weiterleitungs-Entwurf ─────────────────────────────────────

def test_weiterleitung_geht_an_die_aufgeloeste_adresse(aktiv, monkeypatch):
    erfasst = {}

    def fake_draft(mid, betreff, text, modus="reply", to=None):
        erfasst.update(betreff=betreff, modus=modus, to=to)
        return "draft-3"
    monkeypatch.setattr(poller, "_entwurf_anlegen", fake_draft)

    r = poller.konsequenz_ausfuehren(MAIL, 3, empfaenger="Sven Walter")
    assert r["art"] == "weiterleitung"
    assert erfasst["modus"] == "forward", "Weiterleitung braucht createForward, nicht createReply"
    assert erfasst["betreff"].startswith("WG: ")
    assert erfasst["to"][0]["email"] == "s.walter@herbert.de"


def test_unaufloesbarer_empfaenger_erzeugt_keinen_entwurf(aktiv, monkeypatch):
    angelegt = []
    monkeypatch.setattr(poller, "_entwurf_anlegen", lambda *a, **k: angelegt.append(1) or "x")
    r = poller.konsequenz_ausfuehren(MAIL, 3, empfaenger="Erika Mustermann")
    assert r["art"] is None
    assert "nicht aufloesbar" in (r["hinweis"] or "")
    assert not angelegt


def test_ohne_empfaenger_kein_entwurf(aktiv, monkeypatch):
    monkeypatch.setattr(poller, "_entwurf_anlegen", lambda *a, **k: "x")
    assert poller.konsequenz_ausfuehren(MAIL, 3, empfaenger=None)["art"] is None


# ── Schritt 4: Asana ──────────────────────────────────────────────────────

def test_asana_aufgabe_wird_angelegt(aktiv, monkeypatch):
    erfasst = {}

    # HBE-3061: Der Aufruf traegt jetzt zusaetzlich Titel, Frist und Kontext,
    # und der Volltext wird geholt statt der 500-Zeichen-Vorschau.
    def fake(betreff, s_name, s_mail, vorschau, empfangen, **kw):
        erfasst.update(betreff=betreff, sender=s_mail, vorschau=vorschau, **kw)
        return "gid-4711"
    monkeypatch.setattr(poller, "_asana_aufgabe", fake)
    monkeypatch.setattr(poller, "_volltext", lambda *a, **k: "Volltext der Mail")
    monkeypatch.setattr(poller, "_llm_aufgabe",
                        lambda *a, **k: ("Termin am Freitag bestaetigen", None, "Kontext"))
    monkeypatch.setattr(poller, "_mail_archivieren", lambda mid: True)
    monkeypatch.setattr(poller, "_rueckfrage", lambda *a, **k: True)

    r = poller.konsequenz_ausfuehren(MAIL, 4)
    assert r["art"] == "asana"
    assert r["id"] == "gid-4711"
    assert erfasst["betreff"] == MAIL["subject"]
    assert erfasst["sender"] == MAIL["sender_email"]
    assert erfasst["titel"] == "Termin am Freitag bestaetigen"


def test_asana_ohne_token_liefert_none(monkeypatch):
    monkeypatch.setattr(poller, "ASANA_TOKEN", "")
    assert poller._asana_aufgabe("Betreff", "N", "n@x.de", "Text", "2026-09-15") is None


# ── Schreibstil ───────────────────────────────────────────────────────────

def test_schreibstil_faellt_auf_default_zurueck(monkeypatch, tmp_path):
    monkeypatch.setattr(poller, "SCHREIBSTIL_DATEI", str(tmp_path / "fehlt.md"))
    stil = poller._schreibstil()
    assert "Du" in stil and "Sie" in stil


def test_schreibstil_entfernt_frontmatter(monkeypatch, tmp_path):
    f = tmp_path / "Schreibstil.md"
    f.write_text("---\ntags: [kontext]\n---\n\n# Stil\n\nKlar und direkt.\n", encoding="utf-8")
    monkeypatch.setattr(poller, "SCHREIBSTIL_DATEI", str(f))
    stil = poller._schreibstil()
    assert "tags:" not in stil
    assert "Klar und direkt." in stil


# ── JSON-Auswertung ───────────────────────────────────────────────────────
# Bei laengeren Eingaben haengt das Modell gelegentlich Fliesstext hinter das
# JSON. Im Praxistest verlor genau das einen brauchbaren Entwurf.

def test_sauberes_json():
    assert poller._erstes_json_objekt('{"stufe": "voll", "text": "Hallo"}')["stufe"] == "voll"


def test_json_mit_fences():
    assert poller._erstes_json_objekt('```json\n{"schritt": 3}\n```')["schritt"] == 3


def test_json_mit_nachgestelltem_text():
    """Der Fall, der im Praxistest scheiterte."""
    roh = '{"stufe": "voll", "text": "Hallo"}\n\nIch hoffe das passt so.'
    assert poller._erstes_json_objekt(roh)["text"] == "Hallo"


def test_json_mit_vorangestelltem_text():
    roh = 'Hier mein Vorschlag:\n{"stufe": "nein", "text": ""}'
    assert poller._erstes_json_objekt(roh)["stufe"] == "nein"


def test_verschachteltes_json_bleibt_intakt():
    roh = '{"a": {"b": 1}, "c": "x"}  Nachtrag'
    d = poller._erstes_json_objekt(roh)
    assert d["a"]["b"] == 1 and d["c"] == "x"


def test_geschweifte_klammer_im_string_verwirrt_nicht():
    roh = '{"text": "Nutze {{Platzhalter}} hier", "stufe": "voll"} Ende'
    assert poller._erstes_json_objekt(roh)["stufe"] == "voll"


def test_escaped_anfuehrungszeichen_im_string():
    roh = '{"text": "Er sagte \\"ja\\" dazu", "stufe": "voll"} danach'
    assert poller._erstes_json_objekt(roh)["stufe"] == "voll"


@pytest.mark.parametrize("roh", ["", "gar kein json", "{ unvollstaendig"])
def test_unbrauchbare_antwort_wirft(roh):
    with pytest.raises((ValueError, Exception)):
        poller._erstes_json_objekt(roh)


def test_entwurfs_prompt_verbietet_erfinden():
    txt = poller.ENTWURF_SYSTEM
    assert "Erfinde niemals" in txt
    assert "Im Zweifel" in txt
    for stufe in ("voll", "geruest", "nein"):
        assert f'"{stufe}' in txt or stufe in txt
