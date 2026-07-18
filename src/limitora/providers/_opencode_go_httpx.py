"""Private, bounded HTTPX transport for the OpenCode Go dashboard."""

from __future__ import annotations

from datetime import timedelta
import time
from typing import Callable
from urllib.parse import quote

from .ports import HttpRequest, HttpResponse, PortFailure, PortFailureKind


class _HttpxOpenCodeGoTransport:
    BODY_LIMIT = 512 * 1024
    BUDGET = 10.0

    def __init__(self, config, *, monotonic: Callable[[], float] = time.monotonic,
                 client_factory=None, httpx_module=None) -> None:
        self._config = config
        self._monotonic = monotonic
        self._client_factory = client_factory
        self._httpx_module = httpx_module

    def _request(self) -> HttpRequest:
        url = f"{self._config.endpoint}/workspace/{quote(self._config.workspace_id, safe='')}/go"
        return HttpRequest("GET", url, (("Cookie", f"auth={self._config.auth_cookie}"),), None, self._config.timeout)

    @staticmethod
    def _body_failure(size: int) -> PortFailure | None:
        if size >= _HttpxOpenCodeGoTransport.BODY_LIMIT:
            return PortFailure(PortFailureKind.INVALID, "response body exceeds the configured limit")
        return None

    def _remaining(self, deadline: float) -> float:
        return deadline - self._monotonic()

    def fetch(self) -> HttpResponse | PortFailure:
        if not self._valid_config():
            return PortFailure(PortFailureKind.INVALID, "OpenCode Go configuration is invalid")
        deadline = self._monotonic() + self.BUDGET
        if self._remaining(deadline) <= 0:
            return PortFailure(PortFailureKind.TIMEOUT, "OpenCode Go request budget expired")
        request = self._request()
        remaining = self._remaining(deadline)
        try:
            httpx = self._httpx_module
            if httpx is None:
                import httpx
            timeout = httpx.Timeout(remaining, connect=remaining, read=remaining, write=remaining, pool=remaining)
            factory = self._client_factory or httpx.Client
            with factory(follow_redirects=False, trust_env=False, timeout=timeout) as client:
                with client.stream(request.method, request.url, headers=dict(request.headers), content=None) as response:
                    declared = response.headers.get("content-length")
                    if declared is not None and declared.isdigit():
                        failure = self._body_failure(int(declared))
                        if failure is not None:
                            return failure
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        failure = self._body_failure(size)
                        if failure is not None:
                            return failure
                        chunks.append(chunk)
                        if self._remaining(deadline) <= 0:
                            return PortFailure(PortFailureKind.TIMEOUT, "OpenCode Go request budget expired")
                    if self._remaining(deadline) <= 0:
                        return PortFailure(PortFailureKind.TIMEOUT, "OpenCode Go request budget expired")
                    return HttpResponse(response.status_code, b"".join(chunks))
        except ImportError:
            return PortFailure(PortFailureKind.UNAVAILABLE, "OpenCode Go HTTP transport is unavailable")
        except Exception as error:
            httpx = self._httpx_module
            if httpx is None:
                try:
                    import httpx
                except ImportError:
                    return PortFailure(PortFailureKind.UNAVAILABLE, "OpenCode Go HTTP transport is unavailable")
            if isinstance(error, httpx.TimeoutException):
                return PortFailure(PortFailureKind.TIMEOUT, "OpenCode Go request timed out")
            if isinstance(error, httpx.HTTPError):
                return PortFailure(PortFailureKind.UNAVAILABLE, "OpenCode Go source is unavailable")
            return PortFailure(PortFailureKind.FAILED, "OpenCode Go request failed")

    def _valid_config(self) -> bool:
        config = self._config
        return (
            isinstance(config.workspace_id, str) and bool(config.workspace_id.strip()) and config.workspace_id == config.workspace_id.strip()
            and isinstance(config.auth_cookie, str) and bool(config.auth_cookie)
            and config.endpoint == "https://opencode.ai"
            and isinstance(config.timeout, timedelta) and timedelta(0) < config.timeout <= timedelta(seconds=self.BUDGET)
        )
