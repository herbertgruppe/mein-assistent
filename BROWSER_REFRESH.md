# 🔄 Browser-Cache leeren - Schnellanleitung

## Problem
Nach Code-Änderungen werden neue Buttons nicht angezeigt, weil der Browser die alte Version im Cache hat.

## ✅ Sofort-Lösung

### Option 1: Hard Refresh (Empfohlen)

**Windows/Linux:**
```
Strg + F5
oder
Strg + Shift + R
```

**Mac:**
```
Cmd + Shift + R
```

### Option 2: Cache komplett leeren

**Chrome/Edge:**
1. Drücken Sie `F12` (Entwicklertools öffnen)
2. Rechtsklick auf den Reload-Button (neben der Adressleiste)
3. Wählen Sie "Leeren Cache und harte Aktualisierung"

**Firefox:**
1. Drücken Sie `Strg + Shift + Delete`
2. Wählen Sie "Cache"
3. Klicken Sie "Jetzt löschen"

### Option 3: Inkognito/Privat-Modus

Öffnen Sie die App in einem privaten Fenster:

**Chrome/Edge:**
```
Strg + Shift + N
```

**Firefox:**
```
Strg + Shift + P
```

Dann öffnen Sie: http://localhost:8501

## 🎯 Schritt-für-Schritt

1. **Gehen Sie zu:** http://localhost:8501
2. **Drücken Sie:** Strg + F5 (Hard Refresh)
3. **Warten Sie:** 2-3 Sekunden
4. **Prüfen Sie:** Buttons sind jetzt sichtbar

## ✨ Was Sie jetzt sehen sollten

Im Archiv-Tab, bei jedem Bericht:

```
┌─────────────────────────────────────────────────┐
│ 📄 Berichtsname                                 │
│                                                 │
│ [Bericht-Inhalt]                                │
│                                                 │
│ ─────────────────────────────────────────────  │
│                                                 │
│ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────┐│
│ │📥Download│ │📧 E-Mail │ │📦Verschieb│ │🗑️Lösch││
│ └──────────┘ └──────────┘ └──────────┘ └──────┘│
└─────────────────────────────────────────────────┘
```

**Alle 4 Buttons sollten sichtbar sein!**

## 🚨 Falls immer noch nicht sichtbar

### Prüfung 1: Streamlit läuft

```bash
ps aux | grep streamlit
```

Sollte zeigen:
```
sherbert ... streamlit run app.py
```

### Prüfung 2: Richtige URL

Stellen Sie sicher, dass Sie diese URL verwenden:
```
http://localhost:8501
```

### Prüfung 3: JavaScript aktiviert

Streamlit benötigt JavaScript. Prüfen Sie ob es aktiviert ist.

### Prüfung 4: Konsole prüfen

1. Drücken Sie `F12`
2. Gehen Sie zum Tab "Konsole"
3. Suchen Sie nach Fehlern (rot)
4. Wenn Fehler: Machen Sie Screenshot und zeigen Sie ihn mir

## 🔄 Neustart erzwingen

Falls nichts hilft:

```bash
# Im Terminal ausführen:
pkill -f streamlit
sleep 2
source venv/bin/activate
streamlit run app.py
```

Dann im Browser:
```
Strg + F5 (Hard Refresh)
```

---

**Wichtig:** Nachdem Sie Strg + F5 gedrückt haben, sollten alle neuen Features sichtbar sein!
