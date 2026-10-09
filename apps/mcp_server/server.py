"""The level 4 tools over the Model Context Protocol (docs/spec.md section 10.7).

A thin wrapper: the same CampusERP lookups the chat API uses, registered as MCP
tools. It runs over stdio, so an MCP client (Claude Desktop, Claude Code, an IDE)
starts it as a subprocess on behalf of one CampusERP user. That user's CampusERP
session is passed in the environment, never as a tool argument:

    CAMPUS_ERP_SESSION=<value of the __Host-session cookie> \\
    CAMPUS_ERP_CSRF=<value of the __Host-csrf cookie> \\
    uv run python -m apps.mcp_server.server

Example client config (Claude Desktop / Claude Code `.mcp.json`):

    {"mcpServers": {"campus-helpdesk": {
        "command": "uv", "args": ["run", "python", "-m", "apps.mcp_server.server"],
        "cwd": "/path/to/campus-helpdesk",
        "env": {"CAMPUS_ERP_SESSION": "...", "CAMPUS_ERP_CSRF": "...",
                "ERP_API_URL": "http://localhost:8000"}}}}
"""

import os

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from apps.api.erp import ErpClient, ErpCredentials, ErpUnavailable, NotLoggedIn

server = MCPServer(
    name="campus-helpdesk",
    instructions="Look up the logged-in CampusERP user's own fees, attendance, results and leave "
    "balance, and request a bonafide certificate.",
)
_erp = ErpClient(os.environ.get("ERP_API_URL", "http://localhost:8000"))


def _creds() -> ErpCredentials:
    session = os.environ.get("CAMPUS_ERP_SESSION", "")
    if not session:
        raise RuntimeError("CAMPUS_ERP_SESSION is not set; log in to CampusERP and copy the session cookie")
    return ErpCredentials(session, os.environ.get("CAMPUS_ERP_CSRF") or None)


def _call(fn, *args) -> dict:
    try:
        result = fn(_creds(), *args)
    except NotLoggedIn:
        return {"error": "CampusERP session is not valid; log in again"}
    except ErpUnavailable as exc:
        return {"error": f"CampusERP unavailable: {exc}"}
    # Lists are wrapped: the SDK would otherwise send each element as a separate content block.
    return {"items": result} if isinstance(result, list) else result


READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


@server.tool(annotations=READ_ONLY)
def get_my_fees() -> dict:
    """The logged-in student's fee account: charged, paid, balance, amount due now, and terms due."""
    return _call(_erp.my_fees)


@server.tool(annotations=READ_ONLY)
def get_my_attendance() -> dict:
    """The logged-in student's attendance per course, with percentages."""
    return _call(_erp.my_attendance)


@server.tool(annotations=READ_ONLY)
def get_my_results() -> dict:
    """The logged-in student's exam results per term, with SGPA."""
    return _call(_erp.my_results)


@server.tool(annotations=READ_ONLY)
def get_my_leave() -> dict:
    """The logged-in staff member's leave balances for this year."""
    return _call(_erp.my_leave)


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
def request_bonafide(purpose: str, confirm: bool = False) -> dict:
    """Request a bonafide certificate for the logged-in student in CampusERP.

    Confirm before doing: called without confirm=true it only describes what would
    be submitted. Ask the user, then call again with confirm=true.
    """
    if not confirm:
        return {
            "status": "needs_confirmation",
            "message": f"This will submit a bonafide certificate request (purpose: {purpose}). "
            "Ask the user to confirm, then call request_bonafide again with confirm=true.",
        }
    return _call(_erp.request_bonafide, purpose)


if __name__ == "__main__":
    server.run("stdio")
