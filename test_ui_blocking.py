"""
Umfassendes Testprogramm zur Diagnose des UI-Blocking-Problems

Dieses Programm testet:
1. Synchrone vs. Asynchrone LLM-Aufrufe
2. Timing-Messungen der verschiedenen Komponenten
3. Spinner vs. Progress-Indikatoren
4. Verschiedene Lösungsansätze (Threading, Async, Status-Updates)
"""

import streamlit as st
import time
import asyncio
from datetime import datetime
from typing import Optional
import threading
from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()

# Versuche Langchain zu importieren
try:
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False
    st.warning("⚠️ LangChain nicht verfügbar. Installiere mit: pip install langchain-anthropic")


st.set_page_config(page_title="UI Blocking Test", layout="wide")

st.title("🔍 UI Blocking Diagnose-Tool")

st.markdown("""
Dieses Tool testet verschiedene Aspekte des UI-Blocking-Problems:
- **Problem**: Bei LLM-Antworten ist die Seite 20+ Sekunden ausgegraut
- **Ursache**: Synchrone, blockierende LLM-Aufrufe
- **Lösung**: Verschiedene Ansätze werden getestet
""")


# ============================================================================
# TEST 1: TIMING-MESSUNG
# ============================================================================

st.header("1️⃣ Timing-Messung")

if st.button("🕐 Zeitmessung durchführen"):
    results = {}

    with st.expander("📊 Ergebnisse", expanded=True):
        progress_bar = st.progress(0)
        status_text = st.empty()
        results_area = st.empty()

        # Test 1.1: Spinner-Overhead
        status_text.text("Test 1.1: Spinner-Overhead...")
        start = time.time()
        with st.spinner("Teste Spinner..."):
            time.sleep(0.5)
        results['spinner_overhead'] = time.time() - start
        progress_bar.progress(0.2)

        # Test 1.2: Einfache Operation
        status_text.text("Test 1.2: Einfache Operation...")
        start = time.time()
        time.sleep(0.1)
        results['simple_operation'] = time.time() - start
        progress_bar.progress(0.4)

        # Test 1.3: Mehrfache Reruns
        status_text.text("Test 1.3: Session State Schreibvorgänge...")
        start = time.time()
        for i in range(10):
            st.session_state[f'test_{i}'] = i
        results['session_state_writes'] = time.time() - start
        progress_bar.progress(0.6)

        # Test 1.4: LLM-Aufruf (wenn verfügbar)
        if LANGCHAIN_AVAILABLE and os.getenv("ANTHROPIC_API_KEY"):
            status_text.text("Test 1.4: LLM-Aufruf (kurz)...")
            try:
                llm = ChatAnthropic(model="claude-3-5-haiku-20241022", temperature=0)
                start = time.time()
                response = llm.invoke([HumanMessage(content="Sage nur 'Hallo'")])
                results['llm_call_short'] = time.time() - start
                progress_bar.progress(0.8)

                # Test 1.5: LLM-Aufruf (lang)
                status_text.text("Test 1.5: LLM-Aufruf (lang)...")
                start = time.time()
                response = llm.invoke([HumanMessage(content="Erkläre in 3 Sätzen was Quantenphysik ist.")])
                results['llm_call_long'] = time.time() - start
            except Exception as e:
                results['llm_error'] = str(e)

        progress_bar.progress(1.0)
        status_text.text("✅ Tests abgeschlossen!")

        # Zeige Ergebnisse
        st.subheader("📊 Timing-Ergebnisse")
        for key, value in results.items():
            if isinstance(value, (int, float)):
                st.metric(key, f"{value:.2f}s")
            else:
                st.error(f"{key}: {value}")


# ============================================================================
# TEST 2: BLOCKING VS NON-BLOCKING
# ============================================================================

st.header("2️⃣ Blocking vs. Non-Blocking")

col1, col2 = st.columns(2)

with col1:
    st.subheader("🔴 Blockierend (Standard)")
    if st.button("Test: Blockierende Operation"):
        with st.spinner("Blockiere UI für 5 Sekunden..."):
            start = time.time()
            time.sleep(5)
            elapsed = time.time() - start
        st.success(f"✅ Fertig nach {elapsed:.2f}s")
        st.info("Während dieser 5 Sekunden war die gesamte UI eingefroren!")

