from __future__ import annotations

from datetime import datetime, timedelta, timezone


def is_within_retention(posted_at: str | None, reference_at: str, retention_days: int) -> bool:
    """Keep unknown dates; never guess that an undated official post is old."""
    if not posted_at:
        return True
    try:
        posted = _parse_datetime(posted_at)
        reference = _parse_datetime(reference_at)
    except ValueError:
        return True
    return posted >= reference - timedelta(days=retention_days)


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
