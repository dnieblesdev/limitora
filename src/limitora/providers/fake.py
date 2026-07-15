"""Deterministic provider implementation for offline contract tests."""

from __future__ import annotations

from limitora.models import ProviderId, ProviderSnapshot

from .contract import ProviderDetection, ProviderError, ProviderReader, ProviderRequest
from .ports import Clock


class FakeProvider(ProviderReader):
    """Returns one configured outcome and observes detection time through a clock port."""

    def __init__(
        self,
        provider_id: ProviderId,
        clock: Clock,
        *,
        detected: bool,
        outcome: ProviderSnapshot | ProviderError,
        detection_message: str | None = None,
    ) -> None:
        if isinstance(outcome, ProviderSnapshot) and outcome.provider_id != provider_id:
            raise ValueError("fake snapshot provider must match the fake provider")
        if isinstance(outcome, ProviderError) and outcome.provider_id != provider_id:
            raise ValueError("fake error provider must match the fake provider")
        self._provider_id = provider_id
        self._clock = clock
        self._detected = detected
        self._outcome = outcome
        self._detection_message = detection_message

    @property
    def provider_id(self) -> ProviderId:
        return self._provider_id

    def detect(self) -> ProviderDetection:
        return ProviderDetection(
            provider_id=self.provider_id,
            detected=self._detected,
            checked_at=self._clock.now(),
            safe_message=self._detection_message,
        )

    def fetch(self, request: ProviderRequest) -> ProviderSnapshot:
        del request
        if isinstance(self._outcome, ProviderError):
            raise self._outcome
        return self._outcome
