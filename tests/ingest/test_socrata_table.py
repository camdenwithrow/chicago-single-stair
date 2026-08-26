import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pyarrow.parquet as pq

from single_stair.ingest.socrata import app_token_headers
from single_stair.ingest.socrata_table import (
    SocrataTable,
    TableBatch,
    TableBoundary,
    _dataset_boundary,
    _iter_table_batches,
    ingest_socrata_table,
)

TABLE = SocrataTable(
    dataset="example_records",
    dataset_id="abcd-1234",
    domain="data.example.gov",
    key="id",
    grain=("id",),
)


class SocrataTableTests(unittest.IsolatedAsyncioTestCase):
    def test_uses_shared_app_token(self) -> None:
        with patch.dict(os.environ, {"SOCRATA_APP_TOKEN": "shared"}, clear=True):
            headers = app_token_headers()

        self.assertEqual(headers, {"X-App-Token": "shared"})

    def test_omits_header_without_shared_app_token(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            headers = app_token_headers()

        self.assertEqual(headers, {})

    async def test_boundary_requires_a_unique_published_key(self) -> None:
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json=[{"maximum_key": "N9", "expected_records": "3", "distinct_keys": "3"}],
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            boundary = await _dataset_boundary(client, TABLE)

        self.assertEqual(boundary, TableBoundary(expected_records=3, maximum_key="N9"))

    async def test_keyset_paginates_string_keys_to_fixed_boundary(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            where = request.url.params["$where"]
            if "id >" not in where:
                return httpx.Response(200, json=[{"id": "10"}, {"id": "11"}])
            if "'11'" in where:
                return httpx.Response(200, json=[{"id": "N9"}])
            return httpx.Response(200, json=[])

        boundary = TableBoundary(expected_records=3, maximum_key="N9")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            batches = [
                batch
                async for batch in _iter_table_batches(
                    client,
                    TABLE,
                    boundary,
                    page_size=2,
                )
            ]

        self.assertEqual([batch.last_key for batch in batches], ["11", "N9"])
        self.assertEqual([batch.total for batch in batches], [2, 2])

    async def test_ingestion_writes_records_and_source_contract(self) -> None:
        async def batches(*_args, **_kwargs):
            yield TableBatch(
                number=1,
                total=1,
                records=[{"id": "N9", "value": "raw"}],
                first_key="N9",
                last_key="N9",
            )

        boundary = TableBoundary(expected_records=1, maximum_key="N9")
        with tempfile.TemporaryDirectory() as temporary_directory:
            with (
                patch(
                    "single_stair.ingest.socrata_table._dataset_boundary",
                    new=AsyncMock(return_value=boundary),
                ),
                patch("single_stair.ingest.socrata_table._iter_table_batches", new=batches),
            ):
                path = await ingest_socrata_table(
                    TABLE,
                    raw_root=Path(temporary_directory),
                    snapshot_date=date(2026, 8, 26),
                    page_size=1,
                )

            manifest = json.loads((path / "manifest.json").read_text())
            records = pq.read_table(path / "part-00001.parquet").to_pylist()

        self.assertEqual(records, [{"id": "N9", "value": "raw"}])
        self.assertEqual(manifest["metadata"]["grain"], ["id"])
        self.assertEqual(manifest["metadata"]["snapshot_key"], "id")


if __name__ == "__main__":
    unittest.main()
