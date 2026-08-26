import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import geopandas as gpd
import httpx
import pyarrow.parquet as pq
from shapely.geometry import Polygon

from single_stair.ingest.census import (
    ACS_TABLES,
    _ingest_acs_table,
    _ingest_tract_geometry,
    _records_from_census_payload,
)
from single_stair.ingest.download import SourceResponseError


class CensusIngestionTests(unittest.IsolatedAsyncioTestCase):
    def test_parses_source_fields_and_preserves_estimates_and_margins(self) -> None:
        table = ACS_TABLES[0]
        header = ["NAME", *table.variables, "state", "county", "tract"]
        row = [
            "Census Tract 1",
            *[str(index) for index, _ in enumerate(table.variables)],
            "17",
            "031",
            "000100",
        ]

        records = _records_from_census_payload([header, row], table)

        self.assertEqual(records[0]["B11005_001E"], "0")
        self.assertEqual(records[0]["B11005_001M"], "1")
        self.assertNotIn("geoid", records[0])

    async def test_acs_table_snapshot_records_query_contract(self) -> None:
        table = ACS_TABLES[0]
        header = ["NAME", *table.variables, "state", "county", "tract"]
        row = ["Census Tract 1", *["1"] * len(table.variables), "17", "031", "000100"]

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.params.get_list("in"), ["state:17", "county:031"])
            self.assertNotIn("secret-key", str(request.url.copy_with(query=None)))
            return httpx.Response(200, json=[header, row])

        with tempfile.TemporaryDirectory() as temporary_directory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                path = await _ingest_acs_table(
                    client,
                    table,
                    year=2024,
                    api_key="secret-key",
                    raw_root=Path(temporary_directory),
                    snapshot_date=date(2026, 8, 26),
                )

            manifest = json.loads((path / "manifest.json").read_text())
            records = pq.read_table(path / "part-00001.parquet").to_pylist()

        self.assertEqual(records[0]["tract"], "000100")
        self.assertEqual(manifest["metadata"]["vintage"], 2024)
        self.assertEqual(manifest["metadata"]["grain"], ["state", "county", "tract"])

    async def test_acs_auth_error_does_not_expose_the_api_key(self) -> None:
        with patch(
            "single_stair.ingest.census.request_json",
            new=AsyncMock(side_effect=SourceResponseError("Invalid Key")),
        ):
            async with httpx.AsyncClient() as client:
                with self.assertRaises(RuntimeError) as raised:
                    await _ingest_acs_table(
                        client,
                        ACS_TABLES[0],
                        year=2024,
                        api_key="secret-key",
                        raw_root=Path("unused"),
                        snapshot_date=date(2026, 8, 26),
                    )

        self.assertIn("authentication failed", str(raised.exception))
        self.assertNotIn("secret-key", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

    async def test_tract_snapshot_keeps_native_crs_and_filters_cook_county(self) -> None:
        frame = gpd.GeoDataFrame(
            {
                "STATEFP": ["17", "17"],
                "COUNTYFP": ["031", "043"],
                "TRACTCE": ["000100", "000100"],
                "GEOID": ["17031000100", "17043000100"],
            },
            geometry=[
                Polygon([(-88, 41), (-87, 41), (-87, 42), (-88, 41)]),
                Polygon([(-89, 41), (-88, 41), (-88, 42), (-89, 41)]),
            ],
            crs="EPSG:4269",
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            with (
                patch(
                    "single_stair.ingest.census.request_bytes",
                    new=AsyncMock(return_value=b"source archive"),
                ),
                patch("single_stair.ingest.census.gpd.read_file", return_value=frame),
            ):
                async with httpx.AsyncClient() as client:
                    path = await _ingest_tract_geometry(
                        client,
                        year=2024,
                        raw_root=Path(temporary_directory),
                        snapshot_date=date(2026, 8, 26),
                    )

            result = gpd.read_parquet(path / "part-00001.parquet")
            manifest = json.loads((path / "manifest.json").read_text())

        self.assertEqual(result["GEOID"].tolist(), ["17031000100"])
        self.assertEqual(result.crs.to_epsg(), 4269)
        self.assertEqual(manifest["metadata"]["source_scope"], "Illinois")
        self.assertEqual(
            manifest["metadata"]["boundary_product"],
            "TIGER/Line Census Tracts",
        )


if __name__ == "__main__":
    unittest.main()
