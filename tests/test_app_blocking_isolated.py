"""
Isolierter Test für das spezifische Blocking-Problem in app.py

Dieses Programm repliziert EXAKT die problematische Code-Stelle aus app.py
und vergleicht sie mit der verbesserten Version.
"""

import streamlit as st
import time
from datetime import datetime
from dotenv import load_dotenv
import os

load_dotenv()

try:
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
    from langchain_core.tools import tool
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False

st.set_page_config(page_title="App.py Blocking Test", layout="wide")

st.title("🔬 app.py Blocking Problem - Isolierter Test")

st.error("""
**Problem aus app.py (Zeile 2452-2747):**
- Bei Chat-Eingaben wird die gesamte UI für 20+ Sekunden blockiert
- Benutzer sieht nur einen Spinner, keine Updates
- Bei Tool-Calls wird mehrfach blockiert (jedes invoke() blockiert erneut)
""")


# ============================================================================
# SETUP
# ============================================================================

if 'test_messages' not in st.session_state:
    st.session_state.test_messages = []

if not LANGCHAIN_AVAILABLE:
    st.warning("⚠️ Installiere LangChain: pip install langchain-anthropic")
    st.stop()

if not os.getenv("ANTHROPIC_API_KEY"):
    st.warning("⚠️ Setze ANTHROPIC_API_KEY in .env")
    st.stop()


# ============================================================================
# DUMMY TOOLS (wie in app.py)
# ============================================================================

@tool
def get_current_time() -> str:
    """Gibt die aktuelle Zeit zurück (Dummy-Tool für Tests)"""
    time.sleep(1)  # Simuliere API-Call
    return f"Aktuelle Zeit: {datetime.now().strftime('%H:%M:%S')}"

@tool
def calculate_sum(a: int, b: int) -> str:
    """Berechnet die Summe von zwei Zahlen (Dummy-Tool)"""
    time.sleep(0.5)  # Simuliere Berechnung
    return f"Die Summe von {a} und {b} ist {a + b}"

@tool
def fetch_data(query: str) -> str:
    """Simuliert Datenabruf (Dummy-Tool)"""
    time.sleep(1.5)  # Simuliere langsamen API-Call
    return f"Daten für '{query}': [Dummy-Ergebnis nach 1.5s]"


# ============================================================================
# ORIGINAL CODE (wie in app.py - PROBLEMATISCH)
# ============================================================================

def process_message_original(user_input: str):
    """
    Original-Code aus app.py (Zeile 2452-2747)
    PROBLEMATISCH: Blockiert UI komplett
    """
    st.session_state.test_messages.append({
        'role': 'user',
        'content': user_input
    })

    # DIES IST DER PROBLEMATISCHE TEIL!
    with st.spinner("Bereite Antwort vor..."):  # UI BLOCKIERT HIER!
        try:
            llm = ChatAnthropic(
                model="claude-3-5-haiku-20241022",
                temperature=0
            )

            # Binde Tools
            tools = [get_current_time, calculate_sum, fetch_data]
            llm_with_tools = llm.bind_tools(tools)

            # Konvertiere Messages
            lc_messages = []
            for msg in st.session_state.test_messages:
                if msg['role'] == 'user':
                    lc_messages.append(HumanMessage(content=msg['content']))
                elif msg['role'] == 'assistant':
                    if 'tool_calls' in msg and msg['tool_calls']:
                        lc_messages.append(AIMessage(
                            content=msg['content'] if msg['content'] else "",
                            tool_calls=msg['tool_calls']
                        ))
                    else:
                        lc_messages.append(AIMessage(content=msg['content']))
                elif msg['role'] == 'tool':
                    lc_messages.append(ToolMessage(
                        content=msg['content'],
                        tool_call_id=msg.get('tool_call_id', '')
                    ))

            # HIER BEGINNT DIE BLOCKING-SCHLEIFE
            max_iterations = 10
            iteration = 0
            response = llm_with_tools.invoke(lc_messages)  # BLOCKIERT UI!

            while iteration < max_iterations:
                iteration += 1

                if hasattr(response, 'tool_calls') and response.tool_calls:
                    # Speichere AI-Nachricht
                    st.session_state.test_messages.append({
                        'role': 'assistant',
                        'content': response.content if response.content else "",
                        'tool_calls': response.tool_calls
                    })

                    # Verarbeite Tool-Calls
                    tool_messages = []
                    for tool_call in response.tool_calls:
                        tool_name = tool_call['name']
                        tool_args = tool_call['args']

                        # Finde Tool
                        tool_func = None
                        for t in tools:
                            if t.name == tool_name:
                                tool_func = t
                                break

                        if tool_func:
                            # Tool ausführen (blockiert auch!)
                            tool_result = tool_func.invoke(tool_args)

                            st.session_state.test_messages.append({
                                'role': 'tool',
                                'content': tool_result,
                                'tool_call_id': tool_call.get('id')
                            })

                            tool_messages.append(ToolMessage(
                                content=tool_result,
                                tool_call_id=tool_call.get('id')
                            ))

                    # Nächster LLM-Aufruf
                    lc_messages.append(response)
                    lc_messages.extend(tool_messages)
                    response = llm_with_tools.invoke(lc_messages)  # BLOCKIERT WIEDER!
                else:
                    # Finale Antwort
                    st.session_state.test_messages.append({
                        'role': 'assistant',
                        'content': response.content
                    })
                    break

            if iteration >= max_iterations:
                st.warning("⚠️ Maximale Iterationen erreicht")

        except Exception as e:
            st.error(f"Fehler: {e}")
            import traceback
            st.code(traceback.format_exc())

    st.rerun()


