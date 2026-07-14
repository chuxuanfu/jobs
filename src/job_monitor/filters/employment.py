from __future__ import annotations

import re

from job_monitor.models import Job


EXCLUDED_TITLE_PATTERNS = (
    r"\bintern(ship)?\b",
    r"\bpart[ -]?time\b",
    r"\bcontract(or)?\b",
    r"\btemporary\b",
    r"\bseasonal\b",
    r"\bvendor\b",
)


def evaluate_employment(job: Job) -> tuple[str, bool]:
    title = job.title.lower()
    if any(re.search(pattern, title, re.I) for pattern in EXCLUDED_TITLE_PATTERNS):
        return "excluded_by_title_keyword", False

    value = re.sub(r"[^a-z0-9]+", "", (job.employment_type or "").lower())
    if value in {"fulltime", "fulltimeregular", "regular", "regularemployee", "standard"}:
        return "full_time_official_field", True
    if value in {"parttime", "contract", "contractor", "temporary", "seasonal", "intern", "internship"}:
        return "excluded_by_official_employment_type", False
    return "employment_review_required", False
