"""
Tests fuer die 5-Schritte-Regel und die mechanischen Pruefungen (HBE-3044).

Alle Faelle stammen aus der Posteingangs-Durchsicht vom 14.09.2026 — es sind
echte Fehlklassifikationen, die dort aufgefallen sind.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
poller = pytest.importorskip("lena_mail_triage_poller")


def _vor_tagen(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat().replace("+00:00", "Z")


# ── Abbildung der Schritte ──────────────────────────────────────────────────

def test_alle_schritte_haben_eine_aktion():
    assert set(poller.SCHRITT_ZU_AKTION) == {1, 2, 3, 4, 5}
    assert set(poller.SCHRITT_NAMEN) == {1, 2, 3, 4, 5}


def test_schritt_1_und_2_sind_beide_ablegen():
    """Loeschen und Ablegen nutzen dieselbe Outlook-Kategorie."""
    assert poller.SCHRITT_ZU_AKTION[1] == "ablegen"
    assert poller.SCHRITT_ZU_AKTION[2] == "ablegen"


def test_schritt_2_wird_nie_still_archiviert():
    assert 2 in poller.SCHRITTE_OHNE_AUTOARCHIV
    assert 1 not in poller.SCHRITTE_OHNE_AUTOARCHIV


def test_alte_kategorien_werden_abgebildet():
    """warten -> Ablegen, recherchieren -> Terminieren."""
    assert poller.AKTION_ZU_SCHRITT["warten"] == 2
    assert poller.AKTION_ZU_SCHRITT["recherchieren"] == 4
    assert poller.AKTION_ZU_SCHRITT["antworten"] == 5


# ── Pruefung 1: Empfaenger darf nicht der Absender sein ────────────────────

def test_weiterleitung_an_den_absender_wird_verhindert():
    """Echter Fall #182: Mail VON Walter Melcher, Vorschlag AN Walter Melcher."""
    schritt, grund = poller.apply_guards(
        3, "llm:Marketing-Thema", "Walter Melcher",
        sender_email="w.melcher@herbert.de", sender_name="Walter Melcher",
    )
    assert schritt == 2
    assert "empfaenger_ist_absender" in grund


def test_selbst_weiterleitung_auch_ueber_die_adresse_erkannt():
    """Echter Fall #47: Mail von Jan Herbert, Vorschlag an Jan Herbert."""
    schritt, grund = poller.apply_guards(
        3, "llm:Einkauf zustaendig", "jan.herbert@herbert.de",
        sender_email="jan.herbert@herbert.de", sender_name="Jan Herbert",
    )
    assert schritt == 2
    assert "empfaenger_ist_absender" in grund


def test_weiterleitung_an_andere_person_bleibt_erhalten():
    schritt, grund = poller.apply_guards(
        3, "llm:IT-Thema", "Sven Walter",
        sender_email="externer@lieferant.de", sender_name="Externer Lieferant",
        received_at=_vor_tagen(2),
    )
    assert schritt == 3
    assert grund == "llm:IT-Thema"


# ── Pruefung 2: Empfaenger steht schon im Verteiler ────────────────────────

def test_empfaenger_bereits_im_verteiler():
    """Echter Fall #172: Mail war an Sven Walter adressiert, Sven nur in Kopie."""
    schritt, grund = poller.apply_guards(
        3, "llm:IT-Thema", "Sven Walter",
        sender_email="patrick@extern.de", sender_name="Patrick Kleimeyer",
        to_emails=["s.walter@herbert.de"], cc_emails=["s.herbert@herbert.de"],
        received_at=_vor_tagen(2),
    )
    assert schritt == 2
    assert "bereits_im_verteiler" in grund


def test_empfaenger_in_kopie_zaehlt_auch():
    schritt, grund = poller.apply_guards(
        3, "llm:Einkauf", "Jan Herbert",
        sender_email="lieferant@extern.de",
        to_emails=["s.herbert@herbert.de"], cc_emails=["jan.herbert@herbert.de"],
        received_at=_vor_tagen(1),
    )
    assert schritt == 2


