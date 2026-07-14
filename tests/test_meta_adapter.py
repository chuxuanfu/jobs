from __future__ import annotations

import json
from pathlib import Path
import unittest

from job_monitor.adapters.meta import MetaAdapter, parse_job_posting, parse_sitemap
from job_monitor.config import load_company_config, load_settings, project_paths
from job_monitor.filters.location import apply_basic_filters


ROOT = Path(__file__).resolve().parents[1]


RECORD = {
    "@context": "http://schema.org/",
    "@type": "JobPosting",
    "title": "Test Engineer",
    "description": "Official introduction.",
    "responsibilities": "Build systems.&nbsp;Test systems.",
    "qualifications": "Five years.&nbsp;Bachelor's degree.",
    "datePosted": "2026-07-10T14:27:18-07:00",
    "validThrough": "2026-08-12T20:25:52-07:00",
    "employmentType": "Full-time",
    "jobLocation": [{
        "@type": "Place",
        "name": "Menlo Park, CA",
        "address": {
            "@type": "PostalAddress", "addressLocality": "Menlo Park",
            "addressRegion": "CA", "addressCountry": {"@type": "Country", "name": ["USA"]},
        },
    }],
    "baseSalary": {
        "@type": "MonetaryAmount", "currency": "USD",
        "value": {"@type": "QuantitativeValue", "minValue": 150000, "maxValue": 200000, "unitText": "YEAR"},
    },
}


class MetaAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        paths = project_paths(ROOT)
        cls.settings = load_settings(paths)
        cls.adapter = MetaAdapter(load_company_config(paths, "meta"), cls.settings)

    def test_sitemap_parser_keeps_official_job_ids(self):
        payload = b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://www.metacareers.com/profile/job_details/123456/</loc><lastmod>2026-07-13T00:00:00-07:00</lastmod></url></urlset>'
        entries = parse_sitemap(payload)
        self.assertEqual(1, len(entries))
        self.assertIn("123456", entries[0]["url"])

    def test_json_ld_parses_structured_official_fields(self):
        payload = f'<html><script type="application/ld+json">{json.dumps(RECORD)}</script></html>'.encode()
        parsed = parse_job_posting(payload)
        job = self.adapter._job_from_record(
            "123456", {"url": "https://www.metacareers.com/profile/job_details/123456/", "lastmod": None},
            parsed, payload, "https://www.metacareers.com/profile/job_details/123456/", "2026-07-13T16:00:00+00:00",
        )
        apply_basic_filters(job, self.settings)
        self.assertEqual("Build systems.\nTest systems.", job.responsibilities)
        self.assertEqual("Five years.\nBachelor's degree.", job.required_qualifications)
        self.assertEqual(150000.0, job.salary_min)
        self.assertEqual("Menlo Park", job.primary_city)
        self.assertTrue(job.is_eligible_by_basic_filters)


if __name__ == "__main__":
    unittest.main()
