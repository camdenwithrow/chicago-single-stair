import unittest

from single_stair.ingest.chicago_boundaries import (
    COMMUNITY_AREA_BOUNDARIES,
    WARD_BOUNDARIES,
)
from single_stair.ingest.socrata_geometry import _feature_collection


class ChicagoBoundaryTests(unittest.TestCase):
    def test_official_boundary_contracts(self) -> None:
        self.assertEqual(WARD_BOUNDARIES.table.dataset_id, "p293-wvbd")
        self.assertEqual(WARD_BOUNDARIES.table.key_type, "number")
        self.assertEqual(COMMUNITY_AREA_BOUNDARIES.table.dataset_id, "igwz-8jzy")
        self.assertEqual(COMMUNITY_AREA_BOUNDARIES.table.key, "area_num_1")

    def test_converts_geometry_without_duplicating_source_field(self) -> None:
        records = [
            {
                "ward": "1",
                "the_geom": {
                    "type": "Polygon",
                    "coordinates": [[[-87.7, 41.8], [-87.6, 41.8], [-87.7, 41.9], [-87.7, 41.8]]],
                },
            }
        ]

        payload = _feature_collection(records, WARD_BOUNDARIES)

        self.assertEqual(payload["features"][0]["properties"], {"ward": "1"})
        self.assertEqual(payload["features"][0]["geometry"]["type"], "Polygon")


if __name__ == "__main__":
    unittest.main()
