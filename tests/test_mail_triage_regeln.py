"""
Tests fuer Svens eigenen Regelspeicher (HBE-3056).

Die beiden Schluessel stammen aus zwei echten Faellen vom 15.09.2026:

  Anthropic-Belege  invoice+statements@mail.anthropic.com, 72 Mails, immer
                    derselbe Absender, wechselnde Rechnungsnummer im Betreff
                    -> Schluessel ABSENDER

  DMARC-Berichte    8 verschiedene Absender, Betreff durch RFC 7489 festgelegt
                    ("Report Domain: herbertgruppe.com Submitter: …")
                    -> Schluessel BETREFF
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
poller = pytest.importorskip("lena_mail_triage_poller")


@pytest.fixture
def regeln(monkeypatch, tmp_path):
    """Setzt einen definierten Regelbestand."""
    def setze(liste, laufzeit=None):
        monkeypatch.setattr(poller, "_PERSONA_CONFIG", {"regeln": liste})
        p = tmp_path / "regeln.json"
        if laufzeit is not None:
            p.write_text(json.dumps({"regeln": laufzeit}), encoding="utf-8")
        monkeypatch.setattr(poller, "LAUFZEIT_REGELN", str(p))
        monkeypatch.setattr(poller, "_REGELN", None)
    return setze


# ── Schluessel Absender ───────────────────────────────────────────────────

def test_anthropic_beleg_trifft_ueber_den_absender(regeln):
    regeln([{"name": "Anthropic-Belege",
             "absender": "invoice+statements@mail.anthropic.com",
             "aktion": "weiterleiten",
             "empfaenger": "Laura Ann Hernandez-Allmann"}])
    r = poller.match_regel("invoice+statements@mail.anthropic.com",
                           "Your receipt from Anthropic, PBC #2849-3094-3660")
    assert r is not None
    assert r["aktion"] == "weiterleiten"
    assert r["empfaenger"] == "Laura Ann Hernandez-Allmann"


def test_anthropic_anmeldelink_trifft_die_regel_nicht(regeln):
    """Entscheidend: die Regel darf NICHT auf die Domain greifen."""
    regeln([{"absender": "invoice+statements@mail.anthropic.com",
             "aktion": "weiterleiten", "empfaenger": "Laura"}])
    assert poller.match_regel("no-reply-2yb9-meqeq3er7881staea@mail.anthropic.com",
                              "Sicherer Link zur Anmeldung bei Claude.ai") is None


# ── Schluessel Betreff ────────────────────────────────────────────────────

@pytest.mark.parametrize("absender,betreff", [
    ("dmarcreport@microsoft.com", "[Preview] Report Domain: herbertgruppe.com Submitter: enterprise"),
    ("noreply@dmarc.yahoo.com", "Report Domain: herbertgruppe.com Submitter: yahoo.com Report-ID: 1"),
    ("noreply-dmarc-support@google.com", "Report domain: herbertgruppe.com Submitter: google.com"),
    ("abuse@seznam.cz", "Report Domain: herbertgruppe.com Submitter: seznam.cz"),
    ("reporting@dmarc25.jp", "Report Domain: herbertgruppe.com Submitter: docomo.ne.jp"),
])
def test_dmarc_trifft_ueber_den_betreff(regeln, absender, betreff):
    """Acht verschiedene Absender, eine Regel. Microsofts '[Preview] ' und
    Googles Kleinschreibung duerfen nicht stoeren."""
    regeln([{"name": "DMARC", "betreff_enthaelt": "report domain: herbertgruppe.com",
             "aktion": "ablegen"}])
    r = poller.match_regel(absender, betreff)
    assert r is not None, f"{absender} nicht erfasst"
    assert r["aktion"] == "ablegen"


def test_weitergeleiteter_dmarc_einzelfall_bleibt_unberuehrt(regeln):
    """Leitet ein Kollege einen Einzelfall weiter, ist das keine Massenmail."""
    regeln([{"betreff_enthaelt": "report domain: herbertgruppe.com", "aktion": "ablegen"}])
    assert poller.match_regel(
        "m.knappe@herbert.de",
        "WG: 1.2.3 Local Policy Violation DMARC result equals reject") is None


# ── Kombination und Reihenfolge ───────────────────────────────────────────

def test_beide_schluessel_muessen_zutreffen(regeln):
    regeln([{"absender": "@lieferant.de", "betreff_enthaelt": "rechnung",
             "aktion": "weiterleiten", "empfaenger": "Frank Herbert"}])
    assert poller.match_regel("a@lieferant.de", "Rechnung 4711") is not None
    assert poller.match_regel("a@lieferant.de", "Terminanfrage") is None
    assert poller.match_regel("a@andere.de", "Rechnung 4711") is None


def test_erste_passende_regel_gewinnt(regeln):
    regeln([{"absender": "@x.de", "aktion": "ablegen", "name": "erste"},
            {"absender": "@x.de", "aktion": "tun", "name": "zweite"}])
    assert poller.match_regel("a@x.de", "egal")["name"] == "erste"


def test_laufzeitregeln_werden_mitgelesen(regeln):
    regeln([{"absender": "@fest.de", "aktion": "ablegen"}],
           laufzeit=[{"absender": "@spaeter.de", "aktion": "tun"}])
    assert poller.match_regel("a@fest.de", "x")["aktion"] == "ablegen"
    assert poller.match_regel("a@spaeter.de", "x")["aktion"] == "tun"


@pytest.mark.parametrize("kaputt", [
    {"aktion": "ablegen"},                                  # kein Schluessel
    {"absender": "@x.de"},                                  # keine Aktion
    {"absender": "@x.de", "aktion": "unfug"},               # ungueltige Aktion
    {"absender": "", "betreff_enthaelt": "", "aktion": "tun"},
])
def test_unbrauchbare_regeln_werden_verworfen(regeln, kaputt):
    regeln([kaputt])
    assert poller.match_regel("a@x.de", "x") is None


# ── Wirkung in der Triage ─────────────────────────────────────────────────

def test_regel_schlaegt_newsletter_muster(regeln, monkeypatch):
    """Sagt Sven 'weiterleiten', gewinnt das gegen die Newsletter-Regel."""
    regeln([{"absender": "noreply@wichtig.de", "aktion": "weiterleiten",
             "empfaenger": "Sven Walter", "name": "Testregel"}])
    a, prio, rule, _lf, schritt, empf = poller.triage_mail(
        "Irgendein Betreff", "noreply@wichtig.de", "Text", "Absender")
    assert a == "weiterleiten"
    assert rule.startswith("sven_regel:")
    assert empf == "Sven Walter"


def test_regel_ablegen_darf_archivieren(regeln):
    """Eine ausdrueckliche Anweisung ist die staerkste Deckung."""
    regeln([{"betreff_enthaelt": "report domain", "aktion": "ablegen", "name": "DMARC"}])
    a, prio, rule, _lf, schritt, _e = poller.triage_mail(
        "Report Domain: herbertgruppe.com", "irgendwer@fremd.de", "", "")
    assert schritt == 1, "Svens Ablegen ist Schritt 1, nicht Vorschlag"
    assert poller.may_auto_archive("irgendwer@fremd.de", rule, schritt) is True


# ── Schluesselwahl fuer Vorschlaege ───────────────────────────────────────

def test_stabiler_absender_wird_als_schluessel_gewaehlt():
    art, wert = poller.regel_schluessel_waehlen(
        "invoice+statements@mail.anthropic.com", "Your receipt from Anthropic, PBC #2849")
    assert art == "absender"
    assert wert == "invoice+statements@mail.anthropic.com"


def test_zufallsabsender_fuehrt_zum_betreff():
    """no-reply-<zufall>@… ist als Schluessel unbrauchbar."""
    art, wert = poller.regel_schluessel_waehlen(
        "no-reply-2yb9meqeq3er7881staea@mail.anthropic.com",
        "Sicherer Link zur Anmeldung bei Claude.ai")
    assert art == "betreff_enthaelt"
    assert "anmeldung" in wert.lower()


def test_betreffschluessel_ohne_zahlen():
    art, wert = poller.regel_schluessel_waehlen(
        "no-reply-abcdef0123456789xyz@x.de", "Rechnung 4711 vom 12.09.2026")
    assert art == "betreff_enthaelt"
    assert "4711" not in wert


# ── Zwei Kategorien gleichzeitig ──────────────────────────────────────────

def test_zwei_kategorien_die_fremde_gewinnt():
    """Sven fuegt hinzu statt zu ersetzen — das muss genuegen."""
    kats = ["Lena: Ablegen", "Lena: Weiterleiten"]
    assert poller._lena_kategorie(kats, eigene_aktion="ablegen") == "Lena: Weiterleiten"


def test_zwei_kategorien_andersherum():
    kats = ["Lena: Weiterleiten", "Lena: Ablegen"]
    assert poller._lena_kategorie(kats, eigene_aktion="weiterleiten") == "Lena: Ablegen"


def test_eine_kategorie_bleibt_eindeutig():
    assert poller._lena_kategorie(["Lena: Tun"], eigene_aktion="tun") == "Lena: Tun"


def test_ohne_eigene_entscheidung_erste_kategorie():
    assert poller._lena_kategorie(["Lena: Tun", "Lena: Ablegen"]) == "Lena: Tun"


def test_fremde_kategorien_stoeren_nicht():
    assert poller._lena_kategorie(["Rot", "Privat", "Lena: Ablegen"]) == "Lena: Ablegen"
