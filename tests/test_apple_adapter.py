from __future__ import annotations

import json
from pathlib import Path
import unittest

from job_monitor.adapters.apple import AppleAdapter, extract_hydration, parse_detail_payload, parse_search_payload
from job_monitor.config import load_company_config, load_settings, project_paths
from job_monitor.filters.location import apply_basic_filters


ROOT = Path(__file__).resolve().parents[1]


def apple_html(loader_data: dict) -> bytes:
    document = {"loaderData": loader_data, "actionData": None, "errors": None}
    encoded = json.dumps(json.dumps(document, ensure_ascii=False))
    return f'<html><script>window.__staticRouterHydrationData = JSON.parse({encoded});</script></html>'.encode()


SEARCH = {
    "reqId": "200000001-3956",
    "postingTitle": "Test Engineer",
    "transformedPostingTitle": "test-engineer",
    "postDateInGMT": "2026-07-12T12:00:00+00:00",
    "postingDate": "Jul 12, 2026",
    "jobSummary": "Official summary",
    "employmentType": None,
    "homeOffice": False,
    "team": {"teamName": "Hardware", "teamCode": "HRDWR"},
    "locations": [{
        "name": "Sunnyvale", "city": "Sunnyvale", "stateProvince": "California",
        "countryName": "United States", "zipCode": "94085",
    }],
}


DETAIL = {
    **SEARCH,
    "jobNumber": "200000001-3956",
    "employmentType": "Standard",
    "jobType": "CORPORATE",
    "teamNames": ["Hardware"],
    "description": "Official description",
    "responsibilities": "Build and test systems.",
    "minimumQualifications": "Two years experience.",
    "preferredQualifications": "Testing experience.",
    "localeLocation": SEARCH["locations"],
    "postingFooters": [{
        "localizations": {"en_US": [
            {"name": "Pay & Benefits", "content": "The base pay range is between $129,300 and $225,300. Benefits are provided."},
            {"name": "EEO Statement", "content": "<p>Equal opportunity employer.</p>"},
        ]}
    }],
}


class AppleAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        paths = project_paths(ROOT)
        cls.settings = load_settings(paths)
        cls.adapter = AppleAdapter(load_company_config(paths, "apple"), cls.settings)

    def test_extracts_official_hydration_search(self):
        payload = apple_html({"search": {"searchResults": [SEARCH], "totalRecords": 1}})
        self.assertIn("loaderData", extract_hydration(payload))
        records, total = parse_search_payload(payload)
        self.assertEqual(1, total)
        self.assertEqual("200000001-3956", records[0]["reqId"])

    def test_parses_detail_sections_salary_and_location(self):
        payload = apple_html({"jobDetails": {"jobsData": DETAIL}})
        parsed = parse_detail_payload(payload)
        job = self.adapter._job_from_detail(SEARCH, parsed, "https://jobs.apple.com/test", "2026-07-13T16:00:00+00:00")
        apply_basic_filters(job, self.settings)
        self.assertEqual("200000001-3956", job.source_job_id)
        self.assertEqual("Standard", job.employment_type)
        self.assertEqual("Build and test systems.", job.responsibilities)
        self.assertEqual(129300.0, job.salary_min)
        self.assertEqual(225300.0, job.salary_max)
        self.assertEqual(1, len(job.location_specific_compensation))
        self.assertTrue(job.is_eligible_by_basic_filters)

    def test_search_only_record_is_not_guessed_full_time(self):
        job = self.adapter._job_from_search(SEARCH, "2026-07-13T16:00:00+00:00")
        apply_basic_filters(job, self.settings)
        self.assertFalse(job.is_eligible_by_basic_filters)
        self.assertIn("employment_review_required", job.eligibility_reason)

    def test_managed_pipeline_uses_public_detail_identifier(self):
        record = {**SEARCH, "reqId": "PIPE-114438158", "positionId": "114438158"}
        self.assertIn("/details/114438158/", self.adapter._detail_url(record))
        self.assertEqual("114438158", self.adapter._job_from_search(record, "2026-07-13T16:00:00+00:00").source_job_id)

    def test_us_search_scope_survives_missing_detail_country(self):
        detail = {**DETAIL, "localeLocation": [{"name": "Remote", "city": "", "countryName": ""}]}
        job = self.adapter._job_from_detail(SEARCH, detail, None, "2026-07-13T16:00:00+00:00")
        self.assertTrue(job.is_us_job)


if __name__ == "__main__":
    unittest.main()
