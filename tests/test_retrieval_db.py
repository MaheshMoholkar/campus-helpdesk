"""Scope and validity at the SQL level, against the real corpus (scope-leak layer 2)."""

import pytest
import yaml

from apps.api.llm.fake import FakeEmbedder
from apps.api.retrieval import RetrievalConfig, retrieve
from apps.api.retrieval.search import keyword_search, vector_search
from apps.api.scope import Claims, build_scope, is_visible
from tests.conftest import DATA_TODAY, ROOT

pytestmark = pytest.mark.db

DOCS = {d["slug"]: d for d in yaml.safe_load((ROOT / "data" / "documents.yaml").read_text())}
USERS = [None] + [Claims(f"{r}-{c}", r, c) for r in ("student", "staff") for c in ("coe", "cas", "com")]
NOT_LIVE = {"coe-fee-structure-2026-v1", "hostel-fee-2025-26", "all-ganesh-holiday-2026"}


@pytest.mark.parametrize("claims", USERS, ids=lambda c: "anonymous" if c is None else c.sub)
def test_no_search_returns_anything_outside_scope(loaded, claims):
    """Every document's title and opening text are used as queries, for every kind of user.
    Each result must be visible to that user and live. Visibility is judged from the
    documents table with the plain-Python rule, independently of the SQL filter and of
    the tag copies on the chunk rows."""
    scope = build_scope(claims)
    embedder = FakeEmbedder()
    with loaded.connection() as conn:
        truth = {
            row["slug"]: row
            for row in conn.execute(
                "SELECT slug, audience, college_code, is_current, expires_on FROM documents"
            )
        }
        for doc in DOCS.values():
            for query in (doc["title"], doc["body"][:300]):
                results = vector_search(conn, scope, embedder.embed([query])[0], 50, DATA_TODAY)
                results += keyword_search(conn, scope, query, 50, DATA_TODAY)
                for candidate in results:
                    found = truth[candidate.slug]
                    assert is_visible(scope, found["audience"], found["college_code"]), candidate.slug
                    assert found["is_current"], candidate.slug
                    assert found["expires_on"] is None or found["expires_on"] >= DATA_TODAY
                    assert candidate.slug not in NOT_LIVE, candidate.slug


def test_restricted_documents_are_actually_reachable_by_their_audience(loaded):
    """The leak test above would pass trivially if nothing restricted were ever found."""
    scope = build_scope(Claims("T2001", "staff", "coe"))
    with loaded.connection() as conn:
        result = retrieve(
            conn,
            scope,
            "invigilation remuneration per session",
            FakeEmbedder(),
            None,
            RetrievalConfig(today=DATA_TODAY),
        )
    assert "coe-invigilation-duty-2026" in result.stages["final"]


def test_newest_version_wins(loaded):
    scope = build_scope(Claims("S1001", "student", "coe"))
    with loaded.connection() as conn:
        result = retrieve(
            conn,
            scope,
            "COE semester 3 fee last date",
            FakeEmbedder(),
            None,
            RetrievalConfig(today=DATA_TODAY),
        )
    assert "coe-fee-structure-2026-v2" in result.stages["final"]
    assert "coe-fee-structure-2026-v1" not in result.stages["candidates"]


def test_expiry_depends_on_the_date(loaded):
    scope = build_scope(None)
    query = "Diwali vacation dates"
    with loaded.connection() as conn:
        before = retrieve(conn, scope, query, FakeEmbedder(), None, RetrievalConfig(today=DATA_TODAY))
        from datetime import date

        after = retrieve(conn, scope, query, FakeEmbedder(), None, RetrievalConfig(today=date(2026, 11, 5)))
    assert "all-diwali-vacation-2026" in before.stages["candidates"]
    assert "all-diwali-vacation-2026" not in after.stages["candidates"]
