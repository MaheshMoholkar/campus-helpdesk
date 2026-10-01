"""The MCP server exposes the same four tools and forwards the user's token."""

import asyncio
import json

import pytest

pytestmark = pytest.mark.db


def _text(result) -> object:
    """The JSON payload of a CallToolResult (MCP 2.x): structured content if present, else the text."""
    assert not result.is_error, result.content
    if result.structured_content is not None:
        return result.structured_content.get("result", result.structured_content)
    return json.loads(result.content[0].text)


def test_tools_listed_and_working(monkeypatch, login, student_api):
    from apps.mcp_server import server as mcp_module

    monkeypatch.setenv("STUDENT_TOKEN", login("S1001"))
    monkeypatch.setattr(mcp_module, "_records", mcp_module.StudentRecords(client=student_api))

    tools = asyncio.run(mcp_module.server.list_tools())
    assert {t.name for t in tools} == {"get_fee_due", "get_attendance", "get_timetable", "request_bonafide"}

    fees = _text(asyncio.run(mcp_module.server.call_tool("get_fee_due", {})))
    assert fees["items"][0]["amount_due"] == 64500

    preview = _text(asyncio.run(mcp_module.server.call_tool("request_bonafide", {"purpose": "passport"})))
    assert preview["status"] == "needs_confirmation"
    done = _text(
        asyncio.run(mcp_module.server.call_tool("request_bonafide", {"purpose": "passport", "confirm": True}))
    )
    assert done["status"] == "submitted"
