# Absender-Profil für die Mail-Triage

Erzeugt aus Svens Mail-Archiv eine Einstufung pro Absender. Der Mail-Triage-Poller
nutzt sie, um zu entscheiden, welche `ablegen`-Urteile die Mail auch tatsächlich
archivieren dürfen — und welche nur ein Vorschlag bleiben.

## Warum

Vor dem Mail-Konzept 2026-09 archivierte der Poller jede Mail, die das Sprachmodell
als „ablegen" einstufte. Bei rund 920 Ablegen-Urteilen pro Monat verschwanden damit
potenziell auch Mails still aus dem Blickfeld, die dort nicht hingehörten.

Das Profil liefert die fehlende Beweislast: Hat Sven einem Absender in der
Vergangenheit **je** geantwortet? Wenn nicht, und die Historie ist ausreichend lang,
darf automatisch archiviert werden. Sonst nicht.

## Ablauf

```bash
# 1. Archiv-Metadaten einlesen (nur Metadaten — keine Mailinhalte)
python3 scripts/mail_archive_crawl.py

# 2. Profil daraus berechnen
python3 scripts/build_sender_profile.py

# 3. Ergebnis dorthin legen, wo der Poller es erwartet
install -o mein-assistent -g mein-assistent -m 0644 \
    /root/mail-archive/sender_profile.db \
    /var/lib/mail-triage-poller/sender_profile.db
```

Der Crawl ist resumierbar — bereits vollständig gelesene Ordner werden übersprungen.

## Einstufungen

| Einstufung | Kriterium | Wirkung im Poller |
|---|---|---|
| `safe_archive` | ≥20 Mails Historie **und** 0 Reaktionen | `ablegen` archiviert automatisch |
| `conversational` | ≥10 Mails **und** ≥50 % Reaktionsquote | nie automatisch archivieren |
| `neutral` | alles andere | `ablegen` bleibt Vorschlag im Posteingang |

„Reaktion" heißt: eine gesendete Mail mit derselben `conversationId`. Weiterleitungen
zählen mit. Svens eigene Adressen sind ausgeschlossen, weil Selbstversand die Quote
verfälscht.

## Stand der Erstauswertung (2026-09-14)

| | |
|---|---:|
| ausgewertete Posteingangsmails | 59.580 |
| gesendete Mails zum Abgleich | 36.535 |
| verschiedene Absender | 4.544 |
| davon `safe_archive` | 261 |
| davon `conversational` | 33 |
| abgedecktes Volumen durch Auto-Archivierung | 41,4 % |

Nur 4,8 % aller Posteingangsmails haben je eine Reaktion ausgelöst.

## Auffrischen

Das Profil altert langsam — neue Absender landen zunächst in `neutral` und werden
damit konservativ behandelt. Ein Neulauf alle paar Monate genügt. Nach dem Neulauf
den Poller neu starten, damit der Cache verworfen wird:

```bash
systemctl restart mein-assistent-mail-triage-poller
```

## Datenschutz

Beide Skripte lesen ausschließlich **Metadaten**: Absender, Betreff, Ordner,
Zeitstempel, `conversationId`, Anhang-Flag. Keine Mailinhalte, keine Anhänge.
Die erzeugte `archive.db` bleibt auf dem Server und gehört nicht ins Repository.
