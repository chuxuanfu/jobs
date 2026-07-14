from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from job_monitor.hashing import canonical_json
from job_monitor.models import Job
from job_monitor.retention import is_within_retention


JOB_FIELDS = (
    "company", "source_name", "source_url", "apply_url", "canonical_url",
    "source_adapter_version", "title", "normalized_title", "team", "department",
    "employment_type", "workplace_type", "requisition_id", "job_category", "level",
    "posted_at", "posted_at_raw", "posted_at_accuracy", "updated_at", "valid_through",
    "closing_date", "fetched_at", "location_raw", "primary_city", "state", "country",
    "postal_code", "street_address", "latitude", "longitude",
    "distance_from_san_jose_miles", "location_filter_status", "location_review_required",
    "description_raw_html", "description_plain_text", "responsibilities",
    "minimum_qualifications", "preferred_qualifications", "required_qualifications",
    "education_requirements", "experience_requirements", "other_requirements", "benefits",
    "travel_requirements", "work_authorization_text", "equal_opportunity_text",
    "salary_text_raw", "salary_min", "salary_max", "salary_currency", "salary_period",
    "compensation_type", "bonus_text", "equity_text", "other_compensation_text",
    "is_us_job", "is_eligible_by_basic_filters", "eligibility_reason", "content_hash",
    "source_payload_hash", "parser_warning", "fetch_warning",
)


