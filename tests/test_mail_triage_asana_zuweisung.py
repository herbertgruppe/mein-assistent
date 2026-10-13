"""
Tests fuer die Zuweisung der erzeugten Asana-Aufgaben.

Der Fall vom 23.09.2026: Sven fand die Aufgaben, die Lena aus Mails anlegt, in
seiner Aufgabenliste nicht. Sie lagen korrekt im Board "Meine Aufgaben SH",
Section "📬 Aus Mails" — aber ohne assignee. In Asanas Ansicht "Meine Aufgaben"
erscheinen nicht zugewiesene Aufgaben nicht; sie existieren nur im Board.

Zum Vergleich trugen alle von Hand angelegten Aufgaben desselben Boards
"Sven Herbert" als Zustaendigen. 23 maschinell erzeugte Aufgaben trugen
niemanden.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
poller = pytest.importorskip("lena_mail_triage_poller")


class _Erstellt:
    status_code = 201

    @staticmethod
    def json():
        return {"data": {"gid": "neu-1"}}


@pytest.fixture
def leeres_board(monkeypatch):
    monkeypatch.setattr(poller, "ASANA_TOKEN", "tok")
    monkeypatch.setattr(poller, "_asana_offene_titel", lambda *a, **k: set())
    monkeypatch.setattr(poller, "_asana_section_gid", lambda: None)
    poller._asana_cache.clear()


@pytest.fixture
def gesendet(monkeypatch):
    daten = {}

    def fake_post(url, **kw):
        if url.endswith("/tasks"):
            daten.update(kw.get("json", {}).get("data", {}))
        return _Erstellt()
    monkeypatch.setattr(poller.requests, "post", fake_post)
    return daten


def test_aufgabe_wird_sven_zugewiesen(leeres_board, gesendet, monkeypatch):
    monkeypatch.setattr(poller, "ASANA_ASSIGNEE", "1202563118654849")
    poller._asana_aufgabe("Betreff", "N", "n@x.de", "Text", "2026-09-23",
                          titel="Etwas erledigen")
    assert gesendet.get("assignee") == "1202563118654849", \
        "Ohne assignee erscheint die Aufgabe nicht in 'Meine Aufgaben'"


def test_ohne_konfigurierten_empfaenger_kein_feld(leeres_board, gesendet, monkeypatch):
    """Leer gesetzt heisst bewusst unzugewiesen — dann darf das Feld fehlen,
    nicht leer mitgeschickt werden."""
    monkeypatch.setattr(poller, "ASANA_ASSIGNEE", "")
    poller._asana_aufgabe("Betreff", "N", "n@x.de", "Text", "2026-09-23")
    assert "assignee" not in gesendet


def test_zuweisung_stoert_die_uebrigen_felder_nicht(leeres_board, gesendet, monkeypatch):
    monkeypatch.setattr(poller, "ASANA_ASSIGNEE", "42")
    poller._asana_aufgabe("Saldenliste Controlling Dashboard", "Dominik Prestel",
                          "d.prestel@herbert.de", "Text", "2026-09-23",
                          titel="Anforderungen aufzählen", frist="2026-09-30")
    assert gesendet["name"] == "Anforderungen aufzählen"
    assert gesendet["due_on"] == "2026-09-30"
    assert "Betreff: Saldenliste Controlling Dashboard" in gesendet["notes"]
    assert gesendet["projects"] == [poller.ASANA_BOARD_GID]


# ── Dubletten-Cache nach dem Anlegen (Nachtrag zu HBE-3120) ───────────────

def test_beide_schluessel_landen_sofort_im_cache(leeres_board, gesendet, monkeypatch):
    """Sonst legt derselbe Lauf eine zweite Aufgabe zur selben Mail an —
    der Cache wird nur alle zehn Minuten vom Board aufgefrischt."""
    monkeypatch.setattr(poller, "ASANA_ASSIGNEE", "")
    poller._asana_aufgabe("Saldenliste Controlling Dashboard", "N", "n@x.de",
                          "Text", "2026-09-23", titel="Anforderungen aufzählen")
    cache = poller._asana_cache.get("titel", set())
    assert poller._titel_normalisieren("Saldenliste Controlling Dashboard") in cache
    assert poller._titel_normalisieren("Anforderungen aufzählen") in cache


def test_ohne_titel_wird_der_betreff_gemerkt(leeres_board, gesendet, monkeypatch):
    monkeypatch.setattr(poller, "ASANA_ASSIGNEE", "")
    poller._asana_aufgabe("Nur ein Betreff", "N", "n@x.de", "Text", "2026-09-23")
    assert poller._titel_normalisieren("Nur ein Betreff") in poller._asana_cache["titel"]
