from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from job_monitor.config import (
    load_company_config, load_settings,
    project_paths,
)
from job_monitor.backup import backup_project
from job_monitor.exporting import export_combined, export_company, export_unavailable_company
from job_monitor.locking import exclusive_run_lock
from job_monitor.pipeline import record_company_failure, reparse_company, run_company
from job_monitor.storage import CompanyDatabase


COMPANIES = ("apple", "openai", "meta", "google", "broadcom", "nvidia")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobs-monitor", description="Local deterministic official job monitor")
    parser.add_argument("--root", type=Path, help="Project root; defaults to the installed repository")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Fetch one company or all enabled companies")
    run.add_argument("--company", choices=(*COMPANIES, "all"), default="all")
    run.add_argument("--dry-run", action="store_true", help="Fetch and parse without writing files or state")
    run.add_argument("--fetch-only", action="store_true", help="Archive source and original data without changing job state")

    export = subparsers.add_parser("export", help="Export JSON from company databases")
    export.add_argument("--company", choices=(*COMPANIES, "all"), default="all")
    export.add_argument("--mode", choices=("current", "all_open", "new_since", "updated", "closed", "review"), default="current")
    export.add_argument("--since", help="ISO timestamp/date for new_since or updated")

    status = subparsers.add_parser("status", help="Show recent run status")
    status.add_argument("--company", choices=(*COMPANIES, "all"), default="all")
    status.add_argument("--limit", type=int, default=5)

    health = subparsers.add_parser("health", help="Show latest adapter health")
    health.add_argument("--company", choices=(*COMPANIES, "all"), default="all")

    reparse = subparsers.add_parser("reparse", help="Parse an archived official payload without network access")
    reparse.add_argument("--company", choices=("apple", "openai", "meta", "broadcom", "nvidia"), required=True)
    reparse.add_argument("--archive", type=Path, required=True)
    reparse.add_argument("--apply", action="store_true", help="Update normalized state from a known-complete archive")

    subparsers.add_parser("backup", help="Copy local project data to backup_directory")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = project_paths(args.root)
    if args.command == "run":
        companies = COMPANIES if args.company == "all" else (args.company,)
        summaries = []
        def execute_runs() -> None:
            for company in companies:
                try:
                    summaries.append(run_company(company, paths, dry_run=args.dry_run, fetch_only=args.fetch_only))
                except Exception as exc:  # Isolation is intentional: one company must not stop the others.
                    if args.dry_run:
                        summaries.append({"company": company, "success": False, "healthy": False, "error": f"{type(exc).__name__}: {exc}"})
                    else:
                        summaries.append(record_company_failure(paths, company, exc))
        if args.dry_run:
            execute_runs()
        else:
            with exclusive_run_lock(paths.root / "data" / "job_monitor.lock"):
                execute_runs()
        print(json.dumps(summaries, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if all(item.get("skipped") or item.get("healthy") for item in summaries) else 1

    if args.command == "export":
        companies = COMPANIES if args.company == "all" else (args.company,)
        outputs: list[str] = []
        combined: list[tuple[str, list[dict]]] = []
        for company in companies:
            database = _database_if_present(paths, company)
            if database is None:
                config = load_company_config(paths, company)
                if args.mode == "current" and config.get("status") == "blocked_by_source_policy":
                    outputs.append(str(export_unavailable_company(
                        company, paths.results, config["status"], config.get("reason"),
                    )))
                continue
            output = export_company(database, company, paths.results, args.mode, args.since)
            outputs.append(str(output))
            if args.company == "all" and args.mode == "current":
                combined.append((company, database.query_jobs("current")))
        if combined:
            outputs.append(str(export_combined(combined, paths.results)))
        print(json.dumps({"outputs": outputs}, ensure_ascii=False, indent=2))
        return 0

    if args.command in {"status", "health"}:
        companies = COMPANIES if args.company == "all" else (args.company,)
        output = {}
        for company in companies:
            config = load_company_config(paths, company)
            database = _database_if_present(paths, company)
            runs = database.latest_runs(args.limit if args.command == "status" else 1) if database else []
            output[company] = {
                "configured_status": config.get("status", "enabled" if config.get("enabled") else "disabled"),
                "runs": runs,
            }
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.command == "reparse":
        result = reparse_company(args.company, args.archive, paths, apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.command == "backup":
        result = backup_project(paths.root, load_settings(paths).get("backup_directory", ""))
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    return 2


def _database_if_present(paths, company: str) -> CompanyDatabase | None:
    path = paths.databases / f"{company}_jobs.sqlite"
    if not path.exists():
        return None
    return CompanyDatabase(path, paths.root / "migrations" / "001_initial.sql")


if __name__ == "__main__":
    sys.exit(main())
