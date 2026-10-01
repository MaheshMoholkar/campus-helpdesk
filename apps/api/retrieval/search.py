"""The two searches over the chunks table. Both require a Scope and apply it in
the same SQL statement as the search, so a chunk outside the user's scope is
never read into the application at all (docs/spec.md section 10.4).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import psycopg

from apps.api.scope import Scope, scope_filter


@dataclass
class Candidate:
    chunk_id: str
    document_id: str
    slug: str
    title: str
    heading: str | None
    text: str
    issue_date: date
    category: str
    score: float  # meaning depends on the stage: similarity, text rank, fused rank or rerank score


_COLUMNS = """
    c.id AS chunk_id, c.document_id, d.slug, d.title, c.heading, c.text, c.issue_date, c.category
"""


def _to_candidates(rows: list[dict]) -> list[Candidate]:
    return [
        Candidate(
            chunk_id=str(row["chunk_id"]),
            document_id=str(row["document_id"]),
            slug=row["slug"],
            title=row["title"],
            heading=row["heading"],
            text=row["text"],
            issue_date=row["issue_date"],
            category=row["category"],
            score=float(row["score"]),
        )
        for row in rows
    ]


def vector_search(
    conn: psycopg.Connection,
    scope: Scope,
    query_embedding: Sequence[float],
    k: int,
    today: date | None = None,
) -> list[Candidate]:
    """Nearest chunks by cosine similarity. `<=>` is pgvector's cosine distance."""
    where, params = scope_filter(scope, alias="c", today=today)
    rows = conn.execute(
        f"""
        SELECT {_COLUMNS}, 1 - (c.embedding <=> %(query)s) AS score
        FROM chunks c JOIN documents d ON d.id = c.document_id
        WHERE {where}
        ORDER BY c.embedding <=> %(query)s
        LIMIT %(k)s
        """,
        {**params, "query": np.asarray(query_embedding, dtype=np.float32), "k": k},
    ).fetchall()
    return _to_candidates(rows)


def keyword_search(
    conn: psycopg.Connection, scope: Scope, question: str, k: int, today: date | None = None
) -> list[Candidate]:
    """Postgres full-text search.

    plainto_tsquery joins the words with AND, which is too strict for a whole
    question, so the ANDs are turned into ORs: any word may match, and ts_rank
    puts chunks matching more of them first. The question is parsed twice, with
    English stemming and without, to match both kinds of stored text.
    """
    where, params = scope_filter(scope, alias="c", today=today)
    rows = conn.execute(
        f"""
        WITH q AS (
            SELECT replace(plainto_tsquery('english', %(question)s)::text, '&', '|')::tsquery
                || replace(plainto_tsquery('simple', %(question)s)::text, '&', '|')::tsquery AS query
        )
        SELECT {_COLUMNS}, ts_rank(c.tsv, q.query) AS score
        FROM chunks c JOIN documents d ON d.id = c.document_id, q
        WHERE {where} AND c.tsv @@ q.query
        ORDER BY score DESC, c.id
        LIMIT %(k)s
        """,
        {**params, "question": question, "k": k},
    ).fetchall()
    return _to_candidates(rows)
