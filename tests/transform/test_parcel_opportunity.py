import tempfile
import unittest
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from shapely.geometry import Polygon

from single_stair.transform.parcel_opportunity import (
    _building_summary,
    enrich_parcel_opportunity,
    load_opportunity_config,
    normalize_zoning_class,
)


class ParcelOpportunityTests(unittest.TestCase):
    def test_prorates_building_area_allocated_across_tax_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            snapshot = Path(temporary_directory)
            records = [
                {
                    "pin": "11111111111111",
                    "char_bldg_sf": "1000",
                    "char_land_sf": "3000",
                    "tieback_proration_rate": "0.5",
                    "char_use": "Single-Family",
                    "char_apts": None,
                },
                {
                    "pin": "11111111111111",
                    "char_bldg_sf": "1000",
                    "char_land_sf": "3000",
                    "tieback_proration_rate": "0.5",
                    "char_use": "Single-Family",
                    "char_apts": None,
                },
            ]
            pq.write_table(pa.Table.from_pylist(records), snapshot / "part-00001.parquet")

            summary = _building_summary(snapshot)

        self.assertEqual(summary.loc["11111111111111", "existing_building_sqft"], 1000)
        self.assertEqual(summary.loc["11111111111111", "existing_building_sqft_unprorated"], 2000)
        self.assertTrue(summary.loc["11111111111111", "has_prorated_building_area"])

    def test_normalizes_supported_zoning_and_excludes_c3(self) -> None:
        config = load_opportunity_config()

        self.assertEqual(normalize_zoning_class("RM5.5", config), "RM-5.5")
        self.assertEqual(normalize_zoning_class("RT-4A", config), "RT-4")
        self.assertEqual(normalize_zoning_class("B3-2", config), "B-2")
        self.assertIsNone(normalize_zoning_class("C3-3", config))
        self.assertIsNone(normalize_zoning_class("PD 30", config))

    def test_calculates_far_capacity_and_flags(self) -> None:
        parcels = gpd.GeoDataFrame(
            {
                "objectid": [1, 2, 3],
                "pin": ["11111111111111", "22222222222222", "33333333333333"],
                "pinu": [0, 0, 1001],
                "parceltype": [1, 1, 3],
                "zone_class": ["RT-4", "B3-2", "PD 30"],
            },
            geometry=[
                Polygon([(0, 0), (25, 0), (25, 125), (0, 125)]),
                Polygon([(100, 0), (125, 0), (125, 125), (100, 125)]),
                Polygon([(200, 0), (225, 0), (225, 125), (200, 125)]),
            ],
            crs="EPSG:3435",
        ).to_crs("EPSG:4326")
        buildings = pd.DataFrame(
            {
                "pin": ["11111111111111", "22222222222222"],
                "assessor_building_records": [1, 1],
                "existing_building_sqft": [1000.0, 0.0],
                "assessor_land_sqft": [3125.0, 3125.0],
                "assessor_land_area_values": [1, 1],
                "assessor_existing_units": [1.0, 0.0],
            }
        ).set_index("pin")
        city_owned = pd.DataFrame(
            {
                "pin": ["22222222222222"],
                "is_city_owned": [True],
                "city_land_status": ["Owned by City"],
                "city_managing_organization": ["DOH"],
            }
        ).set_index("pin")

        output = enrich_parcel_opportunity(
            parcels,
            building_summary=buildings,
            city_owned_summary=city_owned,
            opportunity_config=load_opportunity_config(),
            policy_id="chicago_proposed",
            estimate_id="median",
        ).set_index("objectid")

        self.assertAlmostEqual(output.loc[1, "parcel_geometry_area_sqft"], 3125, places=1)
        self.assertAlmostEqual(output.loc[1, "analysis_lot_area_sqft"], 3125, places=1)
        self.assertEqual(output.loc[1, "lot_area_source"], "assessor_building_characteristics")
        self.assertAlmostEqual(output.loc[1, "existing_built_far"], 0.32, places=2)
        self.assertEqual(output.loc[1, "current_maximum_far"], 1.2)
        self.assertEqual(output.loc[1, "upzoned_zone_class"], "RM-5")
        self.assertEqual(output.loc[1, "current_zoning_unit_limit"], 3)
        self.assertEqual(output.loc[1, "current_two_stair_three_bedroom_capacity"], 2)
        self.assertEqual(output.loc[1, "current_single_stair_three_bedroom_capacity"], 2)
        self.assertEqual(output.loc[1, "upzoned_single_stair_three_bedroom_capacity"], 4)
        self.assertTrue(output.loc[1, "is_underbuilt"])
        self.assertFalse(output.loc[1, "requires_legal_or_site_review"])

        self.assertTrue(output.loc[2, "is_city_owned"])
        self.assertTrue(output.loc[2, "is_vacant"])
        self.assertEqual(output.loc[2, "upzoned_zone_class"], "B3-3")
        self.assertIn("residential_use_conditions", output.loc[2, "review_reasons"])

        self.assertTrue(output.loc[3, "requires_legal_or_site_review"])
        self.assertIn("unsupported_or_nonresidential_zoning", output.loc[3, "review_reasons"])
        self.assertIn("unitized_parcel", output.loc[3, "review_reasons"])
        self.assertTrue(pd.isna(output.loc[3, "current_single_stair_three_bedroom_capacity"]))


if __name__ == "__main__":
    unittest.main()
