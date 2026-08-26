import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import geopandas as gpd
import httpx

from single_stair.ingest.chicago_zoning import (
    ZoningBatch,
    ZoningBoundary,
    _dataset_boundary,
    _feature_collection,
    _iter_zoning_batches,
    ingest_chicago_zoning,
)


def _zoning_record(object_id: int, zone_class: str = "RT-4") -> dict:
    return {
        "objectid": str(object_id),
        "zoning_id": "63",
        "zone_class": zone_class,
        "edit_date": "2026-08-06T00:00:00.000",
        "the_geom": {
            "type": "Polygon",
            "coordinates": [[[-87.7, 41.8], [-87.6, 41.8], [-87.6, 41.9], [-87.7, 41.8]]],
        },
    }


class ChicagoZoningIngestionTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_objectid_as_snapshot_boundary(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertIn("max(objectid)", request.url.params["$select"])
            return httpx.Response(
                200,
                json=[{"maximum_objectid": "2458388", "expected_records": "14929"}],
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            boundary = await _dataset_boundary(client)

        self.assertEqual(boundary.maximum_objectid, 2_458_388)
        self.assertEqual(boundary.expected_records, 14_929)

    async def test_keyset_paginates_to_fixed_upper_boundary(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            where = request.url.params["$where"]
            if "objectid > 0 " in where:
                return httpx.Response(
                    200,
                    json=[_zoning_record(100), _zoning_record(200, "RS-3")],
                )
            if "objectid > 200 " in where:
                return httpx.Response(200, json=[_zoning_record(300, "B2-3")])
            return httpx.Response(200, json=[])

        boundary = ZoningBoundary(expected_records=3, maximum_objectid=300)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            batches = [batch async for batch in _iter_zoning_batches(client, boundary, page_size=2)]

        self.assertEqual([batch.last_objectid for batch in batches], [200, 300])
        self.assertEqual([batch.total for batch in batches], [2, 2])

    def test_converts_socrata_geometry_to_feature_collection(self) -> None:
        payload = _feature_collection([_zoning_record(100)])

        self.assertEqual(payload["features"][0]["geometry"]["type"], "Polygon")
        self.assertEqual(payload["features"][0]["properties"]["objectid"], "100")
        self.assertNotIn("the_geom", payload["features"][0]["properties"])

    async def test_ingestion_writes_geo_parquet_and_manifest(self) -> None:
        async def zoning_batches(*_args, **_kwargs):
            yield ZoningBatch(
                number=1,
                total=1,
                records=[_zoning_record(100)],
                first_objectid=100,
                last_objectid=100,
            )

        boundary = ZoningBoundary(expected_records=1, maximum_objectid=100)
        with tempfile.TemporaryDirectory() as temporary_directory:
            with (
                patch(
                    "single_stair.ingest.chicago_zoning._dataset_boundary",
                    new=AsyncMock(return_value=boundary),
                ),
                patch(
                    "single_stair.ingest.chicago_zoning._iter_zoning_batches",
                    new=zoning_batches,
                ),
            ):
                snapshot_path = await ingest_chicago_zoning(
                    raw_root=Path(temporary_directory),
                    snapshot_date=date(2026, 8, 25),
                    page_size=1,
                )

            frame = gpd.read_parquet(snapshot_path / "part-00001.parquet")
            manifest = json.loads((snapshot_path / "manifest.json").read_text())

        self.assertEqual(frame["objectid"].tolist(), ["100"])
        self.assertEqual(frame.crs.to_epsg(), 4326)
        self.assertEqual(manifest["metadata"]["grain"], ["objectid"])
        self.assertEqual(
            manifest["metadata"]["latest_source_edit"],
            "2026-08-06T00:00:00.000",
        )


if __name__ == "__main__":
    unittest.main()
