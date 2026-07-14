from __future__ import annotations

from datetime import datetime, timezone
from html import unescape
import json
from pathlib import Path
import re
from typing import Any
from xml.etree import ElementTree

from job_monitor.adapters.base import SourceAdapter
from job_monitor.archive import read_archive_manifest, read_archived_payload
from job_monitor.hashing import job_content_hash, sha256_value
from job_monitor.http import HttpRequestError, PoliteHttpClient
from job_monitor.models import FetchResult, Job, Location, RawArtifact
from job_monitor.refresh import should_refresh_detail
from job_monitor.text import clean_text


JSON_LD_PATTERN = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


class MetaAdapter(SourceAdapter):
    company = "meta"

    def fetch(self) -> FetchResult:
        client = PoliteHttpClient(self.settings)
        fetched_at = datetime.now(timezone.utc).isoformat()
        sitemap_url = self.company_config["sitemap_url"]
        artifacts: list[RawArtifact] = []
        warnings: list[str] = []
        try:
            sitemap_response = client.get(sitemap_url, accept="application/xml,text/xml")
            artifacts.append(_artifact(sitemap_response, "sitemap"))
            entries = parse_sitemap(sitemap_response.body)
        except (HttpRequestError, ValueError, ElementTree.ParseError) as exc:
            if isinstance(exc, HttpRequestError):
                artifacts.append(_error_artifact(exc, "sitemap_error", fetched_at))
                status, headers, body = exc.status, exc.headers, exc.body
            else:
                status = sitemap_response.status if "sitemap_response" in locals() else None
                headers = sitemap_response.headers if "sitemap_response" in locals() else {}
                body = sitemap_response.body if "sitemap_response" in locals() else b""
            return FetchResult(
                self.company, sitemap_url, fetched_at, status, headers, body,
                [], warnings, f"Meta sitemap failed: {exc}", artifacts,
            )

        jobs: list[Job] = []
        detail_attempts = 0
        detail_failures = 0
        refresh_days = int(self.company_config.get("detail_refresh_days", self.settings.get("detail_refresh_days", 7)))
        for entry in entries:
            source_job_id = _job_id_from_url(entry["url"])
            cached = self.existing_jobs.get(source_job_id)
            refresh = should_refresh_detail(
                source_job_id,
                cached.get("posted_at") if cached else None,
                fetched_at,
                has_cached_detail=cached is not None,
                refresh_days=refresh_days,
                recent_days=int(self.company_config.get("recent_detail_days", 2)),
            )
            if refresh:
                detail_attempts += 1
                try:
                    response = client.get(entry["url"], accept="text/html")
                    artifacts.append(_artifact(response, f"detail_{source_job_id}"))
                    record = parse_job_posting(response.body)
                    jobs.append(self._job_from_record(source_job_id, entry, record, response.body, response.request_url, fetched_at))
                    continue
                except (HttpRequestError, ValueError, json.JSONDecodeError) as exc:
                    detail_failures += 1
                    warnings.append(f"detail_failed:{source_job_id}:{type(exc).__name__}:{exc}")
                    if isinstance(exc, HttpRequestError):
                        artifacts.append(_error_artifact(exc, f"detail_error_{source_job_id}", fetched_at))
            if cached is not None:
                job = Job.from_dict(cached)
                job.fetched_at = fetched_at
                job.source_url = entry["url"]
                job.canonical_url = entry["url"]
                job.complete_job_posting_json = {**job.complete_job_posting_json, "sitemap": entry}
                job.source_payload_hash = sha256_value(job.complete_job_posting_json)
                job.content_hash = job_content_hash(job.to_dict())
                jobs.append(job)

        error = None
        if detail_attempts and detail_failures / detail_attempts > 0.20:
            error = f"Meta detail failure rate too high: {detail_failures}/{detail_attempts}"
        if len(jobs) != len(entries):
            warnings.append(f"parsed_count_differs_from_sitemap:{len(jobs)}/{len(entries)}")
            if len(entries) and (len(entries) - len(jobs)) / len(entries) > 0.20:
                error = error or f"Meta parsed count too low: {len(jobs)}/{len(entries)}"
        return FetchResult(
            self.company, sitemap_url, fetched_at, sitemap_response.status,
            sitemap_response.headers, sitemap_response.body, jobs, warnings, error, artifacts,
        )

    def parse_payload(self, payload: bytes, fetched_at: str) -> list[Job]:
        record = parse_job_posting(payload)
        source_url = _string(record.get("url")) or "https://www.metacareers.com/"
        source_job_id = _job_id_from_url(source_url)
        if not source_job_id:
            source_job_id = _string(record.get("identifier")) or "unknown"
        return [self._job_from_record(source_job_id, {"url": source_url, "lastmod": None}, record, payload, source_url, fetched_at)]

    def reparse_archive(self, path: Path, fetched_at: str) -> list[Job]:
        if path.name.endswith(".metadata.json"):
            manifest = read_archive_manifest(path)
            entries: dict[str, dict[str, str | None]] = {}
            details: dict[str, tuple[dict[str, Any], bytes, str]] = {}
            for item in manifest.get("artifacts") or []:
                name = str(item.get("suggested_name") or "")
                if "error_" in name:
                    continue
                payload = read_archived_payload(Path(item["path"]))
                if name == "sitemap":
                    for entry in parse_sitemap(payload):
                        entries[_job_id_from_url(entry["url"])] = entry
                elif name.startswith("detail_"):
                    source_job_id = name.removeprefix("detail_")
                    details[source_job_id] = (parse_job_posting(payload), payload, item.get("request_url") or "")
            jobs = []
            for source_job_id, entry in entries.items():
                if source_job_id not in details:
                    continue
                record, payload, url = details[source_job_id]
                jobs.append(self._job_from_record(source_job_id, entry, record, payload, url, fetched_at))
            return jobs
        return super().reparse_archive(path, fetched_at)

    def _job_from_record(
        self,
        source_job_id: str,
        sitemap_entry: dict[str, str | None],
        record: dict[str, Any],
        raw_html: bytes,
        response_url: str,
        fetched_at: str,
    ) -> Job:
        title = clean_text(_string(record.get("title"))) or ""
        if not source_job_id or not title:
            raise ValueError("Meta JobPosting missing id or title")
        locations = _locations(record.get("jobLocation"))
        full_text = clean_text("\n\n".join(filter(None, [
            _structured_text(record.get("description")),
            _structured_text(record.get("responsibilities")),
            _structured_text(record.get("qualifications")),
        ])))
        salary = _salary(record.get("baseSalary") or record.get("estimatedSalary"))
        source_url = response_url or sitemap_entry["url"]
        workplace = "remote" if str(record.get("jobLocationType") or "").upper() == "TELECOMMUTE" else "unknown"
        job = Job(
            company=self.company,
            source_name=self.company_config["source_name"],
            source_job_id=source_job_id,
            source_url=source_url,
            apply_url=source_url,
            canonical_url=source_url,
            source_adapter_version=self.company_config["adapter_version"],
            title=title,
            normalized_title=re.sub(r"\s+", " ", title).strip(),
            employment_type=clean_text(_employment_type(record.get("employmentType"))),
            workplace_type=workplace,
            requisition_id=source_job_id,
            posted_at=_string(record.get("datePosted")),
            posted_at_raw=_string(record.get("datePosted")),
            posted_at_accuracy="exact" if record.get("datePosted") else None,
            valid_through=_string(record.get("validThrough")),
            closing_date=_string(record.get("validThrough")),
            fetched_at=fetched_at,
            location_raw="; ".join(location.raw for location in locations) or None,
            locations=locations,
            primary_city=locations[0].city if locations else None,
            state=locations[0].state if locations else None,
            country=locations[0].country if locations else None,
            postal_code=locations[0].postal_code if locations else None,
            street_address=locations[0].street_address if locations else None,
            description_raw_html=None,
            description_plain_text=full_text,
            responsibilities=_structured_text(record.get("responsibilities")),
            required_qualifications=_structured_text(record.get("qualifications")),
            complete_job_posting_json={"sitemap": sitemap_entry, "job_posting": record},
            salary_text_raw=salary.get("raw"),
            salary_min=salary.get("min"),
            salary_max=salary.get("max"),
            salary_currency=salary.get("currency"),
            salary_period=salary.get("period"),
            compensation_type="salary" if salary else None,
            location_specific_compensation=[salary] if salary else [],
            is_us_job=_is_us(locations),
        )
        job.source_payload_hash = sha256_value(job.complete_job_posting_json)
        job.content_hash = job_content_hash(job.to_dict())
        return job


