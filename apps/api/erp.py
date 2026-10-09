"""Client for CampusERP, the system this helpdesk extends.

The helpdesk has no users of its own. The browser talks to CampusERP's web app,
which forwards /api/helpdesk/* here together with the user's CampusERP session
cookie. To learn who is asking, the helpdesk calls CampusERP's GET /auth/me with
that same session; to look up someone's fees or attendance it calls CampusERP's
self-service endpoints, again as that user. So CampusERP's own access rules apply
to every lookup: the helpdesk can never see more than the user could see in
CampusERP itself.
"""

import json
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from typing import Any

import httpx

from apps.api.scope import Claims

API = "/api/v1"


class ErpUnavailable(Exception):
    """CampusERP could not be reached or answered with a server error."""


class NotLoggedIn(Exception):
    """The session cookie is missing, expired or revoked."""


@dataclass(frozen=True)
class ErpCredentials:
    """The user's CampusERP session, as forwarded by CampusERP's web app."""

    session: str
    csrf: str | None = None


def credentials_from_cookie_header(
    cookie_header: str | None, session_cookie: str, csrf_cookie: str
) -> ErpCredentials | None:
    """Pick the two CampusERP cookies out of a Cookie header; ignore everything else."""
    if not cookie_header:
        return None
    jar = SimpleCookie()
    try:
        jar.load(cookie_header)
    except CookieError:
        return None
    session = jar.get(session_cookie)
    if session is None or not session.value:
        return None
    csrf = jar.get(csrf_cookie)
    return ErpCredentials(session.value, csrf.value if csrf else None)


@dataclass(frozen=True)
class Identity:
    claims: Claims
    creds: ErpCredentials  # the session to use for the rest of the turn (rotated if CampusERP rotated it)
    set_cookie: list[str]  # Set-Cookie values to pass back to the browser after a rotation


