from datetime import timedelta

from limitora import AuthorizationPolicy, FreshnessPolicy, MetricKind, StatusRequest

request = StatusRequest(
    frozenset({MetricKind.COMMERCIAL_QUOTA}),
    AuthorizationPolicy.DENY_AUTHORIZED_SOURCE,
    FreshnessPolicy(timedelta(minutes=5)),
)
assert request.requested_metrics
