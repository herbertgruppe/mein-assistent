"""HBE-290 smoke test: verify new inline-per-TOP protocol prompt.

Runs the exact system_prompt from utils/protocol.py::extract_protocol_from_transcript_streaming
against a synthetic Herbert-Gruppe-style meeting transcript using the Anthropic SDK
directly (avoids the langchain dep).

Stand-in for the 01.06. Gerätewechsel transcript which lives in Sven's local Obsidian vault
and is not present in this workspace.

Pass criteria:
- Output contains multiple "**Aufgaben:**" blocks (one per TOP), not a single Sammelblock at the end
- Output contains "**Entscheidungen:**" inline under TOPs
- Task lines use "- Vorname Nachname:" format (no "**...**")
"""
import os
import re
import sys
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ast
import textwrap


def load_system_prompt() -> str:
    """Extract the system_prompt literal from utils/protocol.py without importing the module
    (which would pull streamlit / langchain)."""
    src = (ROOT / "utils" / "protocol.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "extract_protocol_from_transcript_streaming":
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "system_prompt" for t in stmt.targets
                ):
                    return ast.literal_eval(stmt.value)
    raise RuntimeError("system_prompt not found")


SYNTHETIC_TRANSCRIPT = textwrap.dedent("""\
    [00:00] Stefan Herbert: Hallo zusammen, danke fürs Kommen. Heute geht es um zwei Themen: den Gerätewechsel
    bei der SH-Geschäftsstelle West und das Thema Asana-Onboarding für die neuen Auszubildenden.

    [00:02] Marc Lehmann: Guten Morgen. Beim Gerätewechsel: wir haben aktuell noch 18 alte Lenovo T480 im
    Umlauf bei SWA und 12 bei SH. Die müssen bis Ende Juli ausgetauscht sein, weil der Support für die
    Windows-10-Image-Linie wegfällt.

    [00:04] Sandra Walter: Die neuen Geräte sind Lenovo T14s Gen 4 mit Windows 11. Beschaffung läuft über
    Bechtle, Liefertermin ist KW 26. Pro Gerät sind das 1.380 Euro netto.

    [00:06] Stefan Herbert: Ok, also Beschluss: wir rollen den Wechsel auf T14s Gen 4 aus, Budget 30 Geräte
    mal 1.380 Euro, das sind 41.400 Euro netto. Marc, du koordinierst mit Bechtle?

    [00:07] Marc Lehmann: Ja, ich melde mich heute noch bei Bechtle und gebe die Bestellung raus. Bis
    Freitag der 06.06. habe ich die Auftragsbestätigung.

    [00:09] Sandra Walter: Ich kümmere mich um den Rollout-Plan. Ich brauche bis Mittwoch 11.06. die
    finale Liste der betroffenen User von Marc.

    [00:10] Stefan Herbert: Jens Krüger sollte das Image vorbereiten, oder?

    [00:11] Marc Lehmann: Genau, Jens Krüger macht das Image bis 20.06. fertig — dann haben wir noch Puffer
    für Tests vor dem Rollout.

    [00:14] Stefan Herbert: Gut, Thema zwei: Asana-Onboarding für die Azubis. Wir haben drei neue Azubis
    ab 01.08., die alle Zugriff auf Asana brauchen.

    [00:16] Sandra Walter: Wir brauchen drei zusätzliche Asana-Lizenzen. Die kosten 10,99 Euro pro User pro
    Monat im Business-Plan. Außerdem brauchen wir ein Onboarding-Template, das wir bisher noch nicht haben.

    [00:18] Stefan Herbert: Beschluss: drei neue Lizenzen werden bestellt. Sandra, kannst du das Template
    bauen?

    [00:19] Sandra Walter: Ja, ich erstelle das Onboarding-Template bis 15.07. — dann haben die Azubis am
    01.08. direkt einen sauberen Workspace.

    [00:21] Marc Lehmann: Eine offene Frage ist noch, ob wir die Azubi-Accounts in eine eigene Asana-Org
    oder in die Hauptorg packen. Das müssen wir noch klären.

    [00:22] Stefan Herbert: Ok, dann nehmen wir das als offenen Punkt mit. Tschüss zusammen, schönen Tag.
    """)


def main() -> int:
    prompt = load_system_prompt()
    print("=== system_prompt (first 400 chars) ===")
    print(prompt[:400])
    print("...\n")

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4000,
        system=prompt,
        messages=[
            {
                "role": "user",
                "content": (
                    "# Meeting: Gerätewechsel SH/SWA + Azubi-Asana-Onboarding\n"
                    "**Datum:** 02.06.2026\n"
                    "**Teilnehmer:** Stefan Herbert, Marc Lehmann, Sandra Walter\n\n"
                    "## Transkript\n"
                    f"{SYNTHETIC_TRANSCRIPT}\n\n"
                    "---\nErstelle jetzt das strukturierte Besprechungsprotokoll:"
                ),
            }
        ],
    )

    output = "".join(b.text for b in resp.content if hasattr(b, "text"))
    print("=== model output ===")
    print(output)
    print("\n=== analysis ===")

    aufgaben_count = len(re.findall(r"\*\*Aufgaben:?\*\*", output))
    entscheidungen_count = len(re.findall(r"\*\*Entscheidungen:?\*\*", output))
    legacy_collector = bool(
        re.search(r"(?im)^#+\s*(Aufgaben & N(ä|ae)chste Schritte|Weitere Schritte)\b", output)
    )
    bold_name_task_lines = re.findall(r"^- \*\*[^*]+\*\*\s*:", output, flags=re.MULTILINE)
    plain_name_task_lines = re.findall(r"^- [A-ZÄÖÜ][\wäöüß]+ [A-ZÄÖÜ][\wäöüß]+:", output, flags=re.MULTILINE)

    print(f"  **Aufgaben:** blocks         : {aufgaben_count}")
    print(f"  **Entscheidungen:** blocks   : {entscheidungen_count}")
    print(f"  Legacy 'Weitere Schritte'    : {legacy_collector}")
    print(f"  Bold-name task lines (legacy): {len(bold_name_task_lines)}")
    print(f"  Plain 'Vorname Nachname:'    : {len(plain_name_task_lines)}")

    fail = []
    if aufgaben_count < 2:
        fail.append(f"expected ≥2 **Aufgaben:** blocks, got {aufgaben_count}")
    if legacy_collector:
        fail.append("output still contains legacy 'Weitere Schritte' / 'Aufgaben & Nächste Schritte' collector")
    if bold_name_task_lines:
        fail.append(f"output still uses legacy '- **Name**:' task format ({len(bold_name_task_lines)} lines)")
    if not plain_name_task_lines:
        fail.append("output does not use new '- Vorname Nachname:' task format")

    if fail:
        print("\nFAIL:")
        for f in fail:
            print(f"  - {f}")
        return 1
    print("\nPASS: inline-pro-TOP layout confirmed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
