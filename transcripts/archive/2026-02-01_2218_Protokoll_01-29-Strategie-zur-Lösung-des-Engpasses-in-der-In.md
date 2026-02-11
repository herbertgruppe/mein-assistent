# Meeting-Protokoll: Strategie zur Lösung des Engpasses in der Inbetriebnahme

**Datum:** 29.01.2026 08:00
**Teilnehmer:** Tomislav Dukic, Philipp Scheidlock, Franjo Senk, Sven Herbert

---

## Thema 1: Aktuelle Engpass-Situation in der Inbetriebnahme

### Diskussion/Kontext
Die Inbetriebnahme-Kapazitäten sind kritisch ausgelastet. Markus Stöhr ist derzeit der einzige vollwertige Inbetriebnahme-Techniker und kann teilweise nicht einmal mehr Protokolle schreiben. Das Palais-Projekt wurde nach hinten verschoben (hätte 2-3 Wochen Inbetriebnahmezeit benötigt). Die Abhängigkeit von einer einzigen Person stellt ein erhebliches Risiko dar - bei Krankheit oder Ausfall können Baustellen nicht abgeschlossen werden. Hersteller bieten kurzfristig keine Inbetriebnahmen an (Vorlaufzeit: Monate). Ein neuer Mitarbeiter (Quereinsteiger aus dem Schaltschrankbau) ist in der Einarbeitung, verfügt aber laut Markus Stöhr über wenig relevante Vorkenntnisse. Die Einarbeitung wird voraussichtlich 1-2 Jahre dauern.

### Entscheidungen
- Die Firma wird nicht sofort zusätzliches Personal einstellen, sondern zunächst Prozesse optimieren
- Focus liegt auf systematischer Aufarbeitung der Inbetriebnahme-Prozesse statt kurzfristiger Personalaufstockung

### Weitere Schritte
- **Sven Herbert & Tomislav Dukic**: WPM-Termin vereinbaren zur Extraktion der Bauteil-Liste aus dem System - Fällig: Morgen (30.01.2026)
- **Franjo Senk**: Einarbeitung des neuen Mitarbeiters fortsetzen und Entwicklung beobachten - Fällig: Nächstes Halbjahr (Zwischenbewertung)

---

## Thema 2: Konzeptionelle Neuausrichtung der Inbetriebnahme

### Diskussion/Kontext
Sven Herbert schlägt eine grundlegende Umstrukturierung vor: Einführung eines "Inbetriebnahme-Managements" mit klarer Aufgabentrennung. Einfache Tätigkeiten (Luftmengenmessungen, Volumenstromregler-Einstellungen, hydraulischer Abgleich, Massenströme einstellen) sollen von Monteuren vor Ort durchgeführt werden. Inbetriebnahme-Techniker konzentrieren sich auf komplexe Aufgaben (RLT-Anlagen, Kältemaschinen, Regelungstechnik, Hydraulik-Troubleshooting). Problem: Markus Stöhr übernimmt derzeit auch Montage-Tätigkeiten (z.B. Ausdehnungsgefäße ablassen), die nicht zu seinen Kernaufgaben gehören. Mängellisten werden erstellt, aber nicht konsequent abgearbeitet. Die TGM-Abteilung rettet aktuell das Ergebnis - deren Kapazitäten dürfen nicht gefährdet werden.

### Entscheidungen
- Implementierung eines Inbetriebnahme-Management-Konzepts mit klarer Aufgabentrennung
- Einfache Tätigkeiten werden zukünftig von Monteuren/Service-Technikern übernommen
- Strikte Vorbereitung vor Inbetriebnahme-Terminen: Wenn Voraussetzungen nicht erfüllt sind, wird der Termin abgebrochen (wie bei externen Herstellern)

### Weitere Schritte
- **Philipp Scheidlock**: Erstellung von Inbetriebnahme-Checklisten für verschiedene Bauteiltypen in WPM/MyTGA - Fällig: Q1 2026
- **Tomislav Dukic**: Erstellung eines allgemeinen Inbetriebnahme-Fahrplans - Fällig: Q1 2026
- **Sven Herbert**: Einarbeitung in WPM-Funktionalität zur Bauteil-Extraktion - Fällig: [?]
- **Philipp Scheidlock**: Interview mit Markus Stöhr zu detaillierten Inbetriebnahme-Tätigkeiten durchführen - Fällig: [?]

