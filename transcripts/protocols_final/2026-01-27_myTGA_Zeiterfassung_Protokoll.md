# Meeting-Protokoll: myTGA_Zeiterfassung

**Datum:** 2026-01-12
**Teilnehmer:** Speaker 1, Speaker 2, Speaker 3 (Philipp), Speaker 4, Speaker 5

---

## Thema 1: Benutzeroberfläche und Darstellung der Zeiterfassung

### Diskussion/Kontext
Speaker 5 präsentierte die neue Matrix-Ansicht im Portal, die eine Übersicht der Mitarbeiterstunden pro Tag ermöglicht. Die Ansicht zeigt eine Liste der Mitarbeiter, bei der auf einzelne Tage geklickt werden kann, um die Stunden des jeweiligen Mitarbeiters anzuzeigen. Bei Klick auf einzelne Buchungen wird rechts die History der Änderungen angezeigt. Es wurde diskutiert, ob die Ansicht für mehrere Mitarbeiter gleichzeitig oder nur für einzelne Mitarbeiter erfolgen soll.

### Entscheidungen
- Die Ansicht erfolgt pro einzelnem Mitarbeiter, nicht als Gruppenansicht für mehrere Mitarbeiter gleichzeitig
- Es wird ein zusätzlicher Tab für den Export erstellt (getrennt von der aktuellen Teilauswertungs-Ansicht)

### Weitere Schritte
- **Jannik **: Erstellung eines separaten Export-Tabs mit Mitarbeiterauswahl und wöchentlicher Summenansicht - Fällig: [?]
- **Jannik**: Implementierung der History-Anzeige für Buchungsänderungen - Fällig: [?]

---

## Thema 2: Farbcodierung und Status-System

### Diskussion/Kontext
Es wurde ein Ampelsystem zur visuellen Darstellung des Buchungsstatus diskutiert. Die Farbcodes sollen den Bearbeitungsstatus der Zeitbuchungen anzeigen. Speaker 3 schlug vor, eine zusätzliche Farbe (blau) für abgeschlossene Tage einzuführen, bei denen alle Buchungen blockiert sind.

### Entscheidungen
- **Grün**: Mindestens 4 Stunden wurden freigegeben/abgenommen
- **Orange**: Zeiten sind vorhanden und erscheinen plausibel, wurden aber noch nicht freigegeben
- **Rot**: Weniger als 4 Stunden oder keine Buchungen vorhanden
- **Blau/Alternative Darstellung**: Für vollständig abgeschlossene Tage mit blockierten Buchungen (genaue Farbwahl noch offen)

### Weitere Schritte
- **Jannik**: Implementierung des Farbcodierungs-Systems gemäß den definierten Regeln - Fällig: [?]


---

## Thema 3: Freigabe- und Export-Funktionalität

### Diskussion/Kontext
Die Freigabe-Funktion wurde detailliert besprochen. Bei Klick auf "Freigeben" wird ein neuer History-Eintrag mit geändertem Status erstellt. Für den Export soll eine separate Ansicht geschaffen werden, die sich vom bisherigen Freigabe-View unterscheidet. Die Export-Ansicht soll Mitarbeiterauswahl ermöglichen und wöchentliche Summen der freigegebenen Zeiten pro Mitarbeiter anzeigen.

### Entscheidungen
- Freigabe erzeugt einen neuen History-Eintrag mit Status "abgenommen"
- Export-View zeigt nur freigegebene/abgenommene Zeiten (keine Korrekturen)
- Export-Ansicht basiert auf dem alten Modell mit Mitarbeiterauswahl via Plus-Button
- Pro Mitarbeiter wird eine Zeile mit der Summe der abgenommenen Wochenstunden angezeigt
- CSV-Export wird implementiert (zusätzlich zur geplanten Schnittstelle)

### Weitere Schritte
- **Speaker 5**: Kopieren und Anpassen des bestehenden Views für die Export-Funktionalität - Fällig: [?]
- **Speaker 5**: Implementierung der CSV-Export-Funktion - Fällig: [?]
- **[?]**: Entwicklung der Schnittstelle für automatisierten Export - Fällig: [?]

---

## Thema 4: Tages-Blockierung und nachträgliche Buchungen

### Diskussion/Kontext
Speaker 3 brachte die Idee ein, ganze Tage zu blockieren, nachdem sie freigegeben wurden. Dies soll verhindern, dass Mitarbeiter nachträglich noch Buchungen auf bereits freigegebene Tage vornehmen können. Die Diskussion wurde am Ende des Transkripts begonnen, aber nicht vollständig abgeschlossen.

### Entscheidungen
- [?]

### Weitere Schritte
- **[?]**: Klärung der Anforderungen zur Tages-Blockierung nach Freigabe - Fällig: [?]
- **[?]**: Entscheidung über Implementierung der Blockierungsfunktion - Fällig: [?]

---

## Zusammenfassung

Das Meeting fokussierte sich auf die Weiterentwicklung der Zeiterfassungs-App myTGA. Die Hauptthemen waren die Gestaltung der Benutzeroberfläche mit einer Matrix-Ansicht pro Mitarbeiter, die Implementierung eines Farbcodierungs-Systems zur Statusanzeige (grün/orange/rot/blau), sowie die Entwicklung einer separaten Export-Funktionalität. Es wurde entschieden, dass nur freigegebene Zeiten exportiert werden und die Export-Ansicht wöchentliche Summen pro Mitarbeiter darstellt. Die Frage der Tages-Blockierung nach Freigabe wurde angesprochen, bedarf aber noch weiterer Klärung.