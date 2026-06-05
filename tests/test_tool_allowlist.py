import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _install_test_stubs() -> None:
    if "streamlit" not in sys.modules:
        streamlit_module = types.ModuleType("streamlit")

        def cache_data(*_args, **_kwargs):
            def decorator(func):
                return func

            return decorator

        def fragment(func):
            return func

        streamlit_module.cache_data = cache_data
        streamlit_module.fragment = fragment
        sys.modules["streamlit"] = streamlit_module

    if "asana" not in sys.modules:
        asana_module = types.ModuleType("asana")
        asana_module.Configuration = type("Configuration", (), {})
        asana_module.ApiClient = type("ApiClient", (), {})
        asana_module.TasksApi = type("TasksApi", (), {})
        asana_module.WorkspacesApi = type("WorkspacesApi", (), {})
        asana_module.StoriesApi = type("StoriesApi", (), {})
        asana_module.ProjectsApi = type("ProjectsApi", (), {})
        asana_module.UsersApi = type("UsersApi", (), {})
        asana_module.AttachmentsApi = type("AttachmentsApi", (), {})
        asana_module.SectionsApi = type("SectionsApi", (), {})
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

            def invoke(self, args=None, **kwargs):
                if kwargs:
                    return self.func(**kwargs)
                if isinstance(args, dict):
                    return self.func(**args)
                if args is None:
                    return self.func()
                return self.func(args)

        class Tool(_BaseTool):
            pass

        class StructuredTool(_BaseTool):
            pass

        def tool(func):
            return _BaseTool(name=func.__name__, description=func.__doc__ or "", func=func)

        tools_module.Tool = Tool
        tools_module.StructuredTool = StructuredTool
        tools_module.tool = tool
        sys.modules["langchain_core.tools"] = tools_module


_install_test_stubs()

from agents._tool_allowlist import assert_tools_allowlisted
from agents.calendar_email_agent import CalendarEmailAgent
from agents.communication_agent import CommunicationAgent
from agents.research_agent import ResearchAgent


class StubLLM:
    def __init__(self):
        self.bound_tools = None

    def bind_tools(self, tools):
        self.bound_tools = list(tools)
        return self

    def invoke(self, _messages):
        return SimpleNamespace(content="ok", tool_calls=[])


def _load_mein_tag_module():
    module_path = REPO_ROOT / "pages" / "mein_tag.py"
    spec = importlib.util.spec_from_file_location("mein_tag_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ToolAllowlistTests(unittest.TestCase):
    def test_assert_tools_allowlisted_rejects_unknown_tool(self):
        with self.assertRaisesRegex(RuntimeError, "non-allowlisted tools: evil_tool"):
            assert_tools_allowlisted([SimpleNamespace(name="evil_tool")], "TestAgent")

    def test_communication_agent_binds_only_allowlisted_tools(self):
        stub_llm = StubLLM()

        with mock.patch.object(CommunicationAgent, "_initialize_llm", return_value=stub_llm), \
             mock.patch.object(
                 CommunicationAgent,
                 "_initialize_email_tool",
                 return_value=SimpleNamespace(
                     invoke=lambda **_kwargs: "sent",
                     email_address="team@example.com",
                 ),
             ), \
             mock.patch.object(CommunicationAgent, "_create_system_prompt", return_value="system"), \
             mock.patch.object(CommunicationAgent, "_create_user_prompt", return_value="user"):
            agent = CommunicationAgent(api_key="test-key")
            result = agent.process("Bitte sende eine Testmail")

        self.assertEqual(result["status"], "success")
        self.assertEqual([tool.name for tool in stub_llm.bound_tools], ["send_email"])

    def test_calendar_email_agent_binds_only_allowlisted_tools(self):
        stub_llm = StubLLM()

        with mock.patch.object(CalendarEmailAgent, "_initialize_llm", return_value=stub_llm), \
             mock.patch.object(
                 CalendarEmailAgent,
                 "_initialize_email_tool",
                 return_value=SimpleNamespace(email_address="team@example.com"),
             ), \
             mock.patch.object(
                 CalendarEmailAgent,
                 "_initialize_outlook_tool",
                 return_value=SimpleNamespace(is_authenticated=lambda: True),
             ), \
             mock.patch.object(CalendarEmailAgent, "_create_system_prompt", return_value="system"), \
             mock.patch.object(CalendarEmailAgent, "_create_user_prompt", return_value="user"):
            agent = CalendarEmailAgent(api_key="test-key")
            result = agent.process("Welche Termine habe ich heute?")

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            sorted(tool.name for tool in stub_llm.bound_tools),
            [
                "add_event_attachment",
                "create_email_draft",
                "list_calendar_events",
                "search_contacts",
                "search_emails",
                "send_email",
            ],
        )

    def test_research_agent_binds_only_allowlisted_tools(self):
        stub_llm = StubLLM()

        with mock.patch.object(ResearchAgent, "_initialize_llm", return_value=stub_llm), \
             mock.patch.object(
                 ResearchAgent,
                 "_initialize_tavily",
                 return_value=SimpleNamespace(name="tavily_search_results_json"),
             ), \
             mock.patch.object(
                 ResearchAgent,
                 "_initialize_document_tool",
                 return_value=SimpleNamespace(
                     invoke=lambda *_args, **_kwargs: "doc results",
                     count_documents=lambda: 0,
                     scan_documents=lambda: [],
                 ),
             ), \
             mock.patch.object(ResearchAgent, "_create_system_prompt", return_value="system"), \
             mock.patch.object(ResearchAgent, "_create_user_prompt", return_value="user"):
            agent = ResearchAgent(api_key="test-key")
            result = agent.process("Recherchiere etwas")

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            sorted(tool.name for tool in stub_llm.bound_tools),
            ["search_local_documents", "tavily_search_results_json"],
        )

    def test_mein_tag_tool_names_are_allowlisted(self):
        mein_tag = _load_mein_tag_module()
        tools = [
            SimpleNamespace(name="list_asana_projects"),
            SimpleNamespace(name="get_project_tasks"),
            SimpleNamespace(name="get_my_tasks"),
        ]

        mein_tag.assert_tools_allowlisted(tools, "MeinTagDashboard")


if __name__ == "__main__":
    unittest.main()
