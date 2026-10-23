"""Small, dependency-free metric helpers."""
from collections.abc import Sequence


def first_relevant_rank(ranked_ids: Sequence[str], relevant: set[str]) -> int | None:
    """1-based rank of the first relevant id, or None if absent."""
    for i, item in enumerate(ranked_ids, start=1):
        if item in relevant:
            return i
    return None


def hit_at_k(rank: int | None, k: int) -> float:
    return 1.0 if rank is not None and rank <= k else 0.0


def reciprocal_rank(rank: int | None) -> float:
    return 1.0 / rank if rank else 0.0


def mean(values: Sequence[float]) -> float | None:
    """Average, or None when there is nothing to average (reported as n/a, not as 0)."""
    return round(sum(values) / len(values), 4) if values else None
