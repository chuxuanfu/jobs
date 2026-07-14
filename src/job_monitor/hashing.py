from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def job_content_hash(job_dict: dict[str, Any]) -> str:
    excluded = {
        "fetched_at", "content_hash", "source_payload_hash", "fetch_warning",
        "is_eligible_by_basic_filters", "eligibility_reason",
        "location_filter_status", "location_review_required",
        "distance_from_san_jose_miles",
    }
    return sha256_value({key: value for key, value in job_dict.items() if key not in excluded})