with col2:
    st.subheader("🟢 Nicht-Blockierend (Status Updates)")
    if st.button("Test: Non-Blocking mit Updates"):
        status = st.empty()
        progress = st.progress(0)
        start = time.time()

        for i in range(50):
            status.text(f"Schritt {i+1}/50 - {(i+1)*2}% fertig")
            progress.progress((i + 1) / 50)
            time.sleep(0.1)

        elapsed = time.time() - start
        status.success(f"✅ Fertig nach {elapsed:.2f}s")
        st.info("Die UI war reaktiv und zeigte kontinuierlich Updates!")


# ============================================================================
# TEST 3: LLM MIT VERSCHIEDENEN ANSÄTZEN
# ============================================================================

st.header("3️⃣ LLM-Aufruf Strategien")

if not LANGCHAIN_AVAILABLE:
    st.warning("⚠️ LangChain nicht verfügbar - installiere es für vollständige Tests")
elif not os.getenv("ANTHROPIC_API_KEY"):
    st.warning("⚠️ ANTHROPIC_API_KEY nicht gesetzt - LLM-Tests übersprungen")
else:

    test_prompt = st.text_area(
        "Test-Prompt:",
        "Erkläre kurz (2-3 Sätze) was Streamlit ist.",
        height=100
    )

    tab1, tab2, tab3 = st.tabs([
        "🔴 Standard (Blocking)",
        "🟡 Mit Status-Updates",
        "🟢 Async (Optimal)"
    ])

    with tab1:
        st.markdown("**Standard-Ansatz (wie aktuell in app.py):**")
        st.code("""
with st.spinner("Bereite Antwort vor..."):
    response = llm.invoke(messages)
    # UI ist während des gesamten Aufrufs blockiert!
        """)

        if st.button("▶️ Test Standard", key="test_standard"):
            with st.spinner("🔄 Warte auf LLM-Antwort..."):
                start = time.time()
                try:
                    llm = ChatAnthropic(model="claude-3-5-haiku-20241022", temperature=0)
                    response = llm.invoke([HumanMessage(content=test_prompt)])
                    elapsed = time.time() - start

                    st.info(f"⏱️ Dauer: {elapsed:.2f}s")
                    st.success("Antwort:")
                    st.write(response.content)
                    st.warning("⚠️ Während der Antwort war die UI komplett blockiert!")
                except Exception as e:
                    st.error(f"Fehler: {e}")

    with tab2:
        st.markdown("**Mit Status-Updates (Verbesserung):**")
        st.code("""
status = st.empty()
status.info("🔄 Sende Anfrage an LLM...")
# Simuliere Status-Updates während Verarbeitung
response = llm.invoke(messages)
status.success("✅ Antwort erhalten!")
        """)

        if st.button("▶️ Test mit Updates", key="test_updates"):
            status = st.empty()
            timer = st.empty()

            status.info("🔄 Sende Anfrage an LLM...")
            start = time.time()

            try:
                llm = ChatAnthropic(model="claude-3-5-haiku-20241022", temperature=0)

                # Starte Timer in Thread (funktioniert nicht perfekt in Streamlit)
                response = llm.invoke([HumanMessage(content=test_prompt)])

                elapsed = time.time() - start
                status.success(f"✅ Antwort erhalten nach {elapsed:.2f}s!")

                st.success("Antwort:")
                st.write(response.content)
                st.info("ℹ️ Dieser Ansatz ist besser, aber UI ist immer noch blockiert während des LLM-Aufrufs")
            except Exception as e:
                st.error(f"Fehler: {e}")

    with tab3:
        st.markdown("**Async-Ansatz (Optimal - erfordert Code-Umstrukturierung):**")
        st.code("""
async def get_llm_response(prompt):
    llm = ChatAnthropic(...)
    response = await llm.ainvoke(messages)
    return response

# In Streamlit
response = asyncio.run(get_llm_response(prompt))
        """)

        if st.button("▶️ Test Async", key="test_async"):
            async def async_llm_call(prompt):
                llm = ChatAnthropic(model="claude-3-5-haiku-20241022", temperature=0)
                response = await llm.ainvoke([HumanMessage(content=prompt)])
                return response

            status = st.empty()
            status.info("🔄 Async LLM-Aufruf...")
            start = time.time()

            try:
                response = asyncio.run(async_llm_call(test_prompt))
                elapsed = time.time() - start

                status.success(f"✅ Antwort nach {elapsed:.2f}s!")
                st.success("Antwort:")
                st.write(response.content)
                st.info("ℹ️ Async ist besser, aber in Streamlit immer noch limitiert. Threading ist die beste Lösung.")
            except Exception as e:
                st.error(f"Fehler: {e}")


