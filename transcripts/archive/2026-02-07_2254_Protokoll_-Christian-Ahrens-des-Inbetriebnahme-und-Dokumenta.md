# Meeting-Protokoll: Inbetriebnahme- und Dokumentationsprozess in MyTGA-Software

**Datum:** 02.02.2026 14:30
**Teilnehmer:** Sven Herbert, Christian Ahrens

---

## Thema 1: Status Funktionstypen und technische Bearbeitung in MyTGA

### Diskussion/Kontext
Sven Herbert hatte Schwierigkeiten beim Verständnis der technischen Bearbeitung und beim Anlegen von Funktionstypen in den Grunddaten. Christian Ahrens erklärte, dass die Entwicklung mit Weigelsdorfer eingestellt wurde, da deren MSR-Abteilung bereits Kabellisten und Inbetriebnahme durchführt. Die Schnittstelle zum System funktioniert bereits - alle Bestellungen sind sichtbar. Im Hintergrund wird aktuell an der Verknüpfung der Funktionstypen gearbeitet, um Bauteile für die Inbetriebnahme zuzuordnen.

### Entscheidungen
- Entwicklung der Funktionstypen-Verknüpfung wird fortgesetzt, trotz Einstellung der Zusammenarbeit mit Weigelsdorfer
- Fokus liegt auf Bestandsdokumentation mit automatischer Datenübernahme

### Weitere Schritte
- **Christian Ahrens**: Funktionstypen-Verknüpfung fertigstellen - Fällig: [?]
- **[?]**: Prüfung der Klassiﬁzierung in MyTGA für Raumzuordnung - Fällig: [?]

---

## Thema 2: Klassiﬁzierung und Raumzuordnung in MyTGA

### Diskussion/Kontext
Christian Ahrens demonstrierte die Klassiﬁzierungsfunktion in MyTGA (Projekt Keplerring 7, Klingerstraße 10). LV-Positionen können per Drag & Drop auf Raumstrukturen gezogen werden. Wenn Funktionstypen hinterlegt sind, werden automatisch alle Inbetriebnahme-Checklisten für die Bauteile erstellt. Die Bestandsdokumentation kann mit Verknüpfungen direkt in die Raumebene übertragen werden, wodurch Wartung, Inbetriebnahme und alle relevanten Daten zentral verfügbar sind.

### Entscheidungen
- Bauteile sollen über Klassiﬁzierung den Räumen zugeordnet werden
- Bestandsdokumentation wird als Basis für Inbetriebnahme genutzt
- Anlagenstruktur (nicht nur Gruppen) wird für die Zuordnung verwendet

### Weitere Schritte
- **[?]**: Entwicklung einer zusätzlichen Ansicht zur Übersicht bereits zugeordneter Bauteile - Fällig: [?]
- **Christian Ahrens/Philipp**: Fertigstellung der Anlagenstruktur-Funktionalität - Fällig: [?]

---

## Thema 3: Workaround für aktuelle Inbetriebnahme-Anforderungen

### Diskussion/Kontext
Sven Herbert benötigt dringend eine Lösung für ca. 10 Bauteile bei einem sensiblen Kunden. Christian Ahrens zeigte den Workaround über das alte Portal-System: Inbetriebnahme-Typen können in den Bauteil-Einstellungen hinterlegt werden. In der App können dann die Checklisten direkt aufgerufen und ausgefüllt werden. Die Funktionalität existiert bereits im alten System und kann sofort genutzt werden.

### Entscheidungen
- Übergangsweise wird das alte Portal-System für Inbetriebnahme genutzt
- Projektleiter liefern Excel-Listen mit Bauteilen, die in Betrieb genommen werden müssen
- Inbetriebnahme-Typen werden manuell in den Einstellungen angelegt

### Weitere Schritte
- **Sven Herbert**: Excel-Liste mit Bauteilen von Projektleitern anfordern - Fällig: [?]
- **Sven Herbert**: Inbetriebnahme-Typen im alten Portal anlegen (5-10 Stück) - Fällig: [?]
- **[?]**: Schulung der Monteure zur Nutzung der Inbetriebnahme-Funktion in der App - Fällig: [?]

---

## Thema 4: Funktionsgruppen und Wartungstypen-Verknüpfung

### Diskussion/Kontext
Christian Ahrens demonstrierte die Funktionsgruppen im Artikelarchiv (z.B. ungeregelter Heizkreis). Bauteile können bereits den Gruppen zugeordnet werden, was schnelle Kostenschätzungen und Angebotserstellung ermöglicht. Für jede Gruppe können Wartungstypen, Inbetriebnahme-Typen und Checklisten hinterlegt werden. Die Verknüpfung zu Symbolen aus der CAD-Bibliothek ist geplant. Hubert Schäfer hat Funktionstypen für alle Gewerke außer Kälte, MSR und Elektronik bereits vorbereitet.

