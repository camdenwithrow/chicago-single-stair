import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import geopandas as gpd
import pyarrow as pa
import pyarrow.parquet as pq
from shapely.geometry import Polygon

from single_stair.transform.clean_and_join import (
    build_clean_and_join,
    normalize_pin,
    parcel_pin,
)


def _snapshot_path(root: Path, dataset: str) -> Path:
    path = root / dataset / "snapshot_date=2026-08-26"
    path.mkdir(parents=True)
    (path / "manifest.json").write_text("{}\n")
    return path


def _write_records(root: Path, dataset: str, records: list[dict]) -> None:
    path = _snapshot_path(root, dataset)
    pq.write_table(pa.Table.from_pylist(records), path / "part-00001.parquet")


def _write_geometry(root: Path, dataset: str, records: dict, geometries: list[Polygon]) -> None:
    path = _snapshot_path(root, dataset)
    frame = gpd.GeoDataFrame(records, geometry=geometries, crs="EPSG:4326")
    frame.to_parquet(path / "part-00001.parquet", index=False)


class CleanAndJoinTests(unittest.TestCase):
    def test_standardizes_supported_pin_formats(self) -> None:
        self.assertEqual(normalize_pin("12-34-567-890-0001"), "12345678900001")
        self.assertEqual(parcel_pin("1234567890", 1), "12345678900001")
        self.assertEqual(parcel_pin("1234567890", None), "12345678900000")
        self.assertIsNone(normalize_pin("123"))

    def test_builds_latest_cards_and_parcel_context(self) -> None:
        chicago = Polygon(
            [
                (-87.70, 41.80),
                (-87.60, 41.80),
                (-87.60, 41.90),
                (-87.70, 41.90),
                (-87.70, 41.80),
            ]
        )
        parcel_inside = Polygon(
            [
                (-87.66, 41.84),
                (-87.65, 41.84),
                (-87.65, 41.85),
                (-87.66, 41.85),
                (-87.66, 41.84),
            ]
        )
        parcel_outside = Polygon(
            [
                (-88.10, 42.10),
                (-88.09, 42.10),
                (-88.09, 42.11),
                (-88.10, 42.11),
                (-88.10, 42.10),
            ]
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            raw = root / "raw"
            staged = root / "staged"
            _write_records(
                raw,
                "cook_county_building_characteristics",
                [
                    {"pin": "12345678900001", "year": "2024.0", "card": "1.0", "value": "old"},
                    {"pin": "12345678900001", "year": "2025.0", "card": "1.0", "value": "new"},
                    {"pin": "12345678900001", "year": "2025.0", "card": "2.0", "value": "second"},
                ],
            )
            _write_geometry(
                raw,
                "cook_county_parcels",
                {
                    "objectid": [1, 2],
                    "pin10": ["1234567890", "9999999999"],
                    "pinu": [1, 0],
                },
                [parcel_inside, parcel_outside],
            )
            _write_geometry(
                raw,
                "chicago_zoning",
                {"objectid": [100], "zoning_id": ["z1"], "zone_class": ["RT-4"]},
                [chicago],
            )
            _write_geometry(
                raw,
                "census_tract_geometry",
                {"GEOID": ["17031000100"]},
                [chicago],
            )
            _write_geometry(raw, "chicago_ward_boundaries", {"ward": ["1"]}, [chicago])
            _write_geometry(
                raw,
                "chicago_community_area_boundaries",
                {"area_num_1": ["1"], "community": ["ROGERS PARK"]},
                [chicago],
            )
            stations = [
                {
                    "stop_id": "station",
                    "stop_name": "Station",
                    "stop_lat": "41.86",
                    "stop_lon": "-87.65",
                }
            ]
            _write_records(raw, "cta_stations", stations)
            _write_records(raw, "metra_stations", stations)

            snapshots = build_clean_and_join(
                raw_root=raw,
                staged_root=staged,
                snapshot_date=date(2026, 8, 26),
            )
            buildings = pq.read_table(
                snapshots.building_characteristics / "part-00001.parquet"
            ).to_pandas()
            parcels = gpd.read_parquet(snapshots.parcel_context / "part-00001.parquet")
            manifest = json.loads((snapshots.parcel_context / "manifest.json").read_text())

        self.assertEqual(len(buildings), 2)
        self.assertEqual(set(buildings["value"]), {"new", "second"})
        self.assertEqual(parcels["objectid"].tolist(), [1])
        self.assertEqual(parcels["pin"].tolist(), ["12345678900001"])
        self.assertEqual(parcels["building_card_count"].tolist(), [2])
        self.assertEqual(parcels["zone_class"].tolist(), ["RT-4"])
        self.assertEqual(parcels["census_tract_geoid"].tolist(), ["17031000100"])
        self.assertTrue(parcels.geometry.is_valid.all())
        self.assertGreater(parcels["transit_distance_ft"].iloc[0], 0)
        self.assertEqual(manifest["metadata"]["target_part_records"], 50_000)
        counts = manifest["metadata"]["join_quality"]["counts"]
        self.assertEqual(counts["source_parcels"], 2)
        self.assertEqual(counts["chicago_parcels"], 1)
        self.assertEqual(counts["building_characteristics"], 1)


if __name__ == "__main__":
    unittest.main()
