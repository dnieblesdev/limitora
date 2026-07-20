"""Contract tests for the JSON v1 projection in limitora.output.

Mirrors the fixture builder pattern from test_cli.py: each scenario constructs
its own StatusSnapshotResult / StatusUndetectedResult / ProviderError and
asserts the deterministic, sanitized JSON envelope documented in the
output-contracts spec.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import unittest

from limitora import (
    Freshness,
    MetricKind,
    ProviderError,
    ProviderErrorKind,
    ProviderId,
    ProviderSnapshot,
    ProviderState,
    ProviderStatus,
    SourceMetadata,
    StatusSnapshotResult,
    StatusUndetectedResult,
)
from limitora.models import Quantity, QuotaWindow, UsageSnapshot, ValueAvailability, WindowKind
from limitora.output import render_json


UTC = timezone.utc
PROVIDER = ProviderId("fixture-provider")
TIME = datetime(2026, 7, 15, 12, tzinfo=UTC)
ONE_HOUR_EARLIER = TIME - timedelta(hours=1)


def snapshot(*, freshness=Freshness.FRESH, windows=(), usage=None):
    return StatusSnapshotResult(ProviderSnapshot(
        provider_id=PROVIDER,
        status=ProviderStatus(PROVIDER, ProviderState.AVAILABLE, TIME),
        fetched_at=TIME,
        data_at=ONE_HOUR_EARLIER,
        source=SourceMetadata("offline-fixture"),
        quota_windows=windows,
        usage=usage,
    ), freshness)


def _known_window():
    return QuotaWindow(
        kind=WindowKind.COMMERCIAL_QUOTA,
        scope="z",
        period="month",
        plan_id="pro",
        availability=ValueAvailability.KNOWN,
        source=SourceMetadata("quota"),
        limit=Quantity(Decimal("10"), MetricKind.COMMERCIAL_QUOTA, "requests"),
        used=Quantity(Decimal("4"), MetricKind.COMMERCIAL_QUOTA, "requests"),
        remaining=Quantity(Decimal("6"), MetricKind.COMMERCIAL_QUOTA, "requests"),
        reset_at=TIME,
    )


class JsonContractVersionTests(unittest.TestCase):
    """The version key is the first key in every payload and equals 1."""

    def test_fresh_snapshot_emits_version_one_as_first_key(self) -> None:
        rendered = render_json(snapshot())

        parsed = json.loads(rendered)
        self.assertEqual("version", next(iter(parsed.keys())))
        self.assertEqual(1, parsed["version"])

    def test_undetected_emits_version_one_as_first_key(self) -> None:
        rendered = render_json(StatusUndetectedResult())

        parsed = json.loads(rendered)
        self.assertEqual("version", next(iter(parsed.keys())))
        self.assertEqual(1, parsed["version"])

    def test_error_envelope_emits_version_one_as_first_key(self) -> None:
        error = ProviderError(
            ProviderErrorKind.TRANSPORT, PROVIDER, "safe failure", retryable=True
        )

        rendered = render_json(error)

        parsed = json.loads(rendered)
        self.assertEqual("version", next(iter(parsed.keys())))
        self.assertEqual(1, parsed["version"])


class FreshSnapshotProjectionTests(unittest.TestCase):
    """The fresh-snapshot scenario covers timestamp, Decimal, and sort-keys rules."""

    def test_fresh_snapshot_has_result_tag_and_typed_value_objects(self) -> None:
        rendered = render_json(snapshot(windows=(_known_window(),)))

        parsed = json.loads(rendered)
        self.assertEqual("snapshot", parsed["result"])
        self.assertEqual({"value": PROVIDER.value}, parsed["provider_id"])
        self.assertEqual({"state": "available", "observed_at": parsed["status"]["observed_at"]}, parsed["status"])
        self.assertEqual({"reference": "offline-fixture"}, parsed["source"])

    def test_fresh_snapshot_timestamps_end_in_z(self) -> None:
        rendered = render_json(snapshot())

        parsed = json.loads(rendered)
        self.assertTrue(parsed["fetched_at"].endswith("Z"))
        self.assertTrue(parsed["data_at"].endswith("Z"))
        self.assertTrue(parsed["status"]["observed_at"].endswith("Z"))

    def test_fresh_snapshot_quantities_serialize_decimal_as_string(self) -> None:
        rendered = render_json(snapshot(windows=(_known_window(),)))

        parsed = json.loads(rendered)
        limit = parsed["quota_windows"][0]["limit"]
        self.assertIsInstance(limit["value"], str)
        self.assertEqual("10", limit["value"])
        self.assertEqual("commercial_quota", limit["metric"])
        self.assertEqual("requests", limit["unit"])

    def test_fresh_snapshot_top_level_keys_are_deterministically_sorted(self) -> None:
        rendered = render_json(snapshot())

        parsed = json.loads(rendered)
        keys = list(parsed.keys())
        self.assertEqual(["version"] + sorted(keys[1:]), keys)


class StaleSnapshotAbsenceTests(unittest.TestCase):
    """Stale snapshots preserve fields with explicit absence markers."""

    def test_stale_snapshot_marks_freshness_and_uses_empty_and_null_absence(self) -> None:
        rendered = render_json(snapshot(freshness=Freshness.STALE))

        parsed = json.loads(rendered)
        self.assertEqual("stale", parsed["freshness"])
        self.assertEqual([], parsed["quota_windows"])
        self.assertIsNone(parsed["usage"])


class WindowNullableScalarTests(unittest.TestCase):
    """Nullable scalars on a window appear as null, not omitted."""

    def test_window_with_all_nullable_fields_serializes_every_nullable_field_as_null(self) -> None:
        window = QuotaWindow(
            kind=WindowKind.OTHER,
            scope="a",
            period="day",
            plan_id=None,
            availability=ValueAvailability.UNKNOWN,
            source=SourceMetadata("missing"),
        )

        rendered = render_json(snapshot(windows=(window,)))
        parsed = json.loads(rendered)
        window_dict = parsed["quota_windows"][0]
        for field in ("plan_id", "limit", "used", "remaining", "reset_at"):
            with self.subTest(field=field):
                self.assertIn(field, window_dict)
                self.assertIsNone(window_dict[field])


class UndetectedEnvelopeTests(unittest.TestCase):
    """Undetected results project to a typed envelope, never null or the snapshot schema."""

    def test_undetected_envelope_is_typed_minimal_and_only_version_and_result(self) -> None:
        rendered = render_json(StatusUndetectedResult())

        parsed = json.loads(rendered)
        self.assertEqual({"version": 1, "result": "undetected"}, parsed)


class ErrorSanitizationTests(unittest.TestCase):
    """Error envelopes drop __cause__, traceback text, raw payloads, and port internals."""

    def test_error_envelope_carries_only_kind_provider_id_safe_message_and_retryable(self) -> None:
        error = ProviderError(
            ProviderErrorKind.TRANSPORT, PROVIDER, "safe failure", retryable=True
        )

        rendered = render_json(error)
        parsed = json.loads(rendered)

        self.assertEqual(
            {"version", "error"}, set(parsed.keys()),
        )
        error_obj = parsed["error"]
        self.assertEqual(
            {"kind", "provider_id", "retryable", "safe_message"},
            set(error_obj.keys()),
        )
        self.assertEqual("transport", error_obj["kind"])
        self.assertEqual({"value": PROVIDER.value}, error_obj["provider_id"])
        self.assertEqual("safe failure", error_obj["safe_message"])
        self.assertTrue(error_obj["retryable"])

    def test_error_envelope_drops_cause_traceback_and_secret_payload(self) -> None:
        error = ProviderError(
            ProviderErrorKind.TRANSPORT, PROVIDER, "safe failure", retryable=True,
        )
        error.__cause__ = RuntimeError("token=secret-credential")

        rendered = render_json(error)

        for forbidden in ("secret", "secret-credential", "RuntimeError", "__cause__", "Traceback"):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, rendered)


class DeterminismTests(unittest.TestCase):
    """render_json is byte-identical across calls for the same input."""

    def test_render_json_is_byte_identical_for_identical_snapshot_input(self) -> None:
        result = snapshot(windows=(_known_window(),))

        self.assertEqual(render_json(result), render_json(result))

    def test_render_json_is_byte_identical_for_identical_undetected_input(self) -> None:
        self.assertEqual(
            render_json(StatusUndetectedResult()),
            render_json(StatusUndetectedResult()),
        )

    def test_render_json_is_byte_identical_for_identical_error_input(self) -> None:
        error = ProviderError(
            ProviderErrorKind.UNAUTHORIZED, PROVIDER, "safe", retryable=False,
        )

        self.assertEqual(render_json(error), render_json(error))


if __name__ == "__main__":
    unittest.main()