class CompanyDatabase:
    def __init__(self, path: Path, migration_path: Path):
        self.path = path
        self.migration_path = migration_path

    def connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        else:
            connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        if not readonly:
            connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @contextmanager
    def session(self, *, readonly: bool = False):
        connection = self.connect(readonly=readonly)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        migration_directory = self.migration_path if self.migration_path.is_dir() else self.migration_path.parent
        with self.session() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
            for migration in sorted(migration_directory.glob("[0-9][0-9][0-9]_*.sql")):
                version = int(migration.name.split("_", 1)[0])
                if version in applied:
                    continue
                connection.executescript(migration.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, _now()),
                )

    def is_baseline(self) -> bool:
        with self.session(readonly=True) as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM runs WHERE healthy=1 AND fetch_only=0").fetchone()
        return not row or row["count"] == 0

    def last_healthy_fetch_count(self) -> int | None:
        with self.session(readonly=True) as connection:
            row = connection.execute(
                "SELECT fetched_count FROM runs WHERE healthy=1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return int(row["fetched_count"]) if row else None

    def create_run(self, company: str, started_at: str, is_baseline: bool, fetch_only: bool) -> int:
        with self.session() as connection:
            cursor = connection.execute(
                "INSERT INTO runs(company, started_at, is_baseline, fetch_only) VALUES (?, ?, ?, ?)",
                (company, started_at, int(is_baseline), int(fetch_only)),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, values: dict[str, Any]) -> None:
        allowed = {
            "success", "healthy", "request_url", "http_status", "fetched_count", "us_count",
            "eligible_count", "new_count", "updated_count", "unchanged_count",
            "possibly_closed_count", "closed_count", "reopened_count", "review_count",
            "missing_posted_date_count", "missing_salary_count", "raw_archive_path",
            "original_path", "result_path", "warnings_json", "error",
        }
        values = {key: value for key, value in values.items() if key in allowed}
        values["finished_at"] = _now()
        assignments = ", ".join(f"{key}=?" for key in values)
        with self.session() as connection:
            connection.execute(
                f"UPDATE runs SET {assignments} WHERE id=?",
                [*values.values(), run_id],
            )

    def fail_unfinished_runs(self, error: str) -> int:
        with self.session() as connection:
            cursor = connection.execute(
                """
                UPDATE runs
                SET finished_at=?, success=0, healthy=0, error=?, warnings_json=?
                WHERE finished_at IS NULL
                """,
                (_now(), error, canonical_json([error])),
            )
            return int(cursor.rowcount)

    def apply_jobs(
        self,
        run_id: int,
        jobs: list[Job],
        observed_at: str,
        is_baseline: bool,
        baseline_days: int,
        close_after_missed_runs: int,
        close_missing: bool = True,
    ) -> dict[str, int]:
        stats = {key: 0 for key in ("new", "updated", "unchanged", "possibly_closed", "closed", "reopened")}
        seen_ids = {job.source_job_id for job in jobs}
        with self.session() as connection:
            existing_rows = {
                row["source_job_id"]: row
                for row in connection.execute("SELECT * FROM jobs").fetchall()
            }
            for job in jobs:
                existing = existing_rows.get(job.source_job_id)
                serialized = _serialized_job(job)
                if existing is None:
                    change_type = "new"
                    status = "open"
                    first_seen = observed_at
                    baseline_in_scope = int(_baseline_in_scope(job.posted_at, observed_at, baseline_days)) if is_baseline else 1
                    last_changed = observed_at
                    stats["new"] += 1
                else:
                    first_seen = existing["first_seen_at"]
                    baseline_in_scope = existing["baseline_in_scope"]
                    if existing["status"] == "closed":
                        change_type = "reopened"
                        stats["reopened"] += 1
                    elif existing["content_hash"] != job.content_hash:
                        change_type = "updated"
                        stats["updated"] += 1
                    else:
                        change_type = "unchanged"
                        stats["unchanged"] += 1
                    status = "open"
                    last_changed = observed_at if change_type in {"reopened", "updated"} else existing["last_changed_at"]

                metadata = {
                    "first_seen_at": first_seen,
                    "last_seen_at": observed_at,
                    "closed_at": None,
                    "status": status,
                    "baseline_in_scope": baseline_in_scope,
                    "last_changed_at": last_changed,
                    "change_type": change_type,
                    "consecutive_missed_runs": 0,
                }
                self._upsert_job(connection, job.source_job_id, serialized, metadata)
                if change_type != "unchanged":
                    self._insert_version(connection, job, run_id, observed_at, change_type, serialized["normalized_job_json"])
                    self._insert_event(connection, job.source_job_id, run_id, observed_at, change_type)

            if not close_missing:
                return stats
            for source_job_id, existing in existing_rows.items():
                if source_job_id in seen_ids or existing["status"] == "closed":
                    continue
                missed = int(existing["consecutive_missed_runs"]) + 1
                if missed >= close_after_missed_runs:
                    status, change_type = "closed", "closed"
                    closed_at = observed_at
                    stats["closed"] += 1
                else:
                    status, change_type = "possibly_closed", "possibly_closed"
                    closed_at = None
                    stats["possibly_closed"] += 1
                connection.execute(
                    """
                    UPDATE jobs
                    SET status=?, change_type=?, consecutive_missed_runs=?, closed_at=?, last_changed_at=?
                    WHERE source_job_id=?
                    """,
                    (status, change_type, missed, closed_at, observed_at, source_job_id),
                )
                self._insert_event(
                    connection, source_job_id, run_id, observed_at, change_type,
                    {"consecutive_missed_runs": missed},
                )
        return stats

    def discovery_ids(self) -> set[str]:
        with self.session(readonly=True) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='discovery_ids'"
            ).fetchone()
            if not table:
                return set()
            rows = connection.execute("SELECT source_job_id FROM discovery_ids").fetchall()
        return {str(row["source_job_id"]) for row in rows}

    def observe_discovery_ids(
        self,
        run_id: int,
        source_job_ids: Iterable[str],
        observed_at: str,
    ) -> int:
        unique_ids = sorted({str(value) for value in source_job_ids if str(value)})
        with self.session() as connection:
            for source_job_id in unique_ids:
                connection.execute(
                    """
                    INSERT INTO discovery_ids(
                        source_job_id, first_seen_at, last_seen_at, first_run_id, last_run_id
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(source_job_id) DO UPDATE SET
                        last_seen_at=excluded.last_seen_at,
                        last_run_id=excluded.last_run_id
                    """,
                    (source_job_id, observed_at, observed_at, run_id, run_id),
                )
        return len(unique_ids)

    def prune_jobs_older_than(
        self,
        reference_at: str,
        retention_days: int,
        *,
        fallback_to_first_seen: bool = False,
    ) -> int:
        """Remove old jobs; optionally age incremental records from first discovery."""
        with self.session() as connection:
            rows = connection.execute("SELECT source_job_id, posted_at, first_seen_at FROM jobs").fetchall()
            old_ids = [
                row["source_job_id"]
                for row in rows
                if (
                    row["posted_at"] is not None
                    and not is_within_retention(row["posted_at"], reference_at, retention_days)
                ) or (
                    row["posted_at"] is None
                    and fallback_to_first_seen
                    and not is_within_retention(row["first_seen_at"], reference_at, retention_days)
                )
            ]
            for source_job_id in old_ids:
                connection.execute("DELETE FROM job_versions WHERE source_job_id=?", (source_job_id,))
                connection.execute("DELETE FROM job_events WHERE source_job_id=?", (source_job_id,))
                connection.execute("DELETE FROM jobs WHERE source_job_id=?", (source_job_id,))
        return len(old_ids)

    def compact(self) -> None:
        """Return deleted pages to disk after retention pruning."""
        connection = self.connect()
        try:
            connection.execute("VACUUM")
        finally:
            connection.close()

    def _upsert_job(
        self,
        connection: sqlite3.Connection,
        source_job_id: str,
        values: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        combined = {"source_job_id": source_job_id, **values, **metadata}
        columns = list(combined)
        placeholders = ",".join("?" for _ in columns)
        update_columns = [column for column in columns if column != "source_job_id"]
        updates = ",".join(f"{column}=excluded.{column}" for column in update_columns)
        connection.execute(
            f"INSERT INTO jobs({','.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(source_job_id) DO UPDATE SET {updates}",
            list(combined.values()),
        )

    @staticmethod
    def _insert_version(
        connection: sqlite3.Connection,
        job: Job,
        run_id: int,
        observed_at: str,
        change_type: str,
        normalized_json: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO job_versions(
                source_job_id, run_id, observed_at, change_type, content_hash,
                source_payload_hash, normalized_job_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.source_job_id, run_id, observed_at, change_type, job.content_hash,
                job.source_payload_hash, normalized_json,
            ),
        )

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        source_job_id: str,
        run_id: int,
        observed_at: str,
        event_type: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO job_events(source_job_id, run_id, event_at, event_type, details_json) VALUES (?, ?, ?, ?, ?)",
            (source_job_id, run_id, observed_at, event_type, canonical_json(details or {})),
        )

    def query_jobs(self, mode: str = "current", since: str | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if mode == "current":
            clauses.extend(["status='open'", "is_eligible_by_basic_filters=1"])
        elif mode == "all_open":
            clauses.append("status='open'")
        elif mode == "new_since":
            event_clause = "EXISTS (SELECT 1 FROM job_events e WHERE e.source_job_id=jobs.source_job_id AND e.event_type='new'"
            if since:
                event_clause += " AND e.event_at>=?"
                parameters.append(since)
            clauses.append(event_clause + ")")
        elif mode == "updated":
            event_clause = "EXISTS (SELECT 1 FROM job_events e WHERE e.source_job_id=jobs.source_job_id AND e.event_type IN ('updated','reopened')"
            if since:
                event_clause += " AND e.event_at>=?"
                parameters.append(since)
            clauses.append(event_clause + ")")
        elif mode == "closed":
            clauses.append("status='closed'")
        elif mode == "review":
            clauses.append("location_review_required=1")
            clauses.append("status='open'")
        else:
            raise ValueError(f"Unknown export mode: {mode}")
        where = " AND ".join(clauses) if clauses else "1=1"
        with self.session(readonly=True) as connection:
            rows = connection.execute(
                f"SELECT jobs.* FROM jobs WHERE {where} ORDER BY posted_at DESC, source_job_id",
                parameters,
            ).fetchall()
        return [_export_row(row) for row in rows]

    def latest_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.session(readonly=True) as connection:
            rows = connection.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]


