"""Path-sandbox tests for CalendarEmailAgent.add_event_attachment (HBE-177)."""

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _install_test_stubs() -> None:
    """Minimal stubs so we can import calendar_email_agent without heavy deps."""
    if "streamlit" not in sys.modules:
        streamlit_module = types.ModuleType("streamlit")

        def cache_data(*_args, **_kwargs):
            def decorator(func):
                return func

            return decorator

        streamlit_module.cache_data = cache_data
        streamlit_module.fragment = lambda f: f
        sys.modules["streamlit"] = streamlit_module

    if "asana" not in sys.modules:
        asana_module = types.ModuleType("asana")
        for cls in (
            "Configuration",
            "ApiClient",
            "TasksApi",
            "WorkspacesApi",
            "StoriesApi",
            "ProjectsApi",
            "UsersApi",
            "AttachmentsApi",
            "SectionsApi",
        ):
            setattr(asana_module, cls, type(cls, (), {}))
        sys.modules["asana"] = asana_module

    if "pydantic" not in sys.modules:
        pydantic_module = types.ModuleType("pydantic")

        class BaseModel:
            pass

        def Field(default=None, description=None):
            return default

        pydantic_module.BaseModel = BaseModel
        pydantic_module.Field = Field
        sys.modules["pydantic"] = pydantic_module

    if "langchain_core" not in sys.modules:
        sys.modules["langchain_core"] = types.ModuleType("langchain_core")

    if "langchain_core.messages" not in sys.modules:
        messages_module = types.ModuleType("langchain_core.messages")

        class _Message:
            def __init__(self, content="", **kwargs):
                self.content = content
                self.tool_calls = kwargs.get("tool_calls", [])
                self.id = kwargs.get("id")

        messages_module.HumanMessage = _Message
        messages_module.SystemMessage = _Message
        messages_module.AIMessage = _Message
        messages_module.ToolMessage = _Message
        sys.modules["langchain_core.messages"] = messages_module

    if "langchain_core.tools" not in sys.modules:
        tools_module = types.ModuleType("langchain_core.tools")

        class _BaseTool:
            def __init__(self, name, description="", func=None, args_schema=None):
                self.name = name
                self.description = description
                self.func = func
                self.args_schema = args_schema

        tools_module.Tool = _BaseTool
        tools_module.StructuredTool = _BaseTool
        tools_module.tool = lambda f: _BaseTool(name=f.__name__, func=f)
        sys.modules["langchain_core.tools"] = tools_module


_install_test_stubs()

from agents.calendar_email_agent import (  # noqa: E402
    _ATTACHMENT_ALLOWED_ROOTS,
    _validate_attachment_path,
)


