from __future__ import annotations

from datetime import datetime, timezone
from html import unescape
import json
from math import ceil
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlencode

from job_monitor.adapters.base import SourceAdapter
from job_monitor.archive import read_archive_manifest, read_archived_payload
from job_monitor.hashing import job_content_hash, sha256_value
from job_monitor.http import HttpRequestError, PoliteHttpClient
from job_monitor.models import FetchResult, Job, Location, RawArtifact
from job_monitor.refresh import should_refresh_detail
from job_monitor.retention import is_within_retention
from job_monitor.text import clean_text, html_to_text_and_sections


HYDRATION_PATTERN = re.compile(
    r"window\.__staticRouterHydrationData\s*=\s*JSON\.parse\((\"(?:\\.|[^\"\\])*\")\)",
    re.DOTALL,
)


class AppleAdapter(SourceAdapter):
    company = "apple"

    def fetch(self) -> FetchResult:
        client = PoliteHttpClient(self.settings)
        fetched_at = datetime.now(timezone.utc).isoformat()
        artifacts: list[RawArtifact] = []
        warnings: list[str] = []
        search_records: list[dict[str, Any]] = []
        first_response = None

        try:
            first_response = client.get(self._search_url(1), accept="text/html")
            artifacts.append(_artifact(first_response, "search_page_00001"))
            page_records, total = parse_search_payload(first_response.body)
            search_records.extend(page_records)
            page_size = max(1, len(page_records))
            for page in range(2, ceil(total / page_size) + 1):
                response = client.get(self._search_url(page), accept="text/html")
                artifacts.append(_artifact(response, f"search_page_{page:05d}"))
                records, reported_total = parse_search_payload(response.body)
                if reported_total != total:
                    warnings.append(f"search_total_changed:{total}->{reported_total}:page={page}")
                    total = max(total, reported_total)
                search_records.extend(records)
        except (HttpRequestError, ValueError, json.JSONDecodeError) as exc:
            if isinstance(exc, HttpRequestError):
                artifacts.append(_error_artifact(exc, f"search_error_{len(artifacts) + 1:05d}", fetched_at))
                status, headers, body = exc.status, exc.headers, exc.body
            else:
                status = first_response.status if first_response else None
                headers = first_response.headers if first_response else {}
                body = first_response.body if first_response else b""
            return FetchResult(
                company=self.company,
                request_url=self._search_url(1),
                fetched_at=fetched_at,
                http_status=status,
                response_headers=headers,
                raw_body=body,
                jobs=[],
                warnings=warnings,
                error=f"Apple search failed: {exc}",
                artifacts=artifacts,
            )

        search_records = _dedupe_search_records(search_records)
        jobs: list[Job] = []
        detail_failures = 0
        detail_attempts = 0
        retention_days = int(self.settings["retention_days"])
        refresh_days = int(self.company_config.get("detail_refresh_days", self.settings.get("detail_refresh_days", 7)))
        recent_days = int(self.company_config.get("recent_detail_days", 2))
        for record in search_records:
            source_job_id = _search_job_id(record)
            posted_at = _string(record.get("postDateInGMT"))
            cached = self.existing_jobs.get(source_job_id)
            in_retention = is_within_retention(posted_at, fetched_at, retention_days)
            refresh = in_retention and should_refresh_detail(
                source_job_id,
                posted_at,
                fetched_at,
                has_cached_detail=cached is not None,
                refresh_days=refresh_days,
                recent_days=recent_days,
            )
            if refresh:
                detail_attempts += 1
                detail_url = self._detail_url(record)
                try:
                    response = client.get(detail_url, accept="text/html")
                    artifacts.append(_artifact(response, f"detail_{source_job_id}"))
                    detail = parse_detail_payload(response.body)
                    jobs.append(self._job_from_detail(record, detail, response.request_url, fetched_at))
                    continue
                except (HttpRequestError, ValueError, json.JSONDecodeError) as exc:
                    detail_failures += 1
                    warnings.append(f"detail_failed:{source_job_id}:{type(exc).__name__}:{exc}")
                    if isinstance(exc, HttpRequestError):
                        artifacts.append(_error_artifact(exc, f"detail_error_{source_job_id}", fetched_at))
            if cached is not None:
                jobs.append(self._job_from_cache(cached, record, fetched_at))
            else:
                jobs.append(self._job_from_search(record, fetched_at))

        error = None
        if detail_attempts and detail_failures / detail_attempts > 0.20:
            error = f"Apple detail failure rate too high: {detail_failures}/{detail_attempts}"
        if len(jobs) != len(search_records):
            error = error or f"Apple parsed count mismatch: {len(jobs)}/{len(search_records)}"
        return FetchResult(
            company=self.company,
            request_url=self._search_url(1),
            fetched_at=fetched_at,
            http_status=first_response.status if first_response else None,
            response_headers=first_response.headers if first_response else {},
            raw_body=first_response.body if first_response else b"",
            jobs=jobs,
            warnings=warnings,
            error=error,
            artifacts=artifacts,
        )

    def parse_payload(self, payload: bytes, fetched_at: str) -> list[Job]:
        document = extract_hydration(payload)
        loader = document.get("loaderData") or {}
        if isinstance(loader.get("search"), dict):
            records = loader["search"].get("searchResults") or []
            return [self._job_from_search(record, fetched_at) for record in records if isinstance(record, dict)]
        details = loader.get("jobDetails") or {}
        detail = details.get("jobsData") if isinstance(details, dict) else None
        if isinstance(detail, dict):
            return [self._job_from_detail(detail, detail, None, fetched_at)]
        raise ValueError("Apple hydration data did not contain search or job details")

    def reparse_archive(self, path: Path, fetched_at: str) -> list[Job]:
        if path.name.endswith(".metadata.json"):
            manifest = read_archive_manifest(path)
            search_records: list[dict[str, Any]] = []
            details: dict[str, tuple[dict[str, Any], str | None]] = {}
            for item in manifest.get("artifacts") or []:
                name = str(item.get("suggested_name") or "")
                payload = read_archived_payload(Path(item["path"]))
                if name.startswith("search_page_"):
                    records, _ = parse_search_payload(payload)
                    search_records.extend(records)
                elif name.startswith("detail_") and not name.startswith("detail_error_"):
                    detail = parse_detail_payload(payload)
                    details[_detail_job_id(detail)] = (detail, item.get("request_url"))
            jobs = []
            for record in _dedupe_search_records(search_records):
                source_job_id = _search_job_id(record)
                if source_job_id in details:
                    detail, request_url = details[source_job_id]
                    jobs.append(self._job_from_detail(record, detail, request_url, fetched_at))
                else:
                    jobs.append(self._job_from_search(record, fetched_at))
            return jobs
        return super().reparse_archive(path, fetched_at)

    def _search_url(self, page: int) -> str:
        base = self.company_config["search_url"]
        return f"{base}?{urlencode({'location': 'united-states-USA', 'page': page})}"

    def _detail_url(self, record: dict[str, Any]) -> str:
        job_id = _search_job_id(record)
        slug = _string(record.get("transformedPostingTitle")) or "job"
        base = self.company_config["detail_url_template"].format(job_id=job_id, slug=slug)
        team = record.get("team") if isinstance(record.get("team"), dict) else {}
        team_code = _string(team.get("teamCode"))
        return f"{base}?{urlencode({'team': team_code})}" if team_code else base

    def _job_from_cache(self, cached: dict[str, Any], search: dict[str, Any], fetched_at: str) -> Job:
        job = Job.from_dict(cached)
        job.fetched_at = fetched_at
        job.title = clean_text(_string(search.get("postingTitle"))) or job.title
        job.normalized_title = _normalize_title(job.title)
        job.posted_at = _string(search.get("postDateInGMT")) or job.posted_at
        job.posted_at_raw = _string(search.get("postingDate")) or job.posted_at_raw
        job.locations = _locations(search.get("locations")) or job.locations
        job.location_raw = "; ".join(location.raw for location in job.locations) or job.location_raw
        job.complete_job_posting_json = {**job.complete_job_posting_json, "search": search}
        job.source_payload_hash = sha256_value(job.complete_job_posting_json)
        job.content_hash = job_content_hash(job.to_dict())
        return job

    def _job_from_search(self, record: dict[str, Any], fetched_at: str) -> Job:
        source_job_id = _search_job_id(record)
        title = clean_text(_string(record.get("postingTitle"))) or ""
        if not source_job_id or not title:
            raise ValueError("Apple search record missing job id or title")
        locations = _locations(record.get("locations"))
        team = record.get("team") if isinstance(record.get("team"), dict) else {}
        url = self._detail_url(record)
        job = Job(
            company=self.company,
            source_name=self.company_config["source_name"],
            source_job_id=source_job_id,
            source_url=url,
            apply_url=url,
            canonical_url=url,
            source_adapter_version=self.company_config["adapter_version"],
            title=title,
            normalized_title=_normalize_title(title),
            team=clean_text(_string(team.get("teamName"))),
            employment_type=None,
            workplace_type="remote" if record.get("homeOffice") is True else "unknown",
            requisition_id=source_job_id,
            posted_at=_string(record.get("postDateInGMT")),
            posted_at_raw=_string(record.get("postingDate")),
            posted_at_accuracy="exact" if record.get("postDateInGMT") else None,
            fetched_at=fetched_at,
            location_raw="; ".join(location.raw for location in locations) or None,
            locations=locations,
            primary_city=locations[0].city if locations else None,
            state=locations[0].state if locations else None,
            country=locations[0].country if locations else None,
            postal_code=locations[0].postal_code if locations else None,
            description_plain_text=clean_text(_string(record.get("jobSummary"))),
            complete_job_posting_json={"search": record},
            # Every record is sourced from Apple's official United States
            # location-filtered search. Detail pages occasionally omit country.
            is_us_job=True,
            parser_warning="detail_not_available_in_this_snapshot",
        )
        job.source_payload_hash = sha256_value(job.complete_job_posting_json)
        job.content_hash = job_content_hash(job.to_dict())
        return job

    def _job_from_detail(
        self,
        search: dict[str, Any],
        detail: dict[str, Any],
        response_url: str | None,
        fetched_at: str,
    ) -> Job:
        source_job_id = _detail_job_id(detail) or _search_job_id(search)
        title = clean_text(_string(detail.get("postingTitle")) or _string(search.get("postingTitle"))) or ""
        if not source_job_id or not title:
            raise ValueError("Apple detail missing job id or title")
        locations = _locations(detail.get("localeLocation") or detail.get("locations") or search.get("locations"))
        canonical_url = response_url or self._detail_url(search)
        footer = _parse_footers(detail.get("postingFooters"), locations)
        content_parts = [
            clean_text(_string(detail.get("jobSummary"))),
            clean_text(_string(detail.get("description"))),
            clean_text(_string(detail.get("responsibilities"))),
            clean_text(_string(detail.get("minimumQualifications"))),
            clean_text(_string(detail.get("preferredQualifications"))),
            footer["benefits"], footer["equal_opportunity_text"], footer["other_footer_text"],
        ]
        raw_html_parts = [
            _string(item.get("content"))
            for item in _footer_entries(detail.get("postingFooters"))
            if _string(item.get("content"))
        ]
        job = Job(
            company=self.company,
            source_name=self.company_config["source_name"],
            source_job_id=source_job_id,
            source_url=canonical_url,
            apply_url=canonical_url,
            canonical_url=canonical_url,
            source_adapter_version=self.company_config["adapter_version"],
            title=title,
            normalized_title=_normalize_title(title),
            team=clean_text(", ".join(str(item) for item in detail.get("teamNames") or []) or None),
            employment_type=clean_text(_string(detail.get("employmentType"))),
            workplace_type="remote" if detail.get("homeOffice") is True else "unknown",
            requisition_id=source_job_id,
            job_category=clean_text(_string(detail.get("jobType"))),
            level=clean_text(" / ".join(filter(None, [_string(detail.get("lowJobTitle")), _string(detail.get("highJobTitle"))])) or None),
            posted_at=_string(detail.get("postDateInGMT")) or _string(search.get("postDateInGMT")),
            posted_at_raw=_string(detail.get("postingDate")) or _string(search.get("postingDate")),
            posted_at_accuracy="exact" if detail.get("postDateInGMT") or search.get("postDateInGMT") else None,
            fetched_at=fetched_at,
            location_raw="; ".join(location.raw for location in locations) or None,
            locations=locations,
            primary_city=locations[0].city if locations else None,
            state=locations[0].state if locations else None,
            country=locations[0].country if locations else None,
            postal_code=locations[0].postal_code if locations else None,
            description_raw_html="\n".join(raw_html_parts) or None,
            description_plain_text=clean_text("\n\n".join(part for part in content_parts if part)),
            responsibilities=clean_text(_string(detail.get("responsibilities"))),
            minimum_qualifications=clean_text(_string(detail.get("minimumQualifications"))),
            preferred_qualifications=clean_text(_string(detail.get("preferredQualifications"))),
            benefits=footer["benefits"],
            equal_opportunity_text=footer["equal_opportunity_text"],
            complete_job_posting_json={"search": search, "detail": detail},
            salary_text_raw=footer["salary_text_raw"],
            salary_min=footer["salary_min"],
            salary_max=footer["salary_max"],
            salary_currency="USD" if footer["salary_min"] is not None else None,
            salary_period="year" if footer["salary_min"] is not None else None,
            compensation_type="salary" if footer["salary_min"] is not None else None,
            location_specific_compensation=footer["ranges"],
            # Preserve the explicit United States scope of the official search
            # even when a detail location omits its country field.
            is_us_job=True,
        )
        job.source_payload_hash = sha256_value(job.complete_job_posting_json)
        job.content_hash = job_content_hash(job.to_dict())
        return job


