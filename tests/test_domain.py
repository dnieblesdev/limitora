"""Deterministic tests for the pure provider-status domain."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from limitora.models import (
    ExclusionReason,
    MetricKind,
    Percentage,
    ProviderId,
    ProviderSnapshot,
    ProviderState,
    ProviderStatus,
    Quantity,
    QuotaWindow,
    SourceMetadata,
    UsageSnapshot,
    ValueAvailability,
    WindowKind,
    aggregate_remaining_percentages,
)


NOW = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
SOURCE = SourceMetadata("provider-api")


def commercial_window(
    *,
    remaining: str | None = "80",
    availability: ValueAvailability = ValueAvailability.KNOWN,
    scope: str = "account",
    period: str = "monthly",
    plan_id: str | None = "pro",
    unit: str = "credits",
) -> QuotaWindow:
    quantities = {}
    if remaining is not None:
        quantities = {
            "limit": Quantity(Decimal("100"), MetricKind.COMMERCIAL_QUOTA, unit),
            "remaining": Quantity(Decimal(remaining), MetricKind.COMMERCIAL_QUOTA, unit),
        }
    return QuotaWindow(
        kind=WindowKind.COMMERCIAL_QUOTA,
        scope=scope,
        period=period,
        plan_id=plan_id,
        availability=availability,
        source=SOURCE,
        **quantities,
    )


def snapshot(
    provider: str, window: QuotaWindow, *, state: ProviderState = ProviderState.AVAILABLE,
    fetched_at: datetime = NOW,
) -> ProviderSnapshot:
    provider_id = ProviderId(provider)
    return ProviderSnapshot(
        provider_id=provider_id,
        status=ProviderStatus(provider_id, state, fetched_at),
        fetched_at=fetched_at,
        data_at=fetched_at,
        source=SOURCE,
        quota_windows=(window,),
    )


class DomainModelTests(unittest.TestCase):
    def test_constructs_known_commercial_window_and_derives_percentages(self) -> None:
        window = QuotaWindow(
            kind=WindowKind.COMMERCIAL_QUOTA,
            scope="account",
            period="monthly",
            plan_id="pro",
            availability=ValueAvailability.KNOWN,
            source=SOURCE,
            limit=Quantity(Decimal("100"), MetricKind.COMMERCIAL_QUOTA, "credits"),
            used=Quantity(Decimal("20"), MetricKind.COMMERCIAL_QUOTA, "credits"),
            remaining=Quantity(Decimal("80"), MetricKind.COMMERCIAL_QUOTA, "credits"),
            reset_at=NOW + timedelta(days=1),
        )
        self.assertEqual(Percentage(Decimal("80")), window.remaining_percentage)
        self.assertEqual(Percentage(Decimal("20")), window.used_percentage)

    def test_missing_or_unlimited_values_are_not_zero_percentages(self) -> None:
        unknown = commercial_window(remaining=None, availability=ValueAvailability.UNKNOWN)
        unlimited = commercial_window(remaining=None, availability=ValueAvailability.UNLIMITED)
        self.assertIsNone(unknown.remaining_percentage)
        self.assertIsNone(unlimited.remaining_percentage)

    def test_rejects_invalid_percentage_and_incompatible_quantities(self) -> None:
        with self.assertRaises(ValueError):
            Percentage(Decimal("100.01"))
        with self.assertRaises(TypeError):
            Percentage(50)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            QuotaWindow(
                kind=WindowKind.COMMERCIAL_QUOTA,
                scope="account",
                period="monthly",
                plan_id="pro",
                availability=ValueAvailability.KNOWN,
                source=SOURCE,
                limit=Quantity(Decimal("100"), MetricKind.COMMERCIAL_QUOTA, "credits"),
                remaining=Quantity(Decimal("1"), MetricKind.TECHNICAL_RATE_LIMIT, "requests"),
            )
        with self.assertRaises(ValueError):
            QuotaWindow(
                kind=WindowKind.COMMERCIAL_QUOTA,
                scope="account",
                period="monthly",
                plan_id="pro",
                availability=ValueAvailability.UNKNOWN,
                source=SOURCE,
                limit=Quantity(Decimal("100"), MetricKind.COMMERCIAL_QUOTA, "credits"),
            )

    def test_usage_keeps_tokens_and_balance_distinct(self) -> None:
        usage = UsageSnapshot(
            provider_id=ProviderId("codex"),
            observed_at=NOW,
            availability=ValueAvailability.KNOWN,
            source=SOURCE,
            token_limit=Quantity(Decimal("100"), MetricKind.TOKENS, "tokens"),
            token_used=Quantity(Decimal("25"), MetricKind.TOKENS, "tokens"),
            balance=Quantity(Decimal("10"), MetricKind.BALANCE, "usd"),
        )
        self.assertEqual(Percentage(Decimal("25")), usage.token_used_percentage)
        self.assertEqual(Percentage(Decimal("75")), usage.token_remaining_percentage)

    def test_snapshot_rejects_ambiguous_windows_and_tracks_freshness(self) -> None:
        provider = ProviderId("codex")
        with self.assertRaises(ValueError):
            ProviderSnapshot(
                provider_id=provider,
                status=ProviderStatus(provider, ProviderState.AVAILABLE, NOW),
                fetched_at=NOW,
                data_at=NOW,
                source=SOURCE,
                quota_windows=(commercial_window(), commercial_window()),
            )
        fresh = snapshot("codex", commercial_window())
        self.assertFalse(fresh.is_stale(NOW + timedelta(minutes=5), timedelta(minutes=5)))
        self.assertTrue(fresh.is_stale(NOW + timedelta(minutes=6), timedelta(minutes=5)))

    def test_aggregate_selects_lowest_valid_remaining_percentage(self) -> None:
        result = aggregate_remaining_percentages(
            (snapshot("codex", commercial_window(remaining="80")), snapshot("openai", commercial_window(remaining="25"))),
            kind=WindowKind.COMMERCIAL_QUOTA,
            scope="account",
            period="monthly",
            plan_id="pro",
            unit="credits",
            now=NOW,
            maximum_age=timedelta(minutes=5),
        )
        self.assertEqual(Percentage(Decimal("25")), result.remaining_percentage)
        self.assertEqual((ProviderId("codex"), ProviderId("openai")), result.included_provider_ids)
        self.assertFalse(result.partial)

    def test_aggregate_excludes_absent_status_stale_and_incompatible_data(self) -> None:
        result = aggregate_remaining_percentages(
            (
                snapshot("valid", commercial_window(remaining="50")),
                snapshot("unlimited", commercial_window(remaining=None, availability=ValueAvailability.UNLIMITED)),
                snapshot("failed", commercial_window(remaining=None, availability=ValueAvailability.ERROR), state=ProviderState.TRANSIENT_ERROR),
                snapshot("stale", commercial_window(), fetched_at=NOW - timedelta(minutes=6)),
                snapshot("different-scope", commercial_window(scope="organization")),
                ProviderSnapshot(
                    provider_id=ProviderId("technical"),
                    status=ProviderStatus(ProviderId("technical"), ProviderState.AVAILABLE, NOW),
                    fetched_at=NOW,
                    data_at=NOW,
                    source=SOURCE,
                    quota_windows=(QuotaWindow(
                        kind=WindowKind.TECHNICAL_RATE_LIMIT,
                        scope="account",
                        period="monthly",
                        plan_id=None,
                        availability=ValueAvailability.KNOWN,
                        source=SOURCE,
                        limit=Quantity(Decimal("100"), MetricKind.TECHNICAL_RATE_LIMIT, "requests"),
                        remaining=Quantity(Decimal("1"), MetricKind.TECHNICAL_RATE_LIMIT, "requests"),
                    ),),
                ),
            ),
            kind=WindowKind.COMMERCIAL_QUOTA,
            scope="account",
            period="monthly",
            plan_id="pro",
            unit="credits",
            now=NOW,
            maximum_age=timedelta(minutes=5),
        )
        self.assertEqual(Percentage(Decimal("50")), result.remaining_percentage)
        self.assertTrue(result.partial)
        self.assertEqual(
            (
                ExclusionReason.INCOMPATIBLE,
                ExclusionReason.STATUS,
                ExclusionReason.STALE,
                ExclusionReason.INCOMPATIBLE,
                ExclusionReason.ABSENT,
            ),
            tuple(exclusion.reason for exclusion in result.exclusions),
        )

    def test_aggregate_rejects_other_kind(self) -> None:
        with self.assertRaises(ValueError):
            aggregate_remaining_percentages(
                (), kind=WindowKind.OTHER, scope="account", period="monthly", plan_id=None, unit="credits", now=NOW,
                maximum_age=timedelta(minutes=5),
            )

    def test_aggregate_excludes_different_commercial_plan_or_unit(self) -> None:
        result = aggregate_remaining_percentages(
            (
                snapshot("compatible", commercial_window(remaining="60")),
                snapshot("other-plan", commercial_window(remaining="1", plan_id="enterprise")),
                snapshot("other-unit", commercial_window(remaining="1", unit="requests")),
            ),
            kind=WindowKind.COMMERCIAL_QUOTA,
            scope="account",
            period="monthly",
            plan_id="pro",
            unit="credits",
            now=NOW,
            maximum_age=timedelta(minutes=5),
        )
        self.assertEqual(Percentage(Decimal("60")), result.remaining_percentage)
        self.assertEqual((ProviderId("compatible"),), result.included_provider_ids)
        self.assertEqual(
            (ExclusionReason.INCOMPATIBLE_COMPARABILITY,) * 2,
            tuple(exclusion.reason for exclusion in result.exclusions),
        )

    def test_terminal_statuses_reject_known_numeric_evidence(self) -> None:
        for state in (
            ProviderState.UNAVAILABLE,
            ProviderState.UNAUTHORIZED,
            ProviderState.TRANSIENT_ERROR,
            ProviderState.INVALID_DATA,
        ):
            with self.subTest(state=state), self.assertRaises(ValueError):
                snapshot("codex", commercial_window(), state=state)

        provider = ProviderId("usage-provider")
        usage = UsageSnapshot(
            provider_id=provider,
            observed_at=NOW,
            availability=ValueAvailability.KNOWN,
            source=SOURCE,
            balance=Quantity(Decimal("10"), MetricKind.BALANCE, "usd"),
        )
        with self.assertRaises(ValueError):
            ProviderSnapshot(
                provider_id=provider,
                status=ProviderStatus(provider, ProviderState.INVALID_DATA, NOW),
                fetched_at=NOW,
                data_at=NOW,
                source=SOURCE,
                usage=usage,
            )


if __name__ == "__main__":
    unittest.main()
