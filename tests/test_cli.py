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


if __name__ == "__main__":
    unittest.main()
