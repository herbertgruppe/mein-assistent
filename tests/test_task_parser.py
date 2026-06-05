#!/usr/bin/env python3
"""
Tests für den deterministischen Task-Parser in utils.protocol.extract_tasks_from_protocol_text.

Deckt die Protokoll-Formate ab, die der Meeting-Manager nach HBE-290 erzeugt
(inline-pro-TOP) plus die Legacy-Sammelblock-Variante.
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# utils/protocol.py importiert streamlit auf Modul-Ebene (für PDF-Helfer),
# der Parser selbst nutzt es nicht. Stub für Test-Umgebung ohne Streamlit:
if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = types.ModuleType("streamlit")

from utils.protocol import extract_tasks_from_protocol_text


INLINE_PROTOCOL = """# Protokoll: Wochenmeeting Technik
**Datum:** 02.06.2026
**Teilnehmer:** Sven Herbert, Anna Maier, Jan Klein

## Zusammenfassung
Wochenlicher Status zu Server-Migration und Onboarding.

## Besprochene Themen

### TOP 1: Server-Migration

Die alte Sun-Maschine muss bis Ende Q2 abgelöst werden, weil der Support endet.
Anna präsentierte den Migration-Plan, Hetzner-Cluster als Ziel.

**Entscheidungen:**
- Migration auf Hetzner-Cluster genehmigt
- Budget 5k freigegeben

**Aufgaben:**
- Sven Herbert: Hetzner-Account anlegen und SSH-Keys verteilen [2026-06-10]
- Anna Maier: Migration-Plan schreiben [Ende Juni]
- Jan Klein: Backup-Strategie definieren

### TOP 2: Mitarbeiter-Onboarding

HR-Checkliste ist veraltet.

**Aufgaben:**
- Klaus Müller: HR-Checkliste aktualisieren [15.06.2026]

## Offene Punkte
- DSGVO-Schulung steht aus
"""


LEGACY_PROTOCOL = """# Protokoll: Altes Format

## Besprochene Themen
Diverse Themen besprochen.

## Aufgaben & Nächste Schritte

- **Sven Herbert**: Hetzner-Account anlegen - Fällig: 2026-06-10
- **Anna Maier**: Migration-Plan schreiben - Fällig: 30.06.2026
- **[?]**: Backup-Strategie - Fällig: [?]
"""


def _run(label, protocol_text, expected_count, checks):
    print(f"\n[Test] {label}")
    tasks = extract_tasks_from_protocol_text(protocol_text)
    print(f"  -> {len(tasks)} Tasks extrahiert")
    for i, t in enumerate(tasks):
        print(f"     [{i}] assignee={t['assignee']!r} due={t['due_date']!r} title={t['title']!r}")

    assert len(tasks) == expected_count, \
        f"Erwartet {expected_count} Tasks, bekam {len(tasks)}"

    for idx, expected in checks.items():
        for key, val in expected.items():
            got = tasks[idx].get(key)
            assert got == val, \
                f"Task[{idx}].{key}: erwartet {val!r}, bekam {got!r}"
    print("  OK")


def test_inline_pro_top():
    _run(
        "Inline-pro-TOP (HBE-290 Format)",
        INLINE_PROTOCOL,
        expected_count=4,
        checks={
            0: {"assignee": "Sven Herbert", "due_date": "2026-06-10"},
            1: {"assignee": "Anna Maier", "due_date": None},
            2: {"assignee": "Jan Klein", "due_date": None},
            3: {"assignee": "Klaus Müller", "due_date": "2026-06-15"},
        },
    )
    tasks = extract_tasks_from_protocol_text(INLINE_PROTOCOL)
    assert "Ende Juni" in tasks[1]["description"], \
        f"Unparsbares Datum sollte in description landen, bekam: {tasks[1]['description']!r}"


def test_legacy_sammelblock():
    _run(
        "Legacy-Sammelblock (** + Fällig)",
        LEGACY_PROTOCOL,
        expected_count=3,
        checks={
            0: {"assignee": "Sven Herbert", "due_date": "2026-06-10"},
            1: {"assignee": "Anna Maier", "due_date": "2026-06-30"},
            # [?]-Platzhalter wird zu assignee=None, Task bleibt aber sichtbar
            # damit der User die offene Verantwortung sieht.
            2: {"assignee": None, "due_date": None, "title": "Backup-Strategie"},
        },
    )


def test_empty_protocol():
    print("\n[Test] Leeres Protokoll")
    assert extract_tasks_from_protocol_text("") == []
    assert extract_tasks_from_protocol_text(None) == []
    print("  OK")


def test_no_task_block():
    print("\n[Test] Protokoll ohne Aufgaben-Block")
    text = "# Meeting\nKein Task-Block hier.\n## Themen\n- Punkt A diskutiert\n"
    assert extract_tasks_from_protocol_text(text) == []
    print("  OK")


def test_llm_arg_ignored():
    print("\n[Test] llm-Argument wird akzeptiert + ignoriert (Backwards-Compat)")
    tasks_with_llm = extract_tasks_from_protocol_text(INLINE_PROTOCOL, llm="anything")
    tasks_without = extract_tasks_from_protocol_text(INLINE_PROTOCOL)
    assert tasks_with_llm == tasks_without
    print("  OK")


def test_bullet_with_colon_in_task():
    print("\n[Test] Task-Text mit Doppelpunkt (Split nur am ERSTEN ':')")
    text = "**Aufgaben:**\n- Sven Herbert: Server: alt -> neu vorbereiten [2026-07-01]\n"
    tasks = extract_tasks_from_protocol_text(text)
    assert len(tasks) == 1
    assert tasks[0]["assignee"] == "Sven Herbert"
    assert "Server: alt" in tasks[0]["description"]
    assert tasks[0]["due_date"] == "2026-07-01"
    print("  OK")


def test_placeholder_assignee():
    print("\n[Test] Platzhalter [?] wird zu None gemappt")
    text = "**Aufgaben:**\n- [?]: Verantwortung klären [2026-07-01]\n"
    tasks = extract_tasks_from_protocol_text(text)
    assert len(tasks) == 1
    assert tasks[0]["assignee"] is None
    assert tasks[0]["due_date"] == "2026-07-01"
    print("  OK")


def test_dr_title_in_name():
    print("\n[Test] Akademischer Titel im Namen bleibt erhalten")
    text = "**Aufgaben:**\n- Dr. Sven Herbert: Vertrag prüfen [2026-07-01]\n"
    tasks = extract_tasks_from_protocol_text(text)
    assert len(tasks) == 1
    assert tasks[0]["assignee"] == "Dr. Sven Herbert"
    print("  OK")


def test_title_truncation():
    print("\n[Test] Title wird auf 80 Zeichen gekürzt mit Ellipsis")
    long_task = "x" * 100
    text = f"**Aufgaben:**\n- Sven Herbert: {long_task}\n"
    tasks = extract_tasks_from_protocol_text(text)
    assert len(tasks) == 1
    assert len(tasks[0]["title"]) <= 80
    assert tasks[0]["title"].endswith("...")
    assert tasks[0]["description"] == long_task
    print("  OK")


if __name__ == "__main__":
    test_inline_pro_top()
    test_legacy_sammelblock()
    test_empty_protocol()
    test_no_task_block()
    test_llm_arg_ignored()
    test_bullet_with_colon_in_task()
    test_placeholder_assignee()
    test_dr_title_in_name()
    test_title_truncation()
    print("\nAlle Tests bestanden.")
