"""Application coordination over the provider reader contract."""

from __future__ import annotations

from limitora.models import ProviderSnapshot
from limitora.providers import ProviderDetection, ProviderReader, ProviderRequest


class StatusService:
    """Coordinates detection before reading from one composition-selected provider."""

    def __init__(self, provider: ProviderReader) -> None:
        self._provider = provider

    def read_status(self, request: ProviderRequest) -> ProviderDetection | ProviderSnapshot:
        """Return an undetected source or its unmodified provider snapshot.

        ProviderError intentionally propagates unchanged from the provider boundary.
        """
        detection = self._provider.detect()
        if not detection.detected:
            return detection
        return self._provider.fetch(request)