def test_fremder_verteiler_blockiert_nicht():
    schritt, _ = poller.apply_guards(
        3, "llm:Personal", "Tim Kneusels",
        sender_email="bewerber@gmx.de",
        to_emails=["s.herbert@herbert.de"], cc_emails=[],
        received_at=_vor_tagen(1),
    )
    assert schritt == 3


# ── Pruefung 3: Alter ──────────────────────────────────────────────────────

@pytest.mark.parametrize("schritt_vorher", [3, 4, 5])
def test_alte_mails_werden_zurueckgestuft(schritt_vorher):
    """Echte Faelle: drei Rueckrufbitten aus Juli, im September vorgeschlagen."""
    schritt, grund = poller.apply_guards(
        schritt_vorher, "llm:Rueckruf erbeten", "Rene Turtschan",
        sender_email="regina@reibstein.de", sender_name="Regina Tuerbsch",
        received_at=_vor_tagen(60),
    )
    assert schritt == 2
    assert "aelter_als" in grund


def test_frische_mails_bleiben_unveraendert():
    schritt, _ = poller.apply_guards(
        5, "llm:kurze Rueckfrage", None,
        sender_email="kunde@extern.de", received_at=_vor_tagen(3),
    )
    assert schritt == 5


def test_alte_ablegen_mail_bleibt_ablegen():
    """Schritt 1 und 2 werden von der Altersregel nicht angefasst."""
    schritt, grund = poller.apply_guards(
        1, "newsletter_sender", None,
        sender_email="newsletter@extern.de", received_at=_vor_tagen(90),
    )
    assert schritt == 1
    assert grund == "newsletter_sender"


def test_fehlendes_datum_stuft_nicht_zurueck():
    schritt, _ = poller.apply_guards(
        3, "llm:x", "Sven Walter", sender_email="a@b.de", received_at="")
    assert schritt == 3


# ── Archivierungs-Sperre fuer Schritt 2 ────────────────────────────────────

def test_schritt_2_darf_nicht_archivieren_trotz_regel():
    """Selbst eine Systemabsender-Regel archiviert bei Schritt 2 nicht."""
    assert poller.may_auto_archive("egal@example.com", "newsletter_sender", schritt=2) is False


def test_schritt_1_darf_archivieren():
    assert poller.may_auto_archive("egal@example.com", "newsletter_sender", schritt=1) is True


def test_ohne_schritt_bleibt_altes_verhalten():
    """Rueckwaertskompatibel — bestehende Aufrufer ohne schritt-Parameter."""
    assert poller.may_auto_archive("egal@example.com", "newsletter_sender") is True


# ── Newsletter-Muster ──────────────────────────────────────────────────────

@pytest.mark.parametrize("adresse", [
    "noreply-dmarc-support@google.com",      # echter Fall #14
    "newsletters-noreply@linkedin.com",
    "messaging-digest-noreply@linkedin.com",
    "no-reply@asana.com",
    "noreply@example.com",
    "donotreply@example.com",
    "invitations@linkedin.com",
    "newsletter@odv.de",
])
def test_maschinenabsender_werden_erkannt(adresse):
    assert poller.NEWSLETTER_SENDER_RE.search(adresse), f"{adresse} nicht erkannt"


@pytest.mark.parametrize("adresse", [
    "d.prestel@herbert.de",
    "jan.herbert@herbert.de",
    "dmihaljevic@bornemann-haustechnik.de",
    "schickel@btga.de",
])
def test_echte_personen_werden_nicht_als_newsletter_erkannt(adresse):
    assert not poller.NEWSLETTER_SENDER_RE.search(adresse), f"{adresse} faelschlich erkannt"


# ── Persona-Prompt ─────────────────────────────────────────────────────────