---

## Thema 3: Systematisierung und Digitalisierung der Inbetriebnahme-Prozesse

### Diskussion/Kontext
Aktuell erfolgt die Inbetriebnahme weitgehend "Freestyle" basierend auf Erfahrung. WPM bietet bereits Funktionen für Inbetriebnahme-Checklisten und technische Bearbeitung, die bisher nicht genutzt werden. Vorschlag: Automatische Generierung von Bauteil-Listen aus WPM mit Zuordnung zu Bauteiltypen (z.B. Zweiwegeventil, Dreiwegeventil). Jeder Bauteiltyp erhält standardisierte Inbetriebnahme- und Wartungs-Checklisten. Protokoll-Erstellung soll über KI vereinfacht werden (Spracheingabe vor Ort). Vorbereitung und Parametrierung sollen bereits durch MSR-Abteilung vor Einbau erfolgen. Referenz: Jäger Heppmann parametriert EnOcean-Komponenten vor und kennzeichnet diese mit Aufklebern für einfachen Einbau.

### Entscheidungen
- WPM-System wird als zentrale Basis für Inbetriebnahme-Management genutzt
- Bauteiltypen werden mit standardisierten Checklisten verknüpft
- KI-gestützte Protokoll-Erstellung wird implementiert
- MSR-Parametrierung erfolgt zukünftig vor Einbau

### Weitere Schritte
- **Sven Herbert**: Bauteil-Liste aus Palais-Projekt extrahieren und Zuordnung zu Bauteiltypen prüfen - Fällig: Diese Woche
- **Philipp Scheidlock**: Zuordnung von Inbetriebnahme-Anforderungen zu Bauteil-Liste durch Markus Stöhr oder qualifizierten Mitarbeiter - Fällig: [?]
- **[?]**: Entwicklung KI-gestützter Protokoll-Erstellung (Spracheingabe) - Fällig: Mittelfristig
- **Markus Stöhr**: Integration von Zeitaufwänden in Bauteil-Checklisten - Fällig: [?]

---

## Thema 4: Priorisierung und Koordination laufender Projekte

### Diskussion/Kontext
Monatliche Abstimmungen zur Projekt-Priorisierung finden bereits mit Jörg Bartelt statt. Problem: Projekte verschieben sich zeitlich (z.B. 2 Monate nach hinten, 4 Wochen nach vorne) und überschneiden sich dann in der Inbetriebnahme-Phase. Aktuelle Projekte: TIB (Waldner-Kommunikation, Trucks-Schulung), M7 (läuft diese Woche aus, dann Ruhe). Palais: 11 Lüftungsanlagen, 3 Zerstromverteiler, Fernwärmestation, Rückkühler, Wärmepumpe - noch kein Inbetriebnahmer vorgesehen. Siemens macht MSR-Bauseite, aber Troubleshooting erfordert Hydraulik- und Regelungstechnik-Kenntnisse. Wege-Anfrage für Palais läuft, aber Hersteller haben ähnliche Kapazitätsprobleme.

### Entscheidungen
- Bestehende monatliche Priorisierungs-Meetings werden fortgeführt
- Palais-Projekt wird als Pilot-Projekt für neue Inbetriebnahme-Systematik genutzt
- Bauzeit-Verlängerung beim Palais-Projekt wurde mit saftigem Nachtrag berechnet

### Weitere Schritte
- **Philipp Scheidlock & Jörg Bartelt**: Fortsetzung monatlicher Priorisierungs-Meetings - Fällig: Laufend
- **Tomislav Dukic**: Palais-Projekt als Test-Case für neue Checklisten nutzen - Fällig: [?]

---

## Thema 5: Ressourcen-Allokation und Team-Kapazitäten

