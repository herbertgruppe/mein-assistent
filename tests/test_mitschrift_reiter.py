"""
Tests fuer den Reiter „Mitschreiben" auf der Protokoll-Uebersicht.

Sven wollte einen Ort, von dem aus sich die Live-Mitschrift starten
laesst — und spaeter Programm und Spracherkennungsmodell herunterladen.
Die Uebersichtsseite ist dieser Ort; sie heisst ohnehin schon
"Herbert Gruppe – Protokolle".

Der Reiter ist bewusst **statisch**: Er holt keine Daten und prueft
nicht, ob das Programm laeuft. Ein `fetch` von https:// auf
http://127.0.0.1 gilt je nach Browser als Mixed Content und wird
blockiert — ein normaler Link dorthin funktioniert dagegen. Eine
Zustandsanzeige, die manchmal faelschlich "laeuft nicht" sagt, waere
schlechter als keine.
"""
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
HTML = (WURZEL / "templates" / "overview.html").read_text(encoding="utf-8")
JS = (WURZEL / "static" / "overview.js").read_text(encoding="utf-8")
CSS = (WURZEL / "static" / "overview.css").read_text(encoding="utf-8")


class TestReiterVorhanden:
    def test_reiter_und_panel_existieren(self):
        assert 'data-ziel="mitschreiben"' in HTML
        assert 'id="panel-mitschreiben"' in HTML

    def test_javascript_kennt_den_reiter(self):
        """Ohne Eintrag in REITER laesst sich der Reiter nicht oeffnen."""
        zeile = next(z for z in JS.splitlines() if "var REITER" in z)
        assert "mitschreiben" in zeile

    def test_panel_hat_passende_id(self):
        """reiterWechsel sucht 'panel-' + ziel — die IDs muessen passen."""
        assert 'panels[i].id === "panel-" + ziel' in JS


class TestStartweg:
    def test_link_auf_die_lokale_mitschrift(self):
        """Der Port muss zum Standardport von mitschrift.py passen."""
        assert "http://127.0.0.1:8700/" in HTML

    def test_link_statt_fetch(self):
        """
        Eine Zustandsabfrage per fetch wuerde am Mixed-Content-Schutz
        scheitern. Wer sie spaeter einbaut, soll hier stolpern.
        """
        panel = HTML.split('id="panel-mitschreiben"')[1].split("</section>")[0]
        assert "fetch(" not in panel

    def test_startanleitung_nennt_die_startdatei(self):
        assert "Besprechung starten" in HTML


class TestHinweisePflicht:
    def test_datenschutz_hinweis_steht_drin(self):
        """
        Vor einer Ausweitung ueber Svens eigene Termine hinaus muessen
        Mitarbeitervertretung und Datenschutz einbezogen werden. Das
        gehoert auf die Seite, nicht nur ins Konzept.
        """
        assert "Mitarbeitervertretung" in HTML
        assert "Datenschutz" in HTML

    def test_sagt_dass_der_ton_auf_dem_geraet_bleibt(self):
        panel = HTML.split('id="panel-mitschreiben"')[1].split("</section>")[0]
        assert "bleiben dort" in panel or "bleibt" in panel


class TestStilVorhanden:
    def test_klassen_sind_definiert(self):
        for klasse in (".hg-btn-gross", ".hg-mitschrift-start", ".hg-schritte"):
            assert klasse in CSS, f"{klasse} fehlt in overview.css"
