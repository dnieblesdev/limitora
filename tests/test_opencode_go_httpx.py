import unittest
from datetime import timedelta

from limitora.providers._opencode_go import OpenCodeGoConfig
from limitora.providers._opencode_go_httpx import _HttpxOpenCodeGoTransport
from limitora.providers.ports import PortFailure, PortFailureKind

class FakeTimeout(Exception):
    pass
class FakeHTTPError(Exception):
    pass


class FakeTimeoutConfig:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
class FakeHTTPX:
    TimeoutException = FakeTimeout
    HTTPError = FakeHTTPError
    Timeout = FakeTimeoutConfig
class FakeResponse:
    def __init__(self, clock, status_code=200, chunks=(), headers=None, complete_at=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._clock = clock
        self._chunks = chunks
        self._complete_at = complete_at
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False
    def iter_bytes(self):
        yield from self._chunks
        if self._complete_at is not None:
            self._clock.value = self._complete_at
class FakeStreamClient:
    def __init__(self, response):
        self.response = response
        self.calls = []
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def stream(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


class MutableClock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value


class OpenCodeGoHttpxTests(unittest.TestCase):
    def config(self, **changes):
        values = dict(workspace_id="space/id", auth_cookie="opaque", endpoint="https://opencode.ai", timeout=timedelta(seconds=10))
        values.update(changes)
        return OpenCodeGoConfig(**values)

    def test_builds_the_exact_private_request_without_leaking_cookie(self):
        transport = _HttpxOpenCodeGoTransport(self.config())
        request = transport._request()

        self.assertEqual("https://opencode.ai/workspace/space%2Fid/go", request.url)
        self.assertEqual(("Cookie", "auth=opaque"), request.headers[0])
        self.assertIsNone(request.body)

    def test_invalid_config_and_expired_budget_short_circuit(self):
        invalid = _HttpxOpenCodeGoTransport(self.config(endpoint="https://evil.example"))
        self.assertEqual(PortFailureKind.INVALID, invalid.fetch().kind)

        clock = iter((10.0, 20.0))
        expired = _HttpxOpenCodeGoTransport(self.config(), monotonic=lambda: next(clock), httpx_module=FakeHTTPX)
        self.assertEqual(PortFailureKind.TIMEOUT, expired.fetch().kind)

    def test_declared_and_streamed_body_caps_are_exclusive(self):
        self.assertEqual(512 * 1024, _HttpxOpenCodeGoTransport.BODY_LIMIT)
        self.assertEqual(PortFailureKind.INVALID, _HttpxOpenCodeGoTransport._body_failure(512 * 1024).kind)
        self.assertIsNone(_HttpxOpenCodeGoTransport._body_failure(512 * 1024 - 1))

    def test_transport_never_returns_httpx_exception_or_secret(self):
        transport = _HttpxOpenCodeGoTransport(self.config())
        failing = _HttpxOpenCodeGoTransport(
            self.config(),
            client_factory=lambda **_: (_ for _ in ()).throw(RuntimeError("Cookie: auth=opaque")),
        )
        result = failing.fetch()
        self.assertIsInstance(result, PortFailure)
        self.assertNotIn("opaque", result.safe_message)

    def test_injected_httpx_module_translates_timeout_and_transport_failures(self):
        for error, expected in ((FakeTimeout("late"), PortFailureKind.TIMEOUT),
                                (FakeHTTPError("broken"), PortFailureKind.UNAVAILABLE),
                                (RuntimeError("broken"), PortFailureKind.FAILED)):
            with self.subTest(error=type(error).__name__):
                failing = _HttpxOpenCodeGoTransport(
                    self.config(),
                    client_factory=lambda **_: (_ for _ in ()).throw(error),
                    httpx_module=FakeHTTPX,
                )
                self.assertEqual(expected, failing.fetch().kind)

    def test_deadline_is_fresh_per_fetch_and_checked_after_empty_completion(self):
        clock = MutableClock()
        responses = [
            FakeResponse(clock, complete_at=11.0),
            FakeResponse(clock),
        ]
        clients = [FakeStreamClient(response) for response in responses]

        def factory(**kwargs):
            return clients.pop(0)

        transport = _HttpxOpenCodeGoTransport(
            self.config(), monotonic=clock, client_factory=factory, httpx_module=FakeHTTPX
        )
        self.assertEqual(PortFailureKind.TIMEOUT, transport.fetch().kind)
        clock.value = 20.0
        result = transport.fetch()
        self.assertEqual((200, b""), (result.status_code, result.body))

    def test_injected_httpx_module_observes_remaining_budget_and_rejects_body(self):
        clock = MutableClock()
        client = FakeStreamClient(FakeResponse(clock, chunks=(b"x",), headers={"content-length": str(512 * 1024)}))
        factory_options = []

        def factory(**kwargs):
            factory_options.append(kwargs)
            return client

        transport = _HttpxOpenCodeGoTransport(
            self.config(), monotonic=clock, client_factory=factory, httpx_module=FakeHTTPX
        )
        result = transport.fetch()
        self.assertEqual(PortFailureKind.INVALID, result.kind)
        timeout = factory_options[0]["timeout"]
        self.assertEqual((10.0, 10.0, 10.0, 10.0),
                         (timeout.kwargs["connect"], timeout.kwargs["read"],
                          timeout.kwargs["write"], timeout.kwargs["pool"]))


if __name__ == "__main__":
    unittest.main()
