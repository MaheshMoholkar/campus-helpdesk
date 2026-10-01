"""End-to-end through HTTP: chat API + mock student API + Postgres, with fake models."""

import json

import pytest
from fastapi.testclient import TestClient

from apps.api.config import Settings
from apps.api.main import create_app
from tests.conftest import DATA_TODAY

pytestmark = pytest.mark.db


class _TestServices:
    """The real Services wiring, with fakes for models and the test student API."""

    def __init__(self, database_url, chat_service, verifier):
        from apps.api.main import Services

        settings = Settings(
            database_url=database_url, ai_backend="fake", ingest_api_key="test-key", as_of_date=DATA_TODAY
        )
        services = Services.__new__(Services)
        services.settings = settings
        services.pool = chat_service._pool
        services.models = chat_service._models
        services.verifier = verifier
        services.records = chat_service._records
        services.chat = chat_service
        self.services = services

    def __call__(self):
        return self.services


@pytest.fixture(scope="module")
def client(database_url, chat_service, verifier):
    factory = _TestServices(database_url, chat_service, verifier)
    factory.services.close = lambda: None  # the pool belongs to the session fixture
    with TestClient(create_app(factory)) as c:
        yield c


def chat(client, question, token=None, **extra):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = client.post("/chat", json={"question": question, **extra}, headers=headers)
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
    result = chat(client, "What is the COE semester 3 tuition fee and last date?")
    assert not any(slug.startswith("coe-fee-structure") for slug in cited(result))
    assert not any(slug.startswith("coe-fee") for slug in result["retrieved"]["candidates"])


def test_student_gets_current_version_of_own_college_circular(client, login):
    result = chat(client, "What is the COE semester 3 tuition fee and last date?", login("S1001"))
    assert cited(result) == {"coe-fee-structure-2026-v2"}
    assert "64,500" in result["answer"] or "15 November" in result["answer"]


def test_student_of_another_college_does_not_see_it(client, login):
    result = chat(client, "What is the COE semester 3 tuition fee and last date?", login("S1002"))
    assert "coe-fee-structure-2026-v2" not in result["retrieved"]["candidates"]


def test_student_cannot_see_staff_documents(client, login):
    result = chat(client, "How many days of earned leave can be encashed?", login("S1001"))
    assert "all-staff-leave-policy-2026" not in result["retrieved"]["candidates"]
    staff = chat(client, "How many days of earned leave can be encashed?", login("T2002"))
    assert "all-staff-leave-policy-2026" in cited(staff)


def test_bad_token_is_401_not_anonymous(client):
    response = client.post("/chat", json={"question": "hi"}, headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_out_of_scope_is_refused(client):
    assert chat(client, "Who will win the cricket match tonight?")["outcome"] == "refused"


def test_personal_questions_need_a_student_login(client, login):
    assert chat(client, "What is my fee due?")["outcome"] == "refused"
    assert chat(client, "What is my fee due?", login("T2001"))["outcome"] == "refused"
    result = chat(client, "What is my fee due?", login("S1001"))
    assert result["outcome"] == "tool" and "64500" in result["answer"]
    assert result["tools_called"] == ["get_fee_due"]


def test_bonafide_needs_confirmation_from_the_same_student(client, login):
    asked = chat(client, "Please request a bonafide certificate for me", login("S1001"))
    action_id = asked["pending_action_id"]
    assert asked["outcome"] == "tool" and action_id

    conversation = asked["conversation_id"]
    # Another student cannot confirm it (and cannot even open the conversation).
    response = client.post(
        "/chat",
        json={"question": "yes", "conversation_id": conversation, "pending_action_id": action_id},
        headers={"Authorization": f"Bearer {login('S1004')}"},
    )
    assert response.status_code == 404

    done = chat(client, "yes", login("S1001"), conversation_id=conversation, pending_action_id=action_id)
    assert "submitted" in done["answer"]
    again = chat(client, "yes", login("S1001"), conversation_id=conversation, pending_action_id=action_id)
    assert again["outcome"] == "refused"


def test_follow_up_is_rewritten_using_history(client, login):
    first = chat(client, "What is the CAS semester fee?", login("S1002"))
    second = chat(client, "last date?", login("S1002"), conversation_id=first["conversation_id"])
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
