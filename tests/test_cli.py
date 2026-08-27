import unittest

from single_stair.cli import _parser


class CliTests(unittest.TestCase):
    def test_parses_building_tax_year(self) -> None:
        arguments = _parser().parse_args(["ingest", "cook-county-buildings", "--tax-year", "2025"])

        self.assertEqual(arguments.source, "cook-county-buildings")
        self.assertEqual(arguments.tax_year, 2025)

    def test_parses_parcel_source(self) -> None:
        arguments = _parser().parse_args(["ingest", "cook-county-parcels"])

        self.assertEqual(arguments.source, "cook-county-parcels")

    def test_parses_chicago_zoning_source(self) -> None:
        arguments = _parser().parse_args(["ingest", "chicago-zoning"])

        self.assertEqual(arguments.source, "chicago-zoning")

    def test_parses_remaining_chicago_sources(self) -> None:
        for source in (
            "chicago-building-permits",
            "chicago-city-land",
            "chicago-boundaries",
            "transit-stations",
        ):
            with self.subTest(source=source):
                arguments = _parser().parse_args(["ingest", source])
                self.assertEqual(arguments.source, source)

    def test_parses_census_vintage(self) -> None:
        arguments = _parser().parse_args(["ingest", "census-housing", "--year", "2023"])

        self.assertEqual(arguments.source, "census-housing")
        self.assertEqual(arguments.year, 2023)

    def test_parses_clean_and_join_transform(self) -> None:
        arguments = _parser().parse_args(["transform", "clean-and-join"])

        self.assertEqual(arguments.command, "transform")
        self.assertEqual(arguments.transformation, "clean-and-join")

    def test_parses_scenario_selection(self) -> None:
        arguments = _parser().parse_args(
            [
                "scenarios",
                "show",
                "--policy",
                "illinois_sb4061",
                "--estimate",
                "progressive",
            ]
        )

        self.assertEqual(arguments.command, "scenarios")
        self.assertEqual(arguments.policy, "illinois_sb4061")
        self.assertEqual(arguments.estimate, "progressive")

    def test_parses_parcel_opportunity_selection(self) -> None:
        arguments = _parser().parse_args(
            [
                "transform",
                "parcel-opportunity",
                "--policy",
                "illinois_sb4061",
                "--estimate",
                "conservative",
            ]
        )

        self.assertEqual(arguments.transformation, "parcel-opportunity")
        self.assertEqual(arguments.policy, "illinois_sb4061")
        self.assertEqual(arguments.estimate, "conservative")

    def test_parses_family_housing_need_transform(self) -> None:
        arguments = _parser().parse_args(["transform", "family-housing-need"])

        self.assertEqual(arguments.command, "transform")
        self.assertEqual(arguments.transformation, "family-housing-need")

    def test_parses_combined_opportunity_selection(self) -> None:
        arguments = _parser().parse_args(
            [
                "transform",
                "combine-opportunity-need",
                "--policy",
                "illinois_sb4061",
                "--estimate",
                "progressive",
            ]
        )

        self.assertEqual(arguments.transformation, "combine-opportunity-need")
        self.assertEqual(arguments.policy, "illinois_sb4061")
        self.assertEqual(arguments.estimate, "progressive")


if __name__ == "__main__":
    unittest.main()
