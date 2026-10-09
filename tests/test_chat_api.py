"""End-to-end through HTTP: chat API + real CampusERP API + Postgres, with fake models.

Requests carry CampusERP's session and CSRF cookies the way CampusERP's web app
forwards them, plus the X-CSRF-Token header its chat panel sends.
"""

import json
from http.cookies import SimpleCookie

import pytest
from fastapi.testclient import TestClient

from apps.api.config import Settings
from apps.api.main import create_app
from tests.conftest import DATA_TODAY

pytestmark = pytest.mark.db


class _TestServices:
    """The real Services wiring, with fake models."""

    def __init__(self, database_url, chat_service, erp):
        from apps.api.main import Services

        services = Services.__new__(Services)
        services.settings = Settings(
            database_url=database_url, ai_backend="fake", ingest_api_key="test-key", as_of_date=DATA_TODAY
        )
        services.pool = chat_service._pool
        services.models = chat_service._models
        services.erp = erp
        services.chat = chat_service
        services.close = lambda: None  # the pool belongs to the session fixture
        self.services = services

    def __call__(self):
        return self.services


@pytest.fixture(scope="module")
def client(database_url, chat_service, erp):
    with TestClient(create_app(_TestServices(database_url, chat_service, erp))) as c:
        yield c


def headers_for(cookie: str | None, csrf: bool = True) -> dict:
    if not cookie:
        return {}
    headers = {"Cookie": cookie}
    if csrf:
        jar = SimpleCookie()
        jar.load(cookie)
        headers["X-CSRF-Token"] = jar["__Host-csrf"].value
    return headers


def post_chat(client, question, cookie=None, csrf=True, **extra):
    return client.post("/chat", json={"question": question, **extra}, headers=headers_for(cookie, csrf))


def chat(client, question, cookie=None, **extra):
    response = post_chat(client, question, cookie, **extra)
    assert response.status_code == 200, response.text
    result = {"answer": "", "citations": [], "events": []}
    for block in response.text.strip().split("\n\n"):
        kind = block.split("\n")[0].removeprefix("event: ")
        data = json.loads(block.split("\n")[1].removeprefix("data: "))
        result["events"].append(kind)
        if kind == "delta":
            result["answer"] += data["text"]
        elif kind == "citations":
            result["citations"] = data["items"]
        else:
            result.update(data)
    return result


def cited(result):
    return {c["slug"] for c in result["citations"]}


SEC_FEE = "What is the SEC semester 3 tuition fee and last date?"


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_anonymous_gets_a_cited_public_answer(client):
    result = chat(client, "What is the revaluation fee per paper?")
    assert result["outcome"] == "answered"
    assert result["events"][0] == "meta" and result["events"][-1] == "done"
    assert "all-exam-policy-2026" in cited(result)
    assert all(c["issue_date"] for c in result["citations"])
    assert "500" in result["answer"]


def test_anonymous_cannot_reach_student_circulars(client):
    result = chat(client, SEC_FEE)
    assert not any(slug.startswith("alpha-fee") for slug in result["retrieved"]["candidates"])


def test_student_gets_current_version_of_own_college_circular(client, login):
    result = chat(client, SEC_FEE, login("student@alpha.test"))
    assert cited(result) == {"alpha-fee-structure-2026-v2"}


def test_student_of_the_other_college_does_not_see_it(client, login):
    result = chat(client, SEC_FEE, login("student@beta.test"))
    assert "alpha-fee-structure-2026-v2" not in result["retrieved"]["candidates"]


def test_student_cannot_see_staff_documents(client, login):
    question = "How many days of earned leave can be encashed?"
    result = chat(client, question, login("student@alpha.test"))
    assert "all-staff-leave-policy-2026" not in result["retrieved"]["candidates"]
    staff = chat(client, question, login("teacher@beta.test"))
    assert "all-staff-leave-policy-2026" in cited(staff)


