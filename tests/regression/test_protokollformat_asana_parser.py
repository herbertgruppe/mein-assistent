"""
Das Protokollformat muss zum Asana-Parser passen.

Mara erzeugt das Protokoll-Markdown, `extract_tasks_from_protocol_text`
zerlegt es in Asana-Unteraufgaben. Beide Seiten sind nur durch eine
Formatkonvention verbunden — ändert sich die eine, fallen still die
Unteraufgaben weg. Das Protokoll sieht dann vollständig aus, in Asana fehlt
die Hälfte.

Dieser Test hält das Format fest, das in Maras SKILL_STAGE2.md steht:

    ## Auf einen Blick        Summary mit Kernentscheidungen
    ## 1. <Thema>             pro TOP: Kurztext, Entscheidung, Aufgaben
    ## Offene Punkte          Unklarheiten am Schluss

Wenn dieser Test bricht, muss Maras Skill-Datei mitgeändert werden — und
umgekehrt.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# utils/protocol.py importiert streamlit auf Modulebene; für den reinen
# Parser ist es irrelevant und in der CI nicht installiert.
sys.modules.setdefault("streamlit", types.ModuleType("streamlit"))

from utils.protocol import extract_tasks_from_protocol_text  # noqa: E402


PROTOKOLL = """# BL-Besprechung HRN — 17.09.2026

## Auf einen Blick

Schwerpunkt war die Zeiterfassung in myTGA. Der Rollout startet im Oktober
mit zwei Pilot-Niederlassungen.

**Kernentscheidungen:**
- Rollout in zwei Stufen statt auf einmal
- Budget für Zusatzlizenzen freigegeben

## Teilnehmer

[[Sven Herbert]], [[Thomas Winzer]], [[Lev Keimes]]

---

## 1. Zeiterfassung myTGA

Der Pilot läuft seit August in Nauheim.

**Entscheidung:** Rollout in zwei Stufen, Start Oktober mit HRN und HBO.

**Aufgaben:**
- Thomas Winzer: Rollout-Plan für HRN erstellen [2026-10-05]
- Lev Keimes: Kurzvideos aufnehmen [2026-09-30]

## 2. Zusatzlizenzen

Für die zweite Stufe fehlen 40 Lizenzen.

**Entscheidung:** Budget freigegeben.

**Aufgaben:**
- Sven Herbert: Bestellung freigeben [2026-09-26]

## 3. Schulungskonzept

Diskussion über einen Präsenztag. Kein Beschluss.

---

## Offene Punkte

- Unklar, ob die Bauleiter eigene Zugänge brauchen
- Ein Sprecher konnte nicht zugeordnet werden
"""


class TestZielformat:
    def test_alle_aufgaben_werden_erkannt(self):
        tasks = extract_tasks_from_protocol_text(PROTOKOLL)
        assert len(tasks) == 3

    def test_zuweisung_und_faelligkeit(self):
        tasks = extract_tasks_from_protocol_text(PROTOKOLL)
        by_assignee = {t["assignee"]: t for t in tasks}

        assert set(by_assignee) == {"Thomas Winzer", "Lev Keimes", "Sven Herbert"}
        assert by_assignee["Thomas Winzer"]["due_date"] == "2026-10-05"
        assert by_assignee["Lev Keimes"]["due_date"] == "2026-09-30"
        assert by_assignee["Sven Herbert"]["due_date"] == "2026-09-26"

    def test_aufgaben_aus_mehreren_tops(self):
        """Der Parser muss über Themen hinweg sammeln, nicht beim ersten aufhören."""
        titles = [t["title"] for t in extract_tasks_from_protocol_text(PROTOKOLL)]
        assert "Rollout-Plan für HRN erstellen" in titles
        assert "Bestellung freigeben" in titles

    def test_kernentscheidungen_sind_keine_aufgaben(self):
        """
        Das Summary listet Entscheidungen als Bullets. Würden die als
        Aufgaben gelesen, entstünden in Asana Geister-Unteraufgaben.
        """
        titles = [t["title"] for t in extract_tasks_from_protocol_text(PROTOKOLL)]
        assert not any("Rollout in zwei Stufen statt" in t for t in titles)
        assert not any("Budget für Zusatzlizenzen" in t for t in titles)

    def test_offene_punkte_sind_keine_aufgaben(self):
        """Unklarheiten sind Hinweise an Sven, keine Arbeitsaufträge."""
        titles = [t["title"] for t in extract_tasks_from_protocol_text(PROTOKOLL)]
        assert not any("Bauleiter eigene Zugänge" in t for t in titles)
        assert not any("Sprecher konnte nicht" in t for t in titles)

    def test_entscheidungszeile_beendet_keinen_aufgabenblock_faelschlich(self):
        """
        `**Entscheidung:**` steht VOR `**Aufgaben:**`. Eine fett gesetzte Zeile
        beendet im Parser einen laufenden Block — das darf den nachfolgenden
        Aufgabenblock nicht verschlucken.
        """
        tasks = extract_tasks_from_protocol_text(PROTOKOLL)
        assert len(tasks) == 3

    def test_thema_ohne_aufgaben_stoert_nicht(self):
        """TOP 3 hat keinen Aufgabenblock — das darf nichts durcheinanderbringen."""
        tasks = extract_tasks_from_protocol_text(PROTOKOLL)
        assert all(t["assignee"] for t in tasks)


class TestFormatgrenzen:
    def test_ohne_datum_bleibt_aufgabe_erhalten(self):
        text = (
            "## 1. Thema\n\n**Aufgaben:**\n"
            "- Sven Herbert: Etwas ohne Frist erledigen\n"
        )
        tasks = extract_tasks_from_protocol_text(text)
        assert len(tasks) == 1
        assert tasks[0]["due_date"] is None

    def test_deutsches_datumsformat(self):
        text = (
            "## 1. Thema\n\n**Aufgaben:**\n"
            "- Sven Herbert: Angebot prüfen [30.09.2026]\n"
        )
        tasks = extract_tasks_from_protocol_text(text)
        assert tasks[0]["due_date"] == "2026-09-30"

    def test_leeres_protokoll(self):
        assert extract_tasks_from_protocol_text("") == []
