import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import geopandas as gpd
import pyarrow as pa
import pyarrow.parquet as pq


class SnapshotError(RuntimeError):
    """Raised when a raw snapshot cannot be validated or finalized."""


@dataclass(frozen=True, slots=True)
class SnapshotPart:
    path: str
    records: int
    sha256: str
    invalid_geometry_count: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ParquetSnapshotWriter:
    """Write immutable record batches to an atomically finalized Parquet snapshot."""

    def __init__(
        self,
        *,
        raw_root: Path,
        dataset: str,
        source_url: str,
        output_crs: str | None,
        snapshot_date: date | None = None,
        retrieved_at: datetime | None = None,
    ) -> None:
        self.retrieved_at = retrieved_at or datetime.now(UTC)
        if self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at must include a timezone")

        self.snapshot_date = snapshot_date or self.retrieved_at.date()
        self.dataset_root = raw_root / dataset
        self.final_path = self.dataset_root / f"snapshot_date={self.snapshot_date.isoformat()}"
        self.working_path = self.dataset_root / (
            f".{self.final_path.name}.{uuid4().hex}.incomplete"
        )
        self.source_url = source_url
        self.output_crs = output_crs
        self.parts: list[SnapshotPart] = []
        self.record_count = 0
        self._active = False

    def __enter__(self) -> "ParquetSnapshotWriter":
        self.dataset_root.mkdir(parents=True, exist_ok=True)
        if self.final_path.exists():
            raise FileExistsError(
                f"Snapshot already exists at {self.final_path}; raw snapshots are immutable"
            )

        self.working_path.mkdir()
        self._active = True
        return self

    def __exit__(self, *_exc_info: object) -> None:
        if self._active and self.working_path.exists():
            shutil.rmtree(self.working_path)
        self._active = False

    def _part_path(self, batch_number: int) -> Path:
        if not self._active:
            raise SnapshotError("Snapshot writer must be used as a context manager")

        part_path = self.working_path / f"part-{batch_number:05d}.parquet"
        if part_path.exists():
            raise SnapshotError(f"Batch {batch_number} has already been written")
        return part_path

    def _record_part(
        self,
        part_path: Path,
        records: int,
        *,
        invalid_geometry_count: int = 0,
    ) -> SnapshotPart:
        part = SnapshotPart(
            path=part_path.name,
            records=records,
            sha256=_sha256(part_path),
            invalid_geometry_count=invalid_geometry_count,
        )
        self.parts.append(part)
        self.record_count += records
        return part

    def write_records_batch(
        self,
        batch_number: int,
        records: list[dict[str, Any]],
    ) -> SnapshotPart:
        if not records:
            raise SnapshotError(f"Batch {batch_number} contains no records")

        part_path = self._part_path(batch_number)
        table = pa.Table.from_pylist(records)
        pq.write_table(table, part_path, compression="zstd")
        return self._record_part(part_path, len(records))

    def commit(
        self,
        *,
        expected_records: int,
        expected_parts: int,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        if not self._active:
            raise SnapshotError("Snapshot writer must be active before it can be committed")
        if self.record_count != expected_records:
            raise SnapshotError(
                f"Expected {expected_records:,} records but wrote {self.record_count:,}"
            )
        if len(self.parts) != expected_parts:
            raise SnapshotError(f"Expected {expected_parts:,} parts but wrote {len(self.parts):,}")

        manifest = {
            "dataset": self.dataset_root.name,
            "snapshot_date": self.snapshot_date.isoformat(),
            "retrieved_at": self.retrieved_at.isoformat(),
            "source_url": self.source_url,
            "output_crs": self.output_crs,
            "record_count": self.record_count,
            "part_count": len(self.parts),
            "files": [asdict(part) for part in sorted(self.parts, key=lambda part: part.path)],
            "metadata": metadata or {},
        }
        manifest_path = self.working_path / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        os.replace(self.working_path, self.final_path)
        self._active = False
        return self.final_path


class GeoParquetSnapshotWriter(ParquetSnapshotWriter):
    """Write immutable GeoJSON batches as GeoParquet snapshot parts."""

    def write_geodataframe_batch(
        self,
        batch_number: int,
        frame: gpd.GeoDataFrame,
    ) -> SnapshotPart:
        if self.output_crs is None:
            raise SnapshotError("GeoParquet snapshots require an output CRS")
        if frame.empty:
            raise SnapshotError(f"Batch {batch_number} contains no features")
        if frame.crs is None or "geometry" not in frame:
            raise SnapshotError(f"Batch {batch_number} does not contain georeferenced geometry")
        if frame.crs.to_string() != self.output_crs:
            raise SnapshotError(
                f"Batch {batch_number} uses {frame.crs.to_string()}, expected {self.output_crs}"
            )
        if frame.geometry.isna().any():
            raise SnapshotError(f"Batch {batch_number} contains missing geometry")

        part_path = self._part_path(batch_number)
        invalid_geometry_count = int((~frame.geometry.is_valid).sum())
        frame.to_parquet(part_path, index=False, compression="zstd")
        return self._record_part(
            part_path,
            len(frame),
            invalid_geometry_count=invalid_geometry_count,
        )

    def write_geojson_batch(self, batch_number: int, payload: dict[str, Any]) -> SnapshotPart:
        if self.output_crs is None:
            raise SnapshotError("GeoParquet snapshots require an output CRS")

        features = payload.get("features")
        if payload.get("type") != "FeatureCollection" or not isinstance(features, list):
            raise SnapshotError("Batch is not a valid GeoJSON FeatureCollection")
        if not features:
            raise SnapshotError(f"Batch {batch_number} contains no features")

        frame = gpd.GeoDataFrame.from_features(features, crs=self.output_crs)
        if frame.crs is None or "geometry" not in frame:
            raise SnapshotError(f"Batch {batch_number} does not contain a georeferenced geometry")
        if frame.geometry.isna().any():
            raise SnapshotError(f"Batch {batch_number} contains missing geometry")

        return self.write_geodataframe_batch(batch_number, frame)
