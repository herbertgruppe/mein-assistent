"""Smoke-Tests for HBE-610 security fixes.

Covers:
- _check_message_id: valid Graph IDs pass, injected paths rejected
- _check_target_folder: valid folder names pass, single-quote injection rejected
- _resolve_folder_id: well-known alias hit, displayName match, 404-not-found
- lena_mail_move: happy path, Graph-502 error path

The api.py module is loaded with minimal stubs for heavy deps (fastapi,
pydantic, dotenv) so the test file runs in the CI environment that only
has `pytest` and `requests` installed.  The validator helper functions
(_check_message_id, _check_target_folder) are tested directly — no real
Pydantic instantiation needed.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]

os.environ.setdefault("API_SECRET_KEY", "test-secret-for-import")


# ---------------------------------------------------------------------------
# Stub loader — same pattern as tests/regression/
# ---------------------------------------------------------------------------

def _load_api_module() -> types.ModuleType:
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

    class _HTTPException(Exception):
        def __init__(self, status_code=500, detail=""):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    def _security(*a, **kw):
        return None

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

        @classmethod
        def model_rebuild(cls, *args, **kwargs):
            """Pydantic v2 forward-ref resolver — no-op stub for tests."""
            return None

    def _field(default=None, **kw):
        return default

    def _field_validator(*args, **kwargs):
        """No-op stub — validators are tested via the helper functions directly."""
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
        spec = importlib.util.spec_from_file_location("api_hbe610", REPO_ROOT / "api.py")
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
_HTTPException = _api.HTTPException


# ---------------------------------------------------------------------------
# Validator helper tests
# ---------------------------------------------------------------------------

class TestCheckMessageId(unittest.TestCase):
    """_check_message_id must accept base64url-safe Graph IDs and reject the rest."""

    def test_valid_graph_id(self):
        """Typical AAMk… base64url ID passes without modification."""
        v = "AAMkAGQwNjQ3ZDRiLTZlMjEtNGM4ZS1hNGVlLTBlZGNiZjZhZWEzNQAQAFk4Jj-Q0qpLm0_5RQfFl7k="
        self.assertEqual(_api._check_message_id(v), v)

    def test_valid_short_id(self):
        self.assertEqual(_api._check_message_id("AbCdEf123_-="), "AbCdEf123_-=")

    def test_rejects_slash(self):
        with self.assertRaises(ValueError):
            _api._check_message_id("AAMk/../../../etc/passwd")

    def test_rejects_single_quote(self):
        with self.assertRaises(ValueError):
            _api._check_message_id("AAMk'; DROP TABLE messages;--")

    def test_rejects_space(self):
        with self.assertRaises(ValueError):
            _api._check_message_id("AAMk 123")

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            _api._check_message_id("")


class TestCheckTargetFolder(unittest.TestCase):
    """_check_target_folder must accept valid folder names and reject injection attempts."""

    def test_valid_ascii(self):
        self.assertEqual(_api._check_target_folder("Inbox"), "Inbox")

    def test_valid_german_umlauts(self):
        self.assertEqual(_api._check_target_folder("Archiv Übersicht"), "Archiv Übersicht")

    def test_valid_path_separator(self):
        # The allowlist permits '/' for nested folder syntax.
        self.assertEqual(_api._check_target_folder("Projekte/2026"), "Projekte/2026")

    def test_valid_hyphen_underscore(self):
        self.assertEqual(_api._check_target_folder("Follow-Up_Items"), "Follow-Up_Items")

    def test_rejects_single_quote(self):
        """OData injection: displayName eq 'x' OR '1'='1'"""
        with self.assertRaises(ValueError):
            _api._check_target_folder("Archiv' OR '1'='1")

    def test_rejects_semicolon(self):
        with self.assertRaises(ValueError):
            _api._check_target_folder("Archiv; DROP TABLE")

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            _api._check_target_folder("")


# ---------------------------------------------------------------------------
# _resolve_folder_id smoke tests
# ---------------------------------------------------------------------------

class TestResolveFolderId(unittest.TestCase):

    def _make_resp(self, status_code: int, body: dict):
        r = mock.MagicMock()
        r.status_code = status_code
        r.json.return_value = body
        r.text = str(body)
        return r

    def test_well_known_alias_hit(self):
        """'inbox' alias → GET /mailFolders/inbox returns real ID directly."""
        headers = {"Authorization": "Bearer tok"}
        folder_resp = self._make_resp(200, {"id": "INBOX_REAL_ID"})

        with mock.patch("requests.get", return_value=folder_resp) as mock_get:
            result = _api._resolve_folder_id("Inbox", headers)

        self.assertEqual(result, "INBOX_REAL_ID")
        # Must have used the well-known path, not the OData query.
        url_called = mock_get.call_args[0][0]
        self.assertIn("/mailFolders/inbox", url_called)

    def test_displayname_match(self):
        """Custom folder resolved via displayName OData query.

        'Kundenprojekte' is not in the well-known alias map, so _resolve_folder_id
        goes directly to the OData query (single requests.get call).
        """
        headers = {"Authorization": "Bearer tok"}
        odata_resp = self._make_resp(200, {"value": [{"id": "CUSTOM_FOLDER_ID", "displayName": "Kundenprojekte"}]})

        with mock.patch("requests.get", return_value=odata_resp):
            result = _api._resolve_folder_id("Kundenprojekte", headers)

        self.assertEqual(result, "CUSTOM_FOLDER_ID")

    def test_not_found_raises_404(self):
        """Unknown folder → HTTPException 404.

        OData query returns 200 with empty value list → 404.
        """
        headers = {"Authorization": "Bearer tok"}
        odata_resp = self._make_resp(200, {"value": []})

        with mock.patch("requests.get", return_value=odata_resp):
            with self.assertRaises(_HTTPException) as ctx:
                _api._resolve_folder_id("NichtVorhanden", headers)

        self.assertEqual(ctx.exception.status_code, 404)

    def test_graph_502_on_odata_error(self):
        """Graph returns non-200 on mailFolders query → HTTPException 502."""
        headers = {"Authorization": "Bearer tok"}

        with mock.patch("requests.get", return_value=self._make_resp(502, {})):
            with self.assertRaises(_HTTPException) as ctx:
                _api._resolve_folder_id("Irgendwas", headers)

        self.assertEqual(ctx.exception.status_code, 502)

    def test_odata_filter_escapes_single_quote(self):
        """Single quote in folder name must be doubled in the OData filter (defense-in-depth).

        'O'Reilly' is not in the alias map → direct OData query; the $filter value
        must contain O''Reilly.
        """
        headers = {"Authorization": "Bearer tok"}
        odata_resp = self._make_resp(200, {"value": [{"id": "ID1"}]})

        with mock.patch("requests.get", return_value=odata_resp) as mock_get:
            _api._resolve_folder_id("O'Reilly", headers)

        params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[1].get("params", {})
        filter_val = params.get("$filter", "")
        self.assertIn("O''Reilly", filter_val)


# ---------------------------------------------------------------------------
# lena_mail_move endpoint smoke tests
# ---------------------------------------------------------------------------

class TestLenaMailMove(unittest.TestCase):
    """Smoke-test the lena_mail_move endpoint — verifies POST /move with destinationId (HBE-1040)."""

    def _make_resp(self, status_code: int, body: dict = None):
        r = mock.MagicMock()
        r.status_code = status_code
        r.json.return_value = body or {}
        r.text = ""
        return r

    def _make_req(self, message_id="AAMkABC123", target_folder="Archiv"):
        req = mock.MagicMock()
        req.message_id = message_id
        req.target_folder = target_folder
        return req

    def _make_tool(self, authenticated=True):
        tool = mock.MagicMock()
        tool.is_authenticated.return_value = authenticated
        tool.access_token = "fake-access-token"
        return tool

    def test_happy_path_calls_post_move_with_destination_id(self):
        """lena_mail_move must call POST /messages/{id}/move with destinationId — not PATCH."""
        req = self._make_req()
        folder_resp = self._make_resp(200, {"id": "FOLDER_ID"})
        move_resp = self._make_resp(201, {"id": "AAMkNEWID456"})

        with mock.patch.object(_api, "_get_outlook_tool", return_value=self._make_tool()), \
             mock.patch("requests.get", return_value=folder_resp), \
             mock.patch("requests.post", return_value=move_resp) as mock_post:
            result = _api.lena_mail_move(req)

        self.assertTrue(result.success)
        self.assertEqual(result.message_id, "AAMkNEWID456")
        self.assertEqual(result.folder, "Archiv")

        call_args = mock_post.call_args
        url = call_args[0][0] if call_args[0] else call_args.kwargs.get("url", "")
        self.assertIn("/move", url)
        body = call_args[1].get("json") or (call_args[0][1] if len(call_args[0]) > 1 else {})
        self.assertIn("destinationId", body)
        self.assertNotIn("parentFolderId", body)

    def test_happy_path_fallback_to_original_id_when_no_new_id(self):
        """If Graph response lacks 'id', fall back to the original message_id."""
        req = self._make_req()
        folder_resp = self._make_resp(200, {"id": "FOLDER_ID"})
        move_resp = self._make_resp(200, {})

        with mock.patch.object(_api, "_get_outlook_tool", return_value=self._make_tool()), \
             mock.patch("requests.get", return_value=folder_resp), \
             mock.patch("requests.post", return_value=move_resp):
            result = _api.lena_mail_move(req)

        self.assertTrue(result.success)
        self.assertEqual(result.message_id, "AAMkABC123")

    def test_graph_502_on_move_error(self):
        """Graph returns 502 on POST /move → HTTPException 502 is raised."""
        req = self._make_req()
        folder_resp = self._make_resp(200, {"id": "FOLDER_ID"})
        move_resp = self._make_resp(502)

        with mock.patch.object(_api, "_get_outlook_tool", return_value=self._make_tool()), \
             mock.patch("requests.get", return_value=folder_resp), \
             mock.patch("requests.post", return_value=move_resp):
            with self.assertRaises(_HTTPException) as ctx:
                _api.lena_mail_move(req)

        self.assertEqual(ctx.exception.status_code, 502)

    def test_unauthenticated_raises_503(self):
        """Outlook not authenticated → HTTPException 503."""
        req = self._make_req()

        with mock.patch.object(_api, "_get_outlook_tool", return_value=self._make_tool(authenticated=False)):
            with self.assertRaises(_HTTPException) as ctx:
                _api.lena_mail_move(req)

        self.assertEqual(ctx.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
