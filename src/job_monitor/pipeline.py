from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from job_monitor.adapters import AppleAdapter, BroadcomAdapter, MetaAdapter, NvidiaAdapter, OpenAIAdapter
from job_monitor.archive import archive_fetch, write_json
from job_monitor.config import Paths, ensure_runtime_directories, load_company_config, load_settings
from job_monitor.exporting import export_company
from job_monitor.filters.location import apply_basic_filters
from job_monitor.health import evaluate_health
from job_monitor.reporting import write_run_report
from job_monitor.retention import is_within_retention
from job_monitor.storage import CompanyDatabase


ADAPTERS = {
    "apple": AppleAdapter,
    "broadcom": BroadcomAdapter,
    "meta": MetaAdapter,
    "nvidia": NvidiaAdapter,
    "openai": OpenAIAdapter,
}


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
    if database.path.exists():
        adapter.set_existing_jobs(database.query_jobs("all_open"))

    try:
        result = adapter.fetch()
    except Exception as exc:
        if dry_run:
            return _failed_summary(company, started_at, database, exc, is_baseline, dry_run, fetch_only)
        summary = _failed_summary(company, started_at, database, exc, is_baseline, dry_run, fetch_only)
        if run_id is not None:
            database.finish_run(run_id, {
                "success": 0, "healthy": 0, "fetched_count": 0,
                "warnings_json": json.dumps(summary["warnings"], ensure_ascii=False),
                "error": summary["error"],
            })
        markdown_path, jsonl_path = write_run_report(paths.logs, summary, settings["timezone"])
        summary["markdown_log_path"] = str(markdown_path)
        summary["machine_log_path"] = str(jsonl_path)
        return summary
    health = evaluate_health(result, previous_count, settings)
    us_jobs = [apply_basic_filters(job, settings) for job in result.jobs if job.is_us_job]
    retention_days = int(settings["retention_days"])
    retained_jobs = [job for job in us_jobs if is_within_retention(job.posted_at, result.fetched_at, retention_days)]
    eligible_jobs = [job for job in retained_jobs if job.is_eligible_by_basic_filters]
    review_jobs = [job for job in retained_jobs if job.location_review_required]
    missing_posted = [job for job in retained_jobs if not job.posted_at]
    missing_salary = [job for job in retained_jobs if not job.salary_text_raw]

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
        "retained_count": len(retained_jobs),
        "pruned_count": 0,
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
            summary["pruned_count"] = database.prune_jobs_older_than(result.fetched_at, retention_days)
            changes = database.apply_jobs(
                run_id,
                retained_jobs,
                result.fetched_at,
                is_baseline,
                retention_days,
                int(settings["close_after_missed_runs"]),
            )
            for name, count in changes.items():
                summary[f"{name}_count"] = count
            if summary["pruned_count"]:
                database.compact()
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
    jobs = adapter.reparse_archive(archive_path, observed_at)
    us_jobs = [apply_basic_filters(job, settings) for job in jobs if job.is_us_job]
    retention_days = int(settings["retention_days"])
    retained_jobs = [job for job in us_jobs if is_within_retention(job.posted_at, observed_at, retention_days)]
    summary: dict[str, Any] = {
        "company": company,
        "archive": str(archive_path),
        "parsed_jobs": len(jobs),
        "us_count": len(us_jobs),
        "retained_count": len(retained_jobs),
        "eligible_count": sum(job.is_eligible_by_basic_filters for job in retained_jobs),
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
    pruned_count = database.prune_jobs_older_than(observed_at, retention_days)
    changes = database.apply_jobs(
        run_id, retained_jobs, observed_at, is_baseline, retention_days,
        int(settings["close_after_missed_runs"]),
    )
    if pruned_count:
        database.compact()
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
        "retained_count": len(retained_jobs),
        "pruned_count": pruned_count,
        "eligible_count": sum(job.is_eligible_by_basic_filters for job in retained_jobs),
        **{f"{name}_count": count for name, count in changes.items()},
        "review_count": sum(job.location_review_required for job in retained_jobs),
        "missing_posted_date_count": sum(not job.posted_at for job in retained_jobs),
        "missing_salary_count": sum(not job.salary_text_raw for job in retained_jobs),
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


def _failed_summary(
    company: str,
    started_at: str,
    database: CompanyDatabase,
    exc: Exception,
    is_baseline: bool,
    dry_run: bool,
    fetch_only: bool,
) -> dict[str, Any]:
    error = f"{type(exc).__name__}: {exc}"
    return {
        "company": company,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "success": False,
        "healthy": False,
        "is_baseline": is_baseline,
        "dry_run": dry_run,
        "fetch_only": fetch_only,
        "request_url": None,
        "http_status": None,
        "fetched_count": 0,
        "us_count": 0,
        "retained_count": 0,
        "pruned_count": 0,
        "eligible_count": 0,
        "new_count": 0,
        "updated_count": 0,
        "unchanged_count": 0,
        "possibly_closed_count": 0,
        "closed_count": 0,
        "reopened_count": 0,
        "review_count": 0,
        "missing_posted_date_count": 0,
        "missing_salary_count": 0,
        "warnings": [error],
        "error": error,
        "database_path": str(database.path),
        "raw_archive_path": None,
        "original_path": None,
        "result_path": None,
    }


def record_company_failure(paths: Paths, company: str, exc: Exception) -> dict[str, Any]:
    """Finalize and log exceptions that occur outside the adapter fetch boundary."""
    settings = load_settings(paths)
    database = CompanyDatabase(
        paths.databases / f"{company}_jobs.sqlite",
        paths.root / "migrations" / "001_initial.sql",
    )
    started_at = datetime.now(timezone.utc).isoformat()
    summary = _failed_summary(company, started_at, database, exc, False, False, False)
    if database.path.exists():
        try:
            database.fail_unfinished_runs(summary["error"])
        except Exception as database_exc:
            summary["warnings"].append(f"could_not_finalize_run:{type(database_exc).__name__}:{database_exc}")
    try:
        markdown_path, jsonl_path = write_run_report(paths.logs, summary, settings["timezone"])
        summary["markdown_log_path"] = str(markdown_path)
        summary["machine_log_path"] = str(jsonl_path)
    except Exception as log_exc:
        summary["warnings"].append(f"could_not_write_failure_log:{type(log_exc).__name__}:{log_exc}")
    return summary
