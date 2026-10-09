"""The MCP server exposes the CampusERP tools and forwards the user's session."""

import asyncio
import json
from http.cookies import SimpleCookie


def _payload(result) -> object:
    """The JSON payload of a CallToolResult (MCP 2.x): structured content if present, else the text."""
    assert not result.is_error, result.content
    if result.structured_content is not None:
        return result.structured_content.get("result", result.structured_content)
    return json.loads(result.content[0].text)


def test_tools_listed_and_working(monkeypatch, login, erp):
    from apps.mcp_server import server as mcp_module

    jar = SimpleCookie()
    jar.load(login("student2@alpha.test"))
    monkeypatch.setenv("CAMPUS_ERP_SESSION", jar["__Host-session"].value)
    monkeypatch.setenv("CAMPUS_ERP_CSRF", jar["__Host-csrf"].value)
    monkeypatch.setattr(mcp_module, "_erp", erp)

    def call(name, args):
        return _payload(asyncio.run(mcp_module.server.call_tool(name, args)))

    tools = asyncio.run(mcp_module.server.list_tools())
    assert {t.name for t in tools} == {
        "get_my_fees",
        "get_my_attendance",
        "get_my_results",
        "get_my_leave",
        "request_bonafide",
    }
    assert call("get_my_fees", {})["due_now"] == "20000.00"  # CampusERP demo data
    assert call("request_bonafide", {"purpose": "passport"})["status"] == "needs_confirmation"
    assert call("request_bonafide", {"purpose": "passport", "confirm": True})["status"] == "requested"
