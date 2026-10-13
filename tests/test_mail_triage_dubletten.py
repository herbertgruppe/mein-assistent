"""
Tests fuer die Dublettenpruefung bei Asana-Aufgaben (HBE-3120).

Der Fall vom 18.09.2026: Zur Mail "Saldenliste Controlling Dashboard" entstanden
zwei Aufgaben.

    07:06  Anforderungen für Saldenliste Controlling Dashboard aufzählen
    12:46  Anforderungen an Saldenliste für Controlling-Dashboard aufzählen

Die Pruefung verglich den Mail-Betreff gegen die Titel der offenen Aufgaben. Das
funktionierte, solange der Titel der Betreff war. Seit HBE-3061 formuliert das
Modell den Titel — seitdem konnten beide Seiten nicht mehr uebereinstimmen und
die Pruefung lief zwei Tage lang ins Leere, ohne dass es auffiel.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
poller = pytest.importorskip("lena_mail_triage_poller")


NOTIZEN = (
    "Dominik Prestel benötigt die genauen Anforderungen.\n\n"
    "Aus einer E-Mail vom 2026-09-18.\n\n"
    "Von: Dominik Prestel <d.prestel@herbert.de>\n"
    "Betreff: Saldenliste Controlling Dashboard\n\n"
    "Guten Morgen Herr Herbert, …"
)


class _Antwort:
    status_code = 200

    def __init__(self, tasks):
        self._tasks = tasks

    def json(self):
        return {"data": self._tasks}


@pytest.fixture
def board(monkeypatch):
    """Ein Board mit genau der Aufgabe vom 18.09. um 07:06."""
    monkeypatch.setattr(poller, "ASANA_TOKEN", "tok")
    monkeypatch.setattr(poller, "_asana_section_gid", lambda: "sec-1")
    poller._asana_cache.clear()
    monkeypatch.setattr(poller.requests, "get", lambda *a, **k: _Antwort([
        {"name": "Anforderungen für Saldenliste Controlling Dashboard aufzählen",
         "completed": False, "notes": NOTIZEN},
    ]))


def test_betreff_aus_den_notizen_wird_erkannt(board):
    schluessel = poller._asana_offene_titel()
    assert poller._titel_normalisieren("Saldenliste Controlling Dashboard") in schluessel
    assert poller._titel_normalisieren(
        "Anforderungen für Saldenliste Controlling Dashboard aufzählen") in schluessel


def test_zweite_aufgabe_zur_selben_mail_wird_verhindert(board, monkeypatch):
    """Der konkrete Fall vom 18.09. — anderer Titel, gleicher Betreff."""
    angelegt = []
    monkeypatch.setattr(poller.requests, "post",
                        lambda *a, **k: angelegt.append(1) or _Antwort([]))
    gid = poller._asana_aufgabe(
        "Saldenliste Controlling Dashboard", "Dominik Prestel", "d.prestel@herbert.de",
        "Text", "2026-09-18",
        titel="Anforderungen an Saldenliste für Controlling-Dashboard aufzählen")
    assert gid is None
    assert not angelegt, "Es darf keine zweite Aufgabe entstehen"


def test_neue_mail_wird_angelegt(board, monkeypatch):
    """Die Sperre darf nicht alles blockieren."""
    class Erstellt:
        status_code = 201
        @staticmethod
        def json():
            return {"data": {"gid": "neu-1"}}
    monkeypatch.setattr(poller, "_asana_section_gid", lambda: None)
    monkeypatch.setattr(poller.requests, "post", lambda *a, **k: Erstellt())
    gid = poller._asana_aufgabe("Ganz anderes Thema", "N", "n@x.de", "T", "2026-09-18",
                                titel="Etwas völlig anderes erledigen")
    assert gid == "neu-1"


def test_gleicher_titel_bei_anderem_betreff_greift_auch(board, monkeypatch):
    """Zweiter Schluessel: der formulierte Titel."""
    angelegt = []
    monkeypatch.setattr(poller.requests, "post",
                        lambda *a, **k: angelegt.append(1) or _Antwort([]))
    gid = poller._asana_aufgabe("WG: Saldenliste — Nachtrag", "N", "n@x.de", "T", "2026-09-18",
                                titel="Anforderungen für Saldenliste Controlling Dashboard aufzählen")
    assert gid is None
    assert not angelegt


# ── Reichweite der Abfrage (HBE-3122) ─────────────────────────────────────
# Die Abfrage ging ueber das ganze Board und war bei 100 Aufgaben
# abgeschnitten. Svens Board hat mehr — die Mail-Aufgaben lagen jenseits der
# Grenze. Die Pruefung sah ausgerechnet die Aufgaben nicht, gegen die sie
# schuetzen soll. Aufgefallen erst beim Abgleich mit dem echten Board.

def test_fragt_die_section_ab_wenn_es_sie_gibt(monkeypatch):
    monkeypatch.setattr(poller, "ASANA_TOKEN", "tok")
    monkeypatch.setattr(poller, "_asana_section_gid", lambda: "sec-42")
    poller._asana_cache.clear()
    gerufen = {}

    def fake(url, **kw):
        gerufen["url"] = url
        return _Antwort([])
    monkeypatch.setattr(poller.requests, "get", fake)
    poller._asana_offene_titel()
    assert "/sections/sec-42/tasks" in gerufen["url"], \
        "Ohne Section-Abfrage bleibt die Pruefung bei 100 Board-Aufgaben blind"


def test_ohne_section_wird_geblaettert(monkeypatch):
    """Fallback aufs Board — dann aber vollstaendig."""
    monkeypatch.setattr(poller, "ASANA_TOKEN", "tok")
    monkeypatch.setattr(poller, "_asana_section_gid", lambda: None)
    poller._asana_cache.clear()
    seiten = [
        {"data": [{"name": "Seite eins", "completed": False}],
         "next_page": {"uri": "https://app.asana.com/api/1.0/tasks?offset=x"}},
        {"data": [{"name": "Seite zwei", "completed": False}], "next_page": None},
    ]
    aufrufe = []

    class R:
        status_code = 200
        def __init__(self, d): self._d = d
        def json(self): return self._d

    def fake(url, **kw):
        aufrufe.append(url)
        return R(seiten[len(aufrufe) - 1])
    monkeypatch.setattr(poller.requests, "get", fake)
    s = poller._asana_offene_titel()
    assert len(aufrufe) == 2, "Die zweite Seite muss geholt werden"
    assert poller._titel_normalisieren("Seite zwei") in s


def test_erledigte_aufgaben_blockieren_nicht(monkeypatch):
    monkeypatch.setattr(poller, "ASANA_TOKEN", "tok")
    monkeypatch.setattr(poller, "_asana_section_gid", lambda: "sec-1")
    poller._asana_cache.clear()
    monkeypatch.setattr(poller.requests, "get", lambda *a, **k: _Antwort([
        {"name": "Alter Titel", "completed": True, "notes": NOTIZEN},
    ]))
    assert poller._asana_offene_titel() == set(), "Erledigtes darf nichts blockieren"


def test_notizen_ohne_betreffzeile_stoeren_nicht(monkeypatch):
    monkeypatch.setattr(poller, "ASANA_TOKEN", "tok")
    monkeypatch.setattr(poller, "_asana_section_gid", lambda: "sec-1")
    poller._asana_cache.clear()
    monkeypatch.setattr(poller.requests, "get", lambda *a, **k: _Antwort([
        {"name": "Handaufgabe", "completed": False, "notes": "von Hand angelegt"},
        {"name": "Ohne Notizen", "completed": False},
    ]))
    s = poller._asana_offene_titel()
    assert poller._titel_normalisieren("Handaufgabe") in s
    assert "" not in s, "Leere Schluessel wuerden alles blockieren"


# ── Klartext in den Telegram-Meldungen (HBE-3120) ─────────────────────────

def test_meldungen_ohne_markdown_auszeichnung():
    """Seit HBE-3107 senden wir ohne parse_mode — Markup erschiene woertlich."""
    quelle = open(poller.__file__, encoding="utf-8").read()
    for marker in ("📋 *Aufgabe angelegt*", "↪️ *An wen weiterleiten?*",
                   "✏️ *Wie soll ich antworten?*", "📌 *Regel vorschlagen?*",
                   "⚠️ *Lena-Triage Hoch-Prio*", "_Mail abgelegt._"):
        assert marker not in quelle, f"Markup in Telegram-Text: {marker}"
