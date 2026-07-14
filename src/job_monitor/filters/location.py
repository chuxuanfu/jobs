from __future__ import annotations

import re
import unicodedata

from job_monitor.models import Job, Location


AMBIGUOUS_REGIONS = ("bay area", "silicon valley", "south bay", "flexible location")


def _normalized(value: str | None) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(character for character in value if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def evaluate_location(job: Job, location_config: dict) -> tuple[str, bool, bool, float | None]:
    locations = job.locations or [Location(raw=job.location_raw or "")]
    if job.is_us_job and (
        job.workplace_type == "remote" or any(_is_explicit_remote(location) for location in locations)
    ):
        return "eligible_explicit_remote_us", True, False, None

    allowed = {_normalized(city) for city in location_config["eligible_cities"]}
    saw_ambiguous = False
    for location in locations:
        candidates = {_normalized(location.city), _normalized(location.raw)}
        if any(_city_matches(candidate, allowed) for candidate in candidates if candidate):
            return "eligible_by_bay_area_city", True, False, None
        combined = " ".join(candidates)
        if any(region in combined for region in AMBIGUOUS_REGIONS):
            saw_ambiguous = True

    if saw_ambiguous or not job.locations:
        return "location_review_required", False, True, None
    return "outside_bay_area_city_list", False, False, None


def _city_matches(candidate: str, allowed: set[str]) -> bool:
    for city in allowed:
        if candidate == city or candidate.startswith(city + " ") or f" {city} " in f" {candidate} ":
            return True
    return False


def _is_explicit_remote(location: Location) -> bool:
    raw = _normalized(location.raw)
    if not re.search(r"\bremote\b", raw):
        return False
    country = _normalized(location.country)
    return country in {"us", "usa", "united states", "united states of america"} or bool(
        re.search(r"\b(us|usa|united states)\b", raw)
    )


def apply_basic_filters(job: Job, settings: dict) -> Job:
    from job_monitor.filters.employment import evaluate_employment

    employment_reason, employment_ok = evaluate_employment(job)
    location_status, location_ok, review_required, distance = evaluate_location(job, settings["location"])
    job.location_filter_status = location_status
    job.location_review_required = review_required
    job.distance_from_san_jose_miles = distance
    job.is_eligible_by_basic_filters = employment_ok and location_ok
    if job.is_eligible_by_basic_filters:
        job.eligibility_reason = f"{employment_reason};{location_status}"
    else:
        job.eligibility_reason = ";".join(reason for reason in (employment_reason, location_status) if reason)
    return job
