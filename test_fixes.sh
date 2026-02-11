#!/bin/bash
echo "🚀 Starte App zum Testen der Fixes..."
echo ""
echo "Teste folgende Bereiche:"
echo "1. Meeting Preparation Tab → Sende eine Nachricht"
echo "2. Asana Chat → Stelle eine Frage"
echo ""
echo "Erwartung:"
echo "✅ Status-Container zeigt live Updates"
echo "✅ Kein ausgegrauer Screen"
echo "✅ Jeder Schritt ist sichtbar"
echo ""
source venv/bin/activate
streamlit run app.py
