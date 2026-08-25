import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from single_stair.ingest.cook_county_parcels import (
    ArcGISResponseError,
    ParcelBatch,
    _fetch_object_ids,
    _fetch_parcel_batch,
    _request_json,
    _validate_parcel_batch,
    ingest_cook_county_parcels,
)
from single_stair.ingest.snapshot import SnapshotError


class ParcelRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_rate_limit_and_honors_retry_after(self) -> None:
        responses = iter(
            [
                httpx.Response(429, headers={"Retry-After": "7"}),
                httpx.Response(200, json={"objectIds": [1, 2]}),
            ]
        )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _request: next(responses))
        ) as client:
            with patch(
                "single_stair.ingest.cook_county_parcels.asyncio.sleep",
                new_callable=AsyncMock,
            ) as sleep:
                payload = await _request_json(client, "GET")

        self.assertEqual(payload, {"objectIds": [1, 2]})
        sleep.assert_awaited_once_with(7.0)

    async def test_fetches_a_batch_with_a_post_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "POST")
            self.assertIn(b"objectIds=10%2C20", request.content)
            return httpx.Response(200, json={"type": "FeatureCollection", "features": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            payload = await _fetch_parcel_batch(client, (10, 20))

        self.assertEqual(payload["features"], [])

    async def test_object_ids_are_sorted_for_stable_batches(self) -> None:
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"objectIds": [30, 10, 20]})
        )

        async with httpx.AsyncClient(transport=transport) as client:
            object_ids = await _fetch_object_ids(client)

        self.assertEqual(object_ids, [10, 20, 30])

    async def test_raises_arcgis_errors_returned_with_http_200(self) -> None:
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"error": {"code": 400, "message": "Invalid query"}},
            )
        )

        async with httpx.AsyncClient(transport=transport) as client:
            with self.assertRaisesRegex(ArcGISResponseError, "Invalid query"):
                await _request_json(client, "GET")

    def test_rejects_a_batch_with_missing_object_ids(self) -> None:
        batch = ParcelBatch(
            number=1,
            total=1,
            object_ids=(10, 20),
            payload={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"objectid": 10},
                        "geometry": None,
                    }
                ],
            },
        )

        with self.assertRaisesRegex(SnapshotError, "1 missing"):
            _validate_parcel_batch(batch)

    async def test_ingestion_writes_the_batch_and_manifest(self) -> None:
        async def parcel_batches(**_kwargs):
            yield ParcelBatch(
                number=1,
                total=1,
                object_ids=(10,),
                payload={
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {"objectid": 10, "name": "sample"},
                            "geometry": {"type": "Point", "coordinates": [-87.7, 41.8]},
                        }
                    ],
                },
            )

        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch(
                "single_stair.ingest.cook_county_parcels.iter_parcel_batches",
                new=parcel_batches,
            ):
                snapshot_path = await ingest_cook_county_parcels(
                    raw_root=Path(temporary_directory),
                    snapshot_date=date(2026, 8, 25),
                )

            manifest = json.loads((snapshot_path / "manifest.json").read_text())

        self.assertEqual(manifest["record_count"], 1)
        self.assertEqual(manifest["metadata"]["source_object_id_count"], 1)
        self.assertEqual(manifest["metadata"]["source_crs"], "EPSG:3435")


if __name__ == "__main__":
    unittest.main()