# ============================================================================
# IMPROVED CODE (mit st.status und Updates)
# ============================================================================

def process_message_improved(user_input: str):
    """
    Verbesserte Version mit st.status()
    BESSER: Zeigt kontinuierliche Updates
    """
    st.session_state.test_messages.append({
        'role': 'user',
        'content': user_input
    })

    # VERBESSERTER TEIL - Mit sichtbaren Updates!
    with st.status("🤖 Verarbeite Anfrage...", expanded=True) as status:
        try:
            st.write("🔧 Initialisiere LLM...")
            llm = ChatAnthropic(
                model="claude-3-5-haiku-20241022",
                temperature=0
            )

            # Binde Tools
            tools = [get_current_time, calculate_sum, fetch_data]
            llm_with_tools = llm.bind_tools(tools)

            # Konvertiere Messages
            lc_messages = []
            for msg in st.session_state.test_messages:
                if msg['role'] == 'user':
                    lc_messages.append(HumanMessage(content=msg['content']))
                elif msg['role'] == 'assistant':
                    if 'tool_calls' in msg and msg['tool_calls']:
                        lc_messages.append(AIMessage(
                            content=msg['content'] if msg['content'] else "",
                            tool_calls=msg['tool_calls']
                        ))
                    else:
                        lc_messages.append(AIMessage(content=msg['content']))
                elif msg['role'] == 'tool':
                    lc_messages.append(ToolMessage(
                        content=msg['content'],
                        tool_call_id=msg.get('tool_call_id', '')
                    ))

            # VERBESSERTE SCHLEIFE mit Updates
            st.write("📤 Sende initiale Anfrage an LLM...")
            start_time = time.time()

            max_iterations = 10
            iteration = 0
            response = llm_with_tools.invoke(lc_messages)

            initial_time = time.time() - start_time
            st.write(f"✅ Initiale Antwort erhalten ({initial_time:.1f}s)")

            while iteration < max_iterations:
                iteration += 1
                st.write(f"🔄 **Iteration {iteration}**")

                if hasattr(response, 'tool_calls') and response.tool_calls:
                    st.write(f"   └─ {len(response.tool_calls)} Tool-Call(s) erkannt")

                    # Speichere AI-Nachricht
                    st.session_state.test_messages.append({
                        'role': 'assistant',
                        'content': response.content if response.content else "",
                        'tool_calls': response.tool_calls
                    })

                    # Verarbeite Tool-Calls
                    tool_messages = []
                    for idx, tool_call in enumerate(response.tool_calls, 1):
                        tool_name = tool_call['name']
                        tool_args = tool_call['args']

                        # Finde Tool
                        tool_func = None
                        for t in tools:
                            if t.name == tool_name:
                                tool_func = t
                                break

                        if tool_func:
                            # Tool ausführen MIT UPDATE
                            st.write(f"   └─ 🔧 Führe aus: `{tool_name}`")
                            tool_start = time.time()

                            tool_result = tool_func.invoke(tool_args)

                            tool_time = time.time() - tool_start
                            st.write(f"      ✓ Fertig ({tool_time:.1f}s)")

                            st.session_state.test_messages.append({
                                'role': 'tool',
                                'content': tool_result,
                                'tool_call_id': tool_call.get('id')
                            })

                            tool_messages.append(ToolMessage(
                                content=tool_result,
                                tool_call_id=tool_call.get('id')
                            ))

                    # Nächster LLM-Aufruf
                    lc_messages.append(response)
                    lc_messages.extend(tool_messages)

                    st.write("   └─ 📤 Sende Follow-up an LLM...")
                    followup_start = time.time()

                    response = llm_with_tools.invoke(lc_messages)

                    followup_time = time.time() - followup_start
                    st.write(f"   └─ ✅ Follow-up erhalten ({followup_time:.1f}s)")
                else:
                    # Finale Antwort
                    st.write("✅ Finale Antwort generiert")
                    st.session_state.test_messages.append({
                        'role': 'assistant',
                        'content': response.content
                    })
                    break

            if iteration >= max_iterations:
                st.warning("⚠️ Maximale Iterationen erreicht")

            total_time = time.time() - start_time
            status.update(
                label=f"✅ Verarbeitung abgeschlossen! (Gesamt: {total_time:.1f}s)",
                state="complete"
            )

        except Exception as e:
            status.update(label="❌ Fehler aufgetreten", state="error")
            st.error(f"Fehler: {e}")
            import traceback
            st.code(traceback.format_exc())

    st.rerun()


