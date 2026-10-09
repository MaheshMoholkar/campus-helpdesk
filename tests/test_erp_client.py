"""The CampusERP client: cookie handling, identity mapping, and what it forwards."""

import httpx
import pytest

from apps.api.erp import (
    ErpClient,
    ErpCredentials,
    NotLoggedIn,
    claims_from_me,
    credentials_from_cookie_header,
)


def test_only_campus_erp_cookies_are_picked():
    header = "theme=dark; __Host-session=abc; tracking=xyz; __Host-csrf=tok"
    assert credentials_from_cookie_header(header, "__Host-session", "__Host-csrf") == ErpCredentials(
        "abc", "tok"
    )
    assert credentials_from_cookie_header("theme=dark", "__Host-session", "__Host-csrf") is None
    assert credentials_from_cookie_header(None, "__Host-session", "__Host-csrf") is None


def _me(kind=None, code="alpha"):
    return {
        "user": {"id": 7, "email": "x@alpha.test", "fullName": "X", "mustChangePassword": False},
        "institute": {
            "id": 1,
            "name": "Sahyadri Engineering College",
            "timezone": "Asia/Kolkata",
            "code": code,
        },
        "permissions": [],
        "roles": [],
        "grantsAll": False,
        "person": {"kind": kind, "id": 3} if kind else None,
    }


@pytest.mark.parametrize(("kind", "role"), [("student", "student"), ("staff", "staff"), (None, "staff")])
def test_claims_from_me(kind, role):
    claims = claims_from_me(_me(kind))
    assert (claims.sub, claims.role, claims.college, claims.person_kind) == ("alpha:7", role, "alpha", kind)


def test_forwarded_headers_and_csrf_on_post():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/auth/me"):
            return httpx.Response(200, json=_me("student"))
        return httpx.Response(201, json={"id": 1, "status": "requested"})

    erp = ErpClient(client=httpx.Client(base_url="http://erp", transport=httpx.MockTransport(handler)))
    creds = ErpCredentials("sess", "tok")
    erp.whoami(creds)
    erp.request_bonafide(creds, "passport")

    get, post = seen
    assert get.headers["cookie"] == "__Host-session=sess" and "x-csrf-token" not in get.headers
    assert post.headers["cookie"] == "__Host-session=sess; __Host-csrf=tok"
    assert post.headers["x-csrf-token"] == "tok"
    assert "origin" not in post.headers


def test_rejected_session_raises():
    erp = ErpClient(
        client=httpx.Client(
            base_url="http://erp", transport=httpx.MockTransport(lambda r: httpx.Response(401))
        )
    )
    with pytest.raises(NotLoggedIn):
        erp.whoami(ErpCredentials("expired"))


def test_rotated_session_is_used_and_passed_back():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_me("student"),
            headers=[("set-cookie", "__Host-session=new; Path=/; Secure; HttpOnly; SameSite=Lax")],
        )

    erp = ErpClient(client=httpx.Client(base_url="http://erp", transport=httpx.MockTransport(handler)))
    identity = erp.whoami(ErpCredentials("old", "tok"))
    assert identity.creds == ErpCredentials("new", "tok")
    assert identity.set_cookie == ["__Host-session=new; Path=/; Secure; HttpOnly; SameSite=Lax"]
