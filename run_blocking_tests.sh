#!/bin/bash

# Automatisches Test-Skript für UI-Blocking Problem
# Führt alle Tests aus und generiert einen Report

set -e

echo "=============================================="
echo "🔍 UI-Blocking Diagnose & Test Suite"
echo "=============================================="
echo ""

# Farben für Output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Prüfe Voraussetzungen
echo -e "${BLUE}[1/7]${NC} Prüfe Voraussetzungen..."

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}✗ Python3 nicht gefunden${NC}"
    exit 1
fi

if ! command -v streamlit &> /dev/null; then
    echo -e "${RED}✗ Streamlit nicht installiert${NC}"
    echo "Installiere mit: pip install streamlit"
    exit 1
fi

# Prüfe ob .env existiert
if [ ! -f .env ]; then
    echo -e "${YELLOW}⚠ .env nicht gefunden${NC}"
    echo "Erstelle .env aus .env.example"
    if [ -f .env.example ]; then
        cp .env.example .env
        echo -e "${GREEN}✓ .env erstellt${NC}"
    fi
fi

# Prüfe ob ANTHROPIC_API_KEY gesetzt ist
if ! grep -q "ANTHROPIC_API_KEY" .env || grep -q "ANTHROPIC_API_KEY=$" .env || grep -q "ANTHROPIC_API_KEY=\"\"" .env; then
    echo -e "${YELLOW}⚠ ANTHROPIC_API_KEY nicht in .env gesetzt${NC}"
    echo "Setze ANTHROPIC_API_KEY in .env für vollständige Tests"
    API_KEY_SET=false
else
    echo -e "${GREEN}✓ ANTHROPIC_API_KEY gefunden${NC}"
    API_KEY_SET=true
fi

# Prüfe Python-Pakete
echo ""
echo -e "${BLUE}[2/7]${NC} Prüfe Python-Pakete..."

MISSING_PACKAGES=()

python3 -c "import streamlit" 2>/dev/null || MISSING_PACKAGES+=("streamlit")
python3 -c "import dotenv" 2>/dev/null || MISSING_PACKAGES+=("python-dotenv")
python3 -c "from langchain_anthropic import ChatAnthropic" 2>/dev/null || MISSING_PACKAGES+=("langchain-anthropic")

