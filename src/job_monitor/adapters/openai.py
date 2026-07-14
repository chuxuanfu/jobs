from __future__ import annotations

from datetime import datetime, timezone
from email.message import Message
import json
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from job_monitor.adapters.base import SourceAdapter
from job_monitor.hashing import job_content_hash, sha256_value
from job_monitor.models import FetchResult, Job, Location
from job_monitor.text import clean_text, first_section, html_to_text_and_sections


US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "district of columbia",
}
US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI",
    "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC",
    "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT",
    "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}


class OpenAIAdapter(SourceAdapter):
    company = "openai"

    def fetch(self) -> FetchResult:
        endpoint = self.company_config["endpoint"]
        fetched_at = datetime.now(timezone.utc).isoformat()
        timeout = int(self.settings.get("request_timeout_seconds", 45))
        max_retries = int(self.settings.get("max_retries", 3))
        request = Request(
            endpoint,
            headers={
                "User-Agent": self.settings["user_agent"],
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
            method="GET",
        )
        last_error: str | None = None
        last_status: int | None = None
        last_headers: dict[str, str] = {}
        for attempt in range(max_retries):
            try:
                with urlopen(request, timeout=timeout) as response:
                    body = response.read()
                    status = response.status
                    headers = _safe_headers(response.headers)
                jobs = self.parse_payload(body, fetched_at)
                return FetchResult(
                    company=self.company,
                    request_url=endpoint,
                    fetched_at=fetched_at,
                    http_status=status,
                    response_headers=headers,
                    raw_body=body,
                    jobs=jobs,
                )
            except HTTPError as exc:
                last_status = exc.code
                last_headers = _safe_headers(exc.headers)
                last_error = f"HTTP {exc.code}: {exc.reason}"
                # A permission block is a circuit-breaker, never something to evade.
                if exc.code in {401, 403}:
                    break
                if exc.code != 429 and exc.code < 500:
                    break
                retry_after = _retry_after_seconds(exc.headers)
                time.sleep(min(retry_after if retry_after is not None else 2 ** attempt, 30))
            except (URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt + 1 < max_retries:
                    time.sleep(2 ** attempt)
        return FetchResult(
            company=self.company,
            request_url=endpoint,
            fetched_at=fetched_at,
            http_status=last_status,
            response_headers=last_headers,
            raw_body=b"",
            jobs=[],
            error=last_error or "Unknown fetch failure",
        )

    def parse_payload(self, payload: bytes, fetched_at: str) -> list[Job]:
        document = json.loads(payload.decode("utf-8"))
        records = document.get("jobs")
        if not isinstance(records, list):
            raise ValueError("OpenAI feed did not contain a jobs list")
        return [self._parse_job(record, fetched_at) for record in records if isinstance(record, dict)]

    def _parse_job(self, record: dict[str, Any], fetched_at: str) -> Job:
        source_job_id = str(record.get("id") or "").strip()
        title = clean_text(str(record.get("title") or "")) or ""
        if not source_job_id or not title:
            raise ValueError("OpenAI record is missing required id or title")

        description_html = record.get("descriptionHtml")
        description_text, sections = html_to_text_and_sections(description_html)
        official_plain = clean_text(record.get("descriptionPlain"))
        locations = _locations_from_record(record)
        location_raw = clean_text(str(record.get("location") or ""))
        workplace_type = _workplace_type(record)
        compensation = record.get("compensation") if isinstance(record.get("compensation"), dict) else {}
        compensation_ranges = _compensation_ranges(compensation)
        primary_salary = next((item for item in compensation_ranges if item.get("compensation_type") == "salary"), None)
        salary_text = _salary_text(compensation)
        source_url = _first_string(record, "jobUrl", "jobPostingUrl")
        apply_url = _first_string(record, "applyUrl")
        job = Job(
            company="openai",
            source_name=self.company_config["source_name"],
            source_job_id=source_job_id,
            source_url=source_url,
            apply_url=apply_url,
            canonical_url=source_url,
            source_adapter_version=self.company_config["adapter_version"],
            title=title,
            normalized_title=_normalize_title(title),
            team=clean_text(_string_or_none(record.get("team"))),
            department=clean_text(_string_or_none(record.get("department"))),
            employment_type=clean_text(_string_or_none(record.get("employmentType"))),
            workplace_type=workplace_type,
            requisition_id=clean_text(_string_or_none(record.get("requisitionId"))),
            job_category=clean_text(_string_or_none(record.get("jobCategory"))),
            level=clean_text(_string_or_none(record.get("level"))),
            posted_at=_string_or_none(record.get("publishedAt")),
            posted_at_raw=_string_or_none(record.get("publishedAt")),
            posted_at_accuracy="exact" if record.get("publishedAt") else None,
            updated_at=_string_or_none(record.get("updatedAt")),
            valid_through=_string_or_none(record.get("validThrough")),
            closing_date=_string_or_none(record.get("closingDate")),
            fetched_at=fetched_at,
            location_raw=location_raw,
            locations=locations,
            primary_city=locations[0].city if locations else None,
            state=locations[0].state if locations else None,
            country=locations[0].country if locations else None,
            postal_code=locations[0].postal_code if locations else None,
            street_address=locations[0].street_address if locations else None,
            latitude=locations[0].latitude if locations else None,
            longitude=locations[0].longitude if locations else None,
            description_raw_html=description_html,
            description_plain_text=official_plain or description_text,
            responsibilities=first_section(sections, {
                "responsibilities", "what you ll do", "what you will do", "in this role you will",
            }),
            minimum_qualifications=first_section(sections, {"minimum qualifications", "minimum requirements"}),
            preferred_qualifications=first_section(sections, {"preferred qualifications", "preferred requirements"}),
            required_qualifications=first_section(sections, {"required qualifications", "requirements"}),
            other_requirements=first_section(sections, {
                "you might thrive in this role if you", "you may be a good fit if you",
                "you ll thrive in this role if you", "you will thrive in this role if you",
            }),
            benefits=first_section(sections, {"benefits", "benefits and perks"}),
            complete_job_posting_json=record,
            salary_text_raw=salary_text,
            salary_min=primary_salary.get("min") if primary_salary else None,
            salary_max=primary_salary.get("max") if primary_salary else None,
            salary_currency=primary_salary.get("currency") if primary_salary else None,
            salary_period=primary_salary.get("period") if primary_salary else None,
            compensation_type=primary_salary.get("compensation_type") if primary_salary else None,
            bonus_text=_component_text(compensation, "bonus"),
            equity_text=_component_text(compensation, "equity"),
            other_compensation_text=_component_text(compensation, "other"),
            location_specific_compensation=compensation_ranges,
            is_us_job=_is_us_record(record, locations),
        )
        job.source_payload_hash = sha256_value(record)
        job.content_hash = job_content_hash(job.to_dict())
        return job


def _safe_headers(headers: Message | None) -> dict[str, str]:
    if headers is None:
        return {}
    allowed = {"content-type", "content-length", "date", "etag", "last-modified", "retry-after", "cache-control"}
    return {key.lower(): value for key, value in headers.items() if key.lower() in allowed}


def _retry_after_seconds(headers: Message | None) -> int | None:
    if not headers:
        return None
    value = headers.get("Retry-After")
    if value and value.isdigit():
        return int(value)
    return None


def _first_string(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip()


def _workplace_type(record: dict[str, Any]) -> str:
    value = str(record.get("workplaceType") or "").strip().lower()
    if value in {"onsite", "on-site", "on site"}:
        return "onsite"
    if value in {"hybrid"}:
        return "hybrid"
    if value in {"remote"} or record.get("isRemote") is True:
        return "remote"
    return "unknown"


def _address_to_location(raw_label: str, address: Any) -> Location:
    postal = address.get("postalAddress", address) if isinstance(address, dict) else {}
    return Location(
        raw=raw_label,
        city=_string_or_none(postal.get("addressLocality")),
        state=_string_or_none(postal.get("addressRegion")),
        country=_string_or_none(postal.get("addressCountry")),
        postal_code=_string_or_none(postal.get("postalCode")),
        street_address=_string_or_none(postal.get("streetAddress")),
        latitude=_number_or_none(address.get("latitude")) if isinstance(address, dict) else None,
        longitude=_number_or_none(address.get("longitude")) if isinstance(address, dict) else None,
    )


def _locations_from_record(record: dict[str, Any]) -> list[Location]:
    result = [_address_to_location(str(record.get("location") or ""), record.get("address"))]
    for item in record.get("secondaryLocations") or []:
        if isinstance(item, dict):
            result.append(_address_to_location(str(item.get("location") or ""), item.get("address")))
        elif isinstance(item, str):
            result.append(Location(raw=item))
    return [item for item in result if item.raw or item.city or item.country]


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _is_us_record(record: dict[str, Any], locations: list[Location]) -> bool:
    texts = [str(record.get("location") or "")]
    texts.extend(location.raw for location in locations)
    for location in locations:
        country = (location.country or "").strip().lower()
        if country in {"united states", "united states of america", "usa", "us"}:
            return True
        state = (location.state or "").strip()
        if state.upper() in US_STATE_CODES or state.lower() in US_STATE_NAMES:
            return True
    joined = " | ".join(texts)
    if re.search(r"\b(remote\s*[-–—,]?\s*(us|usa|united states)|united states|usa)\b", joined, re.I):
        return True
    return bool(re.search(r"(?:,|\s)\s*(?:" + "|".join(sorted(US_STATE_CODES)) + r")(?:\s|,|$)", joined))


def _salary_text(compensation: dict[str, Any]) -> str | None:
    values: list[str] = []
    for key in ("compensationTierSummary", "scrapeableCompensationSalarySummary", "summary"):
        value = compensation.get(key)
        if isinstance(value, str) and value.strip() and value.strip() not in values:
            values.append(value.strip())
    for tier in compensation.get("compensationTiers") or []:
        if isinstance(tier, dict):
            for key in ("tierSummary", "title", "additionalInformation"):
                value = tier.get(key)
                if isinstance(value, str) and value.strip() and value.strip() not in values:
                    values.append(value.strip())
    return "\n".join(values) or None


def _compensation_ranges(compensation: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    tiers = compensation.get("compensationTiers") or []
    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        components = tier.get("components") or []
        for component in components:
            if not isinstance(component, dict):
                continue
            component_type = str(component.get("compensationType") or component.get("type") or "other").lower()
            results.append({
                "location_or_tier": tier.get("title") or tier.get("tierSummary"),
                "compensation_type": _normalize_compensation_type(component_type),
                "min": _number_or_none(component.get("minValue") or component.get("minimum")),
                "max": _number_or_none(component.get("maxValue") or component.get("maximum")),
                "currency": component.get("currencyCode") or component.get("currency"),
                "period": str(component.get("interval") or component.get("period") or "").lower() or None,
                "raw": component,
            })
    return results


def _normalize_compensation_type(value: str) -> str:
    if "salary" in value or "base" in value:
        return "salary"
    if "equity" in value or "stock" in value:
        return "equity"
    if "bonus" in value:
        return "bonus"
    return "other"


def _component_text(compensation: dict[str, Any], wanted: str) -> str | None:
    matches = [item for item in _compensation_ranges(compensation) if item["compensation_type"] == wanted]
    return json.dumps(matches, ensure_ascii=False, sort_keys=True) if matches else None