def extract_hydration(payload: bytes) -> dict[str, Any]:
    text = payload.decode("utf-8")
    match = HYDRATION_PATTERN.search(text)
    if not match:
        raise ValueError("Apple page missing static router hydration data")
    decoded_string = json.loads(match.group(1))
    document = json.loads(decoded_string)
    if not isinstance(document, dict):
        raise ValueError("Apple hydration data was not an object")
    return document


def parse_search_payload(payload: bytes) -> tuple[list[dict[str, Any]], int]:
    loader = extract_hydration(payload).get("loaderData") or {}
    search = loader.get("search") if isinstance(loader, dict) else None
    if not isinstance(search, dict) or not isinstance(search.get("searchResults"), list):
        raise ValueError("Apple search page missing searchResults")
    records = [item for item in search["searchResults"] if isinstance(item, dict)]
    total = int(search.get("totalRecords") or len(records))
    return records, total


def parse_detail_payload(payload: bytes) -> dict[str, Any]:
    loader = extract_hydration(payload).get("loaderData") or {}
    details = loader.get("jobDetails") if isinstance(loader, dict) else None
    job = details.get("jobsData") if isinstance(details, dict) else None
    if not isinstance(job, dict):
        raise ValueError("Apple detail page missing jobsData")
    return job


def _artifact(response, name: str) -> RawArtifact:
    return RawArtifact(
        response.request_url, response.fetched_at, response.status, response.headers,
        response.body, name, response.headers.get("content-type"),
    )


