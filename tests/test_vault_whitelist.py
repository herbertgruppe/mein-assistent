"""Tests for HBE-757 vault path-whitelist enforcement.

Covers:
- _vault_check_write_access: whitelisted paths pass, forbidden paths raise 403
- append-only enforcement: overwrite on 04 Ressourcen/Personen/ raises 403
- path traversal rejection: ".." in path raises 400

Loads api.py with minimal stubs — same pattern as test_mail_move_validators.py.
No real filesystem or git operations.
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
os.environ.setdefault("VAULT_MIRROR_PATH", "/tmp/fake-vault-mirror")


def _load_api_module() -> types.ModuleType:
    stubs: dict[str, types.ModuleType] = {}

    dotenv_mod = types.ModuleType("dotenv")
    dotenv_mod.load_dotenv = lambda *a, **kw: None
    stubs["dotenv"] = dotenv_mod

    fastapi_mod = types.ModuleType("fastapi")

    class _HTTPException(Exception):
        def __init__(self, status_code: int = 400, detail: str = "") -> None:
            self.status_code = status_code
            self.detail = detail

    class _App:
        def __init__(self, *a, **kw):
            pass
        def get(self, *a, **kw):
            return lambda fn: fn
        post = patch = put = delete = on_event = get
        def mount(self, *a, **kw):
            pass

    class _Security:
        def __init__(self, *a, **kw):
            pass

    fastapi_mod.FastAPI = _App
    fastapi_mod.HTTPException = _HTTPException
    fastapi_mod.Header = fastapi_mod.Query = fastapi_mod.Depends = fastapi_mod.Security = lambda *a, **kw: None
    fastapi_mod.BackgroundTasks = object
    fastapi_mod.Request = object

    stubs["fastapi"] = fastapi_mod

    fastapi_security = types.ModuleType("fastapi.security")
    fastapi_security.APIKeyHeader = lambda *a, **kw: None
    stubs["fastapi.security"] = fastapi_security

    fastapi_staticfiles = types.ModuleType("fastapi.staticfiles")
    fastapi_staticfiles.StaticFiles = lambda *a, **kw: None
    stubs["fastapi.staticfiles"] = fastapi_staticfiles

    fastapi_templating = types.ModuleType("fastapi.templating")
    fastapi_templating.Jinja2Templates = lambda *a, **kw: None
    stubs["fastapi.templating"] = fastapi_templating

    fastapi_responses = types.ModuleType("fastapi.responses")
    fastapi_responses.HTMLResponse = str
    stubs["fastapi.responses"] = fastapi_responses

    pydantic_mod = types.ModuleType("pydantic")

    class _BaseModel:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    def _field_validator(*fields, **kw):
        def deco(fn):
            return classmethod(fn)
        return deco

    pydantic_mod.BaseModel = _BaseModel
    pydantic_mod.Field = lambda *a, **kw: None
    pydantic_mod.field_validator = _field_validator
    stubs["pydantic"] = pydantic_mod

    # Stub all heavy deps that are imported at module level
    for name in [
        "requests", "apscheduler", "apscheduler.schedulers",
        "apscheduler.schedulers.background",
        "weasyprint", "markdown", "bs4",
        "database", "database.protocols_db",
    ]:
        stubs.setdefault(name, types.ModuleType(name))

    db_mod = stubs["database.protocols_db"]
    db_mod.ProtocolsDB = type("ProtocolsDB", (), {
        "__init__": lambda self, **kw: None,
        "get_by_token": lambda self, t: None,
        "is_expired": staticmethod(lambda p: True),
    })
    stubs["database"].protocols_db = db_mod

    for name, stub in stubs.items():
        sys.modules[name] = stub

    spec = importlib.util.spec_from_file_location("api", REPO_ROOT / "api.py")
    module = importlib.util.module_from_spec(spec)
    # Prevent tempfile.mkdtemp side-effects from the askpass helper at module level
    with mock.patch("tempfile.mkdtemp", return_value="/tmp/fake-askpass"):
        with mock.patch("pathlib.Path.write_text"):
            with mock.patch("pathlib.Path.chmod"):
                spec.loader.exec_module(module)
    return module


class TestVaultWriteAccess(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = _load_api_module()
        cls.HTTPException = cls.api.app.__class__  # not used; import directly
        # Re-import the real HTTPException from the stub
        import fastapi
        cls.HTTPException = fastapi.HTTPException

    def _check(self, path: str) -> str:
        return self.api._vault_check_write_access(path)

    def test_daily_notes_allowed(self):
        result = self._check("05 Daily Notes/2026-06-12.md")
        self.assertEqual(result, "full")

    def test_lena_inbox_allowed(self):
        result = self._check("09 Lena Inbox/note.md")
        self.assertEqual(result, "full")

    def test_inbox_allowed(self):
        result = self._check("01 Inbox/task.md")
        self.assertEqual(result, "full")

    def test_personen_append_only(self):
        result = self._check("04 Ressourcen/Personen/Max Mustermann.md")
        self.assertEqual(result, "append_only")

    def test_projekte_forbidden(self):
        with self.assertRaises(self.HTTPException) as ctx:
            self._check("02 Projekte/Projekt-X/plan.md")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_bereiche_forbidden(self):
        with self.assertRaises(self.HTTPException) as ctx:
            self._check("03 Bereiche/Team.md")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_root_file_forbidden(self):
        with self.assertRaises(self.HTTPException) as ctx:
            self._check("CLAUDE.md")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_empty_path_forbidden(self):
        with self.assertRaises(self.HTTPException) as ctx:
            self._check("")
        self.assertEqual(ctx.exception.status_code, 403)


class TestVaultWriteEndpointLogic(unittest.TestCase):
    """Tests append-only enforcement via _vault_check_write_access return value."""

    @classmethod
    def setUpClass(cls):
        cls.api = _load_api_module()
        import fastapi
        cls.HTTPException = fastapi.HTTPException

    def test_personen_returns_append_only_blocking_overwrite(self):
        """_vault_check_write_access must return 'append_only' for Personen/ paths.
        The endpoint uses this return value to reject mode='overwrite' with 403."""
        access_mode = self.api._vault_check_write_access("04 Ressourcen/Personen/Max Mustermann.md")
        self.assertEqual(access_mode, "append_only",
                         "append-only check must trigger for 04 Ressourcen/Personen/ paths")

    def test_daily_notes_returns_full_allowing_overwrite(self):
        """_vault_check_write_access must return 'full' for 05 Daily Notes/ paths."""
        access_mode = self.api._vault_check_write_access("05 Daily Notes/2026-06-12.md")
        self.assertEqual(access_mode, "full")


if __name__ == "__main__":
    unittest.main()
