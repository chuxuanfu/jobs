from __future__ import annotations

from pathlib import Path
import unittest

from job_monitor.adapters.google import GoogleAdapter, parse_search_payload
from job_monitor.config import load_company_config, load_settings, project_paths
from job_monitor.filters.location import apply_basic_filters


ROOT = Path(__file__).resolve().parents[1]


class GoogleAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        paths = project_paths(ROOT)
        cls.settings = load_settings(paths)
        cls.adapter = GoogleAdapter(load_company_config(paths, "google"), cls.settings)
        cls.search = (ROOT / "tests/fixtures/google_search.html").read_bytes()
        cls.detail = (ROOT / "tests/fixtures/google_detail.html").read_bytes()

    def test_first_page_parser_keeps_order_ids_and_strips_query(self):
        records = parse_search_payload(
            self.search,
            "https://www.google.com/about/careers/applications/jobs/results",
            20,
        )
        self.assertEqual(["123456789", "987654321"], [record["source_job_id"] for record in records])
        self.assertEqual("Test Hardware Engineer", records[0]["title"])
        self.assertNotIn("?", records[0]["source_url"])

    def test_detail_parser_preserves_sections_locations_and_compensation(self):
        job = self.adapter.parse_payload(self.detail, "2026-07-13T16:00:00+00:00")[0]
        apply_basic_filters(job, self.settings)
        self.assertEqual("123456789", job.source_job_id)
        self.assertEqual("Full-time", job.employment_type)
        self.assertEqual("hybrid", job.workplace_type)
        self.assertEqual("Mountain View", job.primary_city)
        self.assertEqual(3, len(job.locations))
        self.assertEqual("Build systems.\nTest systems.", job.responsibilities)
        self.assertEqual(152000.0, job.salary_min)
        self.assertEqual(222000.0, job.salary_max)
        self.assertEqual("USD", job.salary_currency)
        self.assertIn("bonus", job.bonus_text.lower())
        self.assertIn("equity", job.equity_text.lower())
        self.assertTrue(job.is_eligible_by_basic_filters)
        self.assertIsNone(job.posted_at)


if __name__ == "__main__":
    unittest.main()
