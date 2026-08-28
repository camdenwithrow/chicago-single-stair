import unittest

import duckdb

from single_stair.visualization import _candidate_query


class VisualizationExportTests(unittest.TestCase):
    def test_candidate_export_uses_independent_opportunity_flags(self) -> None:
        connection = duckdb.connect()
        connection.execute(
            """
            CREATE TABLE read_source AS SELECT
                1 objectid, 'pin' pin, -87.6 centroid_lon, 41.8 centroid_lat,
                'Loop' community_area_name, 'RM-5' canonical_zone_class,
                'RM-5.5' upzoned_zone_class, 500.0 transit_distance_ft,
                'CTA' nearest_transit_agency, false is_city_owned, false is_vacant,
                true is_underbuilt, false requires_legal_or_site_review, '' review_reasons,
                0.7 median_need_score, true median_need_high_need_low_supply,
                2 current_two_stair_three_bedroom_capacity,
                3 current_single_stair_three_bedroom_capacity,
                4 upzoned_single_stair_three_bedroom_capacity,
                true has_any_modeled_capacity
            """
        )
        query = _candidate_query().replace("read_parquet(?, union_by_name=true)", "read_source")
        rows = connection.execute(query).fetchall()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 1)


if __name__ == "__main__":
    unittest.main()
