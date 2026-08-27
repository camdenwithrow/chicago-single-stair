import json
import tempfile
import unittest
from pathlib import Path

from single_stair.scenarios import BEDROOM_CATEGORIES, load_scenario_catalog


class ScenarioCatalogTests(unittest.TestCase):
    def test_loads_evidence_backed_defaults(self) -> None:
        catalog = load_scenario_catalog()

        self.assertEqual(catalog.config_version, "1.0.0")
        self.assertEqual(catalog.default_policy_id, "chicago_proposed")
        self.assertEqual(catalog.default_estimate_id, "median")
        self.assertEqual(
            catalog.policies["chicago_proposed"].maximum_stories_above_grade,
            5,
        )
        self.assertEqual(catalog.policies["illinois_sb4061"].maximum_stories_above_grade, 6)
        self.assertEqual(catalog.policies["chicago_proposed"].maximum_units_per_story, 4)
        self.assertEqual(set(catalog.lot_archetypes), {"chicago_25x125", "chicago_50x125"})
        self.assertEqual(catalog.bedroom_reporting.categories, BEDROOM_CATEGORIES)
        self.assertEqual(catalog.bedroom_reporting.default_family_minimum_bedrooms, 3)
        self.assertEqual(catalog.bedroom_reporting.reported_family_thresholds, (2, 3, 4))

    def test_resolves_alternative_policy_and_estimate_with_sources(self) -> None:
        selection = load_scenario_catalog().selection(
            policy_id="illinois_sb4061",
            estimate_id="progressive",
        )

        self.assertEqual(selection["policy"]["maximum_stories_above_grade"], 6)
        self.assertEqual(selection["estimate"]["single_stair_efficiency"], 0.972)
        self.assertEqual(selection["estimate"]["unit_sizes_sqft"]["three_bedroom"], 950)
        self.assertIn("illinois_sb4061", selection["sources"])
        self.assertIn("ihda_unit_standards", selection["sources"])

    def test_profiles_order_capacity_assumptions_consistently(self) -> None:
        catalog = load_scenario_catalog()
        conservative = catalog.estimate_profiles["conservative"]
        median = catalog.estimate_profiles["median"]
        progressive = catalog.estimate_profiles["progressive"]

        self.assertLess(
            conservative.single_stair_efficiency,
            median.single_stair_efficiency,
        )
        self.assertLess(median.single_stair_efficiency, progressive.single_stair_efficiency)
        for category in BEDROOM_CATEGORIES:
            with self.subTest(category=category):
                self.assertGreaterEqual(
                    conservative.unit_sizes_sqft[category],
                    median.unit_sizes_sqft[category],
                )
                self.assertGreaterEqual(
                    median.unit_sizes_sqft[category],
                    progressive.unit_sizes_sqft[category],
                )

    def test_rejects_an_efficiency_gain_that_does_not_match(self) -> None:
        source_path = (
            Path(__file__).parents[1]
            / "src"
            / "single_stair"
            / "config"
            / "building_scenarios.v1.json"
        )
        payload = json.loads(source_path.read_text())
        payload["estimate_profiles"]["median"]["single_stair_efficiency"] = 0.91

        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / "invalid.json"
            config_path.write_text(json.dumps(payload))

            with self.assertRaisesRegex(ValueError, "inconsistent efficiency gain"):
                load_scenario_catalog(config_path)

    def test_rejects_unknown_scenario_selection(self) -> None:
        catalog = load_scenario_catalog()

        with self.assertRaisesRegex(ValueError, "Unknown policy scenario"):
            catalog.selection(policy_id="not-a-policy")


if __name__ == "__main__":
    unittest.main()
