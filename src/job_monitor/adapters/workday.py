from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from math import ceil
from pathlib import Path
import re
from typing import Any

from job_monitor.adapters.base import SourceAdapter
from job_monitor.archive import read_archive_manifest, read_archived_payload
from job_monitor.hashing import job_content_hash, sha256_value
from job_monitor.http import HttpRequestError, PoliteHttpClient
from job_monitor.models import FetchResult, Job, Location, RawArtifact
from job_monitor.refresh import should_refresh_detail
from job_monitor.text import clean_text, first_section, html_to_text_and_sections


class WorkdayAdapter(SourceAdapter):
    """Official public Workday CXS adapter shared by company-specific subclasses."""

    def fetch(self) -> FetchResult:
        client = PoliteHttpClient(self.settings)
        fetched_at = datetime.now(timezone.utc).isoformat()
        artifacts: list[RawArtifact] = []
        warnings: list[str] = []
        list_records: list[dict[str, Any]] = []
        first_response = None
        endpoint = self._list_endpoint()
        limit = int(self.company_config.get("page_size", 20))
        applied_facets = self.company_config.get("applied_facets") or {}

        try:
            first_response = client.post_json(endpoint, _list_request(0, limit, applied_facets))
            artifacts.append(_artifact(first_response, "list_page_00001"))
            first_document = _load_list(first_response.body)
            total = int(first_document["total"])
            list_records.extend(first_document["jobPostings"])
            for page, offset in enumerate(range(limit, total, limit), start=2):
                response = client.post_json(endpoint, _list_request(offset, limit, applied_facets))
                artifacts.append(_artifact(response, f"list_page_{page:05d}"))
                document = _load_list(response.body)
                reported_total = int(document["total"])
                # NVIDIA's filtered CXS response reports total=0 after page one
                # while still returning the requested postings. Treat that as a
                # pagination sentinel, not a count-drop warning.
                if reported_total not in {0, total}:
                    warnings.append(f"list_total_changed:{total}->{reported_total}:offset={offset}")
                    total = max(total, reported_total)
                list_records.extend(document["jobPostings"])
        except (HttpRequestError, ValueError, json.JSONDecodeError) as exc:
            if isinstance(exc, HttpRequestError):
                artifacts.append(_error_artifact(exc, f"list_error_{len(artifacts) + 1:05d}", fetched_at))
                status, headers, body = exc.status, exc.headers, exc.body
            else:
                status = first_response.status if first_response else None
                headers = first_response.headers if first_response else {}
                body = first_response.body if first_response else b""
            return FetchResult(
                self.company, endpoint, fetched_at, status, headers,
                first_response.body if first_response else body, [], warnings,
                f"{self.company} Workday list failed: {exc}", artifacts,
            )

        list_records = _dedupe_records(list_records)
        jobs: list[Job] = []
        detail_attempts = 0
        detail_failures = 0
        refresh_days = int(self.company_config.get("detail_refresh_days", self.settings.get("detail_refresh_days", 7)))
        recent_days = int(self.company_config.get("recent_detail_days", 2))
        for record in list_records:
            source_job_id = _list_job_id(record)
            cached = self.existing_jobs.get(source_job_id)
            estimated_posted = _estimate_posted(record.get("postedOn"), fetched_at)
            possible_us = bool(self.company_config.get("list_is_us")) or _looks_us(_string(record.get("locationsText")))
            should_fetch = possible_us and should_refresh_detail(
                source_job_id, estimated_posted, fetched_at,
                has_cached_detail=cached is not None,
                refresh_days=refresh_days, recent_days=recent_days,
            )
            if should_fetch:
                detail_attempts += 1
                detail_url = self._detail_endpoint(record)
                try:
                    response = client.get(detail_url, accept="application/json")
                    artifacts.append(_artifact(response, f"detail_{source_job_id}"))
                    detail_document = _load_detail(response.body)
                    jobs.append(self._job_from_detail(record, detail_document, fetched_at))
                    continue
                except (HttpRequestError, ValueError, json.JSONDecodeError) as exc:
                    detail_failures += 1
                    warnings.append(f"detail_failed:{source_job_id}:{type(exc).__name__}:{exc}")
                    if isinstance(exc, HttpRequestError):
                        artifacts.append(_error_artifact(exc, f"detail_error_{source_job_id}", fetched_at))
            if cached is not None:
                jobs.append(self._job_from_cache(cached, record, fetched_at))
            else:
                jobs.append(self._job_from_list(record, fetched_at, forced_us=bool(self.company_config.get("list_is_us"))))

        error = None
        if detail_attempts and detail_failures / detail_attempts > 0.20:
            error = f"{self.company} detail failure rate too high: {detail_failures}/{detail_attempts}"
        if len(jobs) != len(list_records):
            error = error or f"{self.company} parsed count mismatch: {len(jobs)}/{len(list_records)}"
        return FetchResult(
            self.company, endpoint, fetched_at,
            first_response.status if first_response else None,
            first_response.headers if first_response else {},
            first_response.body if first_response else b"",
            jobs, warnings, error, artifacts,
        )

    def parse_payload(self, payload: bytes, fetched_at: str) -> list[Job]:
        document = json.loads(payload.decode("utf-8"))
        if isinstance(document.get("jobPostings"), list):
            return [self._job_from_list(item, fetched_at, forced_us=bool(self.company_config.get("list_is_us"))) for item in document["jobPostings"]]
        if isinstance(document.get("jobPostingInfo"), dict):
            detail = document["jobPostingInfo"]
            return [self._job_from_detail(detail, document, fetched_at)]
        raise ValueError("Workday payload missing jobPostings or jobPostingInfo")

    def reparse_archive(self, path: Path, fetched_at: str) -> list[Job]:
        if path.name.endswith(".metadata.json"):
            manifest = read_archive_manifest(path)
            records: list[dict[str, Any]] = []
            details: dict[str, dict[str, Any]] = {}
            for item in manifest.get("artifacts") or []:
                name = str(item.get("suggested_name") or "")
                if "error_" in name:
                    continue
                payload = read_archived_payload(Path(item["path"]))
                if name.startswith("list_page_"):
                    records.extend(_load_list(payload)["jobPostings"])
                elif name.startswith("detail_"):
                    document = _load_detail(payload)
                    details[_detail_job_id(document["jobPostingInfo"])] = document
            jobs = []
            for record in _dedupe_records(records):
                source_job_id = _list_job_id(record)
                jobs.append(
                    self._job_from_detail(record, details[source_job_id], fetched_at)
                    if source_job_id in details
                    else self._job_from_list(record, fetched_at, forced_us=bool(self.company_config.get("list_is_us")))
                )
            return jobs
        return super().reparse_archive(path, fetched_at)

    def _list_endpoint(self) -> str:
        return f"https://{self.company_config['host']}/wday/cxs/{self.company_config['tenant']}/{self.company_config['site']}/jobs"

    def _detail_endpoint(self, record: dict[str, Any]) -> str:
        external_path = _string(record.get("externalPath"))
        if not external_path:
            raise ValueError("Workday list record missing externalPath")
        return f"https://{self.company_config['host']}/wday/cxs/{self.company_config['tenant']}/{self.company_config['site']}{external_path}"

    def _job_from_cache(self, cached: dict[str, Any], record: dict[str, Any], fetched_at: str) -> Job:
        job = Job.from_dict(cached)
        job.fetched_at = fetched_at
        job.title = clean_text(_string(record.get("title"))) or job.title
        job.normalized_title = _normalize_title(job.title)
        if _string(record.get("timeType")):
            job.employment_type = clean_text(_string(record.get("timeType")))
        job.complete_job_posting_json = {**job.complete_job_posting_json, "list": record}
        job.source_payload_hash = sha256_value(job.complete_job_posting_json)
        job.content_hash = job_content_hash(job.to_dict())
        return job

    def _job_from_list(self, record: dict[str, Any], fetched_at: str, *, forced_us: bool) -> Job:
        source_job_id = _list_job_id(record)
        title = clean_text(_string(record.get("title"))) or ""
        if not source_job_id or not title:
            raise ValueError("Workday list record missing id or title")
        raw_location = clean_text(_string(record.get("locationsText")))
        locations = _locations_from_values([raw_location], self.settings)
        source_url = self._public_url(record)
        posted = _estimate_posted(record.get("postedOn"), fetched_at)
        job = Job(
            company=self.company,
            source_name=self.company_config["source_name"],
            source_job_id=source_job_id,
            source_url=source_url,
            apply_url=source_url,
            canonical_url=source_url,
            source_adapter_version=self.company_config["adapter_version"],
            title=title,
            normalized_title=_normalize_title(title),
            employment_type=clean_text(_string(record.get("timeType"))),
            workplace_type="remote" if _contains_remote(raw_location) else "unknown",
            requisition_id=source_job_id,
            posted_at=posted,
            posted_at_raw=_string(record.get("postedOn")),
            posted_at_accuracy="estimated" if posted else None,
            fetched_at=fetched_at,
            location_raw=raw_location,
            locations=locations,
            primary_city=locations[0].city if locations else None,
            state=locations[0].state if locations else None,
            country=locations[0].country if locations else None,
            description_plain_text=None,
            complete_job_posting_json={"list": record},
            is_us_job=forced_us or _is_us_locations(locations),
            parser_warning="detail_not_available_in_this_snapshot",
        )
        job.source_payload_hash = sha256_value(job.complete_job_posting_json)
        job.content_hash = job_content_hash(job.to_dict())
        return job

    def _job_from_detail(self, list_record: dict[str, Any], document: dict[str, Any], fetched_at: str) -> Job:
        detail = document.get("jobPostingInfo")
        if not isinstance(detail, dict):
            raise ValueError("Workday detail missing jobPostingInfo")
        source_job_id = _detail_job_id(detail) or _list_job_id(list_record)
        title = clean_text(_string(detail.get("title")) or _string(list_record.get("title"))) or ""
        if not source_job_id or not title:
            raise ValueError("Workday detail missing id or title")
        raw_locations = [_string(detail.get("location"))]
        for value in detail.get("additionalLocations") or []:
            raw_locations.append(_string(value.get("descriptor")) if isinstance(value, dict) else _string(value))
        locations = _locations_from_values(raw_locations, self.settings)
        description_html = _string(detail.get("jobDescription"))
        description_text, sections = html_to_text_and_sections(description_html)
        compensation = _compensation_ranges(description_text or "", locations)
        primary = compensation[0] if compensation else {}
        source_url = _string(detail.get("externalUrl")) or self._public_url(list_record)
        country = detail.get("country") if isinstance(detail.get("country"), dict) else {}
        requisition_location = detail.get("jobRequisitionLocation") if isinstance(detail.get("jobRequisitionLocation"), dict) else {}
        requisition_country = requisition_location.get("country") if isinstance(requisition_location.get("country"), dict) else {}
        is_us = _is_us_locations(locations) or _country_is_us(country) or _country_is_us(requisition_country)
        job = Job(
            company=self.company,
            source_name=self.company_config["source_name"],
            source_job_id=source_job_id,
            source_url=source_url,
            apply_url=source_url,
            canonical_url=source_url,
            source_adapter_version=self.company_config["adapter_version"],
            title=title,
            normalized_title=_normalize_title(title),
            employment_type=clean_text(_string(detail.get("timeType")) or _string(list_record.get("timeType"))),
            workplace_type="remote" if any(_contains_remote(location.raw) for location in locations) else "unknown",
            requisition_id=clean_text(_string(detail.get("jobReqId"))) or source_job_id,
            posted_at=_date_at_utc(_string(detail.get("startDate"))),
            posted_at_raw=_string(detail.get("postedOn")) or _string(list_record.get("postedOn")),
            posted_at_accuracy="exact" if detail.get("startDate") else "estimated" if _estimate_posted(detail.get("postedOn"), fetched_at) else None,
            valid_through=_date_at_utc(_string(detail.get("endDate"))),
            closing_date=_date_at_utc(_string(detail.get("endDate"))),
            fetched_at=fetched_at,
            location_raw="; ".join(location.raw for location in locations) or None,
            locations=locations,
            primary_city=locations[0].city if locations else None,
            state=locations[0].state if locations else None,
            country=locations[0].country if locations else None,
            description_raw_html=description_html,
            description_plain_text=description_text,
            responsibilities=first_section(sections, {"responsibilities", "key responsibilities", "what you ll be doing", "what you will be doing"}),
            minimum_qualifications=first_section(sections, {"minimum qualifications", "what we need to see", "requirements qualifications", "requirements and qualifications"}),
            preferred_qualifications=first_section(sections, {"preferred qualifications", "ways to stand out", "ways to stand out from the crowd"}),
            benefits=first_section(sections, {"benefits", "compensation and benefits"}),
            complete_job_posting_json={"list": list_record, "detail": document},
            salary_text_raw="\n".join(item["raw"] for item in compensation) or None,
            salary_min=primary.get("min"),
            salary_max=primary.get("max"),
            salary_currency=primary.get("currency"),
            salary_period=primary.get("period"),
            compensation_type="salary" if primary else None,
            location_specific_compensation=compensation,
            is_us_job=is_us,
        )
        job.source_payload_hash = sha256_value(job.complete_job_posting_json)
        job.content_hash = job_content_hash(job.to_dict())
        return job

    def _public_url(self, record: dict[str, Any]) -> str | None:
        external_path = _string(record.get("externalPath"))
        if not external_path:
            return None
        return f"https://{self.company_config['host']}/{self.company_config['site']}{external_path}"


