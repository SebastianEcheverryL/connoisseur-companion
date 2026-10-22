"""Weighted multimodal score fusion (ported from notebook 05), as pure functions."""
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class Hit:
    """One vector-search result. `key` identifies the entity (e.g. a recipe id) across modalities."""

    key: str
    similarity: float
    document: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class FusedHit:
    key: str
    fused: float
    text_score: float = 0.0
    img_score: float = 0.0
    document: str = ""
    metadata: dict = field(default_factory=dict)


def to_similarity(distances: Sequence[float]) -> np.ndarray:
    """Convert cosine distance ('smaller is better') into similarity ('larger is better')."""
    return 1.0 - np.asarray(distances, dtype=np.float32)


def minmax(values: Sequence[float]) -> np.ndarray:
    """Min-max normalise to [0, 1]; a constant array maps to all ones (same confidence)."""
    x = np.asarray(values, dtype=np.float32)
    if x.size == 0:
        return x
    lo, hi = float(x.min()), float(x.max())
    if abs(hi - lo) < 1e-8:
        return np.ones_like(x)
    return (x - lo) / (hi - lo)


def fuse(
    text_hits: Sequence[Hit],
    img_hits: Sequence[Hit],
    w_text: float = 0.6,
    w_img: float = 0.4,
    top_n: int | None = 5,
) -> list[FusedHit]:
    """Normalise scores within each modality, then combine them per entity key.

    Unlike the notebook version (which ranked articles and images as separate rows), hits that
    share a key are merged, so a recipe that matches both by text and by image ranks higher.
    """
    rows: dict[str, FusedHit] = {}
    for hits, weight, attr in ((text_hits, w_text, "text_score"), (img_hits, w_img, "img_score")):
        for hit, norm in zip(hits, minmax([h.similarity for h in hits]), strict=True):
            row = rows.setdefault(hit.key, FusedHit(key=hit.key, fused=0.0))
            setattr(row, attr, max(getattr(row, attr), float(norm)))
            row.document = row.document or hit.document
            row.metadata = row.metadata or dict(hit.metadata)
        for row in rows.values():
            row.fused = w_text * row.text_score + w_img * row.img_score

    ranked = sorted(rows.values(), key=lambda r: r.fused, reverse=True)
    return ranked if top_n is None else ranked[: max(0, top_n)]
