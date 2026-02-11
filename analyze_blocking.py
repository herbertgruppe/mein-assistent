#!/usr/bin/env python3
"""
Nicht-interaktive Code-Analyse für UI-Blocking Probleme
Analysiert app.py und findet problematische Code-Stellen
"""

import re
import os
from datetime import datetime
from pathlib import Path

def analyze_file(filepath):
    """Analysiert eine Python-Datei auf Blocking-Probleme"""

    if not os.path.exists(filepath):
        return None

    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    results = {
        'file': filepath,
        'total_lines': len(lines),
        'issues': [],
        'spinners': [],
        'invoke_calls': [],
        'blocking_patterns': []
    }

    # Suche nach problematischen Patterns
    in_spinner_block = False
    spinner_start_line = 0

    for i, line in enumerate(lines, 1):
        # Suche st.spinner
        if 'st.spinner' in line:
            results['spinners'].append({
                'line': i,
                'code': line.strip()
            })
            in_spinner_block = True
            spinner_start_line = i

        # Suche invoke calls
        if '.invoke(' in line and 'llm' in line.lower():
            results['invoke_calls'].append({
                'line': i,
                'code': line.strip(),
                'in_spinner': in_spinner_block
            })

            # Kritisch: invoke in spinner
            if in_spinner_block:
                results['issues'].append({
                    'severity': 'CRITICAL',
                    'line': i,
                    'type': 'blocking_llm_call',
                    'description': f'LLM invoke() innerhalb st.spinner (Start: Zeile {spinner_start_line})',
                    'code': line.strip()
                })

        # Suche while-Schleifen mit invoke
        if 'while' in line and 'iteration' in line:
            # Prüfe nächste 20 Zeilen auf invoke
            for j in range(i, min(i+20, len(lines))):
                if '.invoke(' in lines[j] and 'llm' in lines[j].lower():
                    results['issues'].append({
                        'severity': 'HIGH',
                        'line': j+1,
                        'type': 'loop_blocking',
                        'description': f'LLM invoke() in Schleife (Zeile {i})',
                        'code': lines[j].strip()
                    })
                    break

        # Ende von Code-Blöcken erkennen (grob)
        if in_spinner_block and line.strip() and not line.startswith(' ') and not line.startswith('\t'):
            in_spinner_block = False

    return results


