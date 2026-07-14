from __future__ import annotations

from pathlib import Path
import unittest

from job_monitor.adapters.openai import OpenAIAdapter
from job_monitor.config import load_company_config, load_settings, project_paths
from job_monitor.filters.location import apply_basic_filters


ROOT = Path(__file__).resolve().parents[1]


class OpenAIAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        paths = project_paths(ROOT)
        cls.settings = load_settings(paths)
        cls.adapter = OpenAIAdapter(load_company_config(paths, "openai"), cls.settings)
        cls.payload = (ROOT / "tests" / "fixtures" / "openai_jobs.json").read_bytes()

    def test_parse_complete_fields_and_multiple_compensation_ranges(self):
        jobs = self.adapter.parse_payload(self.payload, "2026-07-13T16:00:00+00:00")
        self.assertEqual(4, len(jobs))
        job = jobs[0]
        self.assertEqual("local-001", job.source_job_id)
        self.assertEqual("Operate services", job.responsibilities)
        self.assertEqual("Five years of experience.", job.minimum_qualifications)
        self.assertEqual(3, len(job.location_specific_compensation))
        self.assertEqual(200000.0, job.salary_min)
        self.assertEqual("USD", job.salary_currency)
        self.assertEqual("year", job.salary_period)
        self.assertTrue(job.source_payload_hash)
        self.assertTrue(job.content_hash)

    def test_multi_location_hybrid_is_eligible_when_one_city_is_local(self):
        job = self.adapter.parse_payload(self.payload, "2026-07-13T16:00:00+00:00")[0]
        apply_basic_filters(job, self.settings)
        self.assertTrue(job.is_eligible_by_basic_filters)
        self.assertEqual("eligible_by_city_allowlist", job.location_filter_status)

    def test_remote_us_is_eligible(self):
        job = self.adapter.parse_payload(self.payload, "2026-07-13T16:00:00+00:00")[1]
        apply_basic_filters(job, self.settings)
        self.assertTrue(job.is_eligible_by_basic_filters)
        self.assertEqual("eligible_remote_us", job.location_filter_status)

    def test_title_keyword_excludes_intern(self):
        job = self.adapter.parse_payload(self.payload, "2026-07-13T16:00:00+00:00")[2]
        apply_basic_filters(job, self.settings)
        self.assertFalse(job.is_eligible_by_basic_filters)
        self.assertIn("excluded_by_title_keyword", job.eligibility_reason)

    def test_ambiguous_region_requires_review(self):
        job = self.adapter.parse_payload(self.payload, "2026-07-13T16:00:00+00:00")[3]
        apply_basic_filters(job, self.settings)
        self.assertTrue(job.location_review_required)
        self.assertFalse(job.is_eligible_by_basic_filters)


if __name__ == "__main__":
    unittest.main()
