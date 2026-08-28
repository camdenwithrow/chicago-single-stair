import unittest
from importlib.resources import files

import pandas as pd

from single_stair.transform.permit_baseline import parse_permit_description


class PermitDescriptionParserTests(unittest.TestCase):
    def test_parses_new_multifamily_construction(self) -> None:
        parsed = parse_permit_description(
            "PERMIT - NEW CONSTRUCTION",
            "Erect a 3 story six dwelling unit masonry building",
        )

        self.assertEqual(parsed.stories, 3)
        self.assertEqual(parsed.proposed_units, 6)
        self.assertEqual(parsed.net_added_units, 6)
        self.assertEqual(parsed.production_type, "new_construction")

    def test_prefers_explicit_total_over_component_counts(self) -> None:
        parsed = parse_permit_description(
            "PERMIT - NEW CONSTRUCTION",
            "Erect 5-story building, 46 total units building; "
            "36 efficiency units and 10 dwelling units",
        )

        self.assertEqual(parsed.proposed_units, 46)

    def test_parses_unit_adding_alteration(self) -> None:
        parsed = parse_permit_description(
            "PERMIT - RENOVATION/ALTERATION",
            "Existing 2 D.U. building. New A.D.U. unit in basement.",
        )

        self.assertEqual(parsed.existing_units, 2)
        self.assertEqual(parsed.proposed_units, 3)
        self.assertEqual(parsed.net_added_units, 1)
        self.assertEqual(parsed.production_type, "unit_adding_alteration")

    def test_does_not_treat_affected_units_as_production(self) -> None:
        parsed = parse_permit_description(
            "PERMIT – EXPRESS PERMIT PROGRAM",
            "Replace plumbing fixtures. Affects: 12 dwelling units.",
        )

        self.assertEqual(parsed.production_type, "not_production")
        self.assertIsNone(parsed.net_added_units)

    def test_flags_mislabeled_new_construction_for_review(self) -> None:
        parsed = parse_permit_description(
            "PERMIT - NEW CONSTRUCTION",
            "Interior renovation of existing two dwelling unit building",
        )

        self.assertEqual(parsed.production_type, "unresolved")
        self.assertEqual(parsed.confidence, "low")

    def test_uses_total_units_not_units_per_floor(self) -> None:
        parsed = parse_permit_description(
            "PERMIT - NEW CONSTRUCTION",
            "New 5 story building with 8 residential dwelling units, 2 units each floor",
        )

        self.assertEqual(parsed.proposed_units, 8)

    def test_parses_new_townhomes(self) -> None:
        parsed = parse_permit_description(
            "PERMIT - NEW CONSTRUCTION",
            "Erect 9 new townhomes with garages",
        )

        self.assertEqual(parsed.proposed_units, 9)
        self.assertEqual(parsed.production_type, "new_construction")

    def test_validation_fixture_is_a_unique_100_permit_sample(self) -> None:
        resource = files("single_stair").joinpath("config/permit_validation.v1.csv")
        with resource.open("rb") as fixture:
            labels = pd.read_csv(fixture)

        self.assertEqual(len(labels), 100)
        self.assertFalse(labels["permit_id"].duplicated().any())


if __name__ == "__main__":
    unittest.main()