class BroadcomAdapter(WorkdayAdapter):
    company = "broadcom"


class NvidiaAdapter(WorkdayAdapter):
    company = "nvidia"


def _list_request(offset: int, limit: int, facets: dict[str, Any]) -> dict[str, Any]:
    return {"appliedFacets": facets, "limit": limit, "offset": offset, "searchText": ""}


def _load_list(payload: bytes) -> dict[str, Any]:
    document = json.loads(payload.decode("utf-8"))
    if not isinstance(document.get("jobPostings"), list) or not isinstance(document.get("total"), int):
        raise ValueError("Workday list response missing total or jobPostings")
    return document


def _load_detail(payload: bytes) -> dict[str, Any]:
    document = json.loads(payload.decode("utf-8"))
    if not isinstance(document.get("jobPostingInfo"), dict):
        raise ValueError("Workday detail response missing jobPostingInfo")
    return document


def _artifact(response, name: str) -> RawArtifact:
    return RawArtifact(response.request_url, response.fetched_at, response.status, response.headers, response.body, name, "application/json")


def _error_artifact(error: HttpRequestError, name: str, fetched_at: str) -> RawArtifact:
    return RawArtifact(error.request_url, fetched_at, error.status, error.headers, error.body, name, error.headers.get("content-type"))


def _string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _list_job_id(record: dict[str, Any]) -> str:
    bullet = record.get("bulletFields")
    if isinstance(bullet, list):
        for item in bullet:
            if _string(item):
                return _string(item) or ""
    path = _string(record.get("externalPath")) or ""
    match = re.search(r"_([A-Za-z]+\d+)(?:$|[/?])", path)
    return match.group(1) if match else ""


