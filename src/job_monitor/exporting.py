from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from job_monitor.archive import write_json
from job_monitor.storage import CompanyDatabase


def export_company(database: CompanyDatabase, company: str, results_dir: Path, mode: str = "current", since: str | None = None) -> Path:
    jobs = database.query_jobs(mode=mode, since=since)
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
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "company": company,
        "job_count": len(jobs),
        "jobs": jobs,
    }
    return write_json(path, document)


def export_combined(company_documents: Iterable[tuple[str, list[dict]]], results_dir: Path) -> Path:
    jobs = [job for _, company_jobs in company_documents for job in company_jobs]
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
