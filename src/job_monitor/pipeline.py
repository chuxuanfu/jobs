from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from job_monitor.adapters import OpenAIAdapter
from job_monitor.archive import archive_fetch, read_archived_payload, write_json
from job_monitor.config import Paths, ensure_runtime_directories, load_company_config, load_settings
from job_monitor.exporting import export_company
from job_monitor.filters.location import apply_basic_filters
from job_monitor.health import evaluate_health
from job_monitor.reporting import write_run_report
from job_monitor.storage import CompanyDatabase


ADAPTERS = {"openai": OpenAIAdapter}


def run_company(
    company: str,
    paths: Paths,
    *,
    dry_run: bool = False,
    fetch_only: bool = False,
) -> dict[str, Any]:
    company = company.lower()
    settings = load_settings(paths)
    company_config = load_company_config(paths, company)
    if not company_config.get("enabled", False):
        return {
            "company": company,
            "skipped": True,
            "status": company_config.get("status", "disabled"),
            "reason": company_config.get("reason"),
        }
    adapter_name = company_config["adapter"]
    if adapter_name not in ADAPTERS:
        raise ValueError(f"Adapter not implemented: {adapter_name}")
    adapter = ADAPTERS[adapter_name](company_config, settings)

    database = CompanyDatabase(
        paths.databases / f"{company}_jobs.sqlite",
        paths.root / "migrations" / "001_initial.sql",
    )
    if dry_run:
        is_baseline = not database.path.exists() or database.is_baseline()
        previous_count = database.last_healthy_fetch_count() if database.path.exists() else None
    else:
        ensure_runtime_directories(paths, [company])
        database.migrate()
        is_baseline = database.is_baseline()
        previous_count = database.last_healthy_fetch_count()
    started_at = datetime.now(timezone.utc).isoformat()
    run_id = None if dry_run else database.create_run(company, started_at, is_baseline, fetch_only)

    result = adapter.fetch()
    health = evaluate_health(result, previous_count, settings)
    us_jobs = [apply_basic_filters(job, settings) for job in result.jobs if job.is_us_job]
    eligible_jobs = [job for job in us_jobs if job.is_eligible_by_basic_filters]
    review_jobs = [job for job in us_jobs if job.location_review_required]
    missing_posted = [job for job in us_jobs if not job.posted_at]
    missing_salary = [job for job in us_jobs if not job.salary_text_raw]

    summary: dict[str, Any] = {
        "company": company,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "success": result.error is None,
        "healthy": health.healthy,
        "is_baseline": is_baseline,
        "dry_run": dry_run,
        "fetch_only": fetch_only,
        "request_url": result.request_url,
        "http_status": result.http_status,
        "fetched_count": len(result.jobs),
        "us_count": len(us_jobs),
        "eligible_count": len(eligible_jobs),
        "new_count": 0,
        "updated_count": 0,
        "unchanged_count": 0,
        "possibly_closed_count": 0,
        "closed_count": 0,
        "reopened_count": 0,
        "review_count": len(review_jobs),
        "missing_posted_date_count": len(missing_posted),
        "missing_salary_count": len(missing_salary),
        "warnings": [*result.warnings, *health.reasons],
        "error": result.error,
        "database_path": str(database.path),
        "raw_archive_path": None,
        "original_path": None,
        "result_path": None,
    }
    if dry_run:
        return summary

    payload_path, _ = archive_fetch(
        paths.source,
        result,
        company_config["adapter_version"],
        settings["timezone"],
    )
    summary["raw_archive_path"] = str(payload_path)

    if health.healthy:
        original_path = paths.original / company / "current_open_us_jobs.json"
        write_json(
            original_path,
            {
                "schema_version": 1,
                "company": company,
                "source": company_config["source_name"],
                "fetched_at": result.fetched_at,
                "job_count": len(us_jobs),
                "jobs": [job.to_dict() for job in us_jobs],
            },
        )
        summary["original_path"] = str(original_path)
        if not fetch_only and run_id is not None:
            changes = database.apply_jobs(
                run_id,
                us_jobs,
                result.fetched_at,
                is_baseline,
                int(settings["baseline_days"]),
                int(settings["close_after_missed_runs"]),
            )
            for name, count in changes.items():
                summary[f"{name}_count"] = count
            result_path = export_company(database, company, paths.results, mode="current")
            summary["result_path"] = str(result_path)

    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    if run_id is not None:
        database.finish_run(
            run_id,
            {
                key: value
                for key, value in summary.items()
                if key not in {"database_path", "warnings", "finished_at", "company", "started_at"}
            }
            | {"warnings_json": json.dumps(summary["warnings"], ensure_ascii=False)},
        )
    markdown_path, jsonl_path = write_run_report(paths.logs, summary, settings["timezone"])
    summary["markdown_log_path"] = str(markdown_path)
    summary["machine_log_path"] = str(jsonl_path)
    return summary


