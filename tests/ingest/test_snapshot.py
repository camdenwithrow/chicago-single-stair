import json
import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

import geopandas as gpd

from single_stair.ingest.snapshot import GeoParquetSnapshotWriter, SnapshotError


def _feature_collection(object_id: int = 1) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"objectid": object_id, "name": "sample"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-87.7, 41.8], [-87.6, 41.8], [-87.6, 41.9], [-87.7, 41.8]]],
                },
            }
        ],
    }


class GeoParquetSnapshotWriterTests(unittest.TestCase):
    def test_writes_and_atomically_finalizes_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            raw_root = Path(temporary_directory)
            writer = GeoParquetSnapshotWriter(
                raw_root=raw_root,
                dataset="parcels",
                source_url="https://example.test/parcels",
                output_crs="EPSG:4326",
                snapshot_date=date(2026, 8, 25),
                retrieved_at=datetime(2026, 8, 25, 12, tzinfo=UTC),
            )

            with writer:
                writer.write_geojson_batch(1, _feature_collection())
                snapshot_path = writer.commit(
                    expected_records=1,
                    expected_parts=1,
                    metadata={"page_size": 2_000},
                )

            frame = gpd.read_parquet(snapshot_path / "part-00001.parquet")
            manifest = json.loads((snapshot_path / "manifest.json").read_text())

            self.assertEqual(frame["objectid"].tolist(), [1])
            self.assertEqual(frame.crs.to_epsg(), 4326)
            self.assertEqual(manifest["record_count"], 1)
            self.assertEqual(manifest["files"][0]["records"], 1)
            self.assertEqual(len(manifest["files"][0]["sha256"]), 64)
            self.assertFalse(any(raw_root.rglob("*.incomplete")))

    def test_does_not_finalize_an_invalid_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            raw_root = Path(temporary_directory)
            writer = GeoParquetSnapshotWriter(
                raw_root=raw_root,
                dataset="parcels",
                source_url="https://example.test/parcels",
                output_crs="EPSG:4326",
                snapshot_date=date(2026, 8, 25),
            )

            with self.assertRaisesRegex(SnapshotError, "Expected 2 records"):
                with writer:
                    writer.write_geojson_batch(1, _feature_collection())
                    writer.commit(expected_records=2, expected_parts=1)

            self.assertFalse(writer.final_path.exists())
            self.assertFalse(writer.working_path.exists())


if __name__ == "__main__":
    unittest.main()