# ============================================================================
# UI
# ============================================================================

st.header("📊 Vergleich: Original vs. Verbessert")

col1, col2 = st.columns(2)

with col1:
    st.subheader("❌ Original (app.py)")
    st.caption("Blockiert UI komplett")

    st.markdown("""
    **Symptome:**
    - Seite wird ausgegraut
    - Nur Spinner sichtbar
    - Keine Updates während Verarbeitung
    - Fühlt sich "eingefroren" an
    """)

    if st.button("🧪 Test Original-Version", type="secondary"):
        st.session_state.test_mode = 'original'
        st.session_state.test_messages = []
        st.rerun()

with col2:
    st.subheader("✅ Verbessert")
    st.caption("Zeigt kontinuierliche Updates")

    st.markdown("""
    **Verbesserungen:**
    - Status-Container zeigt Updates
    - Jeder Schritt wird geloggt
    - Timing-Informationen sichtbar
    - User weiß was passiert
    """)

    if st.button("🧪 Test Verbesserte Version", type="primary"):
        st.session_state.test_mode = 'improved'
        st.session_state.test_messages = []
        st.rerun()

st.markdown("---")

# Zeige Chat-Historie
if st.session_state.test_messages:
    st.subheader("💬 Chat-Verlauf")
    for msg in st.session_state.test_messages:
        if msg['role'] == 'user':
            with st.chat_message("user"):
                st.write(msg['content'])
        elif msg['role'] == 'assistant':
            with st.chat_message("assistant"):
                if msg['content']:
                    st.write(msg['content'])
                if 'tool_calls' in msg and msg['tool_calls']:
                    with st.expander("🔧 Tool-Calls"):
                        for tc in msg['tool_calls']:
                            st.code(f"{tc['name']}({tc['args']})")
        elif msg['role'] == 'tool':
            with st.chat_message("assistant"):
                st.info(msg['content'])

# Chat-Eingabe
if 'test_mode' in st.session_state and st.session_state.test_mode:
    mode = st.session_state.test_mode

    st.info(f"🧪 Test-Modus: **{mode.upper()}**")

    # Beispiel-Prompts
    st.caption("💡 **Empfohlene Test-Prompts:**")
    col_p1, col_p2, col_p3 = st.columns(3)

    with col_p1:
        if st.button("⏰ Zeit-Tool", use_container_width=True):
            prompt = "Wie spät ist es? Nutze das get_current_time Tool."
            if mode == 'original':
                process_message_original(prompt)
            else:
                process_message_improved(prompt)

    with col_p2:
        if st.button("🔢 Rechnen-Tool", use_container_width=True):
            prompt = "Berechne 42 + 58 mit dem calculate_sum Tool."
            if mode == 'original':
                process_message_original(prompt)
            else:
                process_message_improved(prompt)

    with col_p3:
        if st.button("📊 Multi-Tool", use_container_width=True):
            prompt = "Hole die Zeit, berechne dann 10 + 20, und fetche Daten für 'test'. Nutze alle verfügbaren Tools."
            if mode == 'original':
                process_message_original(prompt)
            else:
                process_message_improved(prompt)

    # Freie Eingabe
    user_input = st.chat_input("Deine Nachricht... (erwähne Tools für Tool-Calling)")

    if user_input:
        if mode == 'original':
            process_message_original(user_input)
        else:
            process_message_improved(user_input)


