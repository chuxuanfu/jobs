from __future__ import annotations

import unittest
import math

from job_monitor.filters.location import evaluate_location, haversine_miles
from job_monitor.models import Job, Location


CONFIG = {
    "center_latitude": 37.363947,
    "center_longitude": -121.928937,
    "radius_miles": 20.0,
    "nearby_cities": ["San Jose"],
}


def job_at_distance(distance_miles: float) -> Job:
    miles_per_degree_latitude = 2 * math.pi * 3958.7613 / 360
    latitude = CONFIG["center_latitude"] + distance_miles / miles_per_degree_latitude
    return Job(
        company="test", source_name="test", source_job_id=str(distance_miles),
        source_url=None, apply_url=None, canonical_url=None, source_adapter_version="1",
        title="Engineer", normalized_title="Engineer", employment_type="FullTime",
        locations=[Location(raw="coordinate", latitude=latitude, longitude=CONFIG["center_longitude"])],
        is_us_job=True,
    )


class LocationTests(unittest.TestCase):
    def test_haversine_zero(self):
        self.assertEqual(0.0, haversine_miles(1, 2, 1, 2))

    def test_radius_boundaries(self):
        for distance, expected in ((19.9, True), (20.0, True), (20.1, False)):
            status, eligible, review, actual = evaluate_location(job_at_distance(distance), CONFIG)
            self.assertEqual(expected, eligible, (distance, actual, status))
            self.assertFalse(review)


if __name__ == "__main__":
    unittest.main()