def test_unlinked_login_still_sees_staff_documents(client, login):
    result = chat(client, "How many days of earned leave can be encashed?", login("admin@alpha.test"))
    assert "all-staff-leave-policy-2026" in cited(result)


def test_invalid_session_is_401_not_anonymous(client):
    response = post_chat(client, "hi", "__Host-session=forged; __Host-csrf=x")
    assert response.status_code == 401


def test_post_with_session_needs_matching_csrf_header(client, login):
    assert post_chat(client, "hi", login("student@alpha.test"), csrf=False).status_code == 403
    wrong = {"Cookie": login("student@alpha.test"), "X-CSRF-Token": "not-the-cookie"}
    assert client.post("/chat", json={"question": "hi"}, headers=wrong).status_code == 403


def test_out_of_scope_is_refused(client):
    assert chat(client, "Who will win the cricket match tonight?")["outcome"] == "refused"


def test_personal_lookups_go_to_campus_erp(client, login):
    assert chat(client, "What is my fee due?")["outcome"] == "refused"  # anonymous
    assert chat(client, "What is my fee due?", login("admin@alpha.test"))["outcome"] == "refused"  # unlinked

    fees = chat(client, "What is my fee due?", login("student2@alpha.test"))
    assert fees["outcome"] == "tool" and fees["tools_called"] == ["get_my_fees"]
    assert "20000.00" in fees["answer"]  # CampusERP demo data

    results = chat(client, "What are my results?", login("student2@alpha.test"))
    assert results["tools_called"] == ["get_my_results"] and "9.14" in results["answer"]

    leave = chat(client, "What is my leave balance?", login("teacher@alpha.test"))
    assert leave["tools_called"] == ["get_my_leave"]


def test_staff_are_not_offered_student_tools(client, login):
    result = chat(client, "What is my fee due?", login("teacher@alpha.test"))
    assert result["outcome"] == "tool" and result["tools_called"] == []


def test_bonafide_needs_confirmation_from_the_same_student(client, login):
    asked = chat(client, "Please request a bonafide certificate for me", login("student@alpha.test"))
    action_id = asked["pending_action_id"]
    assert asked["outcome"] == "tool" and action_id
    conversation = asked["conversation_id"]

    # Another student cannot confirm it (and cannot even open the conversation).
    other = post_chat(
        client, "yes", login("student2@alpha.test"), conversation_id=conversation, pending_action_id=action_id
    )
    assert other.status_code == 404

    done = chat(
        client, "yes", login("student@alpha.test"), conversation_id=conversation, pending_action_id=action_id
    )
    assert "submitted" in done["answer"]
    again = chat(
        client, "yes", login("student@alpha.test"), conversation_id=conversation, pending_action_id=action_id
    )
    assert again["outcome"] == "refused"


def test_follow_up_is_rewritten_using_history(client, login):
    first = chat(client, "What is the DSC semester fee?", login("student@beta.test"))
    second = chat(client, "last date?", login("student@beta.test"), conversation_id=first["conversation_id"])
    assert "fee" in second["rewritten_question"].lower()


def test_feedback(client):
    result = chat(client, "What is the bus pass fee?")
    assert client.post("/feedback", json={"turn_id": result["turn_id"], "rating": 1}).status_code == 201
    assert (
        client.post("/feedback", json={"turn_id": result["conversation_id"], "rating": 1}).status_code == 404
    )
    assert client.post("/feedback", json={"turn_id": result["turn_id"], "rating": 5}).status_code == 422


def test_ingest_requires_api_key(client):
    doc = {
        "slug": "t-api",
        "title": "T",
        "doc_type": "faq",
        "college_code": "all",
        "audience": "public",
        "category": "general",
        "issue_date": "2026-09-01",
        "body": "Body.",
    }
    assert client.post("/ingest", json=doc).status_code == 401
    assert client.post("/ingest", json=doc, headers={"X-API-Key": "test-key"}).status_code == 201