if [ ${#MISSING_PACKAGES[@]} -gt 0 ]; then
    echo -e "${YELLOW}⚠ Fehlende Pakete: ${MISSING_PACKAGES[*]}${NC}"
    echo "Installiere fehlende Pakete..."
    pip install ${MISSING_PACKAGES[*]}
else
    echo -e "${GREEN}✓ Alle Pakete installiert${NC}"
fi

# Prüfe ob Test-Dateien existieren
echo ""
echo -e "${BLUE}[3/7]${NC} Prüfe Test-Dateien..."

TEST_FILES=(
    "test_ui_blocking.py"
    "test_app_blocking_isolated.py"
    "BLOCKING_FIX_ANLEITUNG.md"
)

MISSING_FILES=()
for file in "${TEST_FILES[@]}"; do
    if [ ! -f "$file" ]; then
        MISSING_FILES+=("$file")
    fi
done

if [ ${#MISSING_FILES[@]} -gt 0 ]; then
    echo -e "${RED}✗ Fehlende Test-Dateien: ${MISSING_FILES[*]}${NC}"
    exit 1
else
    echo -e "${GREEN}✓ Alle Test-Dateien vorhanden${NC}"
fi

# Erstelle Logs-Verzeichnis
echo ""
echo -e "${BLUE}[4/7]${NC} Erstelle Test-Verzeichnis..."

mkdir -p test_results
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
REPORT_FILE="test_results/blocking_test_report_${TIMESTAMP}.md"

echo -e "${GREEN}✓ Test-Verzeichnis bereit${NC}"
echo "   Report: $REPORT_FILE"

# Generiere initialen Report
echo ""
echo -e "${BLUE}[5/7]${NC} Generiere Test-Report..."

cat > "$REPORT_FILE" <<EOF
# UI-Blocking Test Report
Generiert: $(date +"%Y-%m-%d %H:%M:%S")

## System-Informationen

- **OS**: $(uname -s)
- **Python**: $(python3 --version)
- **Streamlit**: $(streamlit --version 2>&1 | head -n1)
- **Working Directory**: $(pwd)
- **API Key gesetzt**: $API_KEY_SET

## Test-Dateien

EOF

for file in "${TEST_FILES[@]}"; do
    SIZE=$(du -h "$file" | cut -f1)
    echo "- [x] \`$file\` ($SIZE)" >> "$REPORT_FILE"
done

cat >> "$REPORT_FILE" <<EOF

## Code-Analyse

### Problematische Stellen in app.py

EOF

# Suche nach problematischen Code-Stellen
echo "   └─ Analysiere app.py..."

if [ -f "app.py" ]; then
    # Suche nach st.spinner mit invoke
    echo "#### \`st.spinner\` mit LLM-Aufrufen" >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"
    echo "\`\`\`" >> "$REPORT_FILE"

    grep -n "st.spinner\|llm.*invoke\|llm_with_tools.invoke" app.py | head -n 20 >> "$REPORT_FILE" 2>/dev/null || echo "Keine gefunden" >> "$REPORT_FILE"

    echo "\`\`\`" >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"

    # Zähle Vorkommen
    SPINNER_COUNT=$(grep -c "st.spinner" app.py 2>/dev/null || echo "0")
    INVOKE_COUNT=$(grep -c "\.invoke(" app.py 2>/dev/null || echo "0")

    echo "**Statistiken:**" >> "$REPORT_FILE"
    echo "- \`st.spinner\` Aufrufe: $SPINNER_COUNT" >> "$REPORT_FILE"
    echo "- \`.invoke()\` Aufrufe: $INVOKE_COUNT" >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"

    if [ $SPINNER_COUNT -gt 0 ] && [ $INVOKE_COUNT -gt 0 ]; then
        echo -e "${YELLOW}⚠ Gefunden: $SPINNER_COUNT spinner, $INVOKE_COUNT invoke calls${NC}"
        echo "**⚠️ WARNUNG:** Potentielles Blocking-Problem erkannt!" >> "$REPORT_FILE"
    else
        echo -e "${GREEN}✓ Keine offensichtlichen Probleme gefunden${NC}"
    fi
else
    echo -e "${YELLOW}⚠ app.py nicht gefunden${NC}"
    echo "**FEHLER:** app.py nicht gefunden" >> "$REPORT_FILE"
fi

echo "" >> "$REPORT_FILE"

# Analysiere problematischen Code-Block
echo ""
echo -e "${BLUE}[6/7]${NC} Analysiere kritischen Code-Block..."

if [ -f "app.py" ]; then
    # Suche nach der problematischen Stelle (Zeile 2452)
    LINE_NUM=$(grep -n "Bereite Antwort vor" app.py | head -n1 | cut -d: -f1 2>/dev/null || echo "0")

    if [ "$LINE_NUM" != "0" ]; then
        echo -e "${YELLOW}⚠ Kritische Stelle gefunden bei Zeile $LINE_NUM${NC}"
        echo "### Kritischer Code-Block (Zeile $LINE_NUM)" >> "$REPORT_FILE"
        echo "" >> "$REPORT_FILE"
        echo "\`\`\`python" >> "$REPORT_FILE"

        # Zeige 20 Zeilen ab dort
        sed -n "${LINE_NUM},$((LINE_NUM + 20))p" app.py >> "$REPORT_FILE"

        echo "\`\`\`" >> "$REPORT_FILE"
        echo "" >> "$REPORT_FILE"
    else
        echo -e "${GREEN}✓ 'Bereite Antwort vor' nicht gefunden (möglicherweise bereits gefixt)${NC}"
    fi
fi

# Empfehlungen
cat >> "$REPORT_FILE" <<EOF

## Empfehlungen

### 🔴 Kritisch (Sofort beheben)

1. **Ersetze \`st.spinner()\` mit \`st.status()\`**
   - Zeile 2452 in app.py
   - Ermöglicht live Updates während Verarbeitung

2. **Füge Status-Updates hinzu**
   - Nach jedem LLM-Aufruf: \`st.write("✅ Antwort erhalten")\`
   - Bei Tool-Calls: \`st.write("🔧 Führe Tool aus...")\`

### ⚡ Hoch (Diese Woche)

3. **Implementiere Streaming**
   - Nutze \`llm.stream()\` statt \`llm.invoke()\`
   - Zeige Antwort Wort-für-Wort

4. **Reduziere max_iterations**
   - Von 10 auf 5 reduzieren
   - Verhindert Endlos-Schleifen

### 💡 Medium (Nächster Sprint)

5. **Async LLM-Calls**
   - Nutze \`ainvoke()\` für bessere Performance

6. **Caching**
   - Cache häufige Anfragen mit \`@st.cache_data\`

## Nächste Schritte

1. **Manuelle Tests durchführen**:
   \`\`\`bash
   # Isolierter Test
   streamlit run test_app_blocking_isolated.py

   # Umfassender Test
   streamlit run test_ui_blocking.py
   \`\`\`

2. **Fix anwenden**:
   - Siehe \`BLOCKING_FIX_ANLEITUNG.md\`

3. **Testen nach Fix**:
   - Einfache Nachricht (kein Tool)
   - Mit 1 Tool-Call
   - Mit mehreren Tool-Calls

4. **Performance messen**:
   - Vorher: Zeit stoppen
   - Nachher: Zeit stoppen
   - Vergleichen

## Test-Befehle

\`\`\`bash
# Isolierter Test (vergleicht Original vs. Fix)
streamlit run test_app_blocking_isolated.py

# Umfassende Diagnose
streamlit run test_ui_blocking.py

# Eigene App testen
streamlit run app.py
\`\`\`

## Ressourcen

- Anleitung: \`BLOCKING_FIX_ANLEITUNG.md\`
- Test-Tool 1: \`test_app_blocking_isolated.py\`
- Test-Tool 2: \`test_ui_blocking.py\`

---

**Report Ende**
EOF

echo -e "${GREEN}✓ Report generiert${NC}"

# Zusammenfassung
echo ""
echo -e "${BLUE}[7/7]${NC} Zusammenfassung"
echo ""
echo "=============================================="
echo -e "${GREEN}✅ Test-Suite erfolgreich vorbereitet!${NC}"
echo "=============================================="
echo ""
echo "📄 Report: $REPORT_FILE"
echo ""
echo "🚀 Nächste Schritte:"
echo ""
echo "1. Lese die Anleitung:"
echo "   ${YELLOW}cat BLOCKING_FIX_ANLEITUNG.md${NC}"
echo ""
echo "2. Führe isolierten Test aus:"
echo "   ${YELLOW}streamlit run test_app_blocking_isolated.py${NC}"
echo ""
echo "3. Führe umfassende Tests aus:"
echo "   ${YELLOW}streamlit run test_ui_blocking.py${NC}"
echo ""
echo "4. Wende Fix auf app.py an"
echo "   (siehe BLOCKING_FIX_ANLEITUNG.md)"
echo ""
echo "=============================================="
echo ""

# Öffne Report automatisch (wenn möglich)
if command -v less &> /dev/null; then
    echo "Möchtest du den Report jetzt lesen? (j/n)"
    read -r response
    if [[ "$response" =~ ^[Jj]$ ]]; then
        less "$REPORT_FILE"
    fi
fi

echo ""
echo -e "${GREEN}Fertig!${NC} 🎉"
