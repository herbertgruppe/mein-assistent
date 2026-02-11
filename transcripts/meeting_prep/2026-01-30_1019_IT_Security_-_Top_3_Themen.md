# IT Security - Top 3 Themen für Jour Fixe

**Datum:** 02.02.2026  
**Zusammengestellt für:** Jour Fixe WPM/ myTGA

---

## 1. Ransomware & Backup-Strategie

### Aktuelle Bedrohungslage
- **Ransomware bleibt die größte Bedrohung** für Unternehmen jeder Größe
- Angreifer zielen zunehmend auf Backup-Systeme, um Wiederherstellung zu verhindern
- Durchschnittliche Lösegeldforderungen steigen kontinuierlich

### Handlungsempfehlungen für Herbert/myTGA
- ✅ **3-2-1-Backup-Regel prüfen:** 3 Kopien, 2 verschiedene Medien, 1 offline/offsite
- ✅ **Immutable Backups:** Unveränderbare Backups implementieren
- ✅ **Regelmäßige Restore-Tests:** Backup-Wiederherstellung testen
- ✅ **Incident Response Plan:** Notfallplan für Ransomware-Angriff definieren
- ✅ **Mitarbeiter-Schulung:** Phishing-Awareness-Training (Haupteinfallstor)

### Diskussionspunkte
- Wie ist unser aktueller Backup-Status?
- Wann wurde das letzte Restore-Test durchgeführt?
- Gibt es einen dokumentierten Notfallplan?

---

## 2. Multi-Faktor-Authentifizierung (MFA) & Zugangskontrollen

### Warum jetzt besonders wichtig
- **Passwörter allein sind nicht mehr ausreichend**
- Gestohlene Credentials sind häufigste Ursache für Datenlecks
- Regulatorische Anforderungen (NIS2, DSGVO) verschärfen sich

### Handlungsempfehlungen für Herbert/myTGA
- ✅ **MFA für alle kritischen Systeme:** Microsoft 365, VPN, Admin-Zugänge
- ✅ **Privileged Access Management:** Admin-Rechte minimieren und überwachen
- ✅ **Zero Trust Prinzip:** "Never trust, always verify"
- ✅ **Passwort-Richtlinien überprüfen:** Passkey/Passwordless wo möglich
- ✅ **Offboarding-Prozess:** Account-Verwaltung bei Austritt (siehe Aufgabe "H.Plus - Ruhestand")

### Diskussionspunkte
- Wo haben wir bereits MFA implementiert?
- Welche kritischen Systeme sind noch ungeschützt?
- Wie handhaben wir mobile Mitarbeiter (Dukic, Schneider, Dreier, Thiedt)?
- Account-Management bei Ruhestand/Austritt (H.Plus)

---

## 3. Cloud-Security & Mobile Device Management (MDM)

### Relevanz für Herbert/myTGA
- **Zunehmende mobile Arbeitsplätze** (siehe Aufgaben: iPads für Dukic/Schneider, mobile PCs)
- Cloud-Dienste (Microsoft 365, Autodesk, ekkodale) erfordern spezielle Absicherung
- BYOD vs. Corporate Devices

### Handlungsempfehlungen für Herbert/myTGA
- ✅ **Mobile Device Management (MDM):** Intune oder alternatives MDM für iPads/Laptops
- ✅ **Cloud Access Security Broker (CASB):** Überwachung Cloud-Zugriffe
- ✅ **Data Loss Prevention (DLP):** Verhinderung ungewollter Datenabflüsse
- ✅ **Conditional Access:** Zugriff nur von verwalteten/sicheren Geräten
- ✅ **Verschlüsselung:** Geräte- und Datenverschlüsselung für mobile Mitarbeiter

### Diskussionspunkte
- Wie verwalten wir die neuen iPads (Dukic, Schneider)?
- MDM-Lösung vorhanden oder geplant?
- Richtlinien für mobiles Arbeiten (Dreier, Thiedt)?
- Datenzugriff von privaten Geräten?

---

## Zusammenfassung & Priorisierung

| Thema | Dringlichkeit | Aufwand | Impact |
|-------|---------------|---------|--------|
| **Ransomware & Backup** | 🔴 Hoch | Mittel | Kritisch |
| **MFA & Zugangskontrollen** | 🔴 Hoch | Niedrig-Mittel | Hoch |
| **Cloud & MDM** | 🟡 Mittel | Mittel-Hoch | Hoch |

---

## Nächste Schritte

1. **Quick Wins identifizieren:** Was können wir sofort umsetzen?
2. **Verantwortlichkeiten klären:** Wer kümmert sich um welches Thema?
3. **Budget/Ressourcen:** Welche Investitionen sind notwendig?
4. **Timeline:** Roadmap für IT-Security-Maßnahmen 2026

---

## Verknüpfung mit bestehenden Aufgaben

- **IT-Ausstattung (iPads, mobile PCs)** → MDM-Strategie erforderlich
- **H.Plus Ruhestand** → Account-Management & Offboarding-Prozess
- **Zugriffe HRN (Todoran, DIM/Lev)** → MFA & Berechtigungskonzept
- **Autodesk, ekkodale** → Cloud-Security-Überprüfung

---

*Dieses Dokument dient als Diskussionsgrundlage und sollte an die spezifischen Bedürfnisse von Herbert/myTGA angepasst werden.*