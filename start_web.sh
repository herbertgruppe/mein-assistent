#!/bin/bash

# Startskript für das Streamlit Web-Interface
# Erstellt für: Mein Assistent - Multi-Agenten-System

echo "================================================"
echo "🚀 Starte Web-Interface für Mein Assistent"
echo "================================================"
echo ""

# Prüfe ob Virtual Environment aktiviert ist
if [[ -z "$VIRTUAL_ENV" ]]; then
    echo "⚠️  Virtual Environment nicht aktiviert!"
    echo "Aktiviere Virtual Environment..."

    # Prüfe ob venv existiert
    if [ -d "venv" ]; then
        source venv/bin/activate
        echo "✓ Virtual Environment aktiviert"
    else
        echo "❌ Virtual Environment nicht gefunden!"
        echo "Bitte führen Sie erst 'python -m venv venv' aus."
        exit 1
    fi
fi

# Prüfe ob .env Datei existiert
if [ ! -f ".env" ]; then
    echo "⚠️  Warnung: .env Datei nicht gefunden!"
    echo "Bitte erstellen Sie eine .env Datei mit Ihren API-Keys."
    echo ""
fi

# Prüfe ob streamlit installiert ist
if ! python -c "import streamlit" 2>/dev/null; then
    echo "⚠️  Streamlit nicht installiert!"
    echo "Installiere Streamlit..."
    pip install streamlit
fi

echo ""
echo "📂 Arbeitsverzeichnis: $(pwd)"
echo "🐍 Python: $(which python)"
echo ""
echo "================================================"
echo "🌐 Starte Streamlit App..."
echo "================================================"
echo ""
echo "Die App wird automatisch im Browser geöffnet."
echo "Falls nicht, öffnen Sie: http://localhost:8501"
echo ""
echo "Zum Beenden: Drücken Sie Strg+C"
echo ""

# Starte Streamlit
streamlit run app.py
