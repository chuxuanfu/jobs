from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from job_monitor.adapters.openai import OpenAIAdapter
from job_monitor.archive import archive_fetch, read_archived_payload
from job_monitor.config import load_company_config, load_settings, project_paths
from job_monitor.health import evaluate_health
from job_monitor.models import FetchResult


ROOT = Path(__file__).resolve().parents[1]


class HealthArchiveTests(unittest.TestCase):
    def test_empty_response_is_unhealthy(self):
        result = FetchResult("openai", "https://example.test", "2026-07-13T00:00:00+00:00", 200, {}, b'{"jobs":[]}', [])
        health = evaluate_health(result, 100, load_settings(project_paths(ROOT)))
        self.assertFalse(health.healthy)
        self.assertIn("empty_job_feed", health.reasons)

    def test_abnormal_count_drop_is_unhealthy(self):
        paths = project_paths(ROOT)
        adapter = OpenAIAdapter(load_company_config(paths, "openai"), load_settings(paths))
        jobs = adapter.parse_payload((ROOT / "tests/fixtures/openai_jobs.json").read_bytes(), "2026-07-13T00:00:00+00:00")
        result = FetchResult("openai", "https://example.test", "2026-07-13T00:00:00+00:00", 200, {}, b"x", jobs)
        self.assertFalse(evaluate_health(result, 100, load_settings(paths)).healthy)

    def test_raw_archive_can_be_reparsed(self):
        paths = project_paths(ROOT)
        settings = load_settings(paths)
        adapter = OpenAIAdapter(load_company_config(paths, "openai"), settings)
        payload = (ROOT / "tests/fixtures/openai_jobs.json").read_bytes()
        jobs = adapter.parse_payload(payload, "2026-07-13T00:00:00+00:00")
        result = FetchResult("openai", "https://example.test", "2026-07-13T00:00:00+00:00", 200, {"content-type": "application/json"}, payload, jobs)
        with tempfile.TemporaryDirectory() as directory:
            archive, metadata = archive_fetch(Path(directory), result, "test", "America/Los_Angeles")
            reparsed = adapter.parse_payload(read_archived_payload(archive), "2026-07-14T00:00:00+00:00")
            self.assertEqual(4, len(reparsed))
            self.assertTrue(metadata.exists())


if __name__ == "__main__":
    unittest.main()