def _error_artifact(error: HttpRequestError, name: str, fetched_at: str) -> RawArtifact:
    return RawArtifact(
        error.request_url, fetched_at, error.status, error.headers, error.body,
        name, error.headers.get("content-type"),
    )


def _string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _search_job_id(record: dict[str, Any]) -> str:
    requisition = _string(record.get("reqId"))
    if requisition and requisition.startswith("PIPE-"):
        return _string(record.get("positionId")) or requisition.removeprefix("PIPE-")
    return requisition or _string(record.get("jobNumber")) or ""


def _detail_job_id(record: dict[str, Any]) -> str:
    return _string(record.get("jobNumber")) or _string(record.get("reqId")) or ""


def _dedupe_search_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        source_job_id = _search_job_id(record)
        if source_job_id:
            unique[source_job_id] = record
    return list(unique.values())


def _locations(value: Any) -> list[Location]:
    result = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        raw = _string(item.get("name")) or _string(item.get("city")) or _string(item.get("countryName")) or ""
        result.append(Location(
            raw=raw,
            city=_string(item.get("city")),
            state=_string(item.get("stateProvince")),
            country=_string(item.get("countryName")),
            postal_code=_string(item.get("zipCode")),
        ))
    return result


def _is_us(locations: list[Location]) -> bool:
    return any(
        (location.country or "").strip().lower() in {
            "us", "usa", "united states", "united states of america",
        }
        for location in locations
    )


