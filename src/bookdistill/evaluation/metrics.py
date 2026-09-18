"""Dependency-free information-retrieval metrics."""

from __future__ import annotations

import math
from typing import Sequence
from uuid import UUID


DEFAULT_CUTOFFS = (1, 3, 5, 10)


def recall_at_k(ranked: Sequence[UUID], relevant: set[UUID], k: int) -> float:
    return len(set(ranked[:k]) & relevant) / len(relevant) if relevant else 0.0


def precision_at_k(ranked: Sequence[UUID], relevant: set[UUID], k: int) -> float:
    return len(set(ranked[:k]) & relevant) / k


def reciprocal_rank(ranked: Sequence[UUID], relevant: set[UUID]) -> float:
    for rank, chunk_id in enumerate(ranked, start=1):
        if chunk_id in relevant:
            return 1.0 / rank
    return 0.0


def first_relevant_rank(ranked: Sequence[UUID], relevant: set[UUID]) -> int | None:
    for rank, chunk_id in enumerate(ranked, start=1):
        if chunk_id in relevant:
            return rank
    return None


def ndcg_at_k(ranked: Sequence[UUID], relevant: set[UUID], k: int) -> float:
    gains = [1.0 if chunk_id in relevant else 0.0 for chunk_id in ranked[:k]]
    dcg = sum(gain / math.log2(rank + 1) for rank, gain in enumerate(gains, start=1))
    ideal_count = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    return dcg / idcg if idcg else 0.0


def percentile(values: Sequence[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile_value
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