class ErpClient:
    def __init__(
        self,
        base_url: str = "",
        session_cookie: str = "__Host-session",
        csrf_cookie: str = "__Host-csrf",
        csrf_header: str = "X-CSRF-Token",
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        """Pass `client` to reuse an existing httpx client (unit tests pass one with a mock transport)."""
        self.session_cookie = session_cookie
        self.csrf_cookie = csrf_cookie
        self.csrf_header = csrf_header
        self._http = client or httpx.Client(base_url=base_url, timeout=timeout)

    # --- transport ----------------------------------------------------------

    def _headers(self, creds: ErpCredentials, unsafe: bool) -> dict[str, str]:
        # Only CampusERP's own two cookies are forwarded, never the rest of the browser's jar.
        cookies = f"{self.session_cookie}={creds.session}"
        headers = {"Accept": "application/json"}
        if unsafe:
            # CampusERP's CSRF defence is a double-submit token: the header must echo the
            # cookie. It sends no Origin header, which CampusERP only checks when present.
            if not creds.csrf:
                raise NotLoggedIn("missing CSRF cookie")
            cookies += f"; {self.csrf_cookie}={creds.csrf}"
            headers[self.csrf_header] = creds.csrf
        headers["Cookie"] = cookies
        return headers

    def _call(self, method: str, path: str, creds: ErpCredentials, **kwargs: Any) -> Any:
        unsafe = method not in ("GET", "HEAD")
        try:
            response = self._http.request(method, API + path, headers=self._headers(creds, unsafe), **kwargs)
        except httpx.HTTPError as exc:
            raise ErpUnavailable(str(exc)) from exc
        if response.status_code == 401:
            raise NotLoggedIn(response.text)
        if response.status_code >= 500:
            raise ErpUnavailable(f"{response.status_code} {response.text[:200]}")
        if response.status_code >= 400:
            # 403/404/409 etc. are CampusERP's answer (e.g. "not linked to a student");
            # hand the problem detail back so the model can explain it.
            return {"error": _problem_title(response)}
        return response.json() if response.content else None

    # --- login (dev tooling: evals, tests, MCP setup; the web app logs users in itself) ---

    def login(self, email: str, password: str) -> ErpCredentials:
        """Sign in to CampusERP like its web app does: fetch a CSRF cookie, then POST the login."""
        try:
            csrf = self._http.get("/health/live").cookies.get(self.csrf_cookie)
            response = self._http.post(
                API + "/auth/login",
                json={"email": email, "password": password},
                headers={self.csrf_header: csrf or "", "Cookie": f"{self.csrf_cookie}={csrf}"},
            )
        except httpx.HTTPError as exc:
            raise ErpUnavailable(str(exc)) from exc
        session = response.cookies.get(self.session_cookie)
        if response.status_code != 200 or not session:
            raise NotLoggedIn(f"{response.status_code} {_problem_title(response)}")
        return ErpCredentials(session, csrf)

    def reusable_login(self, email: str, password: str, cache_file: Path) -> ErpCredentials:
        """login(), but reusing a still-valid session saved in cache_file.

        CampusERP allows 20 sign-ins per IP per 5 minutes; tests and evals that log the demo
        users in on every run would hit that quickly. The file holds demo sessions only.
        """
        try:
            cache = json.loads(cache_file.read_text())
        except (OSError, ValueError):
            cache = {}
        if email in cache:
            creds = ErpCredentials(*cache[email])
            try:
                self._call("GET", "/auth/me", creds)
                return creds
            except NotLoggedIn:
                pass
        creds = self.login(email, password)
        cache[email] = [creds.session, creds.csrf]
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache))
        return creds

    # --- identity -----------------------------------------------------------

    def whoami(self, creds: ErpCredentials) -> "Identity":
        """Who the session belongs to, via CampusERP's /auth/me. Called once per chat request.

        CampusERP may rotate a session on its next request (after a role or grant change):
        it answers with a new session cookie and keeps the old one valid for only 30 s.
        Calling /auth/me first means any rotation happens here, before the tools run; the
        new token is used for the rest of the turn, and its Set-Cookie is handed back so the
        browser gets it too. (That is also why identities are not cached.)
        """
        try:
            response = self._http.get(API + "/auth/me", headers=self._headers(creds, unsafe=False))
        except httpx.HTTPError as exc:
            raise ErpUnavailable(str(exc)) from exc
        if response.status_code == 401:
            raise NotLoggedIn(response.text)
        if response.status_code != 200:
            raise ErpUnavailable(f"/auth/me answered {response.status_code}")
        me = response.json()
        rotated = response.cookies.get(self.session_cookie)
        set_cookie: list[str] = []
        if rotated and rotated != creds.session:
            creds = ErpCredentials(rotated, creds.csrf)
            set_cookie = [
                value
                for value in response.headers.get_list("set-cookie")
                if value.startswith(f"{self.session_cookie}=")
            ]
        return Identity(claims_from_me(me), creds, set_cookie)

    # --- self-service lookups (the helpdesk's tools) --------------------------

    def my_fees(self, creds: ErpCredentials) -> Any:
        account = self._call("GET", "/fees/my-account", creds)
        if not isinstance(account, dict) or "error" in account:
            return account
        # The model needs the totals and which terms are due, not every structure line.
        return {
            "student": account["student"]["fullName"],
            "charged": account["charged"],
            "paid": account["paid"],
            "balance": account["balance"],
            "due_now": account["dueNow"],
            "terms": [
                {"term": t["termName"], "total": t["total"], "due": t["due"], "starts_on": t["startsOn"]}
                for t in account["terms"]
            ],
            "currency": "INR",
        }

    def my_attendance(self, creds: ErpCredentials) -> Any:
        rows = self._call("GET", "/teaching/my-attendance", creds)
        if not isinstance(rows, list):
            return rows
        return [
            {
                "course": f"{r['course']['code']} {r['course']['title']}",
                "held": r["held"],
                "present": r["present"],
                "percent": r["percent"],
            }
            for r in rows
        ]

    def my_results(self, creds: ErpCredentials) -> Any:
        results = self._call("GET", "/exams/my-results", creds)
        if not isinstance(results, dict) or "error" in results:
            return results
        return [
            {
                "term": term["term"]["name"],
                "sgpa": term.get("sgpa"),
                "courses": [
                    {
                        "course": f"{c['course']['code']} {c['course']['title']}",
                        "grade": c.get("grade"),
                        "total": c.get("total"),
                        "passed": c.get("isPass"),
                    }
                    for c in term.get("courses", [])
                ],
            }
            for term in results.get("terms", [])
        ]

    def my_leave(self, creds: ErpCredentials) -> Any:
        leave = self._call("GET", "/hr/my-leave", creds)
        if not isinstance(leave, dict) or "error" in leave:
            return leave
        return {
            "year": leave["year"],
            "balances": [
                {
                    "type": b["leaveTypeName"],
                    "quota": b["quota"],
                    "used": b["approved"],
                    "pending": b["pending"],
                    "left": b["left"],
                }
                for b in leave["balances"]
                if b.get("isActive", True)
            ],
        }

    def request_bonafide(self, creds: ErpCredentials, purpose: str) -> Any:
        return self._call("POST", "/people/my-bonafide-requests", creds, json={"purpose": purpose})


def claims_from_me(me: dict) -> Claims:
    """Map CampusERP's /auth/me onto the helpdesk's roles.

    A login linked to a student is a student. Every other login of a college
    (teachers, office staff, administrators) is staff for document visibility;
    `person_kind` records whether it is linked to a staff member, which the
    staff-only tools (leave balance) need.
    """
    person = me.get("person") or {}
    kind = person.get("kind")
    institute = me["institute"]
    college = institute.get("code") or str(institute["id"])
    return Claims(
        sub=f"{college}:{me['user']['id']}",
        role="student" if kind == "student" else "staff",
        college=college,
        name=me["user"].get("fullName"),
        person_kind=kind,
    )


def _problem_title(response: httpx.Response) -> str:
    try:
        body = response.json()
        return body.get("title") or body.get("detail") or response.reason_phrase
    except ValueError:
        return response.reason_phrase