def test_prompt_enthaelt_adressen_der_direktberichte():
    text = poller._build_sven_persona({})
    assert "dmihaljevic@bornemann-haustechnik.de" in text, "Dragans echte Adresse fehlt"
    assert "l.keimes@dimexcon.de" in text
    assert "r.turtschan@reibstein.de" in text


# ── Schritt-Erkennung ──────────────────────────────────────────────────────
# Im ersten Praxistest lieferte das Modell bei 26 von 145 Mails den
# Schrittnamen statt der Zahl. Das darf nicht im Fallback landen.

@pytest.mark.parametrize("wert,erwartet", [
    (1, 1), (4, 4),
    ("3", 3),
    ("terminieren", 4), ("Terminieren", 4), ("TERMINIEREN", 4),
    ("löschen", 1), ("loeschen", 1), ("delete", 1),
    ("erledigen", 5), ("weiterleiten", 3), ("ablegen", 2),
    ("terminate", 4),
    ("Schritt 3", 3),
])
def test_schrittnamen_werden_verstanden(wert, erwartet):
    assert poller._parse_schritt(wert) == erwartet


@pytest.mark.parametrize("wert", [None, "", "voellig unklar", 0, 9, True])
def test_unbrauchbare_werte_liefern_none(wert):
    assert poller._parse_schritt(wert) is None


# ── Namensaufloesung ───────────────────────────────────────────────────────

def test_direktbericht_wird_zur_adresse_aufgeloest():
    assert poller.empfaenger_zu_adresse("Dragan Mihaljevic") == "dmihaljevic@bornemann-haustechnik.de"
    assert poller.empfaenger_zu_adresse("Sven Walter") == "s.walter@herbert.de"


def test_nachname_allein_reicht():
    assert poller.empfaenger_zu_adresse("Kneusels") == "t.kneusels@herbert.de"


def test_adresse_wird_durchgereicht():
    assert poller.empfaenger_zu_adresse("jan.herbert@herbert.de") == "jan.herbert@herbert.de"


def test_unbekannter_name_liefert_none():
    assert poller.empfaenger_zu_adresse("Erika Mustermann") is None


def test_keine_falschtreffer_durch_den_firmennamen():
    """
    Regression: 'Herbert' steckt in jeder @herbert.de-Adresse. Eine Teilstring-
    Suche liess 'Frank Herbert' faelschlich als Empfaenger jeder Mail an
    irgendeinen @herbert.de-Adressaten erscheinen.
    """
    assert poller._already_recipient(
        "Frank Herbert",
        to_emails=["s.herbert@herbert.de"],
        cc_emails=["d.ludwig@herbert.de"],
    ) is False
    # Steht er wirklich drin, greift die Pruefung weiterhin
    assert poller._already_recipient(
        "Frank Herbert",
        to_emails=["f.herbert@herbert.de"],
        cc_emails=[],
    ) is True


def test_unaufloesbarer_empfaenger_blockiert_nicht():
    """Im Zweifel lieber eine ueberfluessige Weiterleitung als eine verhinderte."""
    assert poller._already_recipient(
        "Irgendwer Unbekannt", to_emails=["a@b.de"], cc_emails=[]) is False


def test_namensgleichheit_ist_streng():
    """'Frank Herbert' darf nicht auf den Absender 'Sven Herbert' passen."""
    assert poller._self_forward("Frank Herbert", "s.herbert@herbert.de", "Sven Herbert") is False
    assert poller._self_forward("Sven Herbert", "s.herbert@herbert.de", "Sven Herbert") is True


def test_prompt_beschreibt_die_fuenf_schritte():
    text = poller._build_sven_persona({})
    for wort in ("LÖSCHEN", "ABLEGEN", "WEITERLEITEN", "TERMINIEREN", "ERLEDIGEN"):
        assert wort in text
    assert "NIEDRIGEREN" in text, "Zweifelsregel fehlt"
