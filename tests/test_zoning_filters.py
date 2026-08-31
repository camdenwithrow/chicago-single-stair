import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

from single_stair.zoning_filters import (
    coverage_area_geojson,
    coverage_parcel_geojson,
    current_zoning_config,
    current_zoning_geojson,
    current_zoning_tier,
    read_coverage_parcels,
)


class ZoningCoverageTests(unittest.TestCase):
    def test_exact_publisher_allowlist_preserves_district_families(self):
        expected = {
            "RM-5",
            "RM-5.5",
            "RM-6",
            "RM-6.5",
            "B2-3",
            "B2-5",
            "B1-3",
            "B1-5",
            "B3-3",
            "B3-5",
            "C1-3",
            "C1-5",
            "C2-3",
            "C2-5",
        }
        self.assertEqual(
            {zone for zones in current_zoning_config()["tiers"].values() for zone in zones},
            expected,
        )
        for zone in expected:
            self.assertIsNotNone(current_zoning_tier(zone))
        for zone in ["RS-3", "RT-4", "RM-4.5", "B2-2", "C3-3", "B-3", "PD", None]:
            self.assertIsNone(current_zoning_tier(zone))
        self.assertEqual(current_zoning_tier(" b2-3 "), "res_7")
        self.assertEqual(current_zoning_tier("B1-3"), "com_7")

    def test_zoning_polygons_keep_original_geometry_and_ids(self):
        polygon = Polygon([(-88, 41), (-87, 41), (-87, 42), (-88, 42)])
        with TemporaryDirectory() as directory:
            gpd.GeoDataFrame(
                {"objectid": [7, 8], "zone_class": ["B2-3", "B2-2"]},
                geometry=[polygon, polygon],
                crs="EPSG:4326",
            ).to_parquet(Path(directory) / "part-00001.parquet")
            data = current_zoning_geojson(Path(directory))
        self.assertEqual(len(data["features"]), 1)
        feature = data["features"][0]
        self.assertEqual(feature["id"], "7")
        self.assertEqual(feature["geometry"]["type"], "Polygon")
        self.assertEqual(feature["properties"]["tier"], "res_7")

    def test_coverage_counts_all_selected_records_and_zero_areas(self):
        parcels = pd.DataFrame(
            {
                "objectid": [1, 2, 3, 4],
                "ward": ["1", "1", "1", None],
                "centroid_lon": [-87.6, -87.6, -87.6, None],
                "centroid_lat": [41.8, 41.8, 41.8, None],
                "current_single_stair": [True, True, False, True],
                "illinois_build": [True, True, True, True],
                "vacant": [False, True, False, False],
                "build_reason": ["test"] * 4,
            }
        )
        polygon = Polygon([(-88, 41), (-87, 41), (-87, 42), (-88, 42)])
        boundaries = gpd.GeoDataFrame(
            {"ward": ["1", "2"]}, geometry=[polygon, polygon], crs="EPSG:4326"
        )
        scenario_ids = ["current_single_stair", "illinois_build"]
        areas = coverage_area_geojson(
            boundaries, parcels, scenario_ids, boundary_key="ward", parcel_key="ward"
        )
        first, second = [feature["properties"] for feature in areas["features"]]
        self.assertEqual(first["current_single_stair_parcel_count"], 2)
        self.assertEqual(first["illinois_build_parcel_count"], 3)
        self.assertEqual(second["current_single_stair_parcel_count"], 0)
        points = coverage_parcel_geojson(parcels, scenario_ids)
        self.assertEqual(len(points["features"]), 3)
        self.assertFalse(points["features"][0]["properties"]["vacant"])
        self.assertEqual(points["features"][0]["properties"]["build_reason"], "test")

    def test_parcel_reader_uses_source_classes_not_capacity_or_candidate_flags(self):
        rows = pd.DataFrame(
            {
                "objectid": [1, 2, 3],
                "pin": ["a", "b", "c"],
                "pinu": [None] * 3,
                "parceltype": ["land"] * 3,
                "centroid_lon": [-87.6] * 3,
                "centroid_lat": [41.8] * 3,
                "zone_class": ["B2-3", "B1-3", "B2-2"],
                "zoning_objectid": [7] * 3,
                "ward": ["1"] * 3,
                "community_area_number": ["22"] * 3,
                "community_area_name": ["LOGAN SQUARE"] * 3,
                "analysis_lot_area_sqft": [3125.0] * 3,
                "current_zoning_unit_limit": [3] * 3,
                "lot_area_source": ["parcel_geometry"] * 3,
                "has_land_area_mismatch": [False] * 3,
                "requires_legal_or_site_review": [False] * 3,
                "review_reasons": [None] * 3,
                "parcel_geometry_area_sqft": [3125.0] * 3,
                "transit_distance_ft": [None] * 3,
                "nearest_transit_agency": [None] * 3,
                "is_city_owned": [False] * 3,
                "is_vacant": [False] * 3,
                "is_underbuilt": [False] * 3,
                "median_need_high_need_low_supply": [False] * 3,
            }
        )
        with TemporaryDirectory() as directory:
            source = Path(directory)
            rows.to_parquet(source / "part-00001.parquet")
            frame = read_coverage_parcels(source)
            self.assertEqual(frame.current_single_stair.tolist(), [True, True, False])
            self.assertEqual(frame.tier.iloc[:2].tolist(), ["res_7", "com_7"])
            pd.concat([rows, rows.iloc[:1]]).to_parquet(source / "part-00001.parquet")
            with self.assertRaisesRegex(ValueError, "unique"):
                read_coverage_parcels(source)


if __name__ == "__main__":
    unittest.main()
