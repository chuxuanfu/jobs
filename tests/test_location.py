from __future__ import annotations

import unittest

from job_monitor.filters.location import evaluate_location
from job_monitor.models import Job, Location


CONFIG = {
    "eligible_cities": [
        "San Francisco", "South San Francisco", "San Mateo", "Redwood City",
        "Palo Alto", "Mountain View", "Sunnyvale", "Santa Clara", "San Jose",
    ],
}


def make_job(*locations: Location, workplace_type: str = "hybrid") -> Job:
    return Job(
        company="test", source_name="test", source_job_id="job",
        source_url=None, apply_url=None, canonical_url=None, source_adapter_version="1",
        title="Engineer", normalized_title="Engineer", employment_type="FullTime",
        workplace_type=workplace_type, locations=list(locations), is_us_job=True,
    )


class LocationTests(unittest.TestCase):
    def test_bay_area_corridor_cities_are_eligible(self):
        for city in ("San Francisco", "Redwood City", "Mountain View", "San Jose"):
            status, eligible, review, _ = evaluate_location(
                make_job(Location(raw=f"{city}, CA", city=city, state="CA", country="United States")),
                CONFIG,
            )
            self.assertTrue(eligible, city)
            self.assertEqual("eligible_by_bay_area_city", status)
            self.assertFalse(review)

    def test_hybrid_job_with_explicit_remote_secondary_location_is_eligible(self):
        job = make_job(
            Location(raw="New York City", city="New York", state="NY", country="United States"),
            Location(raw="Remote - US", country="United States"),
            workplace_type="hybrid",
        )
        status, eligible, review, _ = evaluate_location(job, CONFIG)
        self.assertTrue(eligible)
        self.assertEqual("eligible_explicit_remote_us", status)
        self.assertFalse(review)

    def test_outside_city_is_excluded(self):
        status, eligible, review, _ = evaluate_location(
            make_job(Location(raw="New York City", city="New York", state="NY", country="United States")),
            CONFIG,
        )
        self.assertFalse(eligible)
        self.assertEqual("outside_bay_area_city_list", status)
        self.assertFalse(review)


if __name__ == "__main__":
    unittest.main()
