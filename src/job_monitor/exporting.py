from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from job_monitor.archive import write_json
from job_monitor.storage import CompanyDatabase


def export_company(database: CompanyDatabase, company: str, results_dir: Path, mode: str = "current", since: str | None = None) -> Path:
    jobs = [_result_job(company, job) for job in database.query_jobs(mode=mode, since=since)]
    suffix = {
        "current": "open_eligible_jobs",
        "all_open": "all_open_jobs",
        "new_since": "new_jobs",
        "updated": "updated_jobs",
        "closed": "closed_jobs",
        "review": "location_review_jobs",
    }[mode]
    path = results_dir / f"{company}_{suffix}.json"
    document = {
        "schema_version": 2,
        "company_schema": f"{company}.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "company": company,
        "job_count": len(jobs),
        "field_notes": {
            "description.full_text": "Complete official JD in readable text, included once to avoid repeated content.",
            "structured JD fields": "Responsibilities and qualification fields remain in the company database for audit, but are omitted here because they repeat full_text.",
            "audit payload": "Raw HTML and complete official payload remain in the company database and original/source archives, not this user-facing file.",
        },
        "jobs": jobs,
    }
    return write_json(path, document)


def export_combined(company_documents: Iterable[tuple[str, list[dict]]], results_dir: Path) -> Path:
    jobs = [_result_job(company, job) for company, company_jobs in company_documents for job in company_jobs]
    return write_json(
        results_dir / "all_companies_open_eligible_jobs.json",
        {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "current",
            "company": "all",
            "job_count": len(jobs),
            "jobs": jobs,
        },
    )


def export_unavailable_company(company: str, results_dir: Path, status: str, reason: str | None) -> Path:
    return write_json(
        results_dir / f"{company}_open_eligible_jobs.json",
        {
            "schema_version": 2,
            "company_schema": f"{company}.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "current",
            "company": company,
            "status": status,
            "reason": reason,
            "job_count": 0,
            "jobs": [],
        },
    )


def _result_job(company: str, job: dict) -> dict:
    return _public_result_job(job)


def _public_result_job(job: dict) -> dict:
    compensation_ranges = []
    for item in job.get("location_specific_compensation") or []:
        compensation_ranges.append({key: value for key, value in item.items() if key != "raw"})
    return {
        "company": job["company"],
        "source_job_id": job["source_job_id"],
        "requisition_id": job.get("requisition_id"),
        "title": job["title"],
        "normalized_title": job.get("normalized_title"),
        "team": job.get("team"),
        "department": job.get("department"),
        "job_category": job.get("job_category"),
        "level": job.get("level"),
        "employment_type": job.get("employment_type"),
        "workplace_type": job.get("workplace_type"),
        "urls": {
            "source": job.get("source_url"),
            "apply": job.get("apply_url"),
            "canonical": job.get("canonical_url"),
        },
        "timing": {
            "posted_at": job.get("posted_at"),
            "posted_at_raw": job.get("posted_at_raw"),
            "posted_at_accuracy": job.get("posted_at_accuracy"),
            "updated_at": job.get("updated_at"),
            "valid_through": job.get("valid_through"),
            "closing_date": job.get("closing_date"),
            "first_seen_at": job.get("first_seen_at"),
            "last_seen_at": job.get("last_seen_at"),
            "fetched_at": job.get("fetched_at"),
            "closed_at": job.get("closed_at"),
        },
        "location": {
            "raw": job.get("location_raw"),
            "locations": job.get("locations") or [],
            "filter_status": job.get("location_filter_status"),
            "review_required": job.get("location_review_required"),
        },
        "description": {
            "full_text": job.get("description_plain_text"),
            "structured_fields_available_in_database": [
                field for field in (
                    "responsibilities", "minimum_qualifications", "preferred_qualifications",
                    "required_qualifications", "education_requirements", "experience_requirements",
                    "other_requirements", "benefits", "travel_requirements",
                    "work_authorization_text", "equal_opportunity_text",
                ) if job.get(field)
            ],
        },
        "compensation": {
            "salary_text_raw": job.get("salary_text_raw"),
            "salary_min": job.get("salary_min"),
            "salary_max": job.get("salary_max"),
            "salary_currency": job.get("salary_currency"),
            "salary_period": job.get("salary_period"),
            "compensation_type": job.get("compensation_type"),
            "bonus_text": job.get("bonus_text"),
            "equity_text": job.get("equity_text"),
            "other_compensation_text": job.get("other_compensation_text"),
            "location_specific_ranges": compensation_ranges,
        },
        "eligibility": {
            "is_eligible_by_basic_filters": job.get("is_eligible_by_basic_filters"),
            "reason": job.get("eligibility_reason"),
        },
        "status": job.get("status"),
        "change_type": job.get("change_type"),
        "last_changed_at": job.get("last_changed_at"),
        "source_name": job.get("source_name"),
        "source_adapter_version": job.get("source_adapter_version"),
        "content_hash": job.get("content_hash"),
        "parser_warning": job.get("parser_warning"),
        "fetch_warning": job.get("fetch_warning"),
    }
