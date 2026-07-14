from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib


def should_refresh_detail(
    source_job_id: str,
    posted_at: str | None,
    observed_at: str,
    *,
    has_cached_detail: bool,
    refresh_days: int,
    recent_days: int = 2,
) -> bool:
    """Refresh new/recent details plus a deterministic daily shard of cached jobs."""
    if not has_cached_detail:
        return True
    observed = _parse_datetime(observed_at)
    posted = _parse_datetime_or_none(posted_at)
    if posted is not None and posted >= observed - timedelta(days=recent_days):
        return True
    cycle = max(1, refresh_days)
    shard = int(hashlib.sha256(source_job_id.encode("utf-8")).hexdigest()[:8], 16) % cycle
    return shard == observed.date().toordinal() % cycle


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _parse_datetime_or_none(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return _parse_datetime(value)
    except ValueError:
        return None