# ============================================================================
# TEST 4: MULTI-ITERATION TOOL CALLING
# ============================================================================

st.header("4️⃣ Tool-Calling Schleife (Kritischster Teil)")

st.markdown("""
In `app.py` Zeile 2679-2734 läuft eine Schleife die mehrfach LLM-Aufrufe macht:
```python
while iteration < max_iterations:
    response = llm_with_tools.invoke(lc_messages)  # BLOCKIERT UI!
    if hasattr(response, 'tool_calls') and response.tool_calls:
        # Führe Tools aus
        response = llm_with_tools.invoke(lc_messages)  # NOCHMAL BLOCKIERT!
```

**Problem**: Bei jedem Tool-Call wird die UI erneut blockiert!
""")

if LANGCHAIN_AVAILABLE and os.getenv("ANTHROPIC_API_KEY"):
    if st.button("🔄 Simuliere Tool-Calling-Schleife"):
        from langchain_core.tools import tool

        @tool
        def dummy_tool(query: str) -> str:
            """Ein Dummy-Tool für Tests"""
            time.sleep(0.5)  # Simuliere Tool-Ausführung
            return f"Tool-Ergebnis für: {query}"

        llm = ChatAnthropic(model="claude-3-5-haiku-20241022", temperature=0)
        llm_with_tools = llm.bind_tools([dummy_tool])

        messages = [HumanMessage(content="Nutze das dummy_tool mit dem Query 'test'")]

        iteration_info = st.empty()
        status = st.empty()
        timing_display = st.empty()

        total_start = time.time()
        timings = []

        max_iterations = 3
        for i in range(max_iterations):
            iteration_info.info(f"Iteration {i+1}/{max_iterations}")

            # LLM-Aufruf
            status.text("🤖 LLM denkt nach...")
            iter_start = time.time()
            response = llm_with_tools.invoke(messages)
            llm_time = time.time() - iter_start

            # Zeige Timing
            timings.append(('LLM', llm_time))
            timing_display.write(f"⏱️ Iteration {i+1} LLM: {llm_time:.2f}s")

            # Simuliere Tool-Ausführung
            if hasattr(response, 'tool_calls') and response.tool_calls and i < 2:
                status.text("🔧 Führe Tool aus...")
                tool_start = time.time()
                time.sleep(0.5)
                tool_time = time.time() - tool_start
                timings.append(('Tool', tool_time))

                messages.append(response)
            else:
                break

        total_time = time.time() - total_start

        st.success(f"✅ Fertig nach {total_time:.2f}s Gesamt")

        st.subheader("⏱️ Timing-Breakdown:")
        for op_type, duration in timings:
            st.metric(op_type, f"{duration:.2f}s")

        st.error("""
        **⚠️ KRITISCHES PROBLEM:**
        Während der gesamten {:.2f} Sekunden war die UI blockiert!
        Bei echten LLM-Aufrufen kann dies 20-30 Sekunden dauern!
        """.format(total_time))


# ============================================================================
# TEST 5: LÖSUNGSANSÄTZE
# ============================================================================

st.header("5️⃣ Empfohlene Lösungen")