### Entscheidungen
- Funktionsgruppen werden als Basis für standardisierte Prozesse genutzt
- Jede Gruppe erhält eigene Checklisten für Wartung und Inbetriebnahme
- Bibliothek mit Funktionsbildern/Schemen wird benötigt

### Weitere Schritte
- **Sven Herbert**: Normen und Funktionsbilder an Christian Ahrens übergeben - Fällig: [?]
- **Hubert Schäfer**: Fehlende Funktionstypen für Kälte, MSR und Elektronik ergänzen - Fällig: [?]
- **Christian Ahrens**: Checklisten für alle Funktionsgruppen erstellen - Fällig: Mitte 2026

---

## Thema 5: Herstellerdaten-Integration (Reﬂex-Beispiel)

### Diskussion/Kontext
Christian Ahrens hat die komplette Reﬂex-Webseite in das Portal integriert. Produktdaten sind in der gleichen Struktur wie auf der Herstellerwebseite verfügbar. Ziel ist die Integration weiterer Hersteller (z.B. Mapress) mit vollständigen Katalogen, Kürzeln und Kalkulationsminuten. Die Industrie zeigt Bereitschaft zur Zusammenarbeit und Datenbereitstellung. Auslegungstools der Hersteller sollen direkt verknüpft werden.

### Entscheidungen
- Schrittweise Integration aller relevanten Hersteller nach Reﬂex-Vorbild
- Herstellerdaten werden zentral gepﬂegt und standardisiert
- Auslegungstools werden direkt aus dem System heraus aufrufbar

### Weitere Schritte
- **Christian Ahrens**: Integration weiterer Hersteller (Mapress, etc.) - Fällig: Mitte 2026
- **[?]**: Verhandlungen mit Industrie über Datenzugang - Fällig: [?]
- **[?]**: Verknüpfung Herstellerdaten mit Funktionstypen - Fällig: [?]

---

## Thema 6: Export-Funktionen für Bauteil-Listen

### Diskussion/Kontext
Sven Herbert benötigt eine Möglichkeit, Bauteillisten aus dem LV zu exportieren, um zu entscheiden, welche Bauteile in Betriebnahme gehen. Christian Ahrens zeigte zwei Export-Optionen: 1) Excel-Export aus der Projektsteuerung mit Kennzeichnungsmöglichkeit (S für Selbst, F für Fremdleister), 2) Aufmaß-Export mit detaillierter Aufschlüsselung nach Positionen und Mengen. Beide Varianten zeigen Bestellmengen und -zeitpunkte an.

### Entscheidungen
- Excel-Export aus Projektsteuerung wird als primäre Methode genutzt
- Aufmaß-Export dient als Alternative mit detaillierterer Ansicht
- Kennzeichnungssystem (S/F) wird für Zuständigkeitsklärung verwendet

### Weitere Schritte
- **Sven Herbert**: Excel-Export-Problem mit Daniel klären - Fällig: [?]
- **Sven Herbert**: Export-Funktion mit Projektleitern testen - Fällig: [?]

---

## Thema 7: Konzept für durchgängiges Inbetriebnahme-Modul

### Diskussion/Kontext
Christian Ahrens präsentierte das Konzept für ein neues Inbetriebnahme-Modul: Links werden Raumebenen angezeigt, rechts können Bauteile per Drag & Drop zugeordnet werden. Nach Zuordnung werden alle Daten (Bestandsdoku, Vorschaubilder, Checklisten) automatisch in die räumliche Ebene übertragen. Das Modul soll zwischen Controlling und Bestandsunterlagen positioniert werden. Ziel ist ein durchgängiger Prozess von Kalkulation über Bestellung, Inbetriebnahme bis zur Wartung.

### Entscheidungen
- Neues Inbetriebnahme-Modul wird nach beschriebenem Konzept entwickelt
- Modul wird zwischen Controlling und Bestandsunterlagen integriert
- Durchgängiger Datenfluss von Kalkulation bis Wartung ist Kernziel

### Weitere Schritte
- **Christian Ahrens**: Konzept für Inbetriebnahme-Modul ausarbeiten - Fällig: [?]
- **Christian Ahrens/Philipp**: Programmierung des neuen Moduls - Fällig: [?]
- **[?]**: Anforderungen für Aufsplittung von Bauteilen definieren (z.B. Kessel 1/2) - Fällig: [?]

