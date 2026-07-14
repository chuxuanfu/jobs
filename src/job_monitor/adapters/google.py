from __future__ import annotations

from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from job_monitor.adapters.base import SourceAdapter
from job_monitor.archive import read_archive_manifest, read_archived_payload
from job_monitor.hashing import job_content_hash, sha256_value
from job_monitor.http import HttpRequestError, PoliteHttpClient
from job_monitor.models import FetchResult, Job, Location, RawArtifact
from job_monitor.text import clean_text, first_section, html_to_text_and_sections


JOB_PATH_PATTERN = re.compile(r"/jobs/results/(\d+)(?:[-/?#]|$)")
MAIN_PATTERN = re.compile(r"<main\b[^>]*>(.*?)</main>", re.IGNORECASE | re.DOTALL)
H1_PATTERN = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
SALARY_PATTERN = re.compile(
    r"(?P<label>[A-Z][A-Za-z /,&-]{0,80}:\s*)?"
    r"\$(?P<minimum>[\d,]+(?:\.\d+)?)\s*[-–—]\s*"
    r"\$(?P<maximum>[\d,]+(?:\.\d+)?)\s*"
    r"\((?P<currency>[A-Z]{3})\)(?P<extra>[^\n]*)",
)


class GoogleAdapter(SourceAdapter):
    """Incremental discovery from Google's allowed, non-paginated first page.

    The configured official search is already filtered to Full-time and the
    San Francisco Bay Area and sorted by date. Only the first page is read.
    Missing IDs are never treated as closed because this is not a full snapshot.
    """

    company = "google"

    def fetch(self) -> FetchResult:
        client = PoliteHttpClient(self.settings)
        fetched_at = datetime.now(timezone.utc).isoformat()
        search_url = self.company_config["search_url"]
        maximum = int(self.company_config.get("max_results_per_run", 20))
        warnings = [
            "coverage_incremental_first_page_only",
            "baseline_disabled",
            "closure_tracking_disabled",
        ]
        artifacts: list[RawArtifact] = []
        try:
            search_response = client.get(search_url, accept="text/html")
            artifacts.append(_artifact(search_response, "search_first_page"))
            records = parse_search_payload(search_response.body, search_url, maximum)
        except (HttpRequestError, ValueError) as exc:
            if isinstance(exc, HttpRequestError):
                artifacts.append(_error_artifact(exc, "search_error", fetched_at))
                status, headers, body = exc.status, exc.headers, exc.body
            else:
                status = search_response.status if "search_response" in locals() else None
                headers = search_response.headers if "search_response" in locals() else {}
                body = search_response.body if "search_response" in locals() else b""
            return FetchResult(
                self.company, search_url, fetched_at, status, headers, body, [], warnings,
                f"Google first-page search failed: {exc}", artifacts,
                source_item_count=0, parsed_item_count=0, snapshot_complete=False,
            )

        visible_ids = [record["source_job_id"] for record in records]
        if self.initial_seed_only:
            warnings.append(f"initial_seed_registered_without_jobs:{len(visible_ids)}")
            return FetchResult(
                self.company, search_url, fetched_at, search_response.status,
                search_response.headers, search_response.body, [], warnings, None, artifacts,
                source_item_count=len(records), parsed_item_count=len(records),
                discovered_source_ids=visible_ids, snapshot_complete=False,
            )

        new_records = [record for record in records if record["source_job_id"] not in self.seen_source_ids]
        warnings.append(f"new_ids_in_first_page:{len(new_records)}")
        jobs: list[Job] = []
        successful_new_ids: list[str] = []
        failures = 0
        for record in new_records:
            source_job_id = record["source_job_id"]
            try:
                response = client.get(record["source_url"], accept="text/html")
                artifacts.append(_artifact(response, f"detail_{source_job_id}"))
                jobs.append(self._job_from_detail(record, response.body, response.request_url, fetched_at))
                successful_new_ids.append(source_job_id)
            except (HttpRequestError, ValueError, json.JSONDecodeError) as exc:
                failures += 1
                warnings.append(f"detail_failed:{source_job_id}:{type(exc).__name__}:{exc}")
                if isinstance(exc, HttpRequestError):
                    artifacts.append(_error_artifact(exc, f"detail_error_{source_job_id}", fetched_at))

        error = None
        if new_records and failures / len(new_records) > 0.20:
            error = f"Google detail failure rate too high: {failures}/{len(new_records)}"
        already_seen_visible = [source_job_id for source_job_id in visible_ids if source_job_id in self.seen_source_ids]
        return FetchResult(
            self.company, search_url, fetched_at, search_response.status,
            search_response.headers, search_response.body, jobs, warnings, error, artifacts,
            source_item_count=len(records), parsed_item_count=len(records),
            discovered_source_ids=[*already_seen_visible, *successful_new_ids],
            snapshot_complete=False,
        )

    def parse_payload(self, payload: bytes, fetched_at: str) -> list[Job]:
        canonical_url = _canonical_url(payload)
        match = JOB_PATH_PATTERN.search(canonical_url)
        if not match:
            raise ValueError("Google detail page missing canonical job id")
        title = _title_from_detail(payload)
        record = {
            "source_job_id": match.group(1),
            "source_url": canonical_url,
            "title": title,
        }
        return [self._job_from_detail(record, payload, canonical_url, fetched_at)]

    def reparse_archive(self, path: Path, fetched_at: str) -> list[Job]:
        if not path.name.endswith(".metadata.json"):
            return super().reparse_archive(path, fetched_at)
        manifest = read_archive_manifest(path)
        records: dict[str, dict[str, str]] = {}
        details: dict[str, tuple[bytes, str]] = {}
        for item in manifest.get("artifacts") or []:
            name = str(item.get("suggested_name") or "")
            if "error" in name:
                continue
            payload = read_archived_payload(Path(item["path"]))
            if name == "search_first_page":
                for record in parse_search_payload(
                    payload,
                    item.get("request_url") or self.company_config["search_url"],
                    int(self.company_config.get("max_results_per_run", 20)),
                ):
                    records[record["source_job_id"]] = record
            elif name.startswith("detail_"):
                source_job_id = name.removeprefix("detail_")
                details[source_job_id] = (payload, item.get("request_url") or "")
        jobs = []
        for source_job_id, (payload, response_url) in details.items():
            record = records.get(source_job_id) or {
                "source_job_id": source_job_id,
                "source_url": response_url,
                "title": _title_from_detail(payload),
            }
            jobs.append(self._job_from_detail(record, payload, response_url, fetched_at))
        return jobs

    def _job_from_detail(
        self,
        search_record: dict[str, str],
        payload: bytes,
        response_url: str,
        fetched_at: str,
    ) -> Job:
        source_job_id = search_record["source_job_id"]
        main_html = _main_html(payload)
        full_text, sections = html_to_text_and_sections(main_html)
        title = _title_from_detail(payload) or search_record.get("title") or ""
        if not title or not full_text:
            raise ValueError("Google detail page missing title or complete description")
        locations, has_office, has_remote = _locations_from_text(full_text)
        if has_remote and has_office:
            workplace_type = "hybrid"
        elif has_remote:
            workplace_type = "remote"
        else:
            workplace_type = "onsite"
        compensation = _compensation(full_text)
        canonical_url = _strip_query(response_url or search_record["source_url"])
        apply_url = _apply_url(payload) or canonical_url
        level = next((line for line in full_text.splitlines()[:20] if line in {"Early", "Mid", "Advanced", "Director+"}), None)
        official_payload = {
            "search_record": search_record,
            "official_search_filter": self.company_config["search_url"],
            "parsed_section_headings": list(sections),
            "coverage_mode": "incremental_first_page_only",
        }
        job = Job(
            company=self.company,
            source_name=self.company_config["source_name"],
            source_job_id=source_job_id,
            source_url=canonical_url,
            apply_url=apply_url,
            canonical_url=canonical_url,
            source_adapter_version=self.company_config["adapter_version"],
            title=clean_text(title) or title,
            normalized_title=re.sub(r"\s+", " ", title).strip(),
            employment_type="Full-time",
            workplace_type=workplace_type,
            requisition_id=source_job_id,
            level=level,
            posted_at=None,
            posted_at_raw=None,
            posted_at_accuracy=None,
            fetched_at=fetched_at,
            location_raw="; ".join(location.raw for location in locations) or None,
            locations=locations,
            primary_city=locations[0].city if locations else None,
            state=locations[0].state if locations else None,
            country=locations[0].country if locations else None,
            description_raw_html=main_html,
            description_plain_text=full_text,
            responsibilities=first_section(sections, {"responsibilities"}),
            minimum_qualifications=first_section(sections, {"minimum qualifications"}),
            preferred_qualifications=first_section(sections, {"preferred qualifications"}),
            complete_job_posting_json=official_payload,
            salary_text_raw=compensation.get("raw"),
            salary_min=compensation.get("min"),
            salary_max=compensation.get("max"),
            salary_currency=compensation.get("currency"),
            salary_period=None,
            compensation_type="salary" if compensation else None,
            bonus_text=compensation.get("bonus"),
            equity_text=compensation.get("equity"),
            location_specific_compensation=compensation.get("ranges", []),
            is_us_job=any(location.country == "USA" for location in locations),
            parser_warning="posted_date_not_provided;incremental_first_page_only;closure_not_tracked",
        )
        job.source_payload_hash = sha256_value(payload.decode("utf-8", errors="replace"))
        job.content_hash = job_content_hash(job.to_dict())
        return job


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self.canonical: str | None = None
        self._active: dict[str, str] | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        if tag.lower() == "link" and "canonical" in values.get("rel", "").lower() and values.get("href"):
            self.canonical = values["href"]
        if tag.lower() == "a" and values.get("href"):
            self._active = values
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._active is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._active is not None:
            self.links.append({**self._active, "text": clean_text("".join(self._parts)) or ""})
            self._active = None
            self._parts = []