class ValidateAttachmentPathTests(unittest.TestCase):
    """Direct tests of the sandbox helper with a synthetic base dir."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        for root in _ATTACHMENT_ALLOWED_ROOTS:
            (self.base / root).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # --- Happy paths -------------------------------------------------------

    def test_relative_path_inside_input_docs_is_allowed(self) -> None:
        (self.base / "input_docs" / "agenda.pdf").write_bytes(b"x")
        ok, resolved, err = _validate_attachment_path(
            "input_docs/agenda.pdf", base_dir=self.base
        )
        self.assertTrue(ok, msg=err)
        self.assertIsNone(err)
        self.assertEqual(resolved, (self.base / "input_docs" / "agenda.pdf").resolve())

    def test_relative_path_inside_data_protocols_is_allowed(self) -> None:
        target = self.base / "data" / "protocols" / "2026-06-04.md"
        target.write_text("hi", encoding="utf-8")
        ok, resolved, err = _validate_attachment_path(
            "data/protocols/2026-06-04.md", base_dir=self.base
        )
        self.assertTrue(ok, msg=err)
        self.assertEqual(resolved, target.resolve())

    def test_absolute_path_inside_allowed_root_is_allowed(self) -> None:
        target = self.base / "input_docs" / "deep" / "x.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok", encoding="utf-8")
        ok, resolved, err = _validate_attachment_path(str(target), base_dir=self.base)
        self.assertTrue(ok, msg=err)
        self.assertEqual(resolved, target.resolve())

    # --- Negative cases (audit §5.3) --------------------------------------

    def test_absolute_app_env_is_rejected(self) -> None:
        ok, resolved, err = _validate_attachment_path("/app/.env", base_dir=self.base)
        self.assertFalse(ok)
        self.assertIsNone(resolved)
        self.assertIn("ausserhalb", err)

    def test_dotdot_traversal_into_secrets_is_rejected(self) -> None:
        ok, resolved, err = _validate_attachment_path(
            "input_docs/../../secrets.toml", base_dir=self.base
        )
        self.assertFalse(ok)
        self.assertIsNone(resolved)
        self.assertIn("ausserhalb", err)

    def test_dotdot_secrets_toml_is_rejected(self) -> None:
        ok, resolved, err = _validate_attachment_path(
            "../secrets.toml", base_dir=self.base
        )
        self.assertFalse(ok)
        self.assertIsNone(resolved)

    def test_absolute_path_outside_roots_is_rejected(self) -> None:
        outside = self.base.parent / "elsewhere.bin"
        outside.write_bytes(b"x")
        try:
            ok, resolved, err = _validate_attachment_path(
                str(outside), base_dir=self.base
            )
            self.assertFalse(ok)
            self.assertIsNone(resolved)
            self.assertIn("ausserhalb", err)
        finally:
            outside.unlink(missing_ok=True)

    def test_empty_path_is_rejected(self) -> None:
        ok, resolved, err = _validate_attachment_path("   ", base_dir=self.base)
        self.assertFalse(ok)
        self.assertIsNone(resolved)
        self.assertIn("leer", err)

    def test_non_string_path_is_rejected(self) -> None:
        ok, resolved, err = _validate_attachment_path(None, base_dir=self.base)  # type: ignore[arg-type]
        self.assertFalse(ok)
        self.assertIsNone(resolved)


class AddEventAttachmentWrapperTests(unittest.TestCase):
    """Behavioural contract: on sandbox violation NO Graph API call happens."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        for root in _ATTACHMENT_ALLOWED_ROOTS:
            (self.base / root).mkdir(parents=True, exist_ok=True)
        self._cwd_patcher = mock.patch(
            "agents.calendar_email_agent.Path.cwd", return_value=self.base
        )
        self._cwd_patcher.start()

    def tearDown(self) -> None:
        self._cwd_patcher.stop()
        self._tmp.cleanup()

    def _make_agent(self, outlook_tool):
        from agents.calendar_email_agent import CalendarEmailAgent

        agent = CalendarEmailAgent.__new__(CalendarEmailAgent)
        agent.name = "CalendarEmailAgent"
        agent.outlook_tool = outlook_tool
        return agent

    def test_violation_returns_error_without_calling_graph(self) -> None:
        outlook = mock.MagicMock()
        agent = self._make_agent(outlook)
        for bad in ("/app/.env", "../secrets.toml", "/etc/passwd"):
            with self.subTest(bad=bad):
                outlook.reset_mock()
                result = agent._add_event_attachment_wrapper(
                    event_id="evt-1", file_path=bad, file_name="x"
                )
                self.assertIn("Fehler beim Anhängen", result)
                outlook.add_attachment_to_event.assert_not_called()

    def test_allowed_path_reaches_outlook_tool(self) -> None:
        target = self.base / "input_docs" / "agenda.pdf"
        target.write_bytes(b"x")
        outlook = mock.MagicMock()
        outlook.add_attachment_to_event.return_value = {"success": True}
        agent = self._make_agent(outlook)
        result = agent._add_event_attachment_wrapper(
            event_id="evt-1", file_path="input_docs/agenda.pdf", file_name="Agenda.pdf"
        )
        self.assertIn("erfolgreich", result)
        outlook.add_attachment_to_event.assert_called_once()
        kwargs = outlook.add_attachment_to_event.call_args.kwargs
        self.assertEqual(Path(kwargs["file_path"]).resolve(), target.resolve())


if __name__ == "__main__":
    unittest.main()
