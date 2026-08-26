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
        for source in ("chicago-building-permits", "chicago-city-land", "transit-stations"):
            with self.subTest(source=source):
                arguments = _parser().parse_args(["ingest", source])
                self.assertEqual(arguments.source, source)

    def test_parses_census_vintage(self) -> None:
        arguments = _parser().parse_args(["ingest", "census-housing", "--year", "2023"])

        self.assertEqual(arguments.source, "census-housing")
        self.assertEqual(arguments.year, 2023)


if __name__ == "__main__":
    unittest.main()
