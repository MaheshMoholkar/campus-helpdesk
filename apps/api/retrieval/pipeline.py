"""Retrieval pipeline: search, merge, rerank, keep the top few (docs/spec.md section 10.2)."""

import time
from dataclasses import dataclass, field, replace
from datetime import date

import psycopg

from apps.api.llm.base import Embedder, Reranker
from apps.api.retrieval.fusion import reciprocal_rank_fusion
from apps.api.retrieval.search import Candidate, keyword_search, vector_search
from apps.api.scope import Scope


@dataclass
class RetrievalConfig:
    mode: str = "hybrid"  # "vector" | "hybrid"
    candidates_k: int = 30
    final_k: int = 5
    today: date | None = None  # fixed in tests and evals so expiry checks do not drift


@dataclass
class RetrievalResult:
    candidates: list[Candidate]  # merged list before reranking
    final: list[Candidate]  # what is sent to the model
    top_score: float | None  # compared with the abstain threshold
    retrieval_ms: int = 0
    rerank_ms: int = 0
    stages: dict[str, list[str]] = field(default_factory=dict)  # stage -> slugs, for evals


def retrieve(
    conn: psycopg.Connection,
    scope: Scope,
    question: str,
    embedder: Embedder,
    reranker: Reranker | None,
    config: RetrievalConfig,
) -> RetrievalResult:
    started = time.perf_counter()
    query_embedding = embedder.embed([question])[0]
    by_vector = vector_search(conn, scope, query_embedding, config.candidates_k, config.today)

    if config.mode == "hybrid":
        by_keyword = keyword_search(conn, scope, question, config.candidates_k, config.today)
        candidates = reciprocal_rank_fusion(by_vector, by_keyword)
    else:
        by_keyword = []
        candidates = by_vector
    retrieval_ms = int((time.perf_counter() - started) * 1000)

    # The abstain check needs a score that means "how well does the best chunk
    # match". A fused rank does not mean that, so without a reranker the best
    # cosine similarity is used.
    top_score = max((c.score for c in by_vector), default=None)

    rerank_ms = 0
    ranked = candidates
    if reranker is not None and candidates:
        started = time.perf_counter()
        scores = reranker.rerank(question, [c.text for c in candidates])
        ranked = sorted(
            (replace(c, score=s) for c, s in zip(candidates, scores, strict=True)),
            key=lambda c: (-c.score, c.chunk_id),
        )
        top_score = ranked[0].score
        rerank_ms = int((time.perf_counter() - started) * 1000)

    final = ranked[: config.final_k]
    return RetrievalResult(
        candidates=candidates,
        final=final,
        top_score=top_score,
        retrieval_ms=retrieval_ms,
        rerank_ms=rerank_ms,
        stages={
            "vector": [c.slug for c in by_vector],
            "keyword": [c.slug for c in by_keyword],
            "candidates": [c.slug for c in candidates],
            "final": [c.slug for c in final],
        },
    )
