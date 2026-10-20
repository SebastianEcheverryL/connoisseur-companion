"""TTL cache for web lookups: saves Tavily quota and respects OpenStreetMap's usage policies.

Local runs use a disk cache; on AWS this interface will be backed by DynamoDB with a TTL attribute.
"""
import hashlib
import json
from functools import lru_cache
from typing import Any, Protocol

from connoisseur.settings import get_settings


class Cache(Protocol):
    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl_s: int) -> None: ...


class DiskCache:
    def __init__(self, directory: str):
        import diskcache

        self._cache = diskcache.Cache(directory)

    def get(self, key: str) -> Any | None:
        return self._cache.get(key)

    def set(self, key: str, value: Any, ttl_s: int) -> None:
        self._cache.set(key, value, expire=ttl_s)


class NullCache:
    def get(self, key: str) -> Any | None:
        return None

    def set(self, key: str, value: Any, ttl_s: int) -> None:
        return None


def make_key(namespace: str, payload: dict) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:32]
    return f"{namespace}:{digest}"


@lru_cache
def get_cache() -> Cache:
    s = get_settings()
    if s.environment == "test":
        return NullCache()
    return DiskCache(str(s.cache_dir))