---

## Thema 8: Datenfluss und Prozessintegration

### Diskussion/Kontext
Sven Herbert beschrieb die gewünschte durchgängige Datenkette: Bauteile aus LV → Artikelnummern → Anlagenstruktur → Inbetriebnahme (mit Bildern, Einstellwerten) → Wartung. Unterscheidung zwischen mengenbasierten Bauteilen und solchen, die einzeln erfasst werden müssen (mit Ort und Anlage). Produktdaten, Montagevideos, Auslegungstools sollen bereits bei Bestellung verfügbar sein. Inbetriebnehmer macht Fotos und ergänzt Einstellwerte, Wartung nutzt dann alle vorhandenen Daten.

### Entscheidungen
- Durchgängiger Datenfluss von Bestellung bis Wartung wird implementiert
- Bauteile werden nach Bedarf mengenbasiert oder einzeln erfasst
- Modul zur Markierung und Aufsplittung von Bauteilen wird benötigt

### Weitere Schritte
- **[?]**: Modul für Bauteil-Markierung und Mengenaufsplittung entwickeln - Fällig: [?]
- **[?]**: Verknüpfung Bestellnummern → Anlagenstruktur automatisieren - Fällig: [?]
- **[?]**: Workflow für Bilderfassung durch Inbetriebnehmer definieren - Fällig: [?]

---

## Thema 9: Wartungsmodul und historische Daten

### Diskussion/Kontext
Christian Ahrens erläuterte das Konzept für das neue Wartungsmodul: Es soll sowohl ursprüngliche Einstellwerte (aus Inbetriebnahme) als auch aktuelle Werte (aus letzter Wartung) erfassen und vergleichen. Das neue Modul baut auf der Anlagenstruktur auf und ermöglicht Zuordnung zu mehreren Gewerken (z.B. Fühler zu MSR und Heizung). Die AGE (Arbeitsgemeinschaft?) zeigt Interesse an gemeinsamer Entwicklung. Ziel ist ein einheitliches Tool von A bis Z.

### Entscheidungen
- Neues Wartungsmodul wird entwickelt mit Vergleich Soll-/Ist-Werte
- Mehrfachzuordnung von Bauteilen zu Gewerken wird ermöglicht
- Zusammenarbeit mit AGE für Standardisierung wird angestrebt

### Weitere Schritte
- **Christian Ahrens**: Wartungsmodul-Konzept fertigstellen - Fällig: [?]
- **[?]**: Arbeitskreis für einheitliche Wartungs-Checklisten organisieren - Fällig: [?]
- **Christian Ahrens/Markus**: Neue Oberﬂäche fertigstellen - Fällig: ca. 2 Monate

---

## Thema 10: Zeiterfassungssystem - Status und offene Punkte

### Diskussion/Kontext
Sven Herbert präsentierte den aktuellen Stand der Zeiterfassung: Kalenderwochen-Ansicht mit Mitarbeitern, automatische und manuelle Buchungen sind sichtbar. Prüfschritt für Projektleiter ist implementiert. Problem: Mitarbeiter Unnertel korrigiert systematisch seine Zeiten (z.B. 8:27-13:29 → 7:00-12:30). Pausenabzug funktioniert noch nicht korrekt. Buchungstypen können konfiguriert werden (Pausen, bezahlte Arbeitszeit, Zulagen, Notdienst). Monteure sind grundsätzlich zufrieden.

### Entscheidungen
- Zeiterfassungssystem geht in Testphase mit Service/Kundendienst
- Funktion "Tag freigegeben" mit visueller Markierung ist zwingend erforderlich vor Produktivstart
- Pausenabzug muss noch korrigiert werden

### Weitere Schritte
- **Philipp/Entwickler**: Funktion "Tag freigegeben" mit blauer Markierung implementieren - Fällig: vor Produktivstart
- **Philipp/Entwickler**: Pausenabzug in Gesamtstunden-Berechnung korrigieren - Fällig: [?]
- **Sven Herbert**: Zeitmanipulation durch Mitarbeiter Unnertel prüfen - Fällig: [?]

---

## Thema 11: Fahrzeiten-Erfassung und Abrechnung

### Diskussion/Kontext
Diskussion über die Erfassung von Fahrzeiten: Problem der Projektzuordnung bei mehreren Kunden pro Tag. Aktueller Ansatz: Hinfahrt wird wie Rüstzeit auf verschiedene Projekte verteilt, Arbeitszeit beim Kunden wird normal erfasst, Rückfahrt zur Firma muss nachträglich zugeordnet werden. Heimfahrt ist Privatzeit. Langfristige Idee: Pauschalisierte Fahrzeiten pro Kunde im System hinterlegen, unabhängig vom tatsächlichen Startort.

