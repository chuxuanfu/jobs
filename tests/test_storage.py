from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest

from job_monitor.hashing import job_content_hash, sha256_value
from job_monitor.models import Job, Location
from job_monitor.exporting import export_company
from job_monitor.storage import CompanyDatabase


ROOT = Path(__file__).resolve().parents[1]


def make_job(title: str = "Engineer", source_job_id: str = "job-1", posted_at: str | None = "2026-07-01T00:00:00+00:00") -> Job:
    job = Job(
        company="openai", source_name="fixture", source_job_id=source_job_id,
        source_url=f"https://example.test/{source_job_id}", apply_url=None, canonical_url=None,
        source_adapter_version="test", title=title, normalized_title=title,
        employment_type="FullTime", workplace_type="onsite",
        posted_at=posted_at, fetched_at="2026-07-13T00:00:00+00:00",
        locations=[Location(raw="San Jose, CA", city="San Jose", state="CA", country="United States")],
        location_raw="San Jose, CA", location_filter_status="eligible_by_bay_area_city",
        location_review_required=False, is_us_job=True, is_eligible_by_basic_filters=True,
        eligibility_reason="full_time_official_field;eligible_by_bay_area_city",
        description_raw_html="<p>Full description</p>",
        description_plain_text="Full description",
        responsibilities="Build systems",
        complete_job_posting_json={"id": source_job_id, "title": title, "descriptionHtml": "<p>Full description</p>"},
    )
    job.source_payload_hash = sha256_value(job.complete_job_posting_json)
    job.content_hash = job_content_hash(job.to_dict())
    return job


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = CompanyDatabase(Path(self.temp.name) / "openai.sqlite", ROOT / "migrations" / "001_initial.sql")
        self.db.migrate()

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, jobs, when, baseline=False):
        run_id = self.db.create_run("openai", when, baseline, False)
        stats = self.db.apply_jobs(run_id, jobs, when, baseline, 90, 3)
        self.db.finish_run(run_id, {"success": 1, "healthy": 1, "fetched_count": len(jobs)})
        return stats

    def test_dedupe_update_close_and_reopen(self):
        first = make_job()
        self.assertEqual(1, self._run([first], "2026-07-13T00:00:00+00:00", True)["new"])
        self.assertEqual(1, len(self.db.query_jobs("current")))

        same = make_job()
        self.assertEqual(1, self._run([same], "2026-07-14T00:00:00+00:00")["unchanged"])

        updated = make_job("Senior Engineer")
        self.assertEqual(1, self._run([updated], "2026-07-15T00:00:00+00:00")["updated"])

        self.assertEqual(1, self._run([], "2026-07-16T00:00:00+00:00")["possibly_closed"])
        self.assertEqual(1, self._run([], "2026-07-17T00:00:00+00:00")["possibly_closed"])
        self.assertEqual(1, self._run([], "2026-07-18T00:00:00+00:00")["closed"])
        self.assertEqual(1, len(self.db.query_jobs("closed")))

        reopened = make_job("Senior Engineer")
        self.assertEqual(1, self._run([reopened], "2026-07-19T00:00:00+00:00")["reopened"])
        self.assertEqual(1, len(self.db.query_jobs("current")))

    def test_event_based_exports_survive_later_unchanged_runs(self):
        self._run([make_job()], "2026-07-13T00:00:00+00:00", True)
        self._run([make_job()], "2026-07-14T00:00:00+00:00")
        self.assertEqual(1, len(self.db.query_jobs("new_since", "2026-07-12T00:00:00+00:00")))

        self._run([make_job("Senior Engineer")], "2026-07-15T00:00:00+00:00")
        self._run([make_job("Senior Engineer")], "2026-07-16T00:00:00+00:00")
        self.assertEqual(1, len(self.db.query_jobs("updated", "2026-07-14T12:00:00+00:00")))

    def test_unhealthy_empty_response_does_not_close_when_not_applied(self):
        self._run([make_job()], "2026-07-13T00:00:00+00:00", True)
        # Pipeline intentionally does not call apply_jobs for an unhealthy response.
        self.assertEqual("open", self.db.query_jobs("all_open")[0]["status"])

    def test_prune_removes_old_job_and_its_history_but_keeps_unknown_date(self):
        jobs = [
            make_job(source_job_id="old", posted_at="2026-01-01T00:00:00+00:00"),
            make_job(source_job_id="recent", posted_at="2026-07-01T00:00:00+00:00"),
            make_job(source_job_id="unknown", posted_at=None),
        ]
        self._run(jobs, "2026-07-13T00:00:00+00:00", True)
        self.assertEqual(1, self.db.prune_jobs_older_than("2026-07-13T00:00:00+00:00", 90))
        remaining = {job["source_job_id"] for job in self.db.query_jobs("all_open")}
        self.assertEqual({"recent", "unknown"}, remaining)
        self.db.compact()
        with self.db.connect(readonly=True) as connection:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM job_versions WHERE source_job_id='old'").fetchone()[0])
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM job_events WHERE source_job_id='old'").fetchone()[0])

    def test_openai_result_is_unique_and_omits_audit_payload_duplication(self):
        self._run([make_job()], "2026-07-13T00:00:00+00:00", True)
        with tempfile.TemporaryDirectory() as directory:
            output = export_company(self.db, "openai", Path(directory), "current")
            document = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(1, document["job_count"])
        result = document["jobs"][0]
        self.assertNotIn("complete_job_posting_json", result)
        self.assertNotIn("description_raw_html", result)
        self.assertEqual("Full description", result["description"]["full_text"])
        self.assertNotIn("responsibilities", result["description"])
        self.assertEqual(1, len({job["source_job_id"] for job in document["jobs"]}))

    def test_migrations_are_idempotent(self):
        self.db.migrate()
        with self.db.connect(readonly=True) as connection:
            versions = connection.execute("SELECT version, COUNT(*) FROM schema_migrations GROUP BY version").fetchall()
        self.assertEqual([(1, 1)], [tuple(row) for row in versions])


if __name__ == "__main__":
    unittest.main()
