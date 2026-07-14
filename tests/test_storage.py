from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from job_monitor.hashing import job_content_hash, sha256_value
from job_monitor.models import Job, Location
from job_monitor.storage import CompanyDatabase


ROOT = Path(__file__).resolve().parents[1]


def make_job(title: str = "Engineer") -> Job:
    job = Job(
        company="openai", source_name="fixture", source_job_id="job-1",
        source_url="https://example.test/job-1", apply_url=None, canonical_url=None,
        source_adapter_version="test", title=title, normalized_title=title,
        employment_type="FullTime", workplace_type="onsite",
        posted_at="2026-07-01T00:00:00+00:00", fetched_at="2026-07-13T00:00:00+00:00",
        locations=[Location(raw="San Jose, CA", city="San Jose", state="CA", country="United States")],
        location_raw="San Jose, CA", location_filter_status="eligible_by_city_allowlist",
        location_review_required=False, is_us_job=True, is_eligible_by_basic_filters=True,
        eligibility_reason="full_time_official_field;eligible_by_city_allowlist",
        complete_job_posting_json={"id": "job-1", "title": title},
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

    def test_unhealthy_empty_response_does_not_close_when_not_applied(self):
        self._run([make_job()], "2026-07-13T00:00:00+00:00", True)
        # Pipeline intentionally does not call apply_jobs for an unhealthy response.
        self.assertEqual("open", self.db.query_jobs("all_open")[0]["status"])


if __name__ == "__main__":
    unittest.main()
