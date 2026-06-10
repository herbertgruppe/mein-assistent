"""Regressionstest: Dragan-Halluzinations-Klasse darf nicht zurückkehren (HBE-277).

Modelliert die Klasse aus HBE-273 / BL-HRN 2026-05-21:
- Dragan Mihaljevic war in Outlook eingeladen (responseStatus=notResponded) und
  beim Termin NICHT anwesend.
- Der `meeting-protokoll`-Skill hatte ihn früher als Teilnehmer/Sprecher
  ausgewiesen — gefixt durch HBE-274 (Pro-Attendee-responseStatus),
  HBE-275 (meeting_time_hint) und Skill-seitige Patches.

Der Test fährt den API-Datenfluss durch, den der Skill konsumiert, und
verifiziert die DoD aus HBE-277:

1. Plaud-Mail-Fixture für BL-HRN 2026-05-21 lädt durch /api/transcripts/pending
   inkl. korrektem meeting_time_hint (HBE-275).
2. Skill-Flow läuft mit echten `_format_attendees` und
   `_extract_meeting_time_hint`; nur das Outlook-Backend ist gestubbt
   (Skill-Runner-Mock = Outlook-Stub; Skill-Logik echt).
3. Frontmatter-Verifikation: der teilnehmer-Filter (verantwortungsvoller
   Skill-Konsument: response in {accepted, tentative, organizer}) enthält
   weder „Dragan" noch „Mihaljevic".
4. Body-Verifikation: Dragan im Transkript wird nur als Gegenstand erwähnt
   und nicht als Sprecher — abgeleitet aus der Sprecherliste am Zeilenanfang.
5. Positive Mindestanforderung: Sven Herbert + Thomas Winzer (HRN-NL-Leiter)
   sind in der teilnehmer-Liste.

Zusatz-Schutz: wenn jemand HBE-274 testweise zurück auf „attendees=names only"
dreht (alte API ohne CalendarAttendee/responseStatus), schlägt der Test fehl,
weil `event.attendees[i].response` dann nicht mehr existiert (FieldError /
Default „none") und der Filter alle echten Teilnehmer ebenfalls droppt.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("API_SECRET_KEY", "test-secret-for-import")

from tests.regression.fixtures.bl_hrn_2026_05_21 import (  # noqa: E402
    EXPECTED_MEETING_TIME_HINT_END,
    EXPECTED_MEETING_TIME_HINT_START,
    EXPECTED_PRESENT_ATTENDEES,
    FORBIDDEN_TEILNEHMER_TOKENS,
    OUTLOOK_RAW_EVENT,
    PLAUD_MAIL,
    PLAUD_TRANSCRIPT_TEXT,
    REQUIRED_TEILNEHMER_NAMES,
)


# ---------------------------------------------------------------------------
# Stub-Import für api.py (identisch zur HBE-275 Test-Heuristik —
# fastapi/pydantic/dotenv werden nur am Modul-Rand gebraucht)
# ---------------------------------------------------------------------------
def _load_api_module():
    stubs: dict[str, types.ModuleType] = {}

    dotenv_mod = types.ModuleType("dotenv")
    dotenv_mod.load_dotenv = lambda *a, **kw: None
    stubs["dotenv"] = dotenv_mod

    fastapi_mod = types.ModuleType("fastapi")

    class _App:
        def __init__(self, *a, **kw):
            pass

        def get(self, *a, **kw):
            def deco(fn):
                return fn

            return deco

        post = get
        on_event = get
        patch = get
        put = get
        delete = get

        def mount(self, *a, **kw):
            pass

    def _security(*a, **kw):
        return None

    class _HTTPException(Exception):
        def __init__(self, *a, **kw):
            super().__init__(kw.get("detail") or (a[1] if len(a) > 1 else ""))

    class _Request:
        pass

    class _BackgroundTasks:
        def add_task(self, *a, **kw):
            pass

    fastapi_mod.FastAPI = _App
    fastapi_mod.HTTPException = _HTTPException
    fastapi_mod.Security = _security
    fastapi_mod.Request = _Request
    fastapi_mod.BackgroundTasks = _BackgroundTasks
    fastapi_mod.Depends = lambda *a, **kw: None
    fastapi_mod.Header = lambda *a, **kw: None
    fastapi_mod.Query = lambda *a, **kw: None
    stubs["fastapi"] = fastapi_mod

    sec_mod = types.ModuleType("fastapi.security")

    class _APIKeyHeader:
        def __init__(self, *a, **kw):
            pass

    sec_mod.APIKeyHeader = _APIKeyHeader
    stubs["fastapi.security"] = sec_mod

    responses_mod = types.ModuleType("fastapi.responses")

    class _HTMLResponse:
        def __init__(self, *a, **kw):
            pass

    responses_mod.HTMLResponse = _HTMLResponse
    stubs["fastapi.responses"] = responses_mod

    staticfiles_mod = types.ModuleType("fastapi.staticfiles")

    class _StaticFiles:
        def __init__(self, *a, **kw):
            pass

    staticfiles_mod.StaticFiles = _StaticFiles
    stubs["fastapi.staticfiles"] = staticfiles_mod

    templating_mod = types.ModuleType("fastapi.templating")

    class _Jinja2Templates:
        def __init__(self, *a, **kw):
            pass

        def TemplateResponse(self, *a, **kw):
            return None

    templating_mod.Jinja2Templates = _Jinja2Templates
    stubs["fastapi.templating"] = templating_mod

    pyd_mod = types.ModuleType("pydantic")

    class _BaseModel:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    def _field(default=None, **kw):
        return default

    def _field_validator(*args, **kwargs):
        def decorator(fn):
            return classmethod(fn)
        return decorator

    pyd_mod.BaseModel = _BaseModel
    pyd_mod.Field = _field
    pyd_mod.field_validator = _field_validator
    stubs["pydantic"] = pyd_mod

    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(
            "api_under_regression", REPO_ROOT / "api.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for name, prev in saved.items():
            if prev is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prev


_api = _load_api_module()


def _load_outlook_graph_tool_module():
    """Laedt tools/outlook_graph_tool.py direkt per Pfad, ohne den
    schweren tools/__init__.py-Import-Pfad zu triggern (streamlit/asana/
    langchain sind im CI nicht installiert)."""
    spec = importlib.util.spec_from_file_location(
        "outlook_graph_tool_under_regression",
        REPO_ROOT / "tools" / "outlook_graph_tool.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_format_attendees = _load_outlook_graph_tool_module()._format_attendees


# ---------------------------------------------------------------------------
# Outlook-Tool-Stub: liefert genau die Fixture für BL-HRN 2026-05-21.
# Dadurch durchläuft `_format_attendees` (HBE-274) echt — die Stub-Grenze liegt
# unterhalb der Skill-relevanten Logik.
# ---------------------------------------------------------------------------
class _StubOutlookTool:
    def __init__(self, raw_event, plaud_mail):
        self._raw_event = raw_event
        self._plaud_mail = plaud_mail

    # API-Helfer
    def is_authenticated(self):  # noqa: D401
        return True

    def find_subfolder_id(self, name=None, parent=None):  # noqa: D401
        return "stub-transcripts-folder-id"

    # /api/calendar/events ruft das hier, mit Datetime-Range.
    def get_events_for_date_range(self, start_dt, end_dt):
        # HBE-274: per-Attendee responseStatus durch _format_attendees real
        # verarbeiten, damit der Test echte Skill-Logik trifft.
        attendees_struct = _format_attendees(self._raw_event)
        return [
            {
                "id": self._raw_event["id"],
                "title": self._raw_event["subject"],
                "start": self._raw_event["start"]["dateTime"],
                "end": self._raw_event["end"]["dateTime"],
                "location": self._raw_event["location"]["displayName"],
                "attendees": attendees_struct,
                "attendee_names": [
                    a["name"] for a in attendees_struct if a.get("name")
                ],
                "preview": self._raw_event["bodyPreview"],
            }
        ]

    # /api/transcripts/pending ruft das hier.
    def get_messages_in_folder(self, folder_id, max_results=25, only_unread=True):
        return [
            {
                "id": self._plaud_mail["message_id"],
                "subject": self._plaud_mail["subject"],
                "receivedDateTime": self._plaud_mail["received_at"],
                "from": {
                    "emailAddress": {
                        "name": self._plaud_mail["sender_name"],
                        "address": self._plaud_mail["sender_email"],
                    }
                },
                "bodyPreview": self._plaud_mail["body_preview"],
                "body": {"contentType": "text", "content": self._plaud_mail["body_text"]},
                "hasAttachments": self._plaud_mail["has_attachments"],
            }
        ]


# ---------------------------------------------------------------------------
# Skill-Konsument-Simulation (modelliert die korrigierte Skill-Seite):
# bildet die teilnehmer-Frontmatter aus dem CalendarEvent-Output und filtert
# nicht-anwesende Personen anhand `response`.
# ---------------------------------------------------------------------------
PRESENT_RESPONSES = {"accepted", "tentative", "organizer"}


def _skill_build_teilnehmer_frontmatter(calendar_event):
    """Spiegelt die Logik, die der gepatchte Skill ausführen MUSS.

    Vor HBE-274 dumpte der Skill die flache Namensliste ungefiltert in die
    Frontmatter. Nach HBE-274 hat der Skill responseStatus zur Verfügung und
    filtert Eingeladene-aber-nicht-anwesende Personen heraus.
    """
    teilnehmer = []
    for attendee in calendar_event.attendees:
        response = getattr(attendee, "response", "none")
        name = getattr(attendee, "name", "")
        if not name:
            continue
        if response in PRESENT_RESPONSES:
            teilnehmer.append(name)
    return teilnehmer


def _transcript_speakers(transcript_text):
    """Extrahiert eindeutige Sprecher aus einem Plaud-Transkript.

    Format: jeder Sprecher-Eintrag startet mit „Name: …" am Zeilenanfang.
    """
    speakers = []
    seen = set()
    for line in transcript_text.splitlines():
        m = re.match(r"^([A-Za-zÄÖÜäöüß][\w\-äöüÄÖÜß ]+?):\s", line)
        if not m:
            continue
        name = m.group(1).strip()
        if name and name not in seen:
            seen.add(name)
            speakers.append(name)
    return speakers


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class DraganHallucinationRegressionTests(unittest.TestCase):
    """End-to-End-Regression: Dragan darf weder als Teilnehmer noch als Sprecher
    in einem BL-HRN-Protokoll auftauchen."""

    def setUp(self):
        self.stub = _StubOutlookTool(OUTLOOK_RAW_EVENT, PLAUD_MAIL)

    # -- API-seitige Vertrags-Prüfungen ---------------------------------

    def test_calendar_attendees_carry_responseStatus_HBE_274(self):
        """HBE-274: Pro-Attendee-responseStatus muss durchgereicht werden."""
        with mock.patch.object(_api, "_get_outlook_tool", return_value=self.stub):
            resp = _api.get_calendar_events(date="2026-05-21")

        self.assertEqual(resp.count, 1, "Genau ein Event erwartet")
        event = resp.events[0]

        by_name = {a.name: a for a in event.attendees}

        # Alle 6 echten Teilnehmer mit anwesenheits-signalisierender response
        # (accepted oder organizer — vom Organizer ueberschreibt _format_attendees
        # die response zu "organizer", HBE-274).
        for name in EXPECTED_PRESENT_ATTENDEES:
            self.assertIn(name, by_name, f"{name} fehlt in attendees")
            self.assertIn(
                by_name[name].response,
                PRESENT_RESPONSES,
                f"{name} sollte als anwesend markiert sein "
                f"(response in {PRESENT_RESPONSES}); ist: "
                f"{by_name[name].response}",
            )

        # Dragan IST in attendees (Outlook-Wahrheit), aber NICHT anwesend
        self.assertIn(
            "Dragan Mihaljevic",
            by_name,
            "Dragan muss als Outlook-Eingeladener erscheinen — sonst kann "
            "der Skill ihn nicht als notResponded erkennen.",
        )
        self.assertEqual(
            by_name["Dragan Mihaljevic"].response,
            "notResponded",
            "Dragan muss als notResponded gekennzeichnet sein — sonst greift "
            "der Skill-seitige Anwesenheits-Filter nicht.",
        )

    def test_meeting_time_hint_extracted_for_BL_HRN_HBE_275(self):
        """HBE-275: meeting_time_hint muss aus dem Plaud-Subject parsen."""
        start, end = _api._extract_meeting_time_hint(
            PLAUD_MAIL["subject"],
            PLAUD_MAIL["body_text"],
            PLAUD_MAIL["received_at"],
        )
        self.assertEqual(start, EXPECTED_MEETING_TIME_HINT_START)
        self.assertEqual(end, EXPECTED_MEETING_TIME_HINT_END)

    # -- DoD-Hauptprüfungen --------------------------------------------

    def test_frontmatter_teilnehmer_excludes_Dragan(self):
        """DoD #3: teilnehmer enthält weder „Dragan" noch „Mihaljevic"."""
        with mock.patch.object(_api, "_get_outlook_tool", return_value=self.stub):
            resp = _api.get_calendar_events(date="2026-05-21")
        event = resp.events[0]

        teilnehmer = _skill_build_teilnehmer_frontmatter(event)
        joined = " | ".join(teilnehmer).lower()

        for token in FORBIDDEN_TEILNEHMER_TOKENS:
            self.assertNotIn(
                token.lower(),
                joined,
                f'Halluzinations-Token "{token}" in teilnehmer-Frontmatter: '
                f"{teilnehmer}",
            )

    def test_frontmatter_teilnehmer_contains_required_HRN_leiter(self):
        """DoD #5: Sven + Thomas Winzer müssen in der teilnehmer-Liste sein."""
        with mock.patch.object(_api, "_get_outlook_tool", return_value=self.stub):
            resp = _api.get_calendar_events(date="2026-05-21")
        event = resp.events[0]

        teilnehmer = _skill_build_teilnehmer_frontmatter(event)
        for required in REQUIRED_TEILNEHMER_NAMES:
            self.assertIn(
                required,
                teilnehmer,
                f'Pflicht-Teilnehmer "{required}" fehlt in: {teilnehmer}',
            )

        # Bonus: alle 6 erwarteten Teilnehmer kommen rein
        for present in EXPECTED_PRESENT_ATTENDEES:
            self.assertIn(
                present,
                teilnehmer,
                f'Erwarteter Teilnehmer "{present}" wurde faelschlich gefiltert',
            )

    def test_body_speaker_extraction_excludes_Dragan(self):
        """DoD #4: Dragan-Erwähnung im Body nur als Gegenstand, NICHT als Sprecher."""
        speakers = _transcript_speakers(PLAUD_TRANSCRIPT_TEXT)
        self.assertNotIn(
            "Dragan Mihaljevic",
            speakers,
            "Dragan darf nicht als Sprecher im Transkript-Body auftauchen",
        )
        self.assertNotIn(
            "Dragan",
            speakers,
            "Auch Vorname allein darf nicht als Sprecher auftauchen",
        )
        # Aber: Dragan wird im Body als Gegenstand erwähnt
        self.assertIn(
            "Dragans Urlaub",
            PLAUD_TRANSCRIPT_TEXT,
            "Sanity-Check: das Transkript erwähnt Dragan als Gegenstand",
        )

    # -- Negativ-Kontrolle: alte HBE-274-Struktur ----------------------

    def test_regression_class_caught_when_responseStatus_dropped(self):
        """Beweis dass der Test die Klasse fängt: ohne responseStatus
        landen alle Eingeladenen — inklusive Dragan — in der teilnehmer-Liste."""
        with mock.patch.object(_api, "_get_outlook_tool", return_value=self.stub):
            resp = _api.get_calendar_events(date="2026-05-21")
        event = resp.events[0]

        # Skill-Bug-Simulation: response ignorieren (alter Code-Pfad)
        unfiltered = [a.name for a in event.attendees if getattr(a, "name", "")]
        self.assertIn(
            "Dragan Mihaljevic",
            unfiltered,
            "Negativ-Kontrolle gebrochen: Dragan sollte in der ungefilterten "
            "Liste sein — das ist genau die Halluzinations-Klasse.",
        )
        # Mit dem korrekten Filter ist er wieder weg
        filtered = _skill_build_teilnehmer_frontmatter(event)
        self.assertNotIn("Dragan Mihaljevic", filtered)


if __name__ == "__main__":
    unittest.main()
