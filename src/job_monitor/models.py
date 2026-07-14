from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Location:
    raw: str
    city: str | None = None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None
    street_address: str | None = None
    latitude: float | None = None
    longitude: float | None = None


@dataclass
class Job:
    company: str
    source_name: str
    source_job_id: str
    source_url: str | None
    apply_url: str | None
    canonical_url: str | None
    source_adapter_version: str
    title: str
    normalized_title: str
    team: str | None = None
    department: str | None = None
    employment_type: str | None = None
    workplace_type: str = "unknown"
    requisition_id: str | None = None
    job_category: str | None = None
    level: str | None = None
    posted_at: str | None = None
    posted_at_raw: str | None = None
    posted_at_accuracy: str | None = None
    updated_at: str | None = None
    valid_through: str | None = None
    closing_date: str | None = None
    fetched_at: str | None = None
    location_raw: str | None = None
    locations: list[Location] = field(default_factory=list)
    primary_city: str | None = None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None
    street_address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    distance_from_san_jose_miles: float | None = None
    location_filter_status: str = "review"
    location_review_required: bool = True
    description_raw_html: str | None = None
    description_plain_text: str | None = None
    responsibilities: str | None = None
    minimum_qualifications: str | None = None
    preferred_qualifications: str | None = None
    required_qualifications: str | None = None
    education_requirements: str | None = None
    experience_requirements: str | None = None
    other_requirements: str | None = None
    benefits: str | None = None
    travel_requirements: str | None = None
    work_authorization_text: str | None = None
    equal_opportunity_text: str | None = None
    complete_job_posting_json: dict[str, Any] = field(default_factory=dict)
    salary_text_raw: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    compensation_type: str | None = None
    bonus_text: str | None = None
    equity_text: str | None = None
    other_compensation_text: str | None = None
    location_specific_compensation: list[dict[str, Any]] = field(default_factory=list)
    is_us_job: bool = False
    is_eligible_by_basic_filters: bool = False
    eligibility_reason: str = "not_evaluated"
    parser_warning: str | None = None
    fetch_warning: str | None = None
    content_hash: str | None = None
    source_payload_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FetchResult:
    company: str
    request_url: str
    fetched_at: str
    http_status: int | None
    response_headers: dict[str, str]
    raw_body: bytes
    jobs: list[Job]
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class HealthResult:
    healthy: bool
    reasons: list[str]
    fetched_count: int
    parsed_count: int
    parse_success_rate: float
    previous_count: int | None