def _detail_job_id(record: dict[str, Any]) -> str:
    return _string(record.get("jobReqId")) or _string(record.get("id")) or ""


def _dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = {}
    for record in records:
        source_job_id = _list_job_id(record)
        if source_job_id:
            unique[source_job_id] = record
    return list(unique.values())


def _normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _estimate_posted(value: Any, fetched_at: str) -> str | None:
    text = (_string(value) or "").lower()
    observed = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    if "today" in text:
        days = 0
    elif "yesterday" in text:
        days = 1
    else:
        match = re.search(r"(\d+)\+?\s+days?\s+ago", text)
        if not match:
            return None
        days = int(match.group(1))
    return (observed - timedelta(days=days)).date().isoformat() + "T00:00:00+00:00"


def _date_at_utc(value: str | None) -> str | None:
    return f"{value}T00:00:00+00:00" if value else None


def _looks_us(value: str | None) -> bool:
    text = (value or "").strip().lower()
    return bool(re.search(r"^(us|usa|united states)(?:\b|[-,])", text))


def _contains_remote(value: str | None) -> bool:
    return bool(re.search(r"\bremote\b", value or "", flags=re.IGNORECASE))


def _locations_from_values(values: list[str | None], settings: dict) -> list[Location]:
    return [_parse_location(value, settings) for value in values if value]


