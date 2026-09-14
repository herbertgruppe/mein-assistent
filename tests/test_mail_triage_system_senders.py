"""
Tests fuer die Systemabsender-Regeln und die Ablegen-Zweiteilung
(Mail-Konzept 2026-09).

Hintergrund: jobrouter@intern.herbert.de stellte rund ein Drittel des
Postaufkommens und erzeugte 65 % aller Aktions-Markierungen, obwohl Sven die
Einzelmails nicht liest. Diese Tests sichern ab, dass

  1. Systemabsender deterministisch behandelt werden (kein LLM-Aufruf),
  2. die Personal-Ausnahmen sichtbar bleiben,
  3. ein 'ablegen' ohne Deckung durch Regel oder Absender-Profil die Mail
     NICHT verschiebt.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

poller = pytest.importorskip("lena_mail_triage_poller")


JOBROUTER = "jobrouter@intern.herbert.de"

CONFIG = {
    "system_absender": [
        {
            "email": JOBROUTER,
            "aktion": "ablegen",
            "prioritaet": "niedrig",
            "ausnahmen_betreff": [
                "Mitarbeitereintritt", "MA-Eintritt", "MA - Eintritt",
                "Mitarbeiteraustritt", "MA-Austritt", "MA - Austritt",
            ],
            "ausnahme_aktion": "tun",
            "ausnahme_prioritaet": "mittel",
        }
    ]
}


@pytest.fixture(autouse=True)
def _system_senders(monkeypatch):
    """Systemabsender-Regeln aus CONFIG statt aus der echten YAML laden."""
    monkeypatch.setattr(poller, "_SYSTEM_SENDERS", poller._load_system_senders(CONFIG))
    yield
    monkeypatch.setattr(poller, "_SYSTEM_SENDERS", None)


# ── Systemabsender-Matching ──────────────────────────────────────────────────

@pytest.mark.parametrize("subject", [
    "Eingangsrechnungsprüfung: Eingangsrechnungsprüfung",
    "Ein Dokument liegt seit mindestens 5 Tagen für Sie bereit",
    "JobRouter - Bedarfsanforderungsprozess - Vorgang seit 3 Tagen zugewiesen",
    "Ihre JobRouter Statistik zum 14.09.2026 07:00 Uhr",
    "Skontoverfall in 4 Tagen - Vorgang 12345",
    # Bewusst NICHT in der Ausnahme (Entscheidung Sven 2026-09-14):
    "PW - Personalentscheidung: (In Vertretung)",
])
def test_jobrouter_wird_abgelegt(subject):
    hit = poller.match_system_sender(JOBROUTER, subject)
    assert hit is not None, "Systemabsender-Regel muss greifen"
    action, priority, rule_id = hit
    assert action == "ablegen"
    assert priority == "niedrig"
    assert rule_id == f"system_sender:{JOBROUTER}"


@pytest.mark.parametrize("subject,keyword", [
    ("Jobrouter: Mitarbeitereintritt von Lev Kirnats", "mitarbeitereintritt"),
    ("Jobrouter: MA-Eintritt von Attila Dobo", "ma-eintritt"),
    ("Jobrouter: MA - Eintritt von Kai Rettig", "ma - eintritt"),
    ("Jobrouter: Mitarbeiteraustritt von Max Muster", "mitarbeiteraustritt"),
])
def test_personal_ausnahmen_bleiben_sichtbar(subject, keyword):
    action, priority, rule_id = poller.match_system_sender(JOBROUTER, subject)
    assert action == "tun", "Ein-/Austritte liest Sven — duerfen nicht abgelegt werden"
    assert priority == "mittel"
    assert rule_id == f"system_sender_exception:{keyword}"


def test_ausnahme_ist_case_insensitive():
    action, _, _ = poller.match_system_sender(JOBROUTER, "JOBROUTER: MITARBEITEREINTRITT VON X")
    assert action == "tun"


def test_fremder_absender_trifft_keine_regel():
    assert poller.match_system_sender("d.prestel@herbert.de", "AW: Saldenlisten") is None


def test_leerer_absender_trifft_keine_regel():
    assert poller.match_system_sender("", "irgendwas") is None


def test_absender_wird_normalisiert():
    """Gross-/Kleinschreibung und Leerzeichen duerfen die Regel nicht aushebeln."""
    assert poller.match_system_sender(f"  {JOBROUTER.upper()}  ", "Neue Anfrage") is not None


# ── Ablegen-Zweiteilung ─────────────────────────────────────────────────────

@pytest.fixture
def profile_db(tmp_path, monkeypatch):
    path = tmp_path / "sender_profile.db"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE sender_profile (
        sender_email TEXT PRIMARY KEY, sender_name TEXT, n_inbox INTEGER,
        n_replied INTEGER, reply_rate REAL, n_deleted INTEGER,
        verdict TEXT, route_folder TEXT, route_share REAL)""")
    conn.executemany("INSERT INTO sender_profile VALUES (?,?,?,?,?,?,?,?,?)", [
        ("newsletters-noreply@linkedin.com", "LinkedIn", 919, 0, 0.0, 0, "safe_archive", None, None),
        ("t.bak@herbert.de", "T. Bak", 142, 132, 0.93, 0, "conversational", None, None),
        ("neu@example.com", "Neu", 3, 0, 0.0, 0, "neutral", None, None),
    ])
    conn.commit()
    conn.close()
    monkeypatch.setattr(poller, "SENDER_PROFILE_DB", str(path))
    monkeypatch.setattr(poller, "_PROFILE_CACHE", {})
    return path


