"""Opt-in, in-memory reuse of successful provider snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from threading import RLock

from limitora.models import MetricKind, ProviderId, ProviderSnapshot

from .contract import AuthorizationPolicy, ProviderError, ProviderReader, ProviderRequest
from .ports import Clock


@dataclass(frozen=True)
class ProviderCachePolicy:
    reuse_ttl: timedelta
    maximum_stale_age: timedelta

    def __post_init__(self) -> None:
        if self.reuse_ttl < timedelta() or self.maximum_stale_age < timedelta() or self.maximum_stale_age < self.reuse_ttl:
            raise ValueError("cache policy durations must be non-negative and ordered")


@dataclass(frozen=True)
class _CacheKey:
    provider_id: ProviderId
    requested_metrics: frozenset[MetricKind]
    authorization_policy: AuthorizationPolicy


class CachedProviderReader:
    def __init__(self, reader: ProviderReader, policy: ProviderCachePolicy, clock: Clock) -> None:
        if not isinstance(reader, ProviderReader) or type(policy) is not ProviderCachePolicy or not callable(getattr(clock, "now", None)):
            raise ValueError("cache reader, policy, and clock must be valid")
        self._reader, self._policy, self._clock = reader, policy, clock
        self._entries: dict[_CacheKey, ProviderSnapshot] = {}
        self._key_generations: dict[_CacheKey, int] = {}
        self._generation, self._lock = 0, RLock()

    @property
    def provider_id(self) -> ProviderId:
        return self._reader.provider_id

    def detect(self):
        return self._reader.detect()

    def fetch(self, request: ProviderRequest) -> ProviderSnapshot:
        key, now = _CacheKey(self.provider_id, request.requested_metrics, request.authorization_policy), self._clock.now()
        with self._lock:
            prior = self._entries.get(key)
            if prior is not None and _age(now, prior) < self._policy.reuse_ttl:
                return prior
            generation, key_generation = self._generation, self._key_generations.get(key, 0)
        try:
            result = self._reader.fetch(request)
        except ProviderError:
            now = self._clock.now()
            with self._lock:
                if self._current_entry(key, generation, key_generation, prior) and prior is not None and _age(now, prior) <= self._policy.maximum_stale_age:
                    return prior
            raise
        with self._lock:
            if self._generations_match(key, generation, key_generation):
                self._entries[key] = result
        return result

    def invalidate(self, request: ProviderRequest | None = None) -> None:
        with self._lock:
            if request is None:
                self._entries.clear(); self._generation += 1
            else:
                key = _CacheKey(self.provider_id, request.requested_metrics, request.authorization_policy)
                self._entries.pop(key, None); self._key_generations[key] = self._key_generations.get(key, 0) + 1

    def _generations_match(self, key, generation, key_generation) -> bool:
        return self._generation == generation and self._key_generations.get(key, 0) == key_generation

    def _current_entry(self, key, generation, key_generation, prior) -> bool:
        return self._generations_match(key, generation, key_generation) and self._entries.get(key) is prior


def _age(now, snapshot):
    age = now - snapshot.fetched_at
    return age if age >= timedelta() else timedelta.max
