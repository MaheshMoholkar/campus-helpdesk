"""Ingest: idempotency and series handling."""

from datetime import date

import psycopg
import pytest

from apps.api.ingest import DocumentIn, UnknownCollege, ingest_document
from apps.api.llm.fake import FakeEmbedder

pytestmark = pytest.mark.db


def _doc(slug, version=None, series="test-series", body="Some test text."):
    return DocumentIn(
        slug=slug,
        title=f"Test {slug}",
        doc_type="circular",
        college_code="all",
        audience="public",
        category="general",
        issue_date=date(2026, 9, 1),
        series_key=series,
        version=version,
        body=body,
    )


def _current(conn, series):
    return [
        r["slug"]
        for r in conn.execute("SELECT slug FROM documents WHERE series_key = %s AND is_current", (series,))
    ]


def test_reingest_is_unchanged(loaded):
    with loaded.connection() as conn:
        first = ingest_document(conn, FakeEmbedder(), _doc("t-idem", series=None))
        second = ingest_document(conn, FakeEmbedder(), _doc("t-idem", series=None))
        assert (first.status, second.status) == ("created", "unchanged")
        changed = ingest_document(conn, FakeEmbedder(), _doc("t-idem", series=None, body="New text."))
        assert changed.status == "replaced"
        count = conn.execute(
            "SELECT count(*) AS n FROM chunks c JOIN documents d ON d.id = c.document_id WHERE d.slug = 't-idem'"
        ).fetchone()["n"]
        assert count == 1


def test_newest_version_is_current_whatever_the_ingest_order(loaded):
    with loaded.connection() as conn:
        ingest_document(conn, FakeEmbedder(), _doc("t-s-v2", version=2, series="order"))
        ingest_document(conn, FakeEmbedder(), _doc("t-s-v1", version=1, series="order"))
        assert _current(conn, "order") == ["t-s-v2"]
        ingest_document(conn, FakeEmbedder(), _doc("t-s-v3", series="order"))  # version assigned: 3
        assert _current(conn, "order") == ["t-s-v3"]
        live_chunks = conn.execute(
            "SELECT d.slug FROM chunks c JOIN documents d ON d.id = c.document_id"
            " WHERE d.series_key = 'order' AND c.is_current"
        ).fetchall()
        assert {r["slug"] for r in live_chunks} == {"t-s-v3"}


def test_database_refuses_two_current_versions(loaded):
    with loaded.connection() as conn:
        ingest_document(conn, FakeEmbedder(), _doc("t-db-v1", version=1, series="guard"))
        ingest_document(conn, FakeEmbedder(), _doc("t-db-v2", version=2, series="guard"))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("UPDATE documents SET is_current = true WHERE slug = 't-db-v1'")
        conn.rollback()


def test_unknown_college_is_rejected(loaded):
    doc = _doc("t-bad", series=None).model_copy(update={"college_code": "xyz"})
    with loaded.connection() as conn, pytest.raises(UnknownCollege):
        ingest_document(conn, FakeEmbedder(), doc)