def _parse_location(raw: str, settings: dict) -> Location:
    normalized = re.sub(r"[^a-z0-9]+", " ", raw.lower()).strip()
    city = None
    for allowed in sorted(settings["location"]["eligible_cities"], key=len, reverse=True):
        candidate = re.sub(r"[^a-z0-9]+", " ", allowed.lower()).strip()
        if re.search(rf"\b{re.escape(candidate)}\b", normalized):
            city = allowed.replace("San José", "San Jose")
            break
    state = None
    state_match = re.search(r"(?:^|[-, ])(CA|California)(?:$|[-, ])", raw, flags=re.IGNORECASE)
    if state_match:
        state = "California"
    country = "United States" if _looks_us(raw) else None
    return Location(raw=raw, city=city, state=state, country=country)


def _is_us_locations(locations: list[Location]) -> bool:
    return any((location.country or "").lower() in {"us", "usa", "united states", "united states of america"} for location in locations)


def _country_is_us(country: dict[str, Any]) -> bool:
    descriptor = (_string(country.get("descriptor")) or "").lower()
    alpha2 = (_string(country.get("alpha2Code")) or "").upper()
    return alpha2 == "US" or descriptor in {"united states", "united states of america", "usa", "us"}


def _compensation_ranges(text: str, locations: list[Location]) -> list[dict[str, Any]]:
    patterns = [
        r"USD\s*([0-9][0-9,]*(?:\.\d+)?)\s*(?:to|-|–|—)\s*USD\s*([0-9][0-9,]*(?:\.\d+)?)",
        r"([0-9][0-9,]*(?:\.\d+)?)\s*USD\s*(?:to|-|–|—)\s*([0-9][0-9,]*(?:\.\d+)?)\s*USD",
        r"\$\s*([0-9][0-9,]*(?:\.\d+)?)\s*(?:to|-|–|—|and)\s*\$?\s*([0-9][0-9,]*(?:\.\d+)?)",
    ]
    ranges: list[dict[str, Any]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            item = {
                "location": "; ".join(location.raw for location in locations) or None,
                "min": float(match.group(1).replace(",", "")),
                "max": float(match.group(2).replace(",", "")),
                "currency": "USD",
                "period": "year",
                "compensation_type": "salary",
                "raw": match.group(0),
            }
            if not any(existing["min"] == item["min"] and existing["max"] == item["max"] for existing in ranges):
                ranges.append(item)
    return ranges
