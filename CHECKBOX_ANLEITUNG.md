# 📋 Checkbox-Funktion - Anleitung

## Layout der Inbox-Seite (von oben nach unten)

```
┌─────────────────────────────────────────────────────────────┐
│ ## 📬 Posteingang                                            │
│ 🕐 Letzter Worker-Poll: ...                                 │
├─────────────────────────────────────────────────────────────┤
│ [🔄 Jetzt aktualisieren] [Anzahl: 20] [Sortierung: ▼]      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│ ⚠️ HIER ERSCHEINT DIE BULK-AKTIONEN-BAR (nur wenn Emails   │
│    ausgewählt sind):                                         │
│                                                              │
│ 📋 3 Email(s) ausgewählt                                    │
│ [🗄️ Markierte archivieren] [✓ Als gelesen] [🔲 Aufheben]  │
│                                                              │
├─────────────────────────────────────────────────────────────┤
│ **15 ungelesene E-Mails** | 🔴 3 dringend [☑️ Alle auswählen]│
├─────────────────────────────────────────────────────────────┤
│                                                              │
│ ☐ ### Email 1 Betreff             🔴 Kritisch  😐 Neutral  │
│    **Von:** Max Mustermann                                   │
│    **Kategorie:** Rechnung                                   │
│    [📝 Zusammenfassung & Details anzeigen]                  │
│    [Asana-Projekt ▼] [📤 An Asana] [↗️ Weiterleiten] ...   │
│                                                              │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│ ☐ ### Email 2 Betreff             🟡 Normal    😊 Positiv  │
│    **Von:** Anna Schmidt                                     │
│    ...                                                       │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## So funktioniert es:

1. **Checkboxen (☐) sind ganz links vor dem Email-Betreff**
   - Nur bei Emails ohne "pending" Status
   
2. **Klicken Sie eine Checkbox an (☐ → ☑)**
   - Die Checkbox wird markiert
   
3. **Bulk-Aktionen-Bar erscheint OBEN**
   - Zwischen "Jetzt aktualisieren" und der Email-Liste
   - Zeigt: "📋 X Email(s) ausgewählt"
   - Zeigt 3 Buttons

4. **"Alle auswählen" Button ist RECHTS bei den Stats**
   - Neben "**X ungelesene E-Mails**"

## Troubleshooting

Falls Sie die Checkboxen NICHT sehen:
- Scrollen Sie ganz nach oben
- Prüfen Sie ob Emails den Status "pending_*" haben (diese haben keine Checkbox)
- Laden Sie die Seite neu (F5)

Falls die Bulk-Aktionen-Bar NICHT erscheint:
- Stellen Sie sicher dass die Checkbox wirklich angeklickt ist (☑)
- Die Bar erscheint ÜBER der Email-Liste, nicht unten
- Scrollen Sie nach oben falls nötig
