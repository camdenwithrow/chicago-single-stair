import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pyarrow.parquet as pq

from single_stair.ingest.cook_county_buildings import (
    BuildingCharacteristicsBatch,
    DatasetBoundary,
    _iter_building_batches,
    _latest_tax_year,
    _request_rows,
    ingest_cook_county_building_characteristics,
)


def _building_record(sid: int, pin: str) -> dict:
    return {
        ":sid": str(sid),
        "pin": pin,
        "year": "2025",
        "card": "1",
        "char_yrblt": "1910",
        "char_bldg_sf": "2400",
    }


class BuildingCharacteristicsRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_rate_limit(self) -> None:
        responses = iter(
            [
                httpx.Response(429),
                httpx.Response(200, json=[{"tax_year": "2025"}]),
            ]
        )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _request: next(responses))
        ) as client:
            with patch(
                "single_stair.ingest.cook_county_buildings.asyncio.sleep",
                new_callable=AsyncMock,
            ) as sleep:
                rows = await _request_rows(client, {"$limit": 1})

        self.assertEqual(rows, [{"tax_year": "2025"}])
        sleep.assert_awaited_once_with(1)

    async def test_discovers_latest_tax_year(self) -> None:
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(200, json=[{"tax_year": "2025"}])
        )

        async with httpx.AsyncClient(transport=transport) as client:
            tax_year = await _latest_tax_year(client)

        self.assertEqual(tax_year, 2025)

    async def test_keyset_paginates_to_fixed_upper_boundary(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            where = request.url.params["$where"]
            if ":sid > 0 " in where:
                return httpx.Response(
                    200,
                    json=[
                        _building_record(1, "01010000010000"),
                        _building_record(2, "01010000020000"),
                    ],
                )
            if ":sid > 2 " in where:
                return httpx.Response(200, json=[_building_record(3, "01010000030000")])
            return httpx.Response(200, json=[])

        boundary = DatasetBoundary(tax_year=2025, expected_records=3, maximum_sid=3)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            batches = [
                batch async for batch in _iter_building_batches(client, boundary, page_size=2)
            ]

        self.assertEqual([batch.last_sid for batch in batches], [2, 3])
        self.assertEqual([batch.number for batch in batches], [1, 2])

    async def test_ingestion_writes_snapshot_and_manifest(self) -> None:
        async def building_batches(*_args, **_kwargs):
            yield BuildingCharacteristicsBatch(
                number=1,
                total=1,
                tax_year=2025,
                records=[_building_record(1, "01010000010000")],
                first_sid=1,
                last_sid=1,
            )

        boundary = DatasetBoundary(tax_year=2025, expected_records=1, maximum_sid=1)
        with tempfile.TemporaryDirectory() as temporary_directory:
            with (
                patch(
                    "single_stair.ingest.cook_county_buildings._latest_tax_year",
                    new=AsyncMock(return_value=2025),
                ),
                patch(
                    "single_stair.ingest.cook_county_buildings._dataset_boundary",
                    new=AsyncMock(return_value=boundary),
                ),
                patch(
                    "single_stair.ingest.cook_county_buildings._iter_building_batches",
                    new=building_batches,
                ),
            ):
                snapshot_path = await ingest_cook_county_building_characteristics(
                    raw_root=Path(temporary_directory),
                    snapshot_date=date(2026, 8, 25),
                    page_size=1,
                )

            manifest = json.loads((snapshot_path / "manifest.json").read_text())
            table = pq.read_table(snapshot_path / "part-00001.parquet")

        self.assertEqual(table.column("pin").to_pylist(), ["01010000010000"])
        self.assertEqual(manifest["metadata"]["tax_year"], 2025)
        self.assertEqual(manifest["metadata"]["grain"], ["pin", "year", "card"])


if __name__ == "__main__":
    unittest.main()
