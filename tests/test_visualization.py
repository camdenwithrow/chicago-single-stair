import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb
import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

from single_stair.visualization import _candidate_query, _ward_geojson


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

    def test_ward_export_joins_three_bedroom_scenario_metrics(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            boundary_source = root / "boundaries"
            summary_source = root / "summary"
            boundary_source.mkdir()
            summary_source.mkdir()
            boundaries = gpd.GeoDataFrame(
                {"ward": ["1", "2"]},
                geometry=[
                    Polygon([(-88, 41), (-87, 41), (-87, 42), (-88, 42)]),
                    Polygon([(-87, 41), (-86, 41), (-86, 42), (-87, 42)]),
                ],
                crs="EPSG:4326",
            )
            boundaries.to_parquet(boundary_source / "part-00001.parquet")
            pd.DataFrame(
                {
                    "ward": ["1", "1"],
                    "capacity_scenario_id": [
                        "current_two_stair",
                        "current_single_stair",
                    ],
                    "bedroom_category": ["three_bedroom", "three_bedroom"],
                    "modeled_capacity_units": [100, 125],
                    "incremental_capacity_vs_current_two_stair_units": [0, 25],
                    "capacity_gain_parcel_count": [0, 8],
                    "requires_legal_or_site_review_parcel_count": [20, 20],
                }
            ).to_parquet(summary_source / "part-00001.parquet")

            wards = _ward_geojson(boundary_source, summary_source)

        properties = wards["features"][0]["properties"]
        self.assertEqual(properties["ward"], "1")
        self.assertEqual(
            properties["current_single_stair_incremental_capacity_vs_current_two_stair_units"],
            25,
        )
        ward_without_summary = wards["features"][1]["properties"]
        self.assertIsNone(
            ward_without_summary[
                "current_single_stair_incremental_capacity_vs_current_two_stair_units"
            ]
        )


if __name__ == "__main__":
    unittest.main()