def _serialized_job(job: Job) -> dict[str, Any]:
    raw = job.to_dict()
    values = {field: raw.get(field) for field in JOB_FIELDS}
    values["location_review_required"] = int(job.location_review_required)
    values["is_us_job"] = int(job.is_us_job)
    values["is_eligible_by_basic_filters"] = int(job.is_eligible_by_basic_filters)
    values["locations_json"] = canonical_json(raw["locations"])
    values["complete_job_posting_json"] = canonical_json(raw["complete_job_posting_json"])
    values["location_specific_compensation_json"] = canonical_json(raw["location_specific_compensation"])
    values["normalized_job_json"] = canonical_json(raw)
    return values


def _baseline_in_scope(posted_at: str | None, observed_at: str, baseline_days: int) -> bool:
    return is_within_retention(posted_at, observed_at, baseline_days)


def _export_row(row: sqlite3.Row) -> dict[str, Any]:
    job = json.loads(row["normalized_job_json"])
    job.update({
        "status": row["status"],
        "first_seen_at": row["first_seen_at"],
        "last_seen_at": row["last_seen_at"],
        "closed_at": row["closed_at"],
        "last_changed_at": row["last_changed_at"],
        "change_type": row["change_type"],
        "baseline_in_scope": bool(row["baseline_in_scope"]),
    })
    return job


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
