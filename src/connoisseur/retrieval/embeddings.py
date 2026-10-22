"""Text and image embeddings with ONNX models (fastembed): same vectors as notebooks 03-05, no torch.

- Text: all-MiniLM-L6-v2 (384-d)
- Images and image-search queries: CLIP ViT-B/32 (512-d)
"""
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

import numpy as np

from connoisseur.settings import get_settings


def _cache_dir() -> str | None:
    path = get_settings().embedding_cache_dir
    return str(path) if path else None


@lru_cache
def _text_model():
    from fastembed import TextEmbedding

    return TextEmbedding(get_settings().text_embedding_model, cache_dir=_cache_dir())


@lru_cache
def _clip_text_model():
    from fastembed import TextEmbedding

    return TextEmbedding(get_settings().clip_text_model, cache_dir=_cache_dir())


@lru_cache
def _clip_image_model():
    from fastembed import ImageEmbedding

    return ImageEmbedding(get_settings().clip_image_model, cache_dir=_cache_dir())


def _normalise(vectors) -> np.ndarray:
    arr = np.asarray(list(vectors), dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    return arr / np.clip(norms, 1e-12, None)


def embed_texts(texts: Sequence[str], batch_size: int = 64) -> np.ndarray:
    return _normalise(_text_model().embed(list(texts), batch_size=batch_size))


def embed_query_clip_text(query: str) -> np.ndarray:
    """Embed a text query in CLIP space, to search the image collection."""
    return _normalise(_clip_text_model().embed([query]))[0]


def embed_images(paths: Sequence[str | Path], batch_size: int = 16) -> np.ndarray:
    return _normalise(_clip_image_model().embed([str(p) for p in paths], batch_size=batch_size))
