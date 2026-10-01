"""The level 4 tools, as plain functions over the mock student API.

None of them takes a student id. The id travels inside the user's login token,
which is forwarded as-is, and the student API reads it from there. So whatever
the model asks for, it can only ever reach the logged-in student's own records.

The MCP server (apps/mcp_server) registers these same functions.
"""

from typing import Any

import httpx


class RecordsUnavailable(Exception):
    pass


class StudentRecords:
    def __init__(self, base_url: str = "", timeout: float = 10.0, client: httpx.Client | None = None):
        """Pass `client` to reuse an existing httpx client (tests pass the student API's TestClient)."""
        self._http = client or httpx.Client(base_url=base_url, timeout=timeout)

    def _call(self, method: str, path: str, token: str, json: dict | None = None) -> Any:
        try:
            response = self._http.request(
                method, path, json=json, headers={"Authorization": f"Bearer {token}"}
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RecordsUnavailable(str(exc)) from exc
        return response.json()

    def get_fee_due(self, token: str) -> Any:
        return self._call("GET", "/me/fees", token)

    def get_attendance(self, token: str) -> Any:
        return self._call("GET", "/me/attendance", token)

    def get_timetable(self, token: str, day: str | None = None) -> Any:
        timetable = self._call("GET", "/me/timetable", token)
        if day:
            timetable = [slot for slot in timetable if slot["day"].lower() == day.lower()]
        return timetable

    def request_bonafide(self, token: str, purpose: str) -> Any:
        return self._call("POST", "/me/bonafide-requests", token, json={"purpose": purpose})


# Tool definitions in the OpenAI function-calling format, which Ollama accepts.
READ_TOOLS = ("get_fee_due", "get_attendance", "get_timetable")
ACTION_TOOLS = ("request_bonafide",)

TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_fee_due",
            "description": "The logged-in student's fee dues: term, amount, due date and status.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_attendance",
            "description": "The logged-in student's attendance per course, with percentages.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_timetable",
            "description": "The logged-in student's weekly class timetable, optionally for one day.",
            "parameters": {
                "type": "object",
                "properties": {
                    "day": {
                        "type": "string",
                        "enum": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"],
                    }
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_bonafide",
            "description": "Request a bonafide certificate for the logged-in student. "
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
    },
]


def run_read_tool(records: StudentRecords, token: str, name: str, arguments: dict) -> Any:
    if name == "get_fee_due":
        return records.get_fee_due(token)
    if name == "get_attendance":
        return records.get_attendance(token)
    if name == "get_timetable":
        day = arguments.get("day")
        return records.get_timetable(token, day if isinstance(day, str) else None)
    raise ValueError(f"not a read tool: {name}")