def parse_search_payload(payload: bytes, base_url: str, maximum: int = 20) -> list[dict[str, str]]:
    parser = _LinkParser()
    parser.feed(_main_html(payload))
    parser.close()
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in parser.links:
        absolute_url = urljoin(base_url, unescape(link["href"]))
        match = JOB_PATH_PATTERN.search(urlsplit(absolute_url).path)
        if not match or match.group(1) in seen:
            continue
        label = clean_text(link.get("aria-label")) or ""
        title = re.sub(r"^Learn more about\s+", "", label, flags=re.IGNORECASE) or link.get("text") or ""
        if not title:
            continue
        source_job_id = match.group(1)
        records.append({
            "source_job_id": source_job_id,
            "source_url": _strip_query(absolute_url),
            "title": title,
        })
        seen.add(source_job_id)
        if len(records) >= maximum:
            break
    if not records:
        raise ValueError("Google search page did not contain official job links")
    return records


def _main_html(payload: bytes) -> str:
    text = payload.decode("utf-8", errors="replace")
    match = MAIN_PATTERN.search(text)
    if not match:
        raise ValueError("Google page missing main content")
    return match.group(1)


def _title_from_detail(payload: bytes) -> str:
    match = H1_PATTERN.search(_main_html(payload))
    if not match:
        return ""
    title, _ = html_to_text_and_sections(match.group(1))
    return title or ""


