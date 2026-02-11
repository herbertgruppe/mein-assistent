# Debug-Anleitung: 20-Sekunden-Blockierung finden

## Setup:

1. **Streamlit neu starten** (damit Debug-Logs aktiv werden):
   ```bash
   # Stoppe aktuell laufende Streamlit-Instanz (Ctrl+C)
   # Dann:
   streamlit run app.py
   ```

2. **Konsole im Auge behalten** - dort erscheinen die Debug-Logs

## Test durchführen:

1. Öffne Tab "📬 Posteingang"
2. Klicke auf "🗄️ Archivieren" bei einer Email
3. **BEOBACHTE DIE KONSOLE** - es werden Zeitmessungen ausgegeben

## Was die Logs zeigen:

```
[DEBUG] ========== MAIN START @ ... ==========
[DEBUG] initialize_session_state took XXXms
[DEBUG] check_and_reset_cache took XXXms
[DEBUG] render_sidebar took XXXms         <- Hier könnte das Problem sein!
[DEBUG] render_inbox_tab START @ ...
[DEBUG] EmailDB init took XXXms
[DEBUG] Rendering cards took XXXms
[DEBUG] render_inbox_tab TOTAL took XXXms
```

## Beim Button-Klick:

```
[DEBUG] BUTTON CLICKED @ ...
[DEBUG] DB operations took XXXms
[DEBUG] About to st.rerun()...
```

## Dann beim Rerun:

Die gleichen Logs erscheinen wieder - **schau wo die große Zeitlücke ist!**

## Bitte:

Führe den Test durch und **kopiere mir die komplette Konsolen-Ausgabe** beim Klick auf "Archivieren".

Dann sehen wir GENAU wo die 20 Sekunden stecken bleiben!