### Entscheidungen
- Übergangsweise: Hinfahrt wird auf Projekte verteilt, Rückfahrt nachträglich zugeordnet
- Heimfahrt gilt als Privatzeit und wird nicht erfasst
- Langfristig: Pauschalisierte Fahrzeiten pro Kunde

### Weitere Schritte
- **Sven Herbert**: Fahrzeiten-Konzept mit Monteuren finalisieren - Fällig: [?]
- **[?]**: Pauschalisierte Fahrzeiten pro Kunde im System hinterlegen - Fällig: [?]
- **[?]**: Schulung Monteure zur korrekten Fahrzeiten-Erfassung - Fällig: [?]

---

## Thema 12: Integration Zeiterfassung mit Projektsteuerung

### Diskussion/Kontext
Sven Herbert plant Verknüpfung zwischen Projektsteuerung und Zeiterfassung: Link vom Projekt zum Kalender soll schnellen Zugriff auf erfasste Zeiten ermöglichen. Ziel ist Matching von erfassten Stunden mit abgerechneten Stunden beim Kunden. Projektleiter sollen nur ihre eigenen Monteure sehen und prüfen können. Integration mit Fuhrpark-Kontrolle (Ortungssystem) ist geplant zur Plausibilitätsprüfung.

### Entscheidungen
- Link von Projekt zu Kalender/Zeiterfassung wird implementiert
- Projektleiter erhalten gefilterte Ansicht nur für ihre Monteure
- Fuhrpark-Ortungssystem wird zur Kontrolle eingebunden

### Weitere Schritte
- **[?]**: Link Projekt → Kalender implementieren - Fällig: [?]
- **[?]**: Filterung nach Projektleiter/Monteur-Zuordnung entwickeln - Fällig: [?]
- **[?]**: Integration Fuhrpark-Ortungssystem konzipieren - Fällig: [?]

---

## Thema 13: Kosteneinsparung durch Automatisierung

### Diskussion/Kontext
Durch Automatisierung der Zeiterfassung und Reduzierung manueller Dateneingabe werden erhebliche Einsparpotenziale gesehen. Sven Herbert hat als Ziel ausgegeben, bis Mitte des Jahres eine Person in der Personalabteilung einsparen zu können. Christian Ahrens sieht Einsparungen auch bei der Lohnbuchhaltung durch Wegfall von Abtipp- und Kontrollarbeiten.

### Entscheidungen
- Ziel: Einsparung einer Stelle in Personalabteilung bis Mitte 2026
- Automatisierung reduziert Aufwand in Lohnbuchhaltung
- Projektleiter-Prüfung ersetzt manuelle Kontrollen

### Weitere Schritte
- **Sven Herbert**: Prozesse in Personalabteilung analysieren und optimieren - Fällig: Mitte 2026
- **[?]**: Schnittstelle zur Lohnbuchhaltung automatisieren - Fällig: [?]

---

## Zusammenfassung

Das Meeting behandelte umfassend den Status und die Weiterentwicklung des Inbetriebnahme- und Dokumentationsprozesses in MyTGA. Zentrale Erkenntnisse:

**Inbetriebnahme:** Ein durchgängiges System von der Bestellung über die Inbetriebnahme bis zur Wartung wird entwickelt. Übergangsweise kann das alte Portal-System für dringende Fälle genutzt werden. Die Klassiﬁzierung und Raumzuordnung in MyTGA funktioniert bereits, benötigt aber noch die Fertigstellung der Funktionstypen-Verknüpfung.

**Datenintegration:** Die Integration von Herstellerdaten (Reﬂex als Pilotprojekt) zeigt vielversprechende Ergebnisse. Alle relevanten Produktdaten, Auslegungstools und Checklisten sollen zentral verfügbar sein. Die Zusammenarbeit mit der Industrie läuft positiv.

**Zeiterfassung:** Das System ist grundsätzlich funktionsfähig und wird von Monteuren akzeptiert. Vor dem Produktivstart müssen noch die Tag-Freigabe-Funktion und der Pausenabzug implementiert werden. Die Fahrzeiten-Erfassung benötigt noch Feinabstimmung.

**Wirtschaftlichkeit:** Durch die Automatisierung werden signifikante Kosteneinsparungen erwartet, mit dem Ziel einer Stelleneinsparung bis Mitte 2026.