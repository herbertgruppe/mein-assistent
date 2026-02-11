# Meeting-Protokoll: Zeiterfassungs-App_Entwicklung

**Datum:** 2026-01-12
**Teilnehmer:** Speaker 1, Speaker 2, Speaker 3 (Philipp), Speaker 4, Speaker 5

---

## Thema 1: Benutzeroberfläche und Darstellung der Zeitbuchungen

### Diskussion/Kontext
Es wurde die neue Benutzeroberfläche für die Zeiterfassungs-App vorgestellt. Die Ansicht zeigt eine Matrix mit Tagen auf der linken Seite und eine Liste der Mitarbeiter. Durch Klick auf einzelne Tage werden die Stunden des jeweiligen Mitarbeiters angezeigt. Bei Klick auf einzelne Buchungen wird rechts die History mit allen Änderungen dargestellt. Die Darstellung erfolgt pro Mitarbeiter und Tag, nicht in Gruppenansicht.

### Entscheidungen
- Die Ansicht wird pro Mitarbeiter und pro Tag einzeln dargestellt (keine Gruppenansicht)
- Rechts wird die History der Änderungen für jede Buchung angezeigt
- Symbole werden zur Statusdarstellung verwendet

### Weitere Schritte
- **Speaker 5**: Implementierung der Symbole für verschiedene Stati - Fällig: [?]
- **Speaker 5**: Integration der History-Anzeige in die Detailansicht - Fällig: [?]

---

## Thema 2: Farbcodierung und Status-System

### Diskussion/Kontext
Es wurde ein Ampelsystem zur Statusanzeige diskutiert. Grün soll angezeigt werden, wenn mindestens 4 Stunden abgenommen/freigegeben sind. Orange zeigt an, dass Zeiten vorhanden und plausibel sind, aber noch nicht freigegeben wurden. Rot bedeutet weniger als 4 Stunden oder keine Buchungen. Zusätzlich wurde vorgeschlagen, eine vierte Farbe (blau oder grün mit weißer Mitte) einzuführen für komplett abgeschlossene Tage, bei denen alle Buchungen blockiert sind.

### Entscheidungen
- Grün: ≥ 4 Stunden abgenommen/freigegeben
- Orange: Zeiten vorhanden aber noch nicht freigegeben
- Rot: < 4 Stunden oder keine Buchungen
- Vierte Farbe (blau oder modifiziertes Grün): Tag komplett abgeschlossen und blockiert

### Weitere Schritte
- **Speaker 5**: Implementierung des Farbcodierungs-Systems - Fällig: [?]
- **[?]**: Finale Festlegung der vierten Farbe für abgeschlossene Tage - Fällig: [?]

---

## Thema 3: Export-Funktionalität

### Diskussion/Kontext
Die Export-Funktion soll als separater Tab implementiert werden. Anders als in der Freigabe-Ansicht soll hier nicht für jeden Mitarbeiter jeden Tag einzeln exportiert werden. Stattdessen soll eine Gruppenauswahl möglich sein (ähnlich der alten Ansicht), bei der Mitarbeiter per Plus-Button hinzugefügt oder ganze Abteilungen ausgewählt werden können. Die Darstellung zeigt dann pro Mitarbeiter eine Zeile mit der wöchentlichen Summe der freigegebenen Stunden. Eine CSV-Export-Funktion und eine Schnittstelle sind vorgesehen.

### Entscheidungen
- Separater "Export"-Tab wird erstellt
- Export zeigt nur freigegebene/abgenommene Zeiten (keine Korrekturen)
- Darstellung: Pro Mitarbeiter eine Zeile mit wöchentlicher Summe
- Mitarbeiterauswahl per Plus-Button oder Abteilungsauswahl
- CSV-Export und Schnittstelle werden implementiert

### Weitere Schritte
- **Speaker 5**: Erstellung des separaten Export-Tabs - Fällig: [?]
- **Speaker 5**: Implementierung der Mitarbeiter-/Abteilungsauswahl - Fällig: [?]
- **Speaker 5**: Umsetzung der CSV-Export-Funktion - Fällig: [?]

---

## Thema 4: Freigabe-Prozess und Tagessperre

### Diskussion/Kontext
Es wurde diskutiert, wie der Freigabe-Prozess gestaltet werden soll. Der Vorschlag war, ganze Tage freizugeben und zu blockieren, sodass nachträglich keine neuen Buchungen mehr auf bereits freigegebene Tage erfolgen können. Dies würde verhindern, dass Mitarbeiter nachträglich Stunden auf bereits abgeschlossene Tage buchen. Das Meeting wurde an dieser Stelle unterbrochen.

### Entscheidungen
- [?]

### Weitere Schritte
- **[?]**: Klärung des finalen Konzepts zur Tagessperre - Fällig: [?]
- **[?]**: Definition der Regeln für nachträgliche Buchungen - Fällig: [?]

---

## Zusammenfassung

Das Meeting fokussierte sich auf die Weiterentwicklung der Zeiterfassungs-App mit Schwerpunkt auf Benutzeroberfläche, Freigabe-Prozess und Export-Funktionalität. Es wurde eine neue Ansicht mit Matrix-Darstellung und History-Funktion vorgestellt. Ein Ampelsystem mit vier Farbstufen zur Statusanzeige wurde konzipiert. Die Export-Funktion wird als separater Tab mit Gruppierungsmöglichkeiten und CSV-Export realisiert. Der Freigabe-Prozess mit Tagessperre wurde angesprochen, aber nicht abschließend geklärt.