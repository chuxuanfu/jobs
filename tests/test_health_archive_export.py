from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from job_monitor.adapters.openai import OpenAIAdapter
from job_monitor.archive import archive_fetch, read_archive_manifest, read_archived_payload
from job_monitor.config import load_company_config, load_settings, project_paths
from job_monitor.health import evaluate_health
from job_monitor.models import FetchResult, RawArtifact


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

    def test_incremental_first_page_with_no_new_details_is_healthy(self):
        result = FetchResult(
            "google", "https://example.test", "2026-07-13T00:00:00+00:00",
            200, {}, b"<main></main>", [], source_item_count=20,
            parsed_item_count=20, snapshot_complete=False,
        )
        health = evaluate_health(result, 20, load_settings(project_paths(ROOT)))
        self.assertTrue(health.healthy)
        self.assertEqual(20, health.fetched_count)

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

    def test_multiple_raw_responses_are_archived_separately(self):
        artifacts = [
            RawArtifact("https://example.test/list", "2026-07-13T00:00:00+00:00", 200, {"content-type": "application/json"}, b'{"items":[]}', "list", "application/json"),
            RawArtifact("https://example.test/job/1", "2026-07-13T00:00:01+00:00", 200, {"content-type": "text/html"}, b"<html>job</html>", "job_1", "text/html"),
        ]
        result = FetchResult("test", artifacts[0].request_url, artifacts[0].fetched_at, 200, {}, b"", [], artifacts=artifacts)
        with tempfile.TemporaryDirectory() as directory:
            _, metadata_path = archive_fetch(Path(directory), result, "test", "America/Los_Angeles")
            metadata = read_archive_manifest(metadata_path)
            self.assertEqual(2, metadata["artifact_count"])
            self.assertTrue(metadata["artifacts"][0]["path"].endswith(".json.gz"))
            self.assertTrue(metadata["artifacts"][1]["path"].endswith(".html.gz"))

    def test_xml_raw_response_uses_xml_extension(self):
        artifact = RawArtifact("https://example.test/sitemap", "2026-07-13T00:00:00+00:00", 200, {"content-type": "application/xml"}, b"<urlset/>", "sitemap", "application/xml")
        result = FetchResult("test", artifact.request_url, artifact.fetched_at, 200, artifact.response_headers, artifact.raw_body, [], artifacts=[artifact])
        with tempfile.TemporaryDirectory() as directory:
            archive, _ = archive_fetch(Path(directory), result, "test", "America/Los_Angeles")
            self.assertTrue(str(archive).endswith(".xml.gz"))


if __name__ == "__main__":
    unittest.main()
