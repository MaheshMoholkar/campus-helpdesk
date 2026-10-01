"""The level 4 tools over the Model Context Protocol (docs/spec.md section 10.7).

A thin wrapper: the same StudentRecords functions the chat API uses, registered
as MCP tools. It runs over stdio, so an MCP client (Claude Desktop, Claude Code,
an IDE) starts it as a subprocess on behalf of one logged-in student. That
student's login token is passed in the environment, never as a tool argument:

    STUDENT_TOKEN=<token from POST /login> uv run python -m apps.mcp_server.server

Example client config (Claude Desktop / Claude Code `.mcp.json`):

    {"mcpServers": {"campus-helpdesk": {
        "command": "uv", "args": ["run", "python", "-m", "apps.mcp_server.server"],
        "cwd": "/path/to/campus-helpdesk",
        "env": {"STUDENT_TOKEN": "...", "STUDENT_API_URL": "http://localhost:8001"}}}}
"""

import os
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from apps.api.tools.student_records import RecordsUnavailable, StudentRecords

server = MCPServer(
    name="campus-helpdesk",
    instructions="Look up the logged-in Navrang University student's own fee dues, attendance and "
    "timetable, and request a bonafide certificate. All data is invented.",
)
_records = StudentRecords(os.environ.get("STUDENT_API_URL", "http://localhost:8001"))


def _token() -> str:
    token = os.environ.get("STUDENT_TOKEN", "")
    if not token:
        raise RuntimeError("STUDENT_TOKEN is not set; log in with POST /login on the student API first")
    return token


def _call(fn, *args) -> dict:
    try:
        result = fn(_token(), *args)
    except RecordsUnavailable as exc:
        return {"error": f"student records unavailable: {exc}"}
    # Lists are wrapped: the SDK would otherwise send each element as a separate content block.
    return {"items": result} if isinstance(result, list) else result


READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


@server.tool(annotations=READ_ONLY)
def get_fee_due() -> dict:
    """The logged-in student's fee dues: term, amount, due date and status."""
    return _call(_records.get_fee_due)


@server.tool(annotations=READ_ONLY)
def get_attendance() -> dict:
    """The logged-in student's attendance per course, with percentages."""
    return _call(_records.get_attendance)


@server.tool(annotations=READ_ONLY)
def get_timetable(
    day: Literal["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"] | None = None,
) -> dict:
    """The logged-in student's weekly class timetable, optionally for one day."""
    return _call(_records.get_timetable, day)


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
def request_bonafide(purpose: str, confirm: bool = False) -> dict:
    """Request a bonafide certificate for the logged-in student.

    Confirm before doing: called without confirm=true it only describes what would
    be submitted. Ask the user, then call again with confirm=true.
    """
    if not confirm:
        return {
            "status": "needs_confirmation",
            "message": f"This will submit a bonafide certificate request (purpose: {purpose}). "
            "Ask the user to confirm, then call request_bonafide again with confirm=true.",
        }
    return _call(_records.request_bonafide, purpose)


if __name__ == "__main__":
    server.run("stdio")