def print_report(results):
    """Gibt einen formatierten Report aus"""

    print("=" * 80)
    print("🔍 UI-BLOCKING ANALYSE REPORT")
    print("=" * 80)
    print(f"\nDatei: {results['file']}")
    print(f"Zeilen: {results['total_lines']}")
    print(f"Zeitpunkt: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("\n" + "=" * 80)

    # Zusammenfassung
    print("\n📊 ZUSAMMENFASSUNG")
    print("-" * 80)
    print(f"st.spinner Aufrufe:     {len(results['spinners'])}")
    print(f"LLM invoke() Aufrufe:   {len(results['invoke_calls'])}")
    print(f"Kritische Probleme:     {len([i for i in results['issues'] if i['severity'] == 'CRITICAL'])}")
    print(f"Hohe Priorität:         {len([i for i in results['issues'] if i['severity'] == 'HIGH'])}")

    # Probleme
    if results['issues']:
        print("\n" + "=" * 80)
        print("🚨 GEFUNDENE PROBLEME")
        print("=" * 80)

        for idx, issue in enumerate(results['issues'], 1):
            severity_icon = "🔴" if issue['severity'] == 'CRITICAL' else "🟡"
            print(f"\n{severity_icon} Problem #{idx} - {issue['severity']}")
            print(f"   Zeile:        {issue['line']}")
            print(f"   Typ:          {issue['type']}")
            print(f"   Beschreibung: {issue['description']}")
            print(f"   Code:         {issue['code'][:100]}")
    else:
        print("\n✅ Keine kritischen Probleme gefunden!")

    # Details: st.spinner
    if results['spinners']:
        print("\n" + "=" * 80)
        print("📍 ST.SPINNER AUFRUFE")
        print("=" * 80)
        for spinner in results['spinners']:
            print(f"   Zeile {spinner['line']:5d}: {spinner['code'][:70]}")

    # Details: invoke calls
    if results['invoke_calls']:
        print("\n" + "=" * 80)
        print("🤖 LLM INVOKE AUFRUFE")
        print("=" * 80)
        blocking = [c for c in results['invoke_calls'] if c['in_spinner']]
        non_blocking = [c for c in results['invoke_calls'] if not c['in_spinner']]

        if blocking:
            print(f"\n   🔴 BLOCKIEREND (in st.spinner): {len(blocking)}")
            for call in blocking[:10]:  # Zeige max 10
                print(f"      Zeile {call['line']:5d}: {call['code'][:70]}")

        if non_blocking:
            print(f"\n   ✅ NICHT-BLOCKIEREND: {len(non_blocking)}")
            for call in non_blocking[:5]:  # Zeige max 5
                print(f"      Zeile {call['line']:5d}: {call['code'][:70]}")

    print("\n" + "=" * 80)
    print("💡 EMPFEHLUNGEN")
    print("=" * 80)

    if results['issues']:
        print("\n🔧 SOFORT BEHEBEN:")
        print("   1. Ersetze st.spinner() mit st.status()")
        print("   2. Füge st.write() Updates nach jedem invoke() hinzu")
        print("   3. Zeige Tool-Ausführungen explizit an")
        print("\n📖 Siehe: BLOCKING_FIX_ANLEITUNG.md für Details")
    else:
        print("\n✅ Code sieht gut aus!")
        print("   Keine offensichtlichen Blocking-Probleme gefunden.")

    print("\n" + "=" * 80)
    print("🚀 NÄCHSTE SCHRITTE")
    print("=" * 80)
    print("\n1. Interaktive Tests im Browser:")
    print("   source venv/bin/activate")
    print("   streamlit run test_app_blocking_isolated.py")
    print("\n2. Vollständige Diagnose:")
    print("   streamlit run test_ui_blocking.py")
    print("\n3. Fix anwenden:")
    print("   Siehe BLOCKING_FIX_ANLEITUNG.md")
    print("\n" + "=" * 80 + "\n")


def save_report(results, output_file):
    """Speichert Report als Markdown"""

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(f"# UI-Blocking Analyse Report\n\n")
        f.write(f"**Generiert:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"**Datei:** `{results['file']}`\n\n")
        f.write(f"**Zeilen:** {results['total_lines']}\n\n")

        f.write("## Zusammenfassung\n\n")
        f.write(f"- `st.spinner` Aufrufe: **{len(results['spinners'])}**\n")
        f.write(f"- LLM `invoke()` Aufrufe: **{len(results['invoke_calls'])}**\n")
        f.write(f"- Kritische Probleme: **{len([i for i in results['issues'] if i['severity'] == 'CRITICAL'])}**\n")
        f.write(f"- Hohe Priorität: **{len([i for i in results['issues'] if i['severity'] == 'HIGH'])}**\n\n")

        if results['issues']:
            f.write("## 🚨 Gefundene Probleme\n\n")
            for idx, issue in enumerate(results['issues'], 1):
                severity_icon = "🔴" if issue['severity'] == 'CRITICAL' else "🟡"
                f.write(f"### {severity_icon} Problem #{idx} - {issue['severity']}\n\n")
                f.write(f"- **Zeile:** {issue['line']}\n")
                f.write(f"- **Typ:** {issue['type']}\n")
                f.write(f"- **Beschreibung:** {issue['description']}\n")
                f.write(f"- **Code:**\n```python\n{issue['code']}\n```\n\n")

        if results['spinners']:
            f.write("## 📍 st.spinner Aufrufe\n\n")
            for spinner in results['spinners']:
                f.write(f"- Zeile {spinner['line']}: `{spinner['code']}`\n")
            f.write("\n")

        if results['invoke_calls']:
            f.write("## 🤖 LLM invoke() Aufrufe\n\n")
            blocking = [c for c in results['invoke_calls'] if c['in_spinner']]
            if blocking:
                f.write("### 🔴 Blockierend (in st.spinner)\n\n")
                for call in blocking:
                    f.write(f"- Zeile {call['line']}: `{call['code']}`\n")
                f.write("\n")

        f.write("## 💡 Empfehlungen\n\n")
        if results['issues']:
            f.write("### Sofort beheben:\n\n")
            f.write("1. Ersetze `st.spinner()` mit `st.status()`\n")
            f.write("2. Füge `st.write()` Updates nach jedem `invoke()` hinzu\n")
            f.write("3. Zeige Tool-Ausführungen explizit an\n\n")
            f.write("Siehe `BLOCKING_FIX_ANLEITUNG.md` für Details.\n\n")
        else:
            f.write("✅ Keine kritischen Probleme gefunden!\n\n")

        f.write("## 🚀 Nächste Schritte\n\n")
        f.write("1. **Interaktive Tests:**\n")
        f.write("   ```bash\n")
        f.write("   streamlit run test_app_blocking_isolated.py\n")
        f.write("   ```\n\n")
        f.write("2. **Vollständige Diagnose:**\n")
        f.write("   ```bash\n")
        f.write("   streamlit run test_ui_blocking.py\n")
        f.write("   ```\n\n")
        f.write("3. **Fix anwenden:**\n")
        f.write("   Siehe `BLOCKING_FIX_ANLEITUNG.md`\n\n")


if __name__ == "__main__":
    print("\n🔍 Starte Code-Analyse...\n")

    # Analysiere app.py
    results = analyze_file('app.py')

    if results is None:
        print("❌ FEHLER: app.py nicht gefunden!")
        exit(1)

    # Zeige Report
    print_report(results)

    # Speichere Report
    output_dir = Path("test_results")
    output_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"blocking_analysis_{timestamp}.md"

    save_report(results, output_file)
    print(f"📄 Report gespeichert: {output_file}\n")
