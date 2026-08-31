import json
import unittest

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from single_stair.illinois_build import (
    BUILD_PROPERTIES,
    build_minimum_units,
    classify_build_parcel,
    enrich_build_parcels,
    load_build_policy,
)


def parcel(**changes: object) -> dict[str, object]:
    return {
        "zone_class": "RS-3",
        "analysis_lot_area_sqft": 3125,
        "current_zoning_unit_limit": 1,
        "pinu": 0,
        "parceltype": 1,
        "has_land_area_mismatch": False,
        "requires_legal_or_site_review": False,
        "review_reasons": "",
    } | changes


class BuildScreenTests(unittest.TestCase):
    def test_exact_lot_area_boundaries(self) -> None:
        for area, units in (
            (1, 1),
            (2500, 1),
            (2500.01, 4),
            (5000, 4),
            (5000.01, 6),
            (7500, 6),
            (7500.01, 8),
            (100000, 8),
        ):
            with self.subTest(area=area):
                self.assertEqual(build_minimum_units(area), units)

    def test_rejects_invalid_area_without_nonfinite_json(self) -> None:
        for area in (None, pd.NA, float("nan"), float("inf"), -1, 0, True, "bad"):
            with self.subTest(area=area):
                self.assertIsNone(build_minimum_units(area))
                result = classify_build_parcel(parcel(analysis_lot_area_sqft=area))
                self.assertFalse(result["illinois_build"])
                self.assertEqual(result["build_category"], "review")
                json.dumps(result, allow_nan=False)

    def test_clean_rs_lot_is_screened_expansion(self) -> None:
        result = classify_build_parcel(parcel())
        self.assertTrue(result["illinois_build"])
        self.assertEqual(result["build_category"], "screened_expansion")
        self.assertEqual(result["build_minimum_units"], 4)
        self.assertEqual(result["build_effective_unit_limit"], 4)
        self.assertEqual(result["build_additional_unit_allowance"], 3)
        self.assertEqual(result["build_review_reasons"], [])

    def test_higher_existing_allowance_is_never_reduced(self) -> None:
        result = classify_build_parcel(
            parcel(zone_class="RM-6", current_zoning_unit_limit=20), is_baseline=True
        )
        self.assertTrue(result["illinois_build"])
        self.assertEqual(result["build_category"], "baseline")
        self.assertEqual(result["build_effective_unit_limit"], 20)
        self.assertEqual(result["build_additional_unit_allowance"], 0)

    def test_undersized_rs2_lot_compares_to_detached_district_ceiling(self) -> None:
        source = parcel(zone_class="RS-2", current_zoning_unit_limit=0)
        result = classify_build_parcel(source)
        self.assertTrue(result["illinois_build"])
        self.assertEqual(result["build_minimum_units"], 4)
        self.assertEqual(result["build_existing_unit_comparator"], 1)
        self.assertEqual(result["build_existing_unit_limit_basis"], "rs_detached_district_ceiling")
        self.assertEqual(result["build_additional_unit_allowance"], 3)
        self.assertEqual(source["current_zoning_unit_limit"], 0)

    def test_no_increase_and_detached_only_do_not_expand_map(self) -> None:
        for changes, category in (
            ({"current_zoning_unit_limit": 4}, "no_increase"),
            ({"analysis_lot_area_sqft": 2500, "current_zoning_unit_limit": 0}, "detached_only"),
        ):
            result = classify_build_parcel(parcel(**changes))
            self.assertFalse(result["illinois_build"])
            self.assertEqual(result["build_category"], category)

    def test_conditional_and_special_use_zones_need_review(self) -> None:
        for zone in ("RT-4", "RM-4.5", "B2-2", "B1-2", "B3-2", "C1-2", "C2-2"):
            result = classify_build_parcel(parcel(zone_class=zone))
            self.assertFalse(result["illinois_build"], zone)
            self.assertEqual(result["build_category"], "review")
            self.assertTrue(result["build_review_reasons"])

    def test_raw_business_use_class_takes_precedence_over_canonical(self) -> None:
        result = classify_build_parcel(parcel(zone_class="B1-3", canonical_zone_class="B-3"))
        self.assertEqual(result["build_residential_eligibility"], "special_use")
        result = classify_build_parcel(parcel(zone_class=None, canonical_zone_class="B-3"))
        self.assertEqual(result["build_residential_eligibility"], "unknown")
        self.assertFalse(result["illinois_build"])

    def test_unknown_and_explicitly_excluded_classes_are_distinct(self) -> None:
        for zone in ("PD 123", "DX-5", "M1-1", "RS-99", "unrecognized", None):
            result = classify_build_parcel(parcel(zone_class=zone))
            self.assertEqual(result["build_residential_eligibility"], "unknown")
            self.assertFalse(result["illinois_build"])
        result = classify_build_parcel(parcel(zone_class="C3-2"))
        self.assertEqual(result["build_category"], "out_of_scope")
        self.assertIsNone(result["build_minimum_units"])

    def test_relevant_parcel_review_blocks_automatic_addition(self) -> None:
        for changes in (
            {"pinu": 1001},
            {"parceltype": 3},
            {"pinu": None},
            {"parceltype": None},
            {"has_land_area_mismatch": True},
            {"review_reasons": "inconsistent_assessor_land_area"},
            {"current_zoning_unit_limit": None},
            {"current_zoning_unit_limit": 1.5},
            {"current_zoning_unit_limit": -1},
            {"current_zoning_unit_limit": float("inf")},
            {"requires_legal_or_site_review": True},
        ):
            result = classify_build_parcel(parcel(**changes))
            self.assertFalse(result["illinois_build"], changes)
            self.assertEqual(result["build_category"], "review")

    def test_missing_building_characteristics_do_not_hide_zoning_permission(self) -> None:
        result = classify_build_parcel(
            parcel(
                requires_legal_or_site_review=True,
                review_reasons="building_characteristics_unavailable",
            )
        )
        self.assertTrue(result["illinois_build"])

    def test_all_baseline_parcels_retained_even_with_bad_data(self) -> None:
        result = classify_build_parcel({}, is_baseline=True)
        self.assertTrue(result["illinois_build"])
        self.assertEqual(result["build_category"], "baseline")
        self.assertTrue(result["build_review_reasons"])

    def test_frame_preserves_geometry_index_grain_and_input(self) -> None:
        original = gpd.GeoDataFrame(
            [
                parcel(current_single_stair=False),
                parcel(zone_class="RM-6", current_single_stair=True),
            ],
            geometry=[Point(-87.7, 41.9), Point(-87.6, 41.8)],
            crs="EPSG:4326",
            index=[91, 22],
        )
        result = enrich_build_parcels(original)
        self.assertIsInstance(result, gpd.GeoDataFrame)
        self.assertEqual(result.index.tolist(), [91, 22])
        self.assertTrue(result.geometry.equals(original.geometry))
        self.assertEqual(result.crs, original.crs)
        self.assertEqual(result["build_category"].tolist(), ["screened_expansion", "baseline"])
        self.assertNotIn("illinois_build", original.columns)
        self.assertEqual(result["build_minimum_units"].dtype, pd.Int64Dtype())

    def test_empty_frame_and_null_baseline_flags(self) -> None:
        result = enrich_build_parcels(pd.DataFrame())
        self.assertEqual(set(result.columns), set(BUILD_PROPERTIES))
        self.assertEqual(len(result), 0)
        for flag in (pd.NA, None, float("nan"), "False", False):
            result = enrich_build_parcels(pd.DataFrame([{"current_single_stair": flag}]))
            self.assertFalse(result.iloc[0]["illinois_build"])

    def test_versioned_policy_matches_fixed_classifier_bands(self) -> None:
        policy = load_build_policy()
        self.assertEqual(policy["status"], "proposed_not_enacted_scenario")
        self.assertEqual(policy["researched_on"], "2026-08-31")
        for band in policy["unit_allowance_bands"]:
            area = (
                band.get("area_max_inclusive_sqft", band["area_min_exclusive_sqft"] + 1)
                if "area_min_exclusive_sqft" in band
                else band["area_max_inclusive_sqft"]
            )
            self.assertEqual(build_minimum_units(area), band["units"])
        self.assertFalse(policy["screening"]["units_attributable_to_stair_reform"])
        self.assertGreaterEqual(len(policy["sources"]), 5)


if __name__ == "__main__":
    unittest.main()
