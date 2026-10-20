"""Per-user message quotas: a daily cap plus a per-minute rate limit ("denial of wallet" protection).

Locally counters live in memory (or the disk cache); on AWS the same interface will be backed by
DynamoDB atomic counters with a TTL, so limits hold across Lambda instances.
"""
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock
from typing import Protocol

from connoisseur.settings import get_settings


@dataclass
class QuotaDecision:
    allowed: bool
    reason: str | None = None  # "rate_limit" or "daily_limit"
    retry_after_s: int = 0
    remaining_today: int = 0


class CounterStore(Protocol):
    def incr(self, key: str, ttl_s: int) -> int:
        """Atomically increment `key` (created with a TTL) and return the new value."""


class MemoryCounterStore:
    def __init__(self):
        self._data: dict[str, tuple[int, float]] = {}
        self._lock = Lock()

    def incr(self, key: str, ttl_s: int) -> int:
        now = time.time()
        with self._lock:
            value, expires = self._data.get(key, (0, now + ttl_s))
            if expires <= now:
                value, expires = 0, now + ttl_s
            self._data[key] = (value + 1, expires)
            if len(self._data) > 50_000:  # drop expired keys now and then
                self._data = {k: v for k, v in self._data.items() if v[1] > now}
            return value + 1


class QuotaService:
    def __init__(self, store: CounterStore, clock: Callable[[], float] = time.time):
        self.store = store
        self.clock = clock

    def consume(self, user_key: str) -> QuotaDecision:
        s = get_settings()
        now = self.clock()
        minute, day = int(now // 60), time.strftime("%Y-%m-%d", time.gmtime(now))

        per_minute = self.store.incr(f"rpm:{user_key}:{minute}", ttl_s=120)
        if per_minute > s.per_minute_message_limit:
            return QuotaDecision(False, "rate_limit", retry_after_s=60 - int(now % 60))

        today = self.store.incr(f"day:{user_key}:{day}", ttl_s=26 * 3600)
        if today > s.daily_message_limit:
            seconds_to_midnight = 86400 - int(now % 86400)
            return QuotaDecision(False, "daily_limit", retry_after_s=seconds_to_midnight)
        return QuotaDecision(True, remaining_today=s.daily_message_limit - today)


@lru_cache
def get_quota_service() -> QuotaService:
    return QuotaService(MemoryCounterStore())
