"""
Defensive allowlist for LangChain tool binding.
"""

from __future__ import annotations

from typing import Any, Iterable


LLM_TOOL_ALLOWLIST = frozenset(
    {
        "add_event_attachment",
        "create_email_draft",
        "get_my_tasks",
        "get_project_tasks",
        "list_asana_projects",
        "list_calendar_events",
        "search_contacts",
        "search_emails",
        "search_local_documents",
        "send_email",
        "tavily_search_results_json",
    }
)


def _get_tool_name(tool: Any) -> str:
    name = getattr(tool, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()

    if isinstance(tool, dict):
        dict_name = tool.get("name")
        if isinstance(dict_name, str) and dict_name.strip():
            return dict_name.strip()

    raise RuntimeError(f"Tool without a valid name cannot be allowlisted: {tool!r}")


def assert_tools_allowlisted(tools: Iterable[Any], agent_name: str) -> None:
    tool_names = [_get_tool_name(tool) for tool in tools]
    disallowed = sorted(name for name in tool_names if name not in LLM_TOOL_ALLOWLIST)

    if disallowed:
        allowed = ", ".join(sorted(LLM_TOOL_ALLOWLIST))
        unexpected = ", ".join(disallowed)
        raise RuntimeError(
            f"{agent_name} attempted to bind non-allowlisted tools: {unexpected}. "
            f"Allowed tools: {allowed}"
        )