def parse_sitemap(payload: bytes) -> list[dict[str, str | None]]:
    root = ElementTree.fromstring(payload)
    entries = []
    for node in root:
        values = {child.tag.rsplit("}", 1)[-1]: child.text for child in node}
        url = _string(values.get("loc"))
        if url and _job_id_from_url(url):
            entries.append({"url": url, "lastmod": _string(values.get("lastmod"))})
    if not entries:
        raise ValueError("Meta sitemap did not contain job URLs")
    return entries


def parse_job_posting(payload: bytes) -> dict[str, Any]:
    text = payload.decode("utf-8")
    for match in JSON_LD_PATTERN.finditer(text):
        document = json.loads(match.group(1).strip())
        candidates = document if isinstance(document, list) else [document]
        for candidate in candidates:
            if isinstance(candidate, dict) and _has_type(candidate, "JobPosting"):
                return candidate
    raise ValueError("Meta page missing JobPosting JSON-LD")


def _has_type(value: dict[str, Any], expected: str) -> bool:
    types = value.get("@type")
    return expected in (types if isinstance(types, list) else [types])


def _job_id_from_url(value: str) -> str:
    match = re.search(r"/job_details/(\d+)", value or "")
    return match.group(1) if match else ""


def _structured_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = re.sub(r"&nbsp;", "\n", value, flags=re.IGNORECASE)
    return clean_text(unescape(value))