def _footer_entries(value: Any) -> list[dict[str, Any]]:
    entries = []
    for footer in value or []:
        if not isinstance(footer, dict):
            continue
        localizations = footer.get("localizations")
        if not isinstance(localizations, dict):
            continue
        for localized in localizations.values():
            if isinstance(localized, list):
                entries.extend(item for item in localized if isinstance(item, dict))
    return entries


def _parse_footers(value: Any, locations: list[Location]) -> dict[str, Any]:
    benefits_parts: list[str] = []
    eeo_parts: list[str] = []
    other_parts: list[str] = []
    ranges: list[dict[str, Any]] = []
    salary_parts: list[str] = []
    entries = _footer_entries(value)
    for item in entries:
        name = (_string(item.get("name")) or _string(item.get("label")) or "").lower()
        raw_html = _string(item.get("content"))
        text = _html_fragment_text(raw_html)
        if not text:
            continue
        if name in {"pay & benefits", "pay and benefits"}:
            benefits_parts.append(text)
            found = _salary_ranges(text)
            if found:
                salary_parts.append(text)
                for minimum, maximum in found:
                    ranges.append({
                        "location": "; ".join(location.raw for location in locations) or None,
                        "min": minimum,
                        "max": maximum,
                        "currency": "USD",
                        "period": "year",
                        "compensation_type": "salary",
                        "raw": text,
                    })
        elif "eeo" in name or "equal opportunity" in name:
            eeo_parts.append(text)
        else:
            other_parts.append(text)
    primary = ranges[0] if ranges else {}
    return {
        "benefits": clean_text("\n\n".join(benefits_parts)) if benefits_parts else None,
        "equal_opportunity_text": clean_text("\n\n".join(eeo_parts)) if eeo_parts else None,
        "other_footer_text": clean_text("\n\n".join(other_parts)) if other_parts else None,
        "salary_text_raw": clean_text("\n\n".join(salary_parts)) if salary_parts else None,
        "salary_min": primary.get("min"),
        "salary_max": primary.get("max"),
        "ranges": ranges,
    }


def _salary_ranges(value: str) -> list[tuple[float, float]]:
    patterns = [
        r"\$\s*([0-9][0-9,]*(?:\.\d+)?)\s*(?:-|–|—|to|and)\s*\$?\s*([0-9][0-9,]*(?:\.\d+)?)",
        r"between\s+\$\s*([0-9][0-9,]*(?:\.\d+)?)\s+and\s+\$\s*([0-9][0-9,]*(?:\.\d+)?)",
    ]
    found = []
    for pattern in patterns:
        for match in re.finditer(pattern, value, flags=re.IGNORECASE):
            item = (float(match.group(1).replace(",", "")), float(match.group(2).replace(",", "")))
            if item not in found:
                found.append(item)
    return found


def _html_fragment_text(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"</(?:p|li|div|h[1-6])\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return clean_text(unescape(text))