### Diskussion/Kontext
Markus Pfeiffer (25%-Stelle) hat Inbetriebnahme-Kompetenz, ist aber als Obermonteur mit vielen Baustellen ausgelastet. Franjo Senk kann ihn nicht vollständig für Inbetriebnahmen abstellen, da er viele Baustellen verantwortet. Sukzessive Übergabe an nachrückende Mitarbeiter geplant - Ziel: 50% Entlastung bis nächstes Halbjahr. TGM-Mitarbeiter haben Interesse an Inbetriebnahme-Tätigkeiten (z.B. für Palais), aber TGM-Abteilung rettet aktuell das Gesamt-Ergebnis und darf nicht geschwächt werden. TGM-Kunden sind indirekt zukünftige Projekt-Kunden. Qualität darf nicht reduziert werden, da schlechte Inbetriebnahme auch Wartungsverträge gefährdet.

### Entscheidungen
- Markus Pfeiffer bleibt primär in seiner Obermonteur-Funktion
- TGM-Kapazitäten werden geschont und nur im Notfall für Inbetriebnahmen herangezogen
- Fokus auf Prozess-Optimierung statt Personal-Umverteilung

### Weitere Schritte
- **Franjo Senk**: Sukzessive Übergabe von Markus Pfeiffer-Baustellen an nachrückende Mitarbeiter - Fällig: Nächstes Halbjahr
- **Franjo Senk**: Evaluation der Entwicklung nach 6 Monaten - Fällig: Juli 2026
- **Franjo Senk**: Einzelfallprüfung für TGM-Mitarbeiter-Einsatz bei Inbetriebnahmen - Fällig: Nach Bedarf

---

## Thema 6: Qualitätssicherung und Dokumentation

### Diskussion/Kontext
Kritisches Problem bei Diringer-Projekt: Protokolle wurden abgegeben, aber Ventile waren laut Kunde nicht eingestellt (3 Tage nach Protokoll-Übergabe). Widerspruch: Aktuell können teilweise keine Protokolle erstellt werden, aber es werden trotzdem welche abgegeben. Letzter Eindruck beim Kunden zählt am meisten - aktuell schwächeln die letzten 5% trotz guter 95% Vorleistung. Bei Inbetriebnahme kommen oft Fehler zum Vorschein, die vorher gemacht wurden. Protokoll-Erstellung muss professioneller werden. Besser: Transparenz über noch nicht durchgeführte Arbeiten statt unvollständige Protokolle.

### Entscheidungen
- Vollständige und korrekte Protokoll-Erstellung hat höchste Priorität
- Transparente Kommunikation über noch ausstehende Arbeiten statt Verschleierung
- Implementierung der KI-gestützten Protokoll-Erstellung zur Vereinfachung

### Weitere Schritte
- **[?]**: Entwicklung standardisierter Protokoll-Vorlagen für verschiedene Anlagentypen - Fällig: Q1 2026
- **[?]**: Schulung zur korrekten Protokoll-Erstellung für alle Inbetriebnahme-Beteiligten - Fällig: [?]

---

## Zusammenfassung

Das Meeting identifizierte einen kritischen Engpass in der Inbetriebnahme durch die Abhängigkeit von einer Einzelperson (Markus Stöhr). Statt kurzfristiger Personalaufstockung wurde ein systematischer Lösungsansatz entwickelt: Einführung eines Inbetriebnahme-Management-Konzepts mit klarer Aufgabentrennung zwischen einfachen Tätigkeiten (Monteure) und komplexen Inbetriebnahmen (Spezialisten). 

Kernmaßnahmen sind die Erstellung standardisierter Checklisten in WPM/MyTGA für verschiedene Bauteiltypen, die Implementierung KI-gestützter Protokoll-Erstellung und die Verlagerung von Vorbereitungsarbeiten (z.B. MSR-Parametrierung) vor den Einbau. Das Palais-Projekt dient als Pilot für die neue Systematik.

Die TGM-Abteilung bleibt geschützt, da sie aktuell das Gesamt-Ergebnis sichert. Der neue Mitarbeiter benötigt voraussichtlich 1-2 Jahre Einarbeitungszeit. Erste Schritte (Bauteil-Listen-Extraktion, Checklisten-Erstellung) werden in Q1 2026 umgesetzt.