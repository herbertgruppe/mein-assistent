"""
Tests fuer den Modus "SH entscheiden" der Mail-Triage (2026-10-30).

Anlass: Lenas Mail-Triage hatte 79 Asana-Aufgaben aus Mails angelegt. Ueber 20
davon waren reine Information (Personalverwaltung), viele bereits ueberholt.
Aufgaben, die niemand entschieden hat, sind zu grosszuegig. Neuer Ablauf:

  * Abgelegt wird nur, was sicher abgelegt werden kann.
  * Alles, worueber das Sprachmodell entschieden hat, bekommt "Lena: SH
    entscheiden" — sonst nichts.
  * Setzt Sven eine andere Lena-Kategorie, fuehrt Lena die Aktion aus.

Zu sichern ist vor allem: Die Automatik loest NICHTS aus (kein Entwurf, keine
Aufgabe, keine Wichtigkeit, kein Alarm), Svens Anweisung dagegen ALLES — auch bei
Mails, die laenger als sieben Tage auf ihn warten.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
poller = pytest.importorskip("lena_mail_triage_poller")


MAIL = {"message_id": "m1", "conversation_id": "", "subject": "Bitte um Rueckruf",
        "sender_name": "Erika Kundin", "sender_email": "kundin@extern.de",
        "body_preview": "Koennen Sie zurueckrufen?", "received_at": "2026-10-27T07:00:00Z",
        "to_emails": [], "cc_emails": []}


@pytest.fixture
def modus(monkeypatch):
    def setzen(entscheiden: bool, nur_ablegen: bool = False):
        monkeypatch.setattr(poller, "ENTSCHEIDEN", entscheiden)
        monkeypatch.setattr(poller, "NUR_ABLEGEN", nur_ablegen)
        monkeypatch.setattr(poller, "AKTIONEN_AKTIV", True)
    return setzen


# ── Die Entscheidungsfunktionen ───────────────────────────────────────────

@pytest.mark.parametrize("rule_id,regel", [
    ("sven_regel:Anthropic-Belege", True),
    ("system_sender:jobrouter@intern.herbert.de", True),
    ("system_sender_exception:Mitarbeitereintritt", True),
    ("newsletter_sender", True),
    ("calendar_subject", True),
    ("thread:sven_regel:DMARC", True),          # vom Vorgang uebernommen
    ("llm:antwort noetig", False),
    ("llm+memory:extern.de/frage", False),
    ("llm_failed_default", False),
    ("guard:aelter_als_28_tage | llm:x", False),
    ("thread:llm:Rueckruf", False),
])
def test_regel_oder_modell(rule_id, regel):
    assert poller.ist_regel_entscheidung(rule_id) is regel


@pytest.mark.parametrize("aktion", ["weiterleiten", "tun", "antworten", "warten", "recherchieren"])
def test_modellentscheidung_geht_an_sven(modus, aktion):
    modus(True)
    assert poller.zur_entscheidung_vorlegen(aktion, "llm:egal") is True


def test_ablegen_bleibt_scharf(modus):
    modus(True)
    assert poller.zur_entscheidung_vorlegen("ablegen", "llm:egal") is False
    assert poller.zur_entscheidung_vorlegen("ablegen", "newsletter_sender") is False


@pytest.mark.parametrize("rule_id", ["sven_regel:Belege", "system_sender:x", "calendar_subject"])
def test_regeln_bleiben_deterministisch(modus, rule_id):
    """Svens eigene Regeln (z. B. Belege an Laura weiterleiten) bleiben, wie er sie
    gesetzt hat — kein Sprachmodell diskutiert sie weg."""
    modus(True)
    assert poller.zur_entscheidung_vorlegen("weiterleiten", rule_id) is False


def test_ohne_modus_aendert_sich_nichts(modus):
    modus(False)
    assert poller.zur_entscheidung_vorlegen("tun", "llm:x") is False


def test_standard_ist_aus():
    """Ein vergessener Schalter darf das Verhalten nicht still aendern."""
    quelle = open(poller.__file__, encoding="utf-8").read()
    assert 'os.getenv("LENA_MAIL_TRIAGE_ENTSCHEIDEN", "0")' in quelle


def test_kategorie_ist_keine_aktion():
    """Sie darf nicht als Anweisung gelesen werden — sonst fuehre Lena ihre eigene
    Frage aus."""
    assert poller.KATEGORIE_ENTSCHEIDEN not in poller.KATEGORIE_ZU_AKTION
    assert poller._lena_kategorie([poller.KATEGORIE_ENTSCHEIDEN]) is None


def test_aktion_neben_der_frage_gilt():
    """Sven fuegt 'Lena: Tun' hinzu, ohne die Frage abzuwaehlen."""
    kats = [poller.KATEGORIE_ENTSCHEIDEN, "Lena: Tun"]
    assert poller._lena_kategorie(kats) == "Lena: Tun"
    assert poller._lena_kategorie(list(reversed(kats))) == "Lena: Tun"


# ── Der Zyklus ────────────────────────────────────────────────────────────

@pytest.fixture
def zyklus(monkeypatch):
    kategorisiert, wichtigkeit, alarme, konsequenzen, gelernt = [], [], [], [], []
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
    monkeypatch.setattr(poller, "_record_categorization", lambda *a, **k: gelernt.append(a))
    monkeypatch.setattr(poller, "_tg_alert", lambda *a, **k: alarme.append(a))
    monkeypatch.setattr(poller, "konsequenz_ausfuehren",
                        lambda *a, **k: konsequenzen.append(a) or {"art": None, "id": None,
                                                                    "stufe": None, "hinweis": None})
    monkeypatch.setattr(poller, "may_auto_archive", lambda *a, **k: True)
    monkeypatch.setattr(poller, "sender_verdict", lambda *a, **k: "neutral")
    monkeypatch.setattr(poller.time, "sleep", lambda s: None)

    def stellen(mails, entscheidung):
        monkeypatch.setattr(poller, "_fetch_inbox_for_triage", lambda: mails)
        monkeypatch.setattr(poller, "triage_mail",
                            entscheidung if callable(entscheidung) else (lambda *a, **k: entscheidung))
    return stellen, kategorisiert, wichtigkeit, alarme, konsequenzen, gelernt


def _modell(aktion, schritt, prio="mittel", regel="llm:test"):
    return (aktion, prio, regel, None, schritt, None)


@pytest.mark.parametrize("aktion,schritt", [("weiterleiten", 3), ("tun", 4), ("antworten", 5)])
def test_zyklus_modellentscheidung_wird_zur_frage(modus, zyklus, aktion, schritt):
    modus(True)
    stellen, kategorisiert, wichtigkeit, alarme, konsequenzen, gelernt = zyklus
    stellen([dict(MAIL, message_id="x1")], _modell(aktion, schritt, prio="hoch"))
    state = {}
    z = poller._poll_once(state)
    assert kategorisiert == [("x1", "entscheiden")]
    assert z["zur_entscheidung"] == 1
    assert z["categorized"] == 0
    assert not wichtigkeit, "Auch die Wichtigkeit waere ein Vorschlag"
    assert not alarme, "Auch bei Hoch-Prio kein Telegram-Alarm"
    assert not konsequenzen, "Weder Entwurf noch Aufgabe"
    assert not gelernt, "Nichts zu lernen: es gab keine Entscheidung"


def test_zyklus_die_mail_gilt_als_gesehen_und_wartet(modus, zyklus):
    modus(True)
    stellen, *_ = zyklus
    stellen([dict(MAIL, message_id="x1")], _modell("tun", 4))
    state = {}
    poller._poll_once(state)
    assert "x1" in state["processed_message_ids"]
    assert "x1" in state["entscheiden_offen"], "Wird gemerkt: Sie wartet auf Sven"


def test_zyklus_keine_phantomentscheidung(modus, zyklus):
    """Sonst vergliche die Korrektur-Erkennung Svens Kategorie mit einer
    Entscheidung, die Lena nie getroffen hat, und 'lernte' daraus."""
    modus(True)
    stellen, *_ = zyklus
    stellen([dict(MAIL, message_id="x1")], _modell("antworten", 5))
    state = {}
    poller._poll_once(state)
    assert "x1" not in (state.get("triage_results") or {})


def test_zyklus_ablegen_laeuft_wie_bisher(modus, zyklus):
    modus(True)
    stellen, kategorisiert, *_ = zyklus
    stellen([dict(MAIL, message_id="a1")], _modell("ablegen", 1, regel="newsletter_sender"))
    z = poller._poll_once({})
    assert kategorisiert == [("a1", "ablegen")]
    assert z["zur_entscheidung"] == 0


def test_zyklus_regel_weiterleiten_laeuft_wie_bisher(modus, zyklus):
    """Svens Regel 'Anthropic-Belege an Laura' ist KEINE Modellentscheidung."""
    modus(True)
    stellen, kategorisiert, _, _, konsequenzen, _ = zyklus
    stellen([dict(MAIL, message_id="r1")],
            ("weiterleiten", "mittel", "sven_regel:Anthropic-Belege", None, 3, "Laura"))
    z = poller._poll_once({})
    assert kategorisiert == [("r1", "weiterleiten")]
    assert z["zur_entscheidung"] == 0
    assert konsequenzen, "Die Regel loest ihre Weiterleitung weiter aus"


def test_zyklus_gemischter_posteingang(modus, zyklus):
    modus(True)
    stellen, kategorisiert, *_ = zyklus
    mails = [dict(MAIL, message_id=i) for i in ("a", "b", "c", "d")]
    antworten = iter([_modell("ablegen", 1, regel="calendar_subject"),
                      _modell("tun", 4), _modell("weiterleiten", 3), _modell("antworten", 5)])
    stellen(mails, lambda *a, **k: next(antworten))
    z = poller._poll_once({})
    assert kategorisiert == [("a", "ablegen"), ("b", "entscheiden"),
                             ("c", "entscheiden"), ("d", "entscheiden")]
    assert z["zur_entscheidung"] == 3


def test_zyklus_fehlgeschlagene_kategorie_wird_nicht_als_gesehen_gemerkt(monkeypatch, modus, zyklus):
    """Sonst bliebe die Mail unmarkiert im Posteingang und wuerde nie wieder angesehen."""
    modus(True)
    stellen, *_ = zyklus
    monkeypatch.setattr(poller, "_categorize_mail", lambda *a, **k: False)
    stellen([dict(MAIL, message_id="x1")], _modell("tun", 4))
    state = {}
    z = poller._poll_once(state)
    assert z["failed"] == 1
    assert "x1" not in state.get("processed_message_ids", [])
    assert "x1" not in (state.get("entscheiden_offen") or {})


def test_zyklus_ohne_modus_wie_bisher(modus, zyklus):
    modus(False)
    stellen, kategorisiert, *_ = zyklus
    stellen([dict(MAIL, message_id="x1")], _modell("antworten", 5))
    z = poller._poll_once({})
    assert kategorisiert == [("x1", "antworten")]
    assert z["zur_entscheidung"] == 0


def test_modus_hat_vorrang_vor_nur_ablegen(modus, zyklus):
    """Beide gesetzt: Es gilt der neue Ablauf, nicht die Pauschalsperre."""
    modus(True, nur_ablegen=True)
    stellen, kategorisiert, *_ = zyklus
    stellen([dict(MAIL, message_id="x1")], _modell("tun", 4))
    z = poller._poll_once({})
    assert kategorisiert == [("x1", "entscheiden")]
    assert z["nicht_ablegen_uebersprungen"] == 0


# ── Svens Anweisung ───────────────────────────────────────────────────────

@pytest.fixture
def anweisung(monkeypatch):
    ausgefuehrt = []
    monkeypatch.setattr(poller, "ANWEISUNGEN_AKTIV", True)
    monkeypatch.setattr(poller, "konsequenz_ausfuehren",
                        lambda mail, schritt, empf, state=None:
                        ausgefuehrt.append((mail["message_id"], schritt)) or {"art": "asana", "id": "g"})
    return ausgefuehrt


def _alt(tage):
    return (poller.datetime.now(poller.timezone.utc)
            - poller.timedelta(days=tage)).isoformat()


def _anweisen(mid, kategorie, state, tage, **extra):
    m = dict(MAIL, message_id=mid, from_name="E", from_email="kundin@extern.de",
             received_at=_alt(tage), categories=[kategorie])
    z = {}
    poller._anweisung_ausfuehren(m, mid, kategorie, state, {}, {}, z)
    return z


def test_anweisung_loest_die_aufgabe_aus(modus, anweisung):
    modus(True)
    state = {"entscheiden_offen": {"m1": "x"}}
    _anweisen("m1", "Lena: Tun", state, tage=1)
    assert anweisung == [("m1", 4)]


def test_wartende_mail_ist_nie_zu_alt(modus, anweisung):
    """Die 7-Tage-Grenze schuetzt vor dem Schwall beim ersten Lauf, nicht vor
    Svens eigener Arbeit: Eine Mail kann drei Wochen auf ihn warten."""
    modus(True)
    state = {"entscheiden_offen": {"m1": "x"}}
    _anweisen("m1", "Lena: Tun", state, tage=21)
    assert anweisung == [("m1", 4)]


def test_gegenprobe_fremde_alte_mail_bleibt_uebergangen(modus, anweisung):
    """Altbestand ohne 'SH entscheiden' wird weiter nicht rueckwirkend abgearbeitet."""
    modus(True)
    z = _anweisen("m2", "Lena: Tun", {"entscheiden_offen": {"m1": "x"}}, tage=21)
    assert anweisung == []
    assert z.get("anweisungen_uebergangen") == 1


def test_wartende_mail_wird_danach_vergessen(modus, anweisung):
    modus(True)
    state = {"entscheiden_offen": {"m1": "x"}}
    _anweisen("m1", "Lena: Weiterleiten", state, tage=2)
    assert "m1" not in state["entscheiden_offen"]


@pytest.mark.parametrize("kategorie,schritt", [
    ("Lena: Weiterleiten", 3), ("Lena: Tun", 4), ("Lena: Antworten", 5),
])
def test_jede_aktions_kategorie_loest_ihre_konsequenz_aus(modus, anweisung, kategorie, schritt):
    modus(True)
    _anweisen("m1", kategorie, {"entscheiden_offen": {"m1": "x"}}, tage=3)
    assert anweisung == [("m1", schritt)]


def test_pass_ueberspringt_die_frage_selbst(modus, monkeypatch, anweisung):
    """Die Kategorie 'SH entscheiden' allein loest im Korrektur-Durchlauf nichts aus."""
    modus(True)

    class Antwort:
        status_code = 200
        @staticmethod
        def json():
            return {"messages": [dict(MAIL, message_id="w1", received_at=_alt(1),
                                      categories=[poller.KATEGORIE_ENTSCHEIDEN],
                                      from_name="E", from_email="k@x.de")]}
    monkeypatch.setattr(poller.requests, "get", lambda *a, **k: Antwort())
    monkeypatch.setattr(poller, "_get_learning_db", lambda: None)
    state = {"entscheiden_offen": {"w1": "x"}}
    poller.korrekturen_verarbeiten(state)
    assert anweisung == []
    assert "w1" in state["entscheiden_offen"], "Sie wartet weiter"


def test_pass_fuehrt_svens_kategorie_aus(modus, monkeypatch, anweisung):
    modus(True)

    class Antwort:
        status_code = 200
        @staticmethod
        def json():
            return {"messages": [dict(MAIL, message_id="w1", received_at=_alt(20),
                                      categories=[poller.KATEGORIE_ENTSCHEIDEN, "Lena: Tun"],
                                      from_name="E", from_email="k@x.de")]}
    monkeypatch.setattr(poller.requests, "get", lambda *a, **k: Antwort())
    monkeypatch.setattr(poller, "_get_learning_db", lambda: None)
    state = {"entscheiden_offen": {"w1": "x"}}
    poller.korrekturen_verarbeiten(state)
    assert anweisung == [("w1", 4)]


def test_pass_schaut_weit_genug_zurueck(modus, monkeypatch, anweisung):
    """Mails, die auf Sven warten, liegen weiter hinten als die 80 neuesten."""
    modus(True)
    gesehen = {}

    class Antwort:
        status_code = 200
        @staticmethod
        def json():
            return {"messages": []}

    def holen(url, headers=None, params=None, timeout=None):
        gesehen.update(params or {})
        return Antwort()
    monkeypatch.setattr(poller.requests, "get", holen)
    monkeypatch.setattr(poller, "_get_learning_db", lambda: None)
    monkeypatch.setattr(poller, "KORREKTUR_LIMIT", 300)
    poller.korrekturen_verarbeiten({})
    assert gesehen["limit"] == 300


# ── Konsequenz-Sperre ─────────────────────────────────────────────────────

def test_nur_ablegen_allein_sperrt_weiter_alles(modus, monkeypatch):
    """Gegenprobe: Der alte Modus bleibt, was er war."""
    modus(False, nur_ablegen=True)
    angefasst = []
    monkeypatch.setattr(poller, "_asana_aufgabe", lambda *a, **k: angefasst.append(1) or "g")
    r = poller.konsequenz_ausfuehren(MAIL, 4, state={})
    assert r["art"] is None and not angefasst


def test_entscheiden_hebt_die_sperre_fuer_anweisungen_auf(modus, monkeypatch):
    modus(True, nur_ablegen=True)
    monkeypatch.setattr(poller, "_llm_aufgabe", lambda *a, **k: ("Titel", None, "Kontext"))
    monkeypatch.setattr(poller, "_asana_aufgabe", lambda *a, **k: "gid-1")
    monkeypatch.setattr(poller, "_volltext", lambda *a, **k: "Text")
    monkeypatch.setattr(poller, "_mail_archivieren", lambda mid: True)
    monkeypatch.setattr(poller, "_rueckfrage", lambda *a, **k: True)
    r = poller.konsequenz_ausfuehren(MAIL, 4, state={})
    assert r["art"] == "asana"


def test_aufgaben_landen_im_eingang():
    assert poller.ASANA_SECTION_NAME == "📥 Eingang"


# ── Merker ────────────────────────────────────────────────────────────────

def test_merker_waechst_nicht_unbegrenzt(monkeypatch):
    monkeypatch.setattr(poller, "MAX_ENTSCHEIDEN_MERKER", 5)
    state = {}
    for i in range(12):
        poller._entscheiden_merken(state, f"m{i}")
    assert len(state["entscheiden_offen"]) == 5
    assert "m11" in state["entscheiden_offen"], "Die neuesten bleiben"
    assert "m0" not in state["entscheiden_offen"]
