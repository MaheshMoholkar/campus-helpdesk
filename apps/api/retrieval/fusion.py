"""Reciprocal Rank Fusion: merge ranked lists using positions only.

A chunk's fused score is the sum of 1 / (K + rank) over the lists it appears in.
Vector similarity and text rank are on different scales, so their raw scores
cannot be added; ranks can. K = 60 is the value from the original RRF paper.
"""

from dataclasses import replace

from apps.api.retrieval.search import Candidate

RRF_K = 60


def reciprocal_rank_fusion(*ranked_lists: list[Candidate]) -> list[Candidate]:
    fused: dict[str, float] = {}
    first_seen: dict[str, Candidate] = {}
    for ranked in ranked_lists:
        for rank, candidate in enumerate(ranked, start=1):
            fused[candidate.chunk_id] = fused.get(candidate.chunk_id, 0.0) + 1.0 / (RRF_K + rank)
            first_seen.setdefault(candidate.chunk_id, candidate)
    ordered = sorted(fused, key=lambda chunk_id: (-fused[chunk_id], chunk_id))
    return [replace(first_seen[chunk_id], score=fused[chunk_id]) for chunk_id in ordered]