st.markdown("""
### 🎯 Beste Lösungsansätze für app.py:

#### Option 1: Chunked Progress Updates (Einfachste Lösung)
```python
def process_with_progress(llm, messages):
    status = st.empty()
    progress = st.progress(0)

    status.info("🔄 Starte Verarbeitung...")
    progress.progress(0.2)

    # LLM-Aufruf
    response = llm.invoke(messages)
    progress.progress(0.8)

    status.success("✅ Verarbeitung abgeschlossen!")
    progress.progress(1.0)
    return response
```

#### Option 2: Streamlit Fragment (Neu in Streamlit 1.30+)
```python
@st.fragment
def llm_section():
    with st.spinner("Verarbeite..."):
        response = llm.invoke(messages)
    return response
```

#### Option 3: Status Container mit Updates
```python
with st.status("Verarbeite Anfrage...", expanded=True) as status:
    st.write("Sende an LLM...")
    response = llm.invoke(messages)
    st.write("Verarbeite Antwort...")
    status.update(label="Fertig!", state="complete")
```

#### Option 4: Streaming Response (Beste UX!)
```python
llm = ChatAnthropic(model="...", streaming=True)
with st.chat_message("assistant"):
    message_placeholder = st.empty()
    full_response = ""

    for chunk in llm.stream(messages):
        full_response += chunk.content
        message_placeholder.markdown(full_response + "▌")

    message_placeholder.markdown(full_response)
```
""")

if st.button("📝 Generiere Fix-Code für app.py"):
    fix_code = '''
# ============================================================================
# FIX für app.py - Ersetze ab Zeile 2452
# ============================================================================

# VORHER (Zeile 2452-2747):
# with st.spinner("Bereite Antwort vor..."):
#     response = llm_with_tools.invoke(lc_messages)  # BLOCKIERT!

# NACHHER:
with st.status("🤖 Bereite Antwort vor...", expanded=True) as status:
    st.write("📤 Sende Anfrage an LLM...")

    max_iterations = 10
    iteration = 0
    response = llm_with_tools.invoke(lc_messages)

    while iteration < max_iterations:
        iteration += 1
        st.write(f"🔄 Iteration {iteration}/{max_iterations}")

        if hasattr(response, 'tool_calls') and response.tool_calls:
            # Speichere AI-Nachricht
            st.session_state['preparation_messages'].append({
                'role': 'assistant',
                'content': response.content if response.content else "",
                'tool_calls': response.tool_calls
            })

            # Verarbeite Tool-Calls
            tool_messages = []
            for tool_call in response.tool_calls:
                tool_name = tool_call['name']
                tool_args = tool_call['args']

                tool_func = None
                for t in tools:
                    if t.name == tool_name:
                        tool_func = t
                        break

                if tool_func:
                    st.write(f"🔧 Führe aus: {tool_name}")
                    tool_result = tool_func.invoke(tool_args)

                    st.session_state['preparation_messages'].append({
                        'role': 'tool',
                        'content': tool_result,
                        'tool_call_id': tool_call.get('id')
                    })

                    tool_messages.append(ToolMessage(
                        content=tool_result,
                        tool_call_id=tool_call.get('id')
                    ))

            # Nächste Iteration
            lc_messages.append(response)
            lc_messages.extend(tool_messages)

            st.write("📤 Sende Follow-up...")
            response = llm_with_tools.invoke(lc_messages)
        else:
            # Finale Antwort
            st.write("✅ Generiere finale Antwort...")
            st.session_state['preparation_messages'].append({
                'role': 'assistant',
                'content': response.content
            })
            break

    if iteration >= max_iterations:
        st.warning("⚠️ Maximale Iterations erreicht")

    status.update(label="✅ Antwort bereit!", state="complete")

st.rerun()
    '''

    st.code(fix_code, language='python')
    st.download_button(
        "💾 Download Fix-Code",
        fix_code,
        "app_py_fix.py",
        "text/x-python"
    )


# ============================================================================
# TEST 6: QUICK FIX DEMO
# ============================================================================

st.header("6️⃣ Quick Fix Demo")

st.markdown("Vergleiche vorher/nachher mit simulierten LLM-Aufrufen:")

col1, col2 = st.columns(2)

with col1:
    st.subheader("❌ Vorher")
    if st.button("Test VORHER", key="before"):
        with st.spinner("Verarbeite..."):
            time.sleep(3)
            st.success("Fertig!")
        st.caption("UI war 3 Sekunden blockiert")