def test_safe_archive_absender_darf_archiviert_werden(profile_db):
    assert poller.may_auto_archive("newsletters-noreply@linkedin.com", "llm:Werbung") is True


def test_gespraechspartner_wird_nie_still_archiviert(profile_db):
    assert poller.may_auto_archive("t.bak@herbert.de", "llm:sieht nach Ablage aus") is False


def test_neutraler_absender_bleibt_vorschlag(profile_db):
    assert poller.may_auto_archive("neu@example.com", "llm:Newsletter?") is False


def test_unbekannter_absender_bleibt_vorschlag(profile_db):
    """Kein Profil vorhanden -> im Zweifel nicht verschieben."""
    assert poller.may_auto_archive("voellig@unbekannt.de", "llm:egal") is False


@pytest.mark.parametrize("rule_id", [
    "system_sender:jobrouter@intern.herbert.de",
    "newsletter_sender",
    "calendar_subject",
])
def test_deterministische_regeln_duerfen_immer_archivieren(profile_db, rule_id):
    """Regel-Urteile brauchen kein Absender-Profil — sie sind selbst der Beweis."""
    assert poller.may_auto_archive("voellig@unbekannt.de", rule_id) is True


def test_fehlendes_profil_blockiert_llm_ablage(tmp_path, monkeypatch):
    """Ohne Profil-Datei darf keine LLM-Entscheidung eine Mail verschieben."""
    monkeypatch.setattr(poller, "SENDER_PROFILE_DB", str(tmp_path / "fehlt.db"))
    monkeypatch.setattr(poller, "_PROFILE_CACHE", {})
    assert poller.may_auto_archive("egal@example.com", "llm:Werbung") is False
    # Deterministische Regeln funktionieren weiterhin
    assert poller.may_auto_archive("egal@example.com", "newsletter_sender") is True


def test_sender_verdict_cached(profile_db, monkeypatch):
    """Zweiter Aufruf darf die Datei nicht erneut oeffnen."""
    assert poller.sender_verdict("t.bak@herbert.de") == "conversational"
    monkeypatch.setattr(poller, "SENDER_PROFILE_DB", "/nicht/vorhanden.db")
    assert poller.sender_verdict("t.bak@herbert.de") == "conversational"
