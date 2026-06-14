"""Smoke test für die Zwei-Zeitstempel-Disambiguierung (HBE-275).

Verifiziert die Heuristik in `_extract_meeting_time_hint` gegen typische
Plaud-Mail-Betreffe. `received_at` (Mail-Eingangszeit, UTC) bleibt als
Fallback fürs Jahr — die Meeting-Zeit darf damit nicht verwechselt werden.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("API_SECRET_KEY", "test-secret-for-import")


def _load_api_module():
    """Importiert api.py mit Stub-Ersatz für schwere Runtime-Abhängigkeiten.

    Die Heuristik unter Test (`_extract_meeting_time_hint`) ist rein
    stdlib — fastapi/pydantic/dotenv werden nur am Modul-Rand gebraucht.
    """
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

        def include_router(self, *a, **kw):
            pass

    def _security(*a, **kw):
        return None

    class _HTTPException(Exception):
        def __init__(self, *a, **kw):
            super().__init__(kw.get("detail") or (a[1] if len(a) > 1 else ""))

    fastapi_mod.FastAPI = _App
    fastapi_mod.HTTPException = _HTTPException
    fastapi_mod.Security = _security
    fastapi_mod.Request = type("Request", (), {})
    fastapi_mod.BackgroundTasks = type("BackgroundTasks", (), {"add_task": lambda *a, **kw: None})
    fastapi_mod.Depends = lambda *a, **kw: None
    fastapi_mod.Header = lambda *a, **kw: None
    fastapi_mod.Query = lambda *a, **kw: None
    fastapi_mod.File = lambda *a, **kw: None
    fastapi_mod.Form = lambda *a, **kw: None
    fastapi_mod.UploadFile = type("UploadFile", (), {"__init__": lambda *a, **kw: None})
    stubs["fastapi"] = fastapi_mod

    sec_mod = types.ModuleType("fastapi.security")

    class _APIKeyHeader:
        def __init__(self, *a, **kw):
            pass

    sec_mod.APIKeyHeader = _APIKeyHeader
    stubs["fastapi.security"] = sec_mod

    responses_mod = types.ModuleType("fastapi.responses")
    responses_mod.HTMLResponse = type("HTMLResponse", (), {"__init__": lambda *a, **kw: None})
    stubs["fastapi.responses"] = responses_mod

    staticfiles_mod = types.ModuleType("fastapi.staticfiles")
    staticfiles_mod.StaticFiles = type("StaticFiles", (), {"__init__": lambda *a, **kw: None})
    stubs["fastapi.staticfiles"] = staticfiles_mod

    templating_mod = types.ModuleType("fastapi.templating")
    templating_mod.Jinja2Templates = type("Jinja2Templates", (), {
        "__init__": lambda *a, **kw: None,
        "TemplateResponse": lambda *a, **kw: None,
    })
    stubs["fastapi.templating"] = templating_mod

    pyd_mod = types.ModuleType("pydantic")

    class _BaseModel:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    def _field(default=None, **kw):
        return default

    pyd_mod.BaseModel = _BaseModel
    pyd_mod.Field = _field
    pyd_mod.field_validator = lambda *a, **kw: (lambda fn: fn)
    stubs["pydantic"] = pyd_mod

    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("api_under_test", REPO_ROOT / "api.py")
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
_extract_meeting_time_hint = _api._extract_meeting_time_hint


class ExtractMeetingTimeHintTests(unittest.TestCase):
    received = "2026-04-17T15:23:00Z"

    def test_plaud_md_with_time_range(self):
        start, end = _extract_meeting_time_hint(
            "04-17 Besprechung Zeiterfassung 09:30-11:00", "", self.received
        )
        self.assertEqual(start, "2026-04-17T09:30:00")
        self.assertEqual(end, "2026-04-17T11:00:00")

    def test_german_date_single_time(self):
        start, end = _extract_meeting_time_hint(
            "17.04.2026 Strategie-Meeting 14:00", "", self.received
        )
        self.assertEqual(start, "2026-04-17T14:00:00")
        self.assertIsNone(end)

    def test_german_date_no_year_uses_received_year(self):
        start, end = _extract_meeting_time_hint(
            "17.04. Kick-Off 08:15-09:00", "", self.received
        )
        self.assertEqual(start, "2026-04-17T08:15:00")
        self.assertEqual(end, "2026-04-17T09:00:00")

    def test_iso_date_with_range(self):
        start, end = _extract_meeting_time_hint(
            "2026-04-17 Review 09:30-11:00", "", self.received
        )
        self.assertEqual(start, "2026-04-17T09:30:00")
        self.assertEqual(end, "2026-04-17T11:00:00")

    def test_no_recognizable_date_returns_none(self):
        start, end = _extract_meeting_time_hint(
            "Besprechung 09:30-11:00", "", self.received
        )
        self.assertIsNone(start)
        self.assertIsNone(end)

    def test_no_time_returns_none(self):
        start, end = _extract_meeting_time_hint(
            "04-17 Besprechung", "", self.received
        )
        self.assertIsNone(start)
        self.assertIsNone(end)

    def test_invalid_date_returns_none(self):
        start, end = _extract_meeting_time_hint(
            "13-32 Termin 09:30", "", self.received
        )
        self.assertIsNone(start)
        self.assertIsNone(end)

    def test_body_fallback_when_subject_empty(self):
        start, end = _extract_meeting_time_hint(
            "",
            "Aufnahme vom 04-17 um 09:30-11:00.",
            self.received,
        )
        self.assertEqual(start, "2026-04-17T09:30:00")
        self.assertEqual(end, "2026-04-17T11:00:00")

    def test_en_dash_in_time_range(self):
        start, end = _extract_meeting_time_hint(
            "04-17 Workshop 09:30 – 11:00", "", self.received
        )
        self.assertEqual(start, "2026-04-17T09:30:00")
        self.assertEqual(end, "2026-04-17T11:00:00")


if __name__ == "__main__":
    unittest.main()