def _string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _employment_type(value: Any) -> str | None:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return _string(value)


def _locations(value: Any) -> list[Location]:
    records = value if isinstance(value, list) else [value]
    locations = []
    for record in records:
        if not isinstance(record, dict):
            continue
        address = record.get("address") if isinstance(record.get("address"), dict) else {}
        country = address.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name")
        if isinstance(country, list):
            country = ", ".join(str(item) for item in country)
        raw = _string(record.get("name")) or ", ".join(filter(None, [_string(address.get("addressLocality")), _string(address.get("addressRegion"))]))
        locations.append(Location(
            raw=raw,
            city=_string(address.get("addressLocality")),
            state=_string(address.get("addressRegion")),
            country=_string(country),
            postal_code=_string(address.get("postalCode")),
            street_address=_string(address.get("streetAddress")),
        ))
    return locations


def _is_us(locations: list[Location]) -> bool:
    return any((location.country or "").lower() in {"us", "usa", "united states", "united states of america"} for location in locations)


def _salary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    currency = _string(value.get("currency"))
    quantitative = value.get("value") if isinstance(value.get("value"), dict) else value
    minimum = _number(quantitative.get("minValue"))
    maximum = _number(quantitative.get("maxValue"))
    exact = _number(quantitative.get("value"))
    if minimum is None and exact is not None:
        minimum = maximum = exact
    if minimum is None and maximum is None:
        return {}
    period = (_string(quantitative.get("unitText")) or "").lower() or None
    return {
        "location": None,
        "min": minimum,
        "max": maximum,
        "currency": currency,
        "period": period,
        "compensation_type": "salary",
        "raw": json.dumps(value, ensure_ascii=False, sort_keys=True),
    }


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _artifact(response, name: str) -> RawArtifact:
    return RawArtifact(response.request_url, response.fetched_at, response.status, response.headers, response.body, name, response.headers.get("content-type"))


def _error_artifact(error: HttpRequestError, name: str, fetched_at: str) -> RawArtifact:
    return RawArtifact(error.request_url, fetched_at, error.status, error.headers, error.body, name, error.headers.get("content-type"))
