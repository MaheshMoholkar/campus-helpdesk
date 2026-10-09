"""The level 4 tools: CampusERP self-service lookups, run as the logged-in user.

None of them takes a student or staff id. The user's CampusERP session is
forwarded as-is and CampusERP works out whose records to return, so whatever
the model asks for, it can only reach the logged-in user's own records.

The MCP server (apps/mcp_server) registers these same functions.
"""

from typing import Any

from apps.api.erp import ErpClient, ErpCredentials

STUDENT_READ_TOOLS = ("get_my_fees", "get_my_attendance", "get_my_results")
STAFF_READ_TOOLS = ("get_my_leave",)
ACTION_TOOLS = ("request_bonafide",)

_NO_ARGS = {"type": "object", "properties": {}, "additionalProperties": False}

# Tool definitions in the OpenAI function-calling format, which Ollama accepts.
_DEFINITIONS: dict[str, dict] = {
    "get_my_fees": {
        "description": "The logged-in student's fee account from CampusERP: total charged, paid, "
        "balance, amount due now, and the terms that are due.",
        "parameters": _NO_ARGS,
    },
    "get_my_attendance": {
        "description": "The logged-in student's attendance per course from CampusERP: classes held, "
        "present, and percentage.",
        "parameters": _NO_ARGS,
    },
    "get_my_results": {
        "description": "The logged-in student's exam results per term from CampusERP, with SGPA.",
        "parameters": _NO_ARGS,
    },
    "get_my_leave": {
        "description": "The logged-in staff member's leave balances for this year from CampusERP.",
        "parameters": _NO_ARGS,
    },
    "request_bonafide": {
        "description": "Request a bonafide certificate for the logged-in student in CampusERP. "
        "The user is asked to confirm before anything is submitted.",
        "parameters": {
            "type": "object",
            "properties": {
                "purpose": {
                    "type": "string",
                    "description": "Why the certificate is needed, e.g. 'bank account', 'passport'.",
                }
            },
            "required": ["purpose"],
            "additionalProperties": False,
        },
    },
}


def tools_for(person_kind: str | None) -> tuple[list[dict], tuple[str, ...]]:
    """The tool definitions and read-tool names a user may use, by what their login is linked to."""
    if person_kind == "student":
        names = (*STUDENT_READ_TOOLS, *ACTION_TOOLS)
        reads = STUDENT_READ_TOOLS
    elif person_kind == "staff":
        names = STAFF_READ_TOOLS
        reads = STAFF_READ_TOOLS
    else:
        return [], ()
    definitions = [{"type": "function", "function": {"name": n, **_DEFINITIONS[n]}} for n in names]
    return definitions, reads


def run_read_tool(erp: ErpClient, creds: ErpCredentials, name: str, arguments: dict) -> Any:
    if name == "get_my_fees":
        return erp.my_fees(creds)
    if name == "get_my_attendance":
        return erp.my_attendance(creds)
    if name == "get_my_results":
        return erp.my_results(creds)
    if name == "get_my_leave":
        return erp.my_leave(creds)
    raise ValueError(f"not a read tool: {name}")
