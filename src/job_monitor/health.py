from __future__ import annotations

from job_monitor.models import FetchResult, HealthResult


def evaluate_health(result: FetchResult, previous_count: int | None, settings: dict) -> HealthResult:
    fetched_count = result.source_item_count if result.source_item_count is not None else len(result.jobs)
    parsed_count = result.parsed_item_count if result.parsed_item_count is not None else len(result.jobs)
    parse_rate = 1.0 if fetched_count else 0.0
    reasons: list[str] = []
    if result.error:
        reasons.append(result.error)
    if result.http_status is None or not 200 <= result.http_status < 300:
        reasons.append(f"unexpected_http_status:{result.http_status}")
    if fetched_count == 0:
        reasons.append("empty_job_feed")
    minimum_parse_rate = float(settings["health"]["minimum_parse_success_rate"])
    if parse_rate < minimum_parse_rate:
        reasons.append(f"parse_success_rate_below_threshold:{parse_rate:.3f}")
    if previous_count:
        drop_fraction = max(0.0, (previous_count - fetched_count) / previous_count)
        if drop_fraction > float(settings["health"]["maximum_count_drop_fraction"]):
            reasons.append(f"job_count_drop:{drop_fraction:.3f}")
    return HealthResult(
        healthy=not reasons,
        reasons=reasons,
        fetched_count=fetched_count,
        parsed_count=parsed_count,
        parse_success_rate=parse_rate,
        previous_count=previous_count,
    )
