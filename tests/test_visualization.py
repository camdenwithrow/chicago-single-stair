import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import duckdb
import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

from single_stair.visualization import (
    _candidate_query,
    _community_area_geojson,
    _ward_geojson,
    write_visualization_config,
)


class VisualizationConfigTests(unittest.TestCase):
    def test_only_public_map_settings_are_generated_and_safely_serialized(self) -> None:
        settings = {
            "PROTOMAPS_API_KEY": 'fake-key";\n\\test\u2028',
            "PROTOMAPS_URL": "https://example.com/{z}/{x}/{y}.mvt",
            "CENSUS_API_KEY": "private-census-credential",
            "SOCRATA_APP_TOKEN": "private-socrata-credential",
        }
        with TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "web" / "config.js"
            with patch.dict(os.environ, settings, clear=True):
                self.assertEqual(write_visualization_config(output), output)
            script = output.read_text("utf-8")
        config = json.loads(script.split("window.SINGLE_STAIR_CONFIG = ", 1)[1].strip()[:-1])
        self.assertEqual(
            config,
            {
                "protomapsApiKey": settings["PROTOMAPS_API_KEY"],
                "protomapsUrl": settings["PROTOMAPS_URL"],
            },
        )
        self.assertNotIn(settings["CENSUS_API_KEY"], script)
        self.assertNotIn(settings["SOCRATA_APP_TOKEN"], script)

    def test_missing_variables_clear_previous_config(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "config.js"
            with patch.dict(os.environ, {"PROTOMAPS_API_KEY": "old-key"}, clear=True):
                write_visualization_config(output)
            with patch.dict(os.environ, {}, clear=True):
                write_visualization_config(output)
            script = output.read_text("utf-8")
        self.assertNotIn("old-key", script)
        self.assertIn('"protomapsApiKey": ""', script)
        self.assertIn('"protomapsUrl": ""', script)


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

    def test_community_area_export_uses_official_names_and_boundaries(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            boundary_source = root / "boundaries"
            summary_source = root / "summary"
            boundary_source.mkdir()
            summary_source.mkdir()
            gpd.GeoDataFrame(
                {"area_numbe": ["22"], "community": ["LOGAN SQUARE"]},
                geometry=[Polygon([(-88, 41), (-87, 41), (-87, 42), (-88, 42)])],
                crs="EPSG:4326",
            ).to_parquet(boundary_source / "part-00001.parquet")
            pd.DataFrame(
                {
                    "community_area_number": ["22"],
                    "capacity_scenario_id": ["current_single_stair"],
                    "bedroom_category": ["three_bedroom"],
                    "modeled_capacity_units": [125],
                    "incremental_capacity_vs_current_two_stair_units": [25],
                    "capacity_gain_parcel_count": [8],
                    "requires_legal_or_site_review_parcel_count": [20],
                }
            ).to_parquet(summary_source / "part-00001.parquet")

            community_areas = _community_area_geojson(boundary_source, summary_source)

        properties = community_areas["features"][0]["properties"]
        self.assertEqual(properties["community_area_number"], "22")
        self.assertEqual(properties["community_area_name"], "LOGAN SQUARE")
        self.assertEqual(
            properties["current_single_stair_incremental_capacity_vs_current_two_stair_units"],
            25,
        )


if __name__ == "__main__":
    unittest.main()