# ============================================================================
# TIMING COMPARISON
# ============================================================================

st.markdown("---")
st.header("⏱️ Performance-Vergleich")

st.markdown("""
### Geschätzte Timings

| Operation | Original | Verbessert | User-Wahrnehmung |
|-----------|----------|------------|------------------|
| Initiale LLM-Anfrage | 3-8s | 3-8s | ❌ Blockiert → ✅ Update gezeigt |
| Tool-Ausführung | 0.5-2s | 0.5-2s | ❌ Blockiert → ✅ Update gezeigt |
| Follow-up LLM | 3-8s | 3-8s | ❌ Blockiert → ✅ Update gezeigt |
| **GESAMT** (3 Tools) | **15-30s** | **15-30s** | ❌ **Ausgegraut** → ✅ **Live Updates** |

**Wichtig:** Die tatsächliche Dauer bleibt gleich, aber die **User Experience** ist drastisch besser!
""")


# ============================================================================
# CODE DIFF
# ============================================================================

st.markdown("---")
st.header("📝 Code-Änderungen für app.py")

st.markdown("**Ersetze diese Zeilen in app.py:**")

tab1, tab2 = st.tabs(["🔴 Vorher (Zeile 2452)", "🟢 Nachher (Fix)"])

with tab1:
    st.code("""
# ZEILE 2452 - ORIGINAL (PROBLEMATISCH)
with st.spinner("Bereite Antwort vor..."):
    # ... ganzer Code-Block ...
    response = llm_with_tools.invoke(lc_messages)  # UI blockiert!

    while iteration < max_iterations:
        iteration += 1

        if hasattr(response, 'tool_calls') and response.tool_calls:
            # Tool-Ausführung (keine Updates!)
            tool_result = tool_func.invoke(tool_args)
            # ...
            response = llm_with_tools.invoke(lc_messages)  # Blockiert wieder!
    """, language='python')

with tab2:
    st.code("""
# FIX - ERSETZE MIT:
with st.status("🤖 Verarbeite Anfrage...", expanded=True) as status:
    st.write("📤 Sende initiale Anfrage an LLM...")
    response = llm_with_tools.invoke(lc_messages)
    st.write("✅ Initiale Antwort erhalten")

    while iteration < max_iterations:
        iteration += 1
        st.write(f"🔄 Iteration {iteration}")

        if hasattr(response, 'tool_calls') and response.tool_calls:
            for tool_call in response.tool_calls:
                tool_name = tool_call['name']
                st.write(f"   └─ 🔧 Führe aus: {tool_name}")

                # Tool ausführen
                tool_result = tool_func.invoke(tool_args)
                st.write(f"      ✓ Fertig")

            st.write("   └─ 📤 Sende Follow-up...")
            response = llm_with_tools.invoke(lc_messages)
            st.write("   └─ ✅ Follow-up erhalten")

    status.update(label="✅ Verarbeitung abgeschlossen!", state="complete")
    """, language='python')

if st.button("💾 Download vollständigen Fix"):
    st.download_button(
        "Download app_py_fix.txt",
        open(__file__).read(),
        "app_py_fix.txt"
    )


# ============================================================================
# SIDEBAR
# ============================================================================

with st.sidebar:
    st.header("🎯 Test-Info")

    st.metric("Test-Modus",
              st.session_state.get('test_mode', 'Nicht gestartet').upper())

    st.metric("Nachrichten",
              len(st.session_state.test_messages))

    if st.button("🗑️ Zurücksetzen"):
        st.session_state.test_messages = []
        if 'test_mode' in st.session_state:
            del st.session_state.test_mode
        st.rerun()

    st.markdown("---")
    st.caption("""
    **So funktioniert der Test:**

    1. Wähle Original oder Verbessert
    2. Sende einen Test-Prompt (z.B. mit Tools)
    3. Beobachte die UI während der Verarbeitung
    4. Vergleiche die User Experience
    """)
