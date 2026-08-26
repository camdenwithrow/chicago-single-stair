import io
import json
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pyarrow.parquet as pq

from single_stair.ingest.transit_stations import (
    CTA_FEED,
    METRA_FEED,
    _station_records,
    ingest_gtfs_stations,
)


def _gtfs_archive(stops: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("stops.txt", stops)
    return output.getvalue()


class TransitStationTests(unittest.IsolatedAsyncioTestCase):
    def test_cta_keeps_parent_stations_not_platform_stops(self) -> None:
        archive = _gtfs_archive(
            "stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station\n"
            "A,Austin,41.8,-87.7,1,\n"
            "A_N,Austin platform,41.8,-87.7,0,A\n"
        )

        records, _member_date = _station_records(archive, CTA_FEED)

        self.assertEqual([record["stop_id"] for record in records], ["A"])

    def test_metra_feed_treats_each_stop_as_a_station(self) -> None:
        archive = _gtfs_archive(
            "stop_id, stop_name, stop_lat, stop_lon\nGENEVA, Geneva, 41.88, -88.31\n"
        )

        records, _member_date = _station_records(archive, METRA_FEED)

        self.assertEqual(records[0]["stop_name"], "Geneva")

    async def test_ingestion_writes_raw_stop_fields_and_archive_hash(self) -> None:
        archive = _gtfs_archive(
            "stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station\n"
            "A,Austin,41.8,-87.7,1,\n"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch(
                "single_stair.ingest.transit_stations.request_bytes",
                new=AsyncMock(return_value=archive),
            ):
                snapshot = await ingest_gtfs_stations(
                    CTA_FEED,
                    raw_root=Path(temporary_directory),
                    snapshot_date=date(2026, 8, 26),
                )

            manifest = json.loads((snapshot.path / "manifest.json").read_text())
            records = pq.read_table(snapshot.path / "part-00001.parquet").to_pylist()

        self.assertEqual(records[0]["stop_id"], "A")
        self.assertEqual(manifest["metadata"]["grain"], ["stop_id"])
        self.assertEqual(len(manifest["metadata"]["source_archive_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
