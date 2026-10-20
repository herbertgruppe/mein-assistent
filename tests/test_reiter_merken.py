"""
Tests dafuer, dass die Uebersicht den Reiter behaelt.

Sven arbeitet die Freigaben am Stueck ab. Nach jeder bearbeiteten
Besprechung sprang die Uebersicht zurueck auf den ersten Reiter
("Zuordnen") statt auf den, aus dem er kam. Bei 41 Entwuerfen sind das
41 zusaetzliche Klicks.

Der Reiter reist deshalb im Link mit: `?weiter=uebersicht&reiter=freigabe`,
und die Uebersicht oeffnet ihn beim Zurueckkommen.
"""
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

STATIC = REPO_ROOT / "static"


def _js(name):
    return (STATIC / name).read_text(encoding="utf-8")


# ── Die Uebersicht gibt den Reiter mit ──────────────────────────────────────

def test_links_tragen_ihren_reiter():
    js = _js("overview.js")
    assert 'eintrag(e, true, "zuordnung")' in js
    assert 'eintrag(e, true, "freigabe")' in js


def test_reiter_landet_im_link():
    js = _js("overview.js")
    assert '"&reiter=" + reiter' in js


# ── Die Uebersicht stellt ihn wieder her ────────────────────────────────────

def test_reiter_wird_aus_der_adresse_gelesen():
    js = _js("overview.js")
    assert "function gewuenschterReiter" in js
    assert 'get("reiter")' in js, "Parameter nach dem Rueckweg"
    assert "window.location.hash" in js, "Hash fuer ein spaeteres Neuladen"


def test_unbekannter_reiter_wird_abgewiesen():
    """
    Ein Wert aus der Adresszeile darf nicht ungeprueft in einen Selektor
    wandern.
    """
    js = _js("overview.js")
    assert 'REITER = ["zuordnung", "freigabe", "fertig"]' in js
    assert "REITER.indexOf(ziel) === -1" in js


def test_wechsel_wird_in_der_adresse_festgehalten():
    """Damit ein Neuladen im selben Reiter bleibt."""
    js = _js("overview.js")
    assert "replaceState" in js


def test_zurueck_knopf_wird_nicht_zugemuellt():
    """
    pushState wuerde jeden Reiterwechsel in die Historie schreiben — der
    Zurueck-Knopf fuehrte dann durch Zwischenschritte statt zur Vorseite.

    Geprueft wird der Aufruf, nicht das Wort: Im Kommentar daneben steht
    bewusst, warum replaceState gewaehlt wurde.
    """
    js = _js("overview.js")
    assert "history.pushState" not in js
    assert "history.replaceState" in js


def test_umschalten_erst_nach_dem_befuellen():
    """Vorher sind die Listen leer, der Reiter saehe leer aus."""
    js = _js("overview.js")
    aufruf = js.index("var ziel = gewuenschterReiter();")
    assert js.index('fuellen("liste-fertig"') < aufruf


def test_leerer_erster_reiter_wird_uebersprungen():
    """
    Sind alle Zuordnungen erledigt, aber Entwuerfe offen, ist der erste
    Reiter nur eine leere Seite.
    """
    js = _js("overview.js")
    assert 'reiterWechsel("freigabe")' in js


# ── Beide Einzelseiten reichen ihn zurueck ──────────────────────────────────

@pytest.mark.parametrize("datei", ["review.js", "review_assign.js"])
def test_einzelseite_nimmt_den_reiter_mit(datei):
    js = _js(datei)
    assert "get('reiter')" in js, f"{datei} verwirft den Reiter"
    assert "'#' + encodeURIComponent(reiter)" in js


def test_editor_springt_in_den_richtigen_reiter():
    """Svens Fall: nach dem Freigeben zurueck zu den Freigaben."""
    js = _js("review.js")
    stelle = js[js.index("dirty = false;"):]
    assert "/protokolle" in stelle
    assert "reiter" in stelle


def test_zuordnungsseite_ebenso():
    js = _js("review_assign.js")
    assert "var ziel = '/protokolle'" in js
    # Auch der Link fuer den Fall ohne automatischen Ruecksprung
    assert "'<a href=\"' + ziel + '\">" in js


@pytest.mark.parametrize("datei", ["review.js", "review_assign.js"])
def test_ohne_reiter_bleibt_es_bei_der_uebersicht(datei):
    """
    Wer den Link aus einer Nachricht oeffnet, hat keinen Reiter im Pfad —
    dann darf kein kaputtes Ziel entstehen.
    """
    js = _js(datei)
    assert re.search(r"reiter \? '#' \+ encodeURIComponent\(reiter\) : ''", js), (
        f"{datei}: fehlender Reiter muss sauber zu '/protokolle' fuehren"
    )