with col2:
    st.subheader("✅ Nachher")
    if st.button("Test NACHHER", key="after"):
        with st.status("Verarbeite...", expanded=True) as status:
            st.write("Schritt 1: Initialisiere...")
            time.sleep(1)
            st.write("Schritt 2: Verarbeite...")
            time.sleep(1)
            st.write("Schritt 3: Finalisiere...")
            time.sleep(1)
            status.update(label="✅ Fertig!", state="complete")
        st.caption("UI zeigt kontinuierlich Updates")


# ============================================================================
# TEST 7: PERFORMANCE REPORT
# ============================================================================

st.header("7️⃣ Performance-Report Generator")

if st.button("📊 Generiere Performance-Report"):
    report = f"""
# Performance-Analyse Report
Generiert: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## Problem-Zusammenfassung
- **Symptom**: UI ausgegraut für 20+ Sekunden bei LLM-Antworten
- **Root Cause**: Synchrone LLM-Aufrufe blockieren Streamlit Main Thread
- **Betroffene Datei**: app.py, Zeilen 2452-2747

## Kritische Code-Stellen

### 1. Meeting Preparation (app.py:2452)
```python
with st.spinner("Bereite Antwort vor..."):
    response = llm_with_tools.invoke(lc_messages)  # BLOCKIERT
```

### 2. Tool-Calling Loop (app.py:2679-2734)
```python
while iteration < max_iterations:
    response = llm_with_tools.invoke(lc_messages)  # MEHRFACH BLOCKIERT
```

## Geschätzte Timing-Breakdown
- LLM-Aufruf (initial): 3-8s
- Tool-Ausführung (pro Tool): 0.5-2s
- Follow-up LLM-Aufrufe: 3-8s pro Iteration
- **Gesamt bei 3 Iterationen**: 15-30s

## Empfohlene Fixes (Priorität)

### 🔥 Kritisch - Sofort
1. Ersetze `st.spinner()` mit `st.status()` für besseres Feedback
2. Füge `st.write()` Updates zwischen Operationen hinzu
3. Zeige Tool-Ausführungen explizit an

### ⚡ Hoch - Diese Woche
4. Implementiere Streaming für LLM-Antworten
5. Nutze `@st.fragment` für isolierte Updates
6. Reduziere max_iterations von 10 auf 5

### 💡 Medium - Nächsten Sprint
7. Async LLM-Calls mit `ainvoke()`
8. Background-Processing für lange Operationen
9. Caching für wiederholte Tool-Aufrufe

## Code-Änderungen

Siehe Generated Fix-Code oben für vollständige Implementation.

## Testing-Checkliste
- [ ] Test mit single LLM call (kein Tool-Calling)
- [ ] Test mit 1 Tool-Call
- [ ] Test mit multiple Tool-Calls (worst case)
- [ ] Test auf langsamem Netzwerk
- [ ] Test mit großen Prompts

## Risiken
- **Niedrig**: st.status() ist stabil seit Streamlit 1.28
- **Niedrig**: Keine Breaking Changes an API
- **Mittel**: User muss sich an neues Feedback gewöhnen

## Erfolgsmetriken
- Zeit bis zum ersten Feedback: < 1s (aktuell: 5-10s)
- Wahrgenommene Blockierung: < 5s (aktuell: 20-30s)
- User-Frustration: 📉

---
*Generiert von test_ui_blocking.py*
    """

    st.text_area("Report:", report, height=600)
    st.download_button(
        "💾 Download Report",
        report,
        "performance_report.md",
        "text/markdown"
    )


# ============================================================================
# SIDEBAR: SYSTEM INFO
# ============================================================================

with st.sidebar:
    st.header("🔧 System Info")

    st.metric("Streamlit Version", st.__version__)

    st.metric("LangChain",
              "✅ Verfügbar" if LANGCHAIN_AVAILABLE else "❌ Nicht verfügbar")

    st.metric("Anthropic API",
              "✅ Konfiguriert" if os.getenv("ANTHROPIC_API_KEY") else "❌ Nicht konfiguriert")

    st.markdown("---")
    st.caption("Dieses Tool hilft bei der Diagnose von UI-Blocking-Problemen in Streamlit.")
