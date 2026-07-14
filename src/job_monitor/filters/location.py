from __future__ import annotations

import math
import re
import unicodedata

from job_monitor.models import Job, Location


AMBIGUOUS_REGIONS = ("bay area", "silicon valley", "south bay", "flexible location")


def _normalized(value: str | None) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(character for character in value if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 3958.7613
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def evaluate_location(job: Job, location_config: dict) -> tuple[str, bool, bool, float | None]:
    if job.workplace_type == "remote":
        if job.is_us_job:
            return "eligible_remote_us", True, False, None
        return "location_review_required", False, True, None

    allowed = {_normalized(city) for city in location_config["nearby_cities"]}
    radius = float(location_config["radius_miles"])
    center_lat = float(location_config["center_latitude"])
    center_lon = float(location_config["center_longitude"])
    distances: list[float] = []
    saw_ambiguous = False
    for location in job.locations or [Location(raw=job.location_raw or "")]:
        if location.latitude is not None and location.longitude is not None:
            distance = haversine_miles(center_lat, center_lon, location.latitude, location.longitude)
            distances.append(distance)
            if distance <= radius:
                return "eligible_by_coordinates", True, False, distance
        candidates = {_normalized(location.city), _normalized(location.raw)}
        if any(_city_matches(candidate, allowed) for candidate in candidates if candidate):
            return "eligible_by_city_allowlist", True, False, min(distances) if distances else None
        combined = " ".join(candidates)
        if any(region in combined for region in AMBIGUOUS_REGIONS):
            saw_ambiguous = True

    if distances:
        return "outside_radius", False, False, min(distances)
    if saw_ambiguous or not job.locations:
        return "location_review_required", False, True, None
    return "outside_city_allowlist", False, False, None


def _city_matches(candidate: str, allowed: set[str]) -> bool:
    for city in allowed:
        if candidate == city or candidate.startswith(city + " ") or f" {city} " in f" {candidate} ":
            return True
    return False


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