def reparse_company(company: str, archive_path: Path, paths: Paths, *, apply: bool = False) -> dict[str, Any]:
    """Reparse a complete archived feed; state changes require explicit apply."""
    company = company.lower()
    settings = load_settings(paths)
    company_config = load_company_config(paths, company)
    adapter_name = company_config["adapter"]
    if adapter_name not in ADAPTERS:
        raise ValueError(f"Adapter not implemented: {adapter_name}")
    adapter = ADAPTERS[adapter_name](company_config, settings)
    observed_at = datetime.now(timezone.utc).isoformat()
    jobs = adapter.parse_payload(read_archived_payload(archive_path), observed_at)
    us_jobs = [apply_basic_filters(job, settings) for job in jobs if job.is_us_job]
    summary: dict[str, Any] = {
        "company": company,
        "archive": str(archive_path),
        "parsed_jobs": len(jobs),
        "us_count": len(us_jobs),
        "eligible_count": sum(job.is_eligible_by_basic_filters for job in us_jobs),
        "apply": apply,
    }
    if not apply:
        return summary

    ensure_runtime_directories(paths, [company])
    database = CompanyDatabase(
        paths.databases / f"{company}_jobs.sqlite",
        paths.root / "migrations" / "001_initial.sql",
    )
    database.migrate()
    is_baseline = database.is_baseline()
    run_id = database.create_run(company, observed_at, is_baseline, False)
    changes = database.apply_jobs(
        run_id, us_jobs, observed_at, is_baseline, int(settings["baseline_days"]),
        int(settings["close_after_missed_runs"]),
    )
    original_path = paths.original / company / "current_open_us_jobs.json"
    write_json(
        original_path,
        {
            "schema_version": 1,
            "company": company,
            "source": company_config["source_name"],
            "fetched_at": observed_at,
            "reparsed_from": str(archive_path),
            "job_count": len(us_jobs),
            "jobs": [job.to_dict() for job in us_jobs],
        },
    )
    result_path = export_company(database, company, paths.results, mode="current")
    full_summary = {
        "company": company,
        "started_at": observed_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "success": True,
        "healthy": True,
        "is_baseline": is_baseline,
        "dry_run": False,
        "fetch_only": False,
        "request_url": f"archive:{archive_path}",
        "http_status": None,
        "fetched_count": len(jobs),
        "us_count": len(us_jobs),
        "eligible_count": sum(job.is_eligible_by_basic_filters for job in us_jobs),
        **{f"{name}_count": count for name, count in changes.items()},
        "review_count": sum(job.location_review_required for job in us_jobs),
        "missing_posted_date_count": sum(not job.posted_at for job in us_jobs),
        "missing_salary_count": sum(not job.salary_text_raw for job in us_jobs),
        "warnings": ["offline_reparse"],
        "error": None,
        "database_path": str(database.path),
        "raw_archive_path": str(archive_path),
        "original_path": str(original_path),
        "result_path": str(result_path),
    }
    database.finish_run(
        run_id,
        {
            **full_summary,
            "warnings_json": json.dumps(full_summary["warnings"], ensure_ascii=False),
        },
    )
    markdown_path, jsonl_path = write_run_report(paths.logs, full_summary, settings["timezone"])
    full_summary["markdown_log_path"] = str(markdown_path)
    full_summary["machine_log_path"] = str(jsonl_path)
    return full_summary
