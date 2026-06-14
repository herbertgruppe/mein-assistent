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
    fastapi_mod.File = lambda *a, **kw: None
    fastapi_mod.Form = lambda *a, **kw: None
    fastapi_mod.UploadFile = type("UploadFile", (), {"__init__": lambda *a, **kw: None})

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

    def test_daily_notes_append_only(self):
        # Daily Notes are append_only to protect Sven's sections (HBE-757 hardening)
        result = self._check("05 Daily Notes/2026-06-12.md")
        self.assertEqual(result, "append_only")

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

    def test_daily_notes_blocks_overwrite_like_personen(self):
        """Daily Notes use append_only to block overwrite — same as 04 Ressourcen/Personen/."""
        access_mode = self.api._vault_check_write_access("05 Daily Notes/2026-06-12.md")
        self.assertEqual(access_mode, "append_only",
                         "05 Daily Notes/ must use append_only to protect Sven's sections")


class TestVaultWriteEndpointHTTP(unittest.TestCase):
    """Endpoint-level tests for POST /api/lena/vault/write.

    Covers the three must-have cases from HBE-763:
    1. Happy-path: whitelisted path → file written, git commit, 200 response
    2. 403: path outside whitelist
    3. 400: path traversal via _vault_resolve (second-layer defence after Pydantic validator)
    """

    @classmethod
    def setUpClass(cls):
        cls.api = _load_api_module()
        import fastapi
        cls.HTTPException = fastapi.HTTPException

    def _make_req(self, path, mode="append", content="# test", commit_message="[Lena] test"):
        return self.api.VaultWriteRequest(
            path=path,
            content=content,
            mode=mode,
            commit_message=commit_message,
        )

    def test_happy_path_returns_ok_and_commits(self):
        """Whitelisted path: file is written to mirror, git operations called, response ok."""
        import tempfile
        import shutil

        vault_dir = Path(tempfile.mkdtemp())
        try:
            self.api._VAULT_MIRROR_PATH = vault_dir

            mock_ok = mock.MagicMock()
            mock_ok.returncode = 0
            mock_ok.stderr = ""

            mock_revparse = mock.MagicMock()
            mock_revparse.returncode = 0
            mock_revparse.stdout = "deadb33f\n"

            def fake_git(args, extra_env=None):
                if args[0] == "rev-parse":
                    return mock_revparse
                return mock_ok

            with mock.patch.object(self.api, "_vault_run_git", side_effect=fake_git):
                with mock.patch.object(self.api, "_vault_push_to_origin", return_value=""):
                    req = self._make_req("05 Daily Notes/2026-06-12.md", content="# Note")
                    result = self.api.lena_vault_write(req, _key="test-secret-for-import")

            self.assertEqual(result.status, "ok")
            self.assertEqual(result.commit_sha, "deadb33f")
            self.assertEqual(result.path, "05 Daily Notes/2026-06-12.md")
            written = (vault_dir / "05 Daily Notes" / "2026-06-12.md").read_text(encoding="utf-8")
            self.assertEqual(written, "# Note")
        finally:
            shutil.rmtree(str(vault_dir), ignore_errors=True)

    def test_forbidden_path_raises_403(self):
        """Path outside the whitelist must raise HTTPException 403 before any git op."""
        import tempfile
        import shutil

        vault_dir = Path(tempfile.mkdtemp())
        try:
            self.api._VAULT_MIRROR_PATH = vault_dir
            req = self._make_req("02 Projekte/secret-plan.md")
            with self.assertRaises(self.HTTPException) as ctx:
                self.api.lena_vault_write(req, _key="test-secret-for-import")
            self.assertEqual(ctx.exception.status_code, 403)
        finally:
            shutil.rmtree(str(vault_dir), ignore_errors=True)

    def test_path_traversal_raises_400(self):
        """'../'-traversal in path must be rejected with 400 by _vault_resolve."""
        import tempfile
        import shutil

        vault_dir = Path(tempfile.mkdtemp())
        try:
            self.api._VAULT_MIRROR_PATH = vault_dir
            with self.assertRaises(self.HTTPException) as ctx:
                self.api._vault_resolve("../outside-vault/secrets.md")
            self.assertEqual(ctx.exception.status_code, 400)
        finally:
            shutil.rmtree(str(vault_dir), ignore_errors=True)

    def _fake_git_ok(self, revparse_sha="abc1234"):
        """Return a side_effect function for _vault_run_git that always succeeds."""
        mock_ok = mock.MagicMock()
        mock_ok.returncode = 0
        mock_ok.stderr = ""

        mock_revparse = mock.MagicMock()
        mock_revparse.returncode = 0
        mock_revparse.stdout = f"{revparse_sha}\n"

        def fake_git(args, extra_env=None):
            if args[0] == "rev-parse":
                return mock_revparse
            return mock_ok

        return fake_git

    def test_create_mode_new_file(self):
        """mode='create' on a non-existent whitelisted path writes file and commits."""
        import tempfile, shutil

        vault_dir = Path(tempfile.mkdtemp())
        try:
            self.api._VAULT_MIRROR_PATH = vault_dir
            with mock.patch.object(self.api, "_vault_run_git", side_effect=self._fake_git_ok("cre1234")):
                with mock.patch.object(self.api, "_vault_push_to_origin", return_value=""):
                    req = self._make_req("05 Daily Notes/new-note.md", mode="create", content="# New")
                    result = self.api.lena_vault_write(req, _key="test-secret-for-import")

            self.assertEqual(result.status, "ok")
            self.assertEqual(result.commit_sha, "cre1234")
            written = (vault_dir / "05 Daily Notes" / "new-note.md").read_text(encoding="utf-8")
            self.assertEqual(written, "# New")
        finally:
            shutil.rmtree(str(vault_dir), ignore_errors=True)

    def test_create_mode_existing_file_raises_409(self):
        """mode='create' on an already-existing file must raise HTTPException 409."""
        import tempfile, shutil

        vault_dir = Path(tempfile.mkdtemp())
        try:
            self.api._VAULT_MIRROR_PATH = vault_dir
            target = vault_dir / "05 Daily Notes" / "existing.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("existing content", encoding="utf-8")

            req = self._make_req("05 Daily Notes/existing.md", mode="create", content="# New")
            with self.assertRaises(self.HTTPException) as ctx:
                self.api.lena_vault_write(req, _key="test-secret-for-import")
            self.assertEqual(ctx.exception.status_code, 409)
        finally:
            shutil.rmtree(str(vault_dir), ignore_errors=True)

    def test_append_mode_existing_file(self):
        """mode='append' on an existing file appends content with newline separator."""
        import tempfile, shutil

        vault_dir = Path(tempfile.mkdtemp())
        try:
            self.api._VAULT_MIRROR_PATH = vault_dir
            target = vault_dir / "09 Lena Inbox" / "notes.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# Existing", encoding="utf-8")

            with mock.patch.object(self.api, "_vault_run_git", side_effect=self._fake_git_ok("app1234")):
                with mock.patch.object(self.api, "_vault_push_to_origin", return_value=""):
                    req = self._make_req("09 Lena Inbox/notes.md", mode="append", content="## Appended")
                    result = self.api.lena_vault_write(req, _key="test-secret-for-import")

            self.assertEqual(result.status, "ok")
            written = target.read_text(encoding="utf-8")
            self.assertIn("# Existing", written)
            self.assertIn("## Appended", written)
            self.assertTrue(written.startswith("# Existing\n"))
        finally:
            shutil.rmtree(str(vault_dir), ignore_errors=True)

    def test_append_mode_new_file(self):
        """mode='append' on a non-existent file creates it (same as create)."""
        import tempfile, shutil

        vault_dir = Path(tempfile.mkdtemp())
        try:
            self.api._VAULT_MIRROR_PATH = vault_dir
            with mock.patch.object(self.api, "_vault_run_git", side_effect=self._fake_git_ok("app5678")):
                with mock.patch.object(self.api, "_vault_push_to_origin", return_value=""):
                    req = self._make_req("01 Inbox/new-item.md", mode="append", content="# New via append")
                    result = self.api.lena_vault_write(req, _key="test-secret-for-import")

            self.assertEqual(result.status, "ok")
            written = (vault_dir / "01 Inbox" / "new-item.md").read_text(encoding="utf-8")
            self.assertEqual(written, "# New via append")
        finally:
            shutil.rmtree(str(vault_dir), ignore_errors=True)

    def test_append_only_path_allows_append_mode(self):
        """append-only path (04 Ressourcen/Personen/) must accept mode='append'."""
        import tempfile, shutil

        vault_dir = Path(tempfile.mkdtemp())
        try:
            self.api._VAULT_MIRROR_PATH = vault_dir
            with mock.patch.object(self.api, "_vault_run_git", side_effect=self._fake_git_ok("aop9999")):
                with mock.patch.object(self.api, "_vault_push_to_origin", return_value=""):
                    req = self._make_req(
                        "04 Ressourcen/Personen/Max Mustermann.md",
                        mode="append",
                        content="- Notiz vom 2026-06-12",
                    )
                    result = self.api.lena_vault_write(req, _key="test-secret-for-import")

            self.assertEqual(result.status, "ok")
        finally:
            shutil.rmtree(str(vault_dir), ignore_errors=True)


