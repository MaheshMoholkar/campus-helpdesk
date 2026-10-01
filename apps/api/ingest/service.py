"""Ingest flow (docs/spec.md section 10.3): validate, chunk, embed, store.

Storing happens in one transaction, including the hand-over of "current" from
one version of a series to the next.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Literal

import numpy as np
import psycopg
from pydantic import BaseModel, Field

from apps.api.ingest.chunker import chunk_document
from apps.api.llm.base import Embedder


class DocumentIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str = Field(min_length=1)
    doc_type: Literal["circular", "policy", "placement_notice", "faq"]
    college_code: str
    audience: Literal["public", "student", "staff"]
    category: Literal["exam", "fee", "hostel", "admission", "placement", "general"]
    language: Literal["en", "hi", "mr"] = "en"
    issue_date: date
    expires_on: date | None = None
    series_key: str | None = None
    version: int | None = Field(default=None, ge=1)
    reference_no: str | None = None
    body: str = Field(min_length=1)


@dataclass
class IngestResult:
    document_id: str
    slug: str
    version: int
    chunks: int
    status: Literal["created", "replaced", "unchanged"]


class UnknownCollege(ValueError):
    pass


def _content_hash(doc: DocumentIn, embedder: Embedder) -> str:
    # The embedding model is part of the hash, so switching models re-embeds everything.
    payload = {**doc.model_dump(mode="json"), "_embedder": getattr(embedder, "model_id", "unknown")}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def ingest_document(conn: psycopg.Connection, embedder: Embedder, doc: DocumentIn) -> IngestResult:
    content_hash = _content_hash(doc, embedder)

    existing = conn.execute(
        "SELECT id, version, content_hash FROM documents WHERE slug = %s", (doc.slug,)
    ).fetchone()
    if existing and existing["content_hash"] == content_hash:
        count = conn.execute(
            "SELECT count(*) AS n FROM chunks WHERE document_id = %s", (existing["id"],)
        ).fetchone()["n"]
        return IngestResult(str(existing["id"]), doc.slug, existing["version"], count, "unchanged")

    if not conn.execute("SELECT 1 FROM colleges WHERE code = %s", (doc.college_code,)).fetchone():
        raise UnknownCollege(doc.college_code)

    chunks = chunk_document(doc.body)
    # Title and heading are embedded with the text so a chunk that only says
    # "the last date is 15 November" still matches a question about fees.
    embeddings = embedder.embed(
        ["\n".join(filter(None, [doc.title, chunk.heading, chunk.text])) for chunk in chunks]
    )
    conn.rollback()  # the embedding call can be slow; do not hold a transaction open across it

    with conn.transaction():
        if doc.series_key:
            # Serialise ingests into the same series.
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"series:{doc.series_key}",))

        version = doc.version
        if version is None:
            if existing:
                version = existing["version"]
            elif doc.series_key:
                version = conn.execute(
                    "SELECT coalesce(max(version), 0) + 1 AS v FROM documents WHERE series_key = %s",
                    (doc.series_key,),
                ).fetchone()["v"]
            else:
                version = 1

        if doc.series_key:
            # Nobody in the series is current while we work; the newest is promoted below.
            conn.execute(
                "UPDATE chunks SET is_current = false WHERE document_id IN"
                " (SELECT id FROM documents WHERE series_key = %s)",
                (doc.series_key,),
            )
            conn.execute("UPDATE documents SET is_current = false WHERE series_key = %s", (doc.series_key,))

        is_current = doc.series_key is None
        fields = {
            "slug": doc.slug,
            "title": doc.title,
            "doc_type": doc.doc_type,
            "college_code": doc.college_code,
            "audience": doc.audience,
            "category": doc.category,
            "language": doc.language,
            "issue_date": doc.issue_date,
            "expires_on": doc.expires_on,
            "series_key": doc.series_key,
            "version": version,
            "is_current": is_current,
            "reference_no": doc.reference_no,
            "content_hash": content_hash,
            "body": doc.body,
        }
        if existing:
            document_id = existing["id"]
            assignments = ", ".join(f"{name} = %({name})s" for name in fields)
            conn.execute(
                f"UPDATE documents SET {assignments} WHERE id = %(id)s", {**fields, "id": document_id}
            )
            conn.execute("DELETE FROM chunks WHERE document_id = %s", (document_id,))
        else:
            columns = ", ".join(fields)
            placeholders = ", ".join(f"%({name})s" for name in fields)
            document_id = conn.execute(
                f"INSERT INTO documents ({columns}) VALUES ({placeholders}) RETURNING id", fields
            ).fetchone()["id"]

        with conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO chunks (document_id, chunk_index, heading, text, embedding, tsv,
                                    college_code, audience, category, language,
                                    issue_date, expires_on, is_current)
                VALUES (%(document_id)s, %(chunk_index)s, %(heading)s, %(text)s, %(embedding)s,
                        to_tsvector(%(ts_config)s::regconfig, %(searchable)s),
                        %(college_code)s, %(audience)s, %(category)s, %(language)s,
                        %(issue_date)s, %(expires_on)s, %(is_current)s)
                """,
                [
                    {
                        "document_id": document_id,
                        "chunk_index": index,
                        "heading": chunk.heading,
                        "text": chunk.text,
                        "embedding": np.asarray(embedding, dtype=np.float32),
                        # English gets stemming ("fees" matches "fee"); other languages are
                        # matched as written.
                        "ts_config": "english" if doc.language == "en" else "simple",
                        "searchable": "\n".join(filter(None, [doc.title, chunk.heading, chunk.text])),
                        "college_code": doc.college_code,
                        "audience": doc.audience,
                        "category": doc.category,
                        "language": doc.language,
                        "issue_date": doc.issue_date,
                        "expires_on": doc.expires_on,
                        "is_current": is_current,
                    }
                    for index, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True))
                ],
            )

        if doc.series_key:
            # Newest version wins, whatever order the versions were ingested in.
            newest = conn.execute(
                "SELECT id FROM documents WHERE series_key = %s ORDER BY version DESC LIMIT 1",
                (doc.series_key,),
            ).fetchone()["id"]
            conn.execute("UPDATE documents SET is_current = true WHERE id = %s", (newest,))
            conn.execute("UPDATE chunks SET is_current = true WHERE document_id = %s", (newest,))

    return IngestResult(
        str(document_id), doc.slug, version, len(chunks), "replaced" if existing else "created"
    )
