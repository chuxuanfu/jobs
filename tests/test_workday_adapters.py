from __future__ import annotations

import json
from pathlib import Path
import unittest

from job_monitor.adapters.workday import BroadcomAdapter, NvidiaAdapter, _load_list
from job_monitor.config import load_company_config, load_settings, project_paths
from job_monitor.filters.location import apply_basic_filters


ROOT = Path(__file__).resolve().parents[1]


def detail_document(*, job_id: str, location: str, description: str) -> dict:
    return {
        "jobPostingInfo": {
            "id": "internal-id",
            "title": "Senior Test Engineer",
            "jobDescription": description,
            "location": location,
            "postedOn": "Posted Today",
            "startDate": "2026-07-13",
            "timeType": "Full time",
            "jobReqId": job_id,
            "jobPostingId": f"Senior-Test-Engineer_{job_id}",
            "country": {"descriptor": "United States of America"},
            "jobRequisitionLocation": {"descriptor": location, "country": {"descriptor": "United States of America", "alpha2Code": "US"}},
            "externalUrl": f"https://example.test/job/{job_id}",
        }
    }


LIST_RECORD = {
    "title": "Senior Test Engineer",
    "externalPath": "/job/US-CA-Santa-Clara/Senior-Test-Engineer_JR123",
    "timeType": "Full time",
    "locationsText": "US, CA, Santa Clara",
    "postedOn": "Posted Today",
    "bulletFields": ["JR123"],
}


class WorkdayAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        paths = project_paths(ROOT)
        cls.settings = load_settings(paths)
        cls.broadcom = BroadcomAdapter(load_company_config(paths, "broadcom"), cls.settings)
        cls.nvidia = NvidiaAdapter(load_company_config(paths, "nvidia"), cls.settings)

    def test_broadcom_detail_parses_official_fields(self):
        description = """
        <h2>Job Description</h2><p>Official introduction.</p>
        <h2>Key Responsibilities</h2><ul><li>Build systems.</li></ul>
        <h2>Requirements and Qualifications</h2><ul><li>Five years.</li></ul>
        <h2>Compensation and Benefits</h2><p>The annual base salary range is USD 167,500.00 To USD 268,000.00.</p>
        """
        document = detail_document(job_id="R026513", location="USA-California-San Jose-1320 Ridder Park Drive", description=description)
        record = {**LIST_RECORD, "bulletFields": ["R026513"], "externalPath": "/job/USA-California-San-Jose/Test_R026513"}
        job = self.broadcom._job_from_detail(record, document, "2026-07-13T16:00:00+00:00")
        apply_basic_filters(job, self.settings)
        self.assertEqual("R026513", job.source_job_id)
        self.assertEqual("San Jose", job.primary_city)
        self.assertEqual(167500.0, job.salary_min)
        self.assertEqual("Build systems.", job.responsibilities)
        self.assertTrue(job.is_eligible_by_basic_filters)

    def test_nvidia_detail_preserves_multiple_salary_ranges(self):
        description = """
        <p><b>What you'll be doing:</b></p><ul><li>Validate systems.</li></ul>
        <p><b>What we need to see:</b></p><ul><li>Ten years.</li></ul>
        <p>The base salary range is 196,000 USD - 310,500 USD for Level 5, and 232,000 USD - 368,000 USD for Level 6.</p>
        """
        document = detail_document(job_id="JR123", location="US, CA, Santa Clara", description=description)
        job = self.nvidia._job_from_detail(LIST_RECORD, document, "2026-07-13T16:00:00+00:00")
        self.assertEqual(2, len(job.location_specific_compensation))
        self.assertEqual("Validate systems.", job.responsibilities)
        self.assertTrue((job.minimum_qualifications or "").startswith("Ten years."))

    def test_list_payload_rejects_missing_required_shape(self):
        with self.assertRaises(ValueError):
            self.nvidia.parse_payload(json.dumps({"total": 1}).encode(), "2026-07-13T16:00:00+00:00")

    def test_workday_zero_total_page_remains_valid(self):
        document = _load_list(json.dumps({"total": 0, "jobPostings": [LIST_RECORD]}).encode())
        self.assertEqual(1, len(document["jobPostings"]))


if __name__ == "__main__":
    unittest.main()