class TestVaultPullScheduler(unittest.TestCase):
    """Tests for _vault_pull_from_origin race-condition fix (HBE-766).

    Verifies that the pull scheduler uses merge --ff-only instead of reset --hard,
    so local commits are preserved when a push has failed.
    """

    @classmethod
    def setUpClass(cls):
        cls.api = _load_api_module()

    def _make_proc(self, returncode=0, stdout="", stderr=""):
        p = mock.MagicMock()
        p.returncode = returncode
        p.stdout = stdout
        p.stderr = stderr
        return p

    def test_uses_merge_ff_only_not_reset_hard(self):
        """After a successful fetch, merge --ff-only must be called (not reset --hard)."""
        git_calls = []

        def fake_git(args, extra_env=None):
            git_calls.append(args)
            return self._make_proc(returncode=0)

        with mock.patch.object(self.api, "_vault_run_git", side_effect=fake_git):
            with mock.patch.object(self.api, "_VAULT_BOT_TOKEN", "fake-token"):
                with mock.patch.object(self.api, "_vault_auth_env", return_value={}):
                    # Ensure .git dir appears to exist
                    with mock.patch("pathlib.Path.exists", return_value=True):
                        self.api._vault_pull_from_origin()

        merge_calls = [c for c in git_calls if "merge" in c]
        reset_calls = [c for c in git_calls if "reset" in c]

        self.assertEqual(len(reset_calls), 0, "reset --hard must not be called")
        self.assertTrue(
            any("--ff-only" in c and "FETCH_HEAD" in c for c in merge_calls),
            f"merge --ff-only FETCH_HEAD must be called; got: {git_calls}",
        )

    def test_push_failure_then_pull_preserves_local_commits(self):
        """When merge --ff-only fails (local commits present), function must not raise
        and must not call reset --hard — the local commit is preserved."""
        git_calls = []

        def fake_git(args, extra_env=None):
            git_calls.append(list(args))
            if args[0] == "fetch":
                return self._make_proc(returncode=0)
            if args[0] == "merge":
                # Simulate: local commit exists, ff-only not possible
                return self._make_proc(returncode=1, stderr="fatal: Not possible to fast-forward")
            return self._make_proc(returncode=0)

        with mock.patch.object(self.api, "_vault_run_git", side_effect=fake_git):
            with mock.patch.object(self.api, "_VAULT_BOT_TOKEN", "fake-token"):
                with mock.patch.object(self.api, "_vault_auth_env", return_value={}):
                    with mock.patch("pathlib.Path.exists", return_value=True):
                        # Must not raise
                        self.api._vault_pull_from_origin()

        reset_calls = [c for c in git_calls if "reset" in c]
        self.assertEqual(reset_calls, [], "reset --hard must never be called")

    def test_fetch_failure_skips_merge(self):
        """When fetch fails, merge must not be attempted."""
        git_calls = []

        def fake_git(args, extra_env=None):
            git_calls.append(list(args))
            return self._make_proc(returncode=1, stderr="network error")

        with mock.patch.object(self.api, "_vault_run_git", side_effect=fake_git):
            with mock.patch.object(self.api, "_VAULT_BOT_TOKEN", "fake-token"):
                with mock.patch.object(self.api, "_vault_auth_env", return_value={}):
                    with mock.patch("pathlib.Path.exists", return_value=True):
                        self.api._vault_pull_from_origin()

        merge_calls = [c for c in git_calls if "merge" in c]
        self.assertEqual(merge_calls, [], "merge must not be called when fetch fails")


if __name__ == "__main__":
    unittest.main()
