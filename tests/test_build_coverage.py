import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

from single_stair.build_coverage import (
    build_addition_geojson,
    build_map_scenario,
    build_screening_summary,
    potential_build_review,
    write_build_audit,
)
from single_stair.illinois_build import enrich_build_parcels
from single_stair.zoning_filters import coverage_area_geojson, coverage_parcel_geojson


def coverage_fixture() -> pd.DataFrame:
    common = {
        "pin": "example",
        "analysis_lot_area_sqft": 3125,
        "current_zoning_unit_limit": 1,
        "pinu": 0,
        "parceltype": 1,
        "has_land_area_mismatch": False,
        "review_reasons": "",
        "ward": "1",
        "community_area_number": "1",
        "centroid_lon": -87.7,
        "centroid_lat": 41.9,
    }
    return enrich_build_parcels(
        pd.DataFrame(
            [
                common
                | {
                    "objectid": 1,
                    "zone_class": "RM-5",
                    "zoning": "RM-5",
                    "current_single_stair": True,
                },
                common
                | {
                    "objectid": 2,
                    "zone_class": "RS-3",
                    "zoning": "RS-3",
                    "current_single_stair": False,
                },
                common
                | {
                    "objectid": 3,
                    "zone_class": "RT-4",
                    "zoning": "RT-4",
                    "current_single_stair": False,
                },
                common
                | {
                    "objectid": 4,
                    "zone_class": "PD",
                    "zoning": "PD",
                    "current_single_stair": False,
                },
            ]
        )
    )


class BuildCoverageTests(unittest.TestCase):
    def test_coverage_union_and_review_are_distinct_and_reconcile(self) -> None:
        coverage = coverage_fixture()
        scenarios = ["current_single_stair", "illinois_build"]
        points = coverage_parcel_geojson(coverage, scenarios)
        self.assertEqual([feature["id"] for feature in points["features"]], ["1", "2"])
        self.assertEqual(potential_build_review(coverage).tolist(), [False, False, True, False])
        summary = build_screening_summary(coverage)
        self.assertEqual(summary["screened_additional_parcel_records"], 1)
        self.assertEqual(summary["potential_additions_requiring_review"], 1)
        self.assertEqual(summary["unassessed_district_parcel_records"], 1)
        boundary = gpd.GeoDataFrame(
            {"ward": ["1"]},
            geometry=[Polygon([(-88, 41), (-87, 41), (-87, 42), (-88, 42)])],
            crs="EPSG:4326",
        )
        area = coverage_area_geojson(
            boundary, coverage, scenarios, boundary_key="ward", parcel_key="ward"
        )
        properties = area["features"][0]["properties"]
        self.assertEqual(properties["current_single_stair_parcel_count"], 1)
        self.assertEqual(properties["illinois_build_parcel_count"], 2)

    def test_only_addition_parcel_geometry_exported_not_zoning_district(self) -> None:
        coverage = coverage_fixture()
        geometry = gpd.GeoDataFrame(
            {"objectid": [1, 2, 3, 4]},
            geometry=[Polygon([(-88, 41), (-87, 41), (-87, 42), (-88, 42)])] * 4,
            crs="EPSG:4326",
        )
        with TemporaryDirectory() as temporary:
            source = Path(temporary)
            geometry.to_parquet(source / "part-00001.parquet")
            result = build_addition_geojson(source, coverage)
        self.assertEqual(len(result["features"]), 1)
        feature = result["features"][0]
        self.assertEqual(feature["id"], "build:RS-3:1")
        self.assertEqual(feature["geometry"]["type"], "Polygon")
        self.assertTrue(feature["properties"]["illinois_build"])
        self.assertFalse(feature["properties"]["current_single_stair"])
        self.assertEqual(feature["properties"]["coverage_kind"], "build_added_footprint")
        self.assertEqual(feature["properties"]["parcel_count"], 1)
        json.dumps(result, allow_nan=False)

    def test_footprint_union_preserves_area_without_filling_unscreened_gaps(self) -> None:
        from shapely.geometry import shape

        coverage = coverage_fixture().iloc[[1, 1, 1]].copy()
        coverage["objectid"] = [2, 5, 6]
        geometry = gpd.GeoDataFrame(
            {"objectid": [2, 5, 6]},
            geometry=[
                Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
                Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),
                Polygon([(3, 0), (4, 0), (4, 1), (3, 1)]),
            ],
            crs="EPSG:4326",
        )
        with TemporaryDirectory() as temporary:
            source = Path(temporary)
            geometry.to_parquet(source / "part-00001.parquet")
            result = build_addition_geojson(source, coverage)
        self.assertEqual(len(result["features"]), 1)
        feature = result["features"][0]
        footprint = shape(feature["geometry"])
        self.assertEqual(feature["properties"]["parcel_count"], 3)
        self.assertEqual(footprint.area, 3)
        self.assertEqual(len(footprint.geoms), 2)

    def test_audit_preserves_excluded_and_unassessed_records(self) -> None:
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "build_screening.parquet"
            write_build_audit(coverage_fixture(), output)
            audit = pd.read_parquet(output)
        self.assertEqual(audit.objectid.tolist(), [1, 2, 3, 4])
        self.assertEqual(audit.iloc[2].build_category, "review")
        self.assertEqual(audit.iloc[3].build_residential_eligibility, "unknown")
        self.assertEqual(build_map_scenario()["id"], "illinois_build")

    def test_empty_additions_do_not_require_parcel_geometry_io(self) -> None:
        coverage = coverage_fixture().iloc[:1]
        self.assertEqual(build_addition_geojson(Path("not-present"), coverage)["features"], [])


if __name__ == "__main__":
    unittest.main()
