"""Test-Fixture für BL-HRN 2026-05-21 — Dragan-Halluzinations-Regression (HBE-277).

Modelliert die exakte Klasse aus HBE-273:
- Outlook-Termin mit Dragan Mihaljevic als eingeladenem, aber nicht-anwesendem
  Teilnehmer (responseStatus.response = "notResponded").
- Plaud-Transkript mit den 6 tatsächlich anwesenden Sprechern.
- Dragan wird im Transkript NUR als Gegenstand erwähnt
  („über Dragans Urlaub gesprochen"), nicht als Sprecher.

Die Fixture liefert Outlook-MS-Graph-rohe Strukturen, damit `_format_attendees`
echt durchläuft und HBE-274 (responseStatus-Durchreichung) regression-fest geprüft
wird.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Outlook MS-Graph-Roh-Event (so wie es vom Graph-API kommt, vor _format_attendees)
# ---------------------------------------------------------------------------
OUTLOOK_RAW_EVENT = {
    "id": "AAMkAGI2NGQ4NGFlBL-HRN-2026-05-21-fixture",
    "subject": "BL-HRN — Bornemann Bereichsleiterrunde",
    "start": {"dateTime": "2026-05-21T13:00:00.0000000", "timeZone": "UTC"},
    "end": {"dateTime": "2026-05-21T15:00:00.0000000", "timeZone": "UTC"},
    "location": {"displayName": "Bornemann HQ + Teams"},
    "bodyPreview": "Bereichsleiterrunde HRN — Quartals-Review",
    "organizer": {
        "emailAddress": {
            "name": "Sven Herbert",
            "address": "sven.herbert@herbertgruppe.com",
        }
    },
    "attendees": [
        {
            "type": "required",
            "status": {"response": "accepted", "time": "2026-05-20T08:00:00Z"},
            "emailAddress": {
                "name": "Sven Herbert",
                "address": "sven.herbert@herbertgruppe.com",
            },
        },
        {
            "type": "required",
            "status": {"response": "accepted", "time": "2026-05-20T08:01:00Z"},
            "emailAddress": {
                "name": "Thomas Winzer",
                "address": "thomas.winzer@herbertgruppe.com",
            },
        },
        {
            "type": "required",
            "status": {"response": "accepted", "time": "2026-05-20T08:02:00Z"},
            "emailAddress": {
                "name": "Frank Herbert",
                "address": "frank.herbert@herbertgruppe.com",
            },
        },
        {
            "type": "required",
            "status": {"response": "accepted", "time": "2026-05-20T08:03:00Z"},
            "emailAddress": {
                "name": "Bernd Herbert",
                "address": "bernd.herbert@herbertgruppe.com",
            },
        },
        {
            "type": "required",
            "status": {"response": "accepted", "time": "2026-05-20T08:04:00Z"},
            "emailAddress": {
                "name": "Dennis Appelshäuser",
                "address": "dennis.appelshaeuser@herbertgruppe.com",
            },
        },
        {
            "type": "required",
            "status": {"response": "accepted", "time": "2026-05-20T08:05:00Z"},
            "emailAddress": {
                "name": "Philipp Scheidlock",
                "address": "philipp.scheidlock@herbertgruppe.com",
            },
        },
        # Die regressionsrelevante Person: eingeladen, aber nicht zugesagt,
        # nicht anwesend. Vor HBE-274 wurde dieser Eintrag als Anwesenheit
        # interpretiert (Klasse Dragan-Halluzination, HBE-273).
        {
            "type": "required",
            "status": {"response": "notResponded", "time": "0001-01-01T00:00:00Z"},
            "emailAddress": {
                "name": "Dragan Mihaljevic",
                "address": "dragan.mihaljevic@bornemann.de",
            },
        },
    ],
}


# ---------------------------------------------------------------------------
# Plaud-Mail (so wie sie als PendingTranscript-Rohstruktur vorliegt)
# ---------------------------------------------------------------------------
# Subject mit erkennbarem Datum + Zeit-Range (treibt HBE-275 meeting_time_hint).
PLAUD_MAIL_SUBJECT = "2026-05-21 BL-HRN Besprechung 13:00-15:00"
PLAUD_MAIL_RECEIVED_AT = "2026-05-22T05:14:00Z"  # Mail kommt Tag danach an

# Transkript-Auszug: 6 echte Sprecher, Dragan nur als Gegenstand
# („über Dragans Urlaub"), niemals als Sprecher.
PLAUD_TRANSCRIPT_TEXT = """Thomas Winzer: Guten Morgen zusammen, willkommen zur BL-HRN Runde.
Sven Herbert: Danke, Thomas. Lasst uns mit den Q2-Zahlen anfangen.
Frank Herbert: Ich habe die Umsätze für Bornemann zusammengefasst.
Bernd Herbert: Die Margen sehen besser aus als im Vorjahr.
Dennis Appelshäuser: Bei den Service-Aufträgen haben wir 12% Wachstum.
Philipp Scheidlock: Auf der IT-Seite läuft die Migration nach Plan.
Sven Herbert: Wir müssen kurz über Dragans Urlaub sprechen — er ist diese Woche nicht da.
Thomas Winzer: Genau, Dragan kommt erst nächste Woche zurück. Vertretung läuft über mich.
Frank Herbert: Verstanden. Dann nehmen wir das nächste TOP.
Bernd Herbert: Personalplanung — wir haben drei offene Stellen.
"""

PLAUD_MAIL = {
    "message_id": "AQMkAGUyZmYyNjE5BL-HRN-fixture",
    "subject": PLAUD_MAIL_SUBJECT,
    "received_at": PLAUD_MAIL_RECEIVED_AT,
    "sender_name": "Plaud Notes",
    "sender_email": "notes@plaud.ai",
    "body_preview": "BL-HRN Bereichsleiterrunde — Transkript",
    "body_text": (
        "Hallo Sven,\n\nhier ist dein Transkript der Besprechung "
        f"„{PLAUD_MAIL_SUBJECT}\":\n\n"
        f"{PLAUD_TRANSCRIPT_TEXT}\n\n"
        "Beste Grüße\nPlaud Notes\n"
    ),
    "has_attachments": False,
    "attachments": [],
}


# ---------------------------------------------------------------------------
# Erwartungen (für Tests bequem zentralisiert)
# ---------------------------------------------------------------------------
EXPECTED_PRESENT_ATTENDEES = [
    "Sven Herbert",
    "Thomas Winzer",
    "Frank Herbert",
    "Bernd Herbert",
    "Dennis Appelshäuser",
    "Philipp Scheidlock",
]

# Zwingend in der Teilnehmer-Frontmatter (HRN-NL-Leiter + Organizer)
REQUIRED_TEILNEHMER_NAMES = ["Sven Herbert", "Thomas Winzer"]

# Verbotene Tokens in der Teilnehmer-Frontmatter (Halluzinations-Subjekt)
FORBIDDEN_TEILNEHMER_TOKENS = ["Dragan", "Mihaljevic"]

# Erwarteter meeting_time_hint aus HBE-275 (naive ISO-Local)
EXPECTED_MEETING_TIME_HINT_START = "2026-05-21T13:00:00"
EXPECTED_MEETING_TIME_HINT_END = "2026-05-21T15:00:00"
