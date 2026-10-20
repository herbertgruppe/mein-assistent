"""
Tests fuer den Rueckweg von einem einzelnen Vorgang zur Uebersicht.

Sven arbeitet die offenen Zuordnungen am Stueck ab. Bisher endete jeder
Vorgang in einer Sackgasse: nach dem Absenden stand eine Bestaetigung da,
und man musste von Hand zurueckblaettern. Bei 21 offenen Aufnahmen ist das
21 Mal derselbe Griff.

Die Logik sitzt im Frontend (ein Query-Parameter `weiter=uebersicht`), hier
geprueft wird deshalb, was serverseitig dazugehoert: dass die Seiten den
Parameter ueberhaupt annehmen und dass der Rueckweg verlinkt ist.
"""
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

STATIC = REPO_ROOT / "static"
TEMPLATES = REPO_ROOT / "templates"


def _lies(pfad):
    return pfad.read_text(encoding="utf-8")


# ── Die Uebersicht markiert ihre Links ──────────────────────────────────────

def test_uebersicht_haengt_den_hinweis_an():
    js = _lies(STATIC / "overview.js")
    assert "weiter=uebersicht" in js, (
        "Ohne diesen Hinweis weiss die Einzelseite nicht, dass sie "
        "zurueckkehren soll"
    )


def test_uebersicht_haengt_sauber_an_bestehende_parameter_an():
    """Ein Link mit ? darf nicht durch ein zweites ? zerstoert werden."""
    js = _lies(STATIC / "overview.js")
    assert 'indexOf("?") === -1' in js


# ── Die Zuordnungsseite kehrt zurueck ───────────────────────────────────────

def test_zuordnung_wertet_den_hinweis_aus():
    js = _lies(STATIC / "review_assign.js")
    assert "fertigMelden" in js
    assert "weiter" in js and "uebersicht" in js
    assert "/protokolle" in js


def test_zuordnung_nutzt_den_rueckweg_bei_freigabe_und_verwerfen():
    """
    Beide Wege enden fuer Sven gleich: der Vorgang ist erledigt und er will
    zum naechsten. Frueher blieb er bei beiden stehen.
    """
    js = _lies(STATIC / "review_assign.js")
    assert js.count("fertigMelden(") >= 3, (
        "Erwartet: Definition plus Aufruf bei Freigabe und bei Verwerfen"
    )
    # Die alten Sackgassen duerfen nicht zurueckbleiben
    assert "'<div class=\"hg-recording-title\">✅ Freigegeben</div>'" not in js
    assert "'<div class=\"hg-recording-title\">🗑 Verworfen</div>'" not in js


def test_zuordnung_bietet_auch_ohne_hinweis_einen_weg():
    """
    Wer den Link aus einer Nachricht oeffnet, soll nicht stranden.

    Das Ziel steht seit dem Reiter-Merken in einer Variablen: '/protokolle'
    plus optionalem Reiter-Hash.
    """
    js = _lies(STATIC / "review_assign.js")
    assert "var ziel = '/protokolle'" in js
    assert "'<a href=\"' + ziel + '\">" in js


# ── Der Review-Editor ebenso ────────────────────────────────────────────────

def test_editor_kehrt_zurueck_statt_auf_die_erfolgsseite():
    js = _lies(STATIC / "review.js")
    assert "weiter" in js and "/protokolle" in js
    # Die Erfolgsseite bleibt fuer alle anderen Wege erhalten
    assert "/success" in js


# ── Die Erfolgsseite ────────────────────────────────────────────────────────

def test_erfolgsseite_verlinkt_die_uebersicht():
    html = _lies(TEMPLATES / "review_success.html")
    assert "/protokolle" in html


def test_erfolgsseite_laedt_ihr_stylesheet_vom_richtigen_pfad():
    """
    Gemountet ist /review-static/ — /static/ gehoert Streamlit. Mit dem alten
    Pfad kam die Seite ohne jede Formatierung.
    """
    html = _lies(TEMPLATES / "review_success.html")
    assert "/review-static/review.css" in html
    assert 'href="/static/review.css"' not in html


# ── Kein Template laedt mehr vom falschen Pfad ──────────────────────────────

def test_kein_template_nutzt_den_streamlit_pfad():
    for datei in TEMPLATES.glob("*.html"):
        html = _lies(datei)
        treffer = re.findall(r'(?:href|src)="/static/[^"]+"', html)
        assert not treffer, f"{datei.name} laedt von /static/: {treffer}"
