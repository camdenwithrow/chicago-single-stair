import unittest

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

from single_stair.scenarios import BEDROOM_CATEGORIES
from single_stair.transform.combine_opportunity_need import (
    _transit_band,
    aggregate_opportunity,
    enrich_opportunity_with_need,
    load_combined_analysis_config,
)


def _parcel_frame() -> gpd.GeoDataFrame:
    records: dict[str, list[object]] = {
        "objectid": [1, 2],
        "census_tract_geoid": ["17031010100", "17031010200"],
        "transit_distance_ft": [1000.0, 3000.0],
        "community_area_number": [1, 1],
        "community_area_name": ["Rogers Park", "Rogers Park"],
        "ward": [49, 49],
        "canonical_zone_class": ["RT-4", "RT-4"],
        "is_city_owned": [True, False],
        "is_underbuilt": [True, False],
        "is_vacant": [False, False],
        "requires_legal_or_site_review": [False, True],
    }
    for category in BEDROOM_CATEGORIES:
        records[f"current_two_stair_{category}_capacity"] = [1, 2]
        records[f"current_single_stair_{category}_capacity"] = [2, 2]
        records[f"upzoned_single_stair_{category}_capacity"] = [3, 4]
    return gpd.GeoDataFrame(
        records,
        geometry=[
            Polygon([(0, 0), (1, 0), (1, 1), (0, 0)]),
            Polygon([(2, 0), (3, 0), (3, 1), (2, 0)]),
        ],
        crs="EPSG:4326",
    )


def _need_frame() -> pd.DataFrame:
    values: dict[str, list[object]] = {
        "census_tract_geoid": ["17031010100"],
        "median_need_score": [0.9],
    }
    for profile in ("conservative", "median", "progressive"):
        values[f"{profile}_need_high_need_low_supply"] = [profile != "conservative"]
    return pd.DataFrame(values)


class CombineOpportunityNeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_combined_analysis_config()

    def test_retains_every_parcel_and_adds_independent_research_fields(self) -> None:
        result = enrich_opportunity_with_need(
            _parcel_frame(), _need_frame(), self.config
        ).set_index("objectid")

        self.assertEqual(len(result), 2)
        self.assertTrue(result.loc[1, "has_family_housing_need"])
        self.assertFalse(result.loc[2, "has_family_housing_need"])
        self.assertEqual(result.loc[1, "transit_band_id"], "within_quarter_mile")
        self.assertEqual(result.loc[2, "transit_band_id"], "half_to_one_mile")
        self.assertTrue(result.loc[1, "within_half_mile_transit"])
        self.assertFalse(result.loc[2, "within_half_mile_transit"])
        self.assertTrue(result.loc[1, "in_median_high_need_low_supply_tract"])
        self.assertFalse(result.loc[2, "in_median_high_need_low_supply_tract"])
        self.assertEqual(result.loc[1, "single_stair_three_bedroom_capacity_change"], 1)
        self.assertEqual(result.loc[2, "single_stair_three_bedroom_capacity_change"], 0)
        self.assertTrue(result.loc[1, "median_high_need_city_owned"])
        self.assertTrue(result["has_any_modeled_capacity"].all())

    def test_aggregates_scenarios_without_turning_flags_into_a_candidate_score(self) -> None:
        parcels = enrich_opportunity_with_need(_parcel_frame(), _need_frame(), self.config)

        summary = aggregate_opportunity(
            parcels,
            aggregation_type="ward",
            grouping_columns=("ward",),
            policy_id="chicago_proposed",
            estimate_id="median",
        )
        row = summary.loc[
            (summary["capacity_scenario_id"] == "current_single_stair")
            & (summary["bedroom_category"] == "three_bedroom")
        ].iloc[0]

        self.assertEqual(row["parcel_count"], 2)
        self.assertEqual(row["modeled_capacity_units"], 4)
        self.assertEqual(row["incremental_capacity_vs_current_two_stair_units"], 1)
        self.assertEqual(row["capacity_gain_parcel_count"], 1)
        self.assertEqual(row["within_half_mile_transit_parcel_count"], 1)
        self.assertEqual(row["is_city_owned_parcel_count"], 1)
        self.assertEqual(row["in_median_high_need_low_supply_tract_parcel_count"], 1)

    def test_aggregation_treats_missing_capacity_as_unmodeled_not_zero_gain(self) -> None:
        parcels = enrich_opportunity_with_need(_parcel_frame(), _need_frame(), self.config)
        parcels.loc[1, "current_single_stair_three_bedroom_capacity"] = pd.NA

        summary = aggregate_opportunity(
            parcels,
            aggregation_type="ward",
            grouping_columns=("ward",),
            policy_id="chicago_proposed",
            estimate_id="median",
        )
        row = summary.loc[
            (summary["capacity_scenario_id"] == "current_single_stair")
            & (summary["bedroom_category"] == "three_bedroom")
        ].iloc[0]

        self.assertEqual(row["parcel_count"], 2)
        self.assertEqual(row["modeled_parcel_count"], 1)
        self.assertEqual(row["capacity_gain_parcel_count"], 1)

    def test_transit_band_boundaries_do_not_overlap(self) -> None:
        distances = pd.Series([0, 1320, 1320.1, 2640, 2640.1, 5280, 5280.1, pd.NA])

        band_ids, _labels = _transit_band(distances, self.config)

        self.assertEqual(
            band_ids.iloc[:7].tolist(),
            [
                "within_quarter_mile",
                "within_quarter_mile",
                "quarter_to_half_mile",
                "quarter_to_half_mile",
                "half_to_one_mile",
                "half_to_one_mile",
                "over_one_mile",
            ],
        )
        self.assertTrue(pd.isna(band_ids.iloc[7]))


if __name__ == "__main__":
    unittest.main()