def _links(payload: bytes) -> _LinkParser:
    parser = _LinkParser()
    parser.feed(payload.decode("utf-8", errors="replace"))
    parser.close()
    return parser


def _canonical_url(payload: bytes) -> str:
    parser = _links(payload)
    return parser.canonical or ""


def _apply_url(payload: bytes) -> str | None:
    for link in _links(payload).links:
        if (link.get("aria-label") or "").strip().lower() == "apply":
            return unescape(link["href"])
    return None


def _locations_from_text(text: str) -> tuple[list[Location], bool, bool]:
    office_values: list[str] = []
    remote_values: list[str] = []
    office = re.search(r"In-office locations:\s*(.+?)(?=\nRemote location\(s\):|\nMinimum qualifications:|$)", text, re.DOTALL)
    remote = re.search(r"Remote location\(s\):\s*(.+?)(?=\nMinimum qualifications:|$)", text, re.DOTALL)
    if office:
        office_values = _split_locations(office.group(1))
    if remote:
        remote_values = _split_locations(remote.group(1))
    if not office_values and not remote_values:
        header = text[:1200]
        office_values = list(dict.fromkeys(
            f"{city}, {state}, USA"
            for city, state in re.findall(r"\b([A-Z][A-Za-z .'-]+),\s*([A-Z]{2}),\s*USA\b", header)
        ))
    locations = [_location(value, remote=False) for value in office_values]
    locations.extend(_location(value, remote=True) for value in remote_values)
    return locations, bool(office_values), bool(remote_values)


def _split_locations(value: str) -> list[str]:
    value = value.strip().rstrip(".")
    return [item.strip().rstrip(".") for item in value.split(";") if item.strip()]


def _location(value: str, *, remote: bool) -> Location:
    pieces = [piece.strip() for piece in value.split(",")]
    country = "USA" if pieces and pieces[-1].lower() in {"usa", "us", "united states"} else None
    city = pieces[0] if len(pieces) >= 3 and len(pieces[-2]) == 2 else None
    state = pieces[-2] if len(pieces) >= 2 and country else None
    raw = f"Remote - {value}" if remote else value
    return Location(raw=raw, city=city, state=state, country=country)


def _compensation(text: str) -> dict[str, Any]:
    ranges: list[dict[str, Any]] = []
    raw_values: list[str] = []
    for match in SALARY_PATTERN.finditer(text):
        raw = clean_text(match.group(0)) or match.group(0)
        minimum = float(match.group("minimum").replace(",", ""))
        maximum = float(match.group("maximum").replace(",", ""))
        label = clean_text((match.group("label") or "").rstrip(": "))
        extra = clean_text(match.group("extra"))
        ranges.append({
            "location": label,
            "min": minimum,
            "max": maximum,
            "currency": match.group("currency"),
            "period": None,
            "compensation_type": "salary",
            "raw": raw,
        })
        raw_values.append(raw)
    if not ranges:
        return {}
    first = ranges[0]
    combined = "\n".join(dict.fromkeys(raw_values))
    return {
        "raw": combined,
        "min": first["min"],
        "max": first["max"],
        "currency": first["currency"],
        "bonus": combined if re.search(r"\bbonus\b", combined, re.IGNORECASE) else None,
        "equity": combined if re.search(r"\bequity\b", combined, re.IGNORECASE) else None,
        "ranges": ranges,
    }


def _strip_query(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def _artifact(response, name: str) -> RawArtifact:
    return RawArtifact(response.request_url, response.fetched_at, response.status, response.headers, response.body, name, response.headers.get("content-type"))


def _error_artifact(error: HttpRequestError, name: str, fetched_at: str) -> RawArtifact:
    return RawArtifact(error.request_url, fetched_at, error.status, error.headers, error.body, name, error.headers.get("content-type"))
