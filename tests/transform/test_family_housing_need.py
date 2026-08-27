import math
import unittest

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

from single_stair.transform.family_housing_need import (
    _calculate_rankings,
    calculate_family_housing_need,
    load_family_housing_need_config,
)


def _table(values: dict[str, int]) -> pd.DataFrame:
    return pd.DataFrame([{"state": "17", "county": "031", "tract": "010100", **values}])


class FamilyHousingNeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_family_housing_need_config()
        self.geometry = gpd.GeoDataFrame(
            {"GEOID": ["17031010100"]},
            geometry=[Polygon([(-87.7, 41.8), (-87.6, 41.8), (-87.6, 41.9), (-87.7, 41.8)])],
            crs="EPSG:4269",
        )
        self.tables = {
            "B11005": _table(
                {
                    "B11005_001E": 1000,
                    "B11005_001M": 100,
                    "B11005_002E": 300,
                    "B11005_002M": 30,
                    "B11005_003E": 280,
                    "B11005_003M": 28,
                }
            ),
            "B25115": _table(
                {
                    "B25115_015E": 400,
                    "B25115_015M": 40,
                    "B25115_016E": 300,
                    "B25115_016M": 30,
                    "B25115_018E": 150,
                    "B25115_018M": 10,
                    "B25115_022E": 50,
                    "B25115_022M": 10,
                    "B25115_025E": 50,
                    "B25115_025M": 10,
                }
            ),
            "B25042": _table(
                {
                    "B25042_009E": 400,
                    "B25042_009M": 40,
                    "B25042_010E": 10,
                    "B25042_010M": 2,
                    "B25042_011E": 90,
                    "B25042_011M": 9,
                    "B25042_012E": 100,
                    "B25042_012M": 10,
                    "B25042_013E": 120,
                    "B25042_013M": 12,
                    "B25042_014E": 60,
                    "B25042_014M": 6,
                    "B25042_015E": 20,
                    "B25042_015M": 2,
                }
            ),
            "B25014": _table(
                {
                    "B25014_008E": 400,
                    "B25014_008M": 40,
                    "B25014_009E": 300,
                    "B25014_009M": 30,
                    "B25014_010E": 50,
                    "B25014_010M": 5,
                    "B25014_011E": 30,
                    "B25014_011M": 3,
                    "B25014_012E": 15,
                    "B25014_012M": 2,
                    "B25014_013E": 5,
                    "B25014_013M": 1,
                }
            ),
            "B25070": _table(
                {
                    "B25070_001E": 400,
                    "B25070_001M": 40,
                    "B25070_002E": 20,
                    "B25070_002M": 2,
                    "B25070_003E": 30,
                    "B25070_003M": 3,
                    "B25070_004E": 40,
                    "B25070_004M": 4,
                    "B25070_005E": 50,
                    "B25070_005M": 5,
                    "B25070_006E": 60,
                    "B25070_006M": 6,
                    "B25070_007E": 70,
                    "B25070_007M": 7,
                    "B25070_008E": 40,
                    "B25070_008M": 4,
                    "B25070_009E": 30,
                    "B25070_009M": 3,
                    "B25070_010E": 20,
                    "B25070_010M": 2,
                    "B25070_011E": 40,
                    "B25070_011M": 4,
                }
            ),
        }

    def test_calculates_family_supply_gap_overcrowding_and_burden(self) -> None:
        result = calculate_family_housing_need(
            self.tables,
            self.geometry,
            {"17031010100"},
            self.config,
        ).iloc[0]

        self.assertEqual(result["renter_households_with_children_estimate"], 250)
        self.assertAlmostEqual(result["renter_households_with_children_moe"], math.sqrt(300))
        self.assertEqual(result["renter_occupied_3_plus_bedroom_units_estimate"], 200)
        self.assertEqual(result["family_housing_3_plus_gap_median_estimate"], 50)
        self.assertLess(
            result["family_housing_3_plus_gap_conservative_estimate"],
            result["family_housing_3_plus_gap_median_estimate"],
        )
        self.assertGreater(
            result["family_housing_3_plus_gap_progressive_estimate"],
            result["family_housing_3_plus_gap_median_estimate"],
        )
        self.assertEqual(result["renter_overcrowded_units_estimate"], 50)
        self.assertAlmostEqual(result["renter_overcrowding_rate_pct"], 12.5)
        self.assertEqual(result["renter_cost_burdened_households_estimate"], 160)
        self.assertAlmostEqual(result["renter_cost_burden_rate_pct"], 160 / 360 * 100)
        self.assertEqual(result["census_tract_geoid"], "17031010100")
        self.assertEqual(result.geometry.geom_type, "Polygon")

    def test_census_negative_sentinel_becomes_missing(self) -> None:
        self.tables["B25042"].loc[0, "B25042_013E"] = -666666666

        result = calculate_family_housing_need(
            self.tables,
            self.geometry,
            {"17031010100"},
            self.config,
        ).iloc[0]

        self.assertTrue(pd.isna(result["renter_occupied_3_plus_bedroom_units_estimate"]))
        self.assertTrue(pd.isna(result["family_housing_3_plus_gap_median_estimate"]))

    def test_rankings_make_high_need_low_supply_rule_explicit(self) -> None:
        frame = pd.DataFrame(
            {
                "renter_households_with_children_estimate": [10, 20, 30, 40],
                "renter_households_with_children_moe": [1, 1, 1, 1],
                "renter_occupied_3_plus_bedroom_units_estimate": [40, 30, 20, 10],
                "renter_occupied_3_plus_bedroom_units_moe": [1, 1, 1, 1],
                "renter_households_with_children_share_pct": [10, 20, 30, 40],
                "renter_households_with_children_share_moe_pct": [0, 0, 0, 0],
                "renter_occupied_3_plus_bedroom_share_pct": [40, 30, 20, 10],
                "renter_occupied_3_plus_bedroom_share_moe_pct": [0, 0, 0, 0],
                "renter_overcrowded_units_estimate": [1, 2, 3, 4],
                "renter_overcrowded_units_moe": [0, 0, 0, 0],
                "renter_overcrowding_rate_pct": [1, 2, 3, 4],
                "renter_overcrowding_rate_moe_pct": [0, 0, 0, 0],
                "renter_cost_burdened_households_estimate": [10, 20, 30, 40],
                "renter_cost_burdened_households_moe": [0, 0, 0, 0],
                "renter_cost_burden_rate_pct": [10, 20, 30, 40],
                "renter_cost_burden_rate_moe_pct": [0, 0, 0, 0],
            }
        )

        _calculate_rankings(frame, self.config)

        self.assertFalse(frame.loc[0, "median_need_high_need_low_supply"])
        self.assertTrue(frame.loc[3, "median_need_high_need_low_supply"])
        self.assertLess(frame.loc[0, "median_need_score"], frame.loc[3, "median_need_score"])

    def test_uncertainty_scores_are_monotonic_against_one_reference_distribution(self) -> None:
        frame = pd.DataFrame(
            {
                "renter_households_with_children_estimate": [20, 30, 40, 50],
                "renter_households_with_children_moe": [2, 3, 4, 5],
                "renter_occupied_3_plus_bedroom_units_estimate": [50, 40, 30, 20],
                "renter_occupied_3_plus_bedroom_units_moe": [5, 4, 3, 2],
                "renter_households_with_children_share_pct": [20, 30, 40, 50],
                "renter_households_with_children_share_moe_pct": [2, 3, 4, 5],
                "renter_occupied_3_plus_bedroom_share_pct": [50, 40, 30, 20],
                "renter_occupied_3_plus_bedroom_share_moe_pct": [5, 4, 3, 2],
                "renter_overcrowded_units_estimate": [5, 10, 15, 20],
                "renter_overcrowded_units_moe": [1, 1, 1, 1],
                "renter_overcrowding_rate_pct": [5, 10, 15, 20],
                "renter_overcrowding_rate_moe_pct": [1, 1, 1, 1],
                "renter_cost_burdened_households_estimate": [20, 30, 40, 50],
                "renter_cost_burdened_households_moe": [2, 3, 4, 5],
                "renter_cost_burden_rate_pct": [20, 30, 40, 50],
                "renter_cost_burden_rate_moe_pct": [2, 3, 4, 5],
            }
        )

        _calculate_rankings(frame, self.config)

        self.assertTrue((frame["conservative_need_score"] <= frame["median_need_score"]).all())
        self.assertTrue((frame["median_need_score"] <= frame["progressive_need_score"]).all())


if __name__ == "__main__":
    unittest.main()
