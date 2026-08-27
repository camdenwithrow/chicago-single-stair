import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from math import ceil
from pathlib import Path
from typing import Any

import httpx

from single_stair.ingest.snapshot import GeoParquetSnapshotWriter, SnapshotError
from single_stair.ingest.socrata import app_token_headers
from single_stair.ingest.socrata_table import (
    MAX_PAGE_SIZE,
    SocrataTable,
    TableBatch,
    _dataset_boundary,
    _iter_table_batches,
)

OUTPUT_CRS = "EPSG:4326"


@dataclass(frozen=True, slots=True)
class SocrataGeometryTable:
    table: SocrataTable
    geometry_field: str
    required_fields: tuple[str, ...]


ProgressCallback = Callable[[TableBatch], None]


def _feature_collection(
    records: list[dict[str, Any]],
    specification: SocrataGeometryTable,
) -> dict[str, Any]:
    features = []
    for record in records:
        missing = [field for field in specification.required_fields if not record.get(field)]
        if missing:
            raise SnapshotError(
                f"{specification.table.dataset} row is missing: {', '.join(missing)}"
            )
        geometry = record.get(specification.geometry_field)
        if not isinstance(geometry, dict) or not geometry.get("type"):
            raise SnapshotError(f"{specification.table.dataset} row is missing geometry")
        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    key: value
                    for key, value in record.items()
                    if key != specification.geometry_field
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


async def ingest_socrata_geometry_table(
    specification: SocrataGeometryTable,
    *,
    raw_root: Path = Path("data/raw"),
    snapshot_date: date | None = None,
    page_size: int = MAX_PAGE_SIZE,
    progress: ProgressCallback | None = None,
) -> Path:
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")

    table = specification.table
    headers = app_token_headers()
    async with httpx.AsyncClient(headers=headers, timeout=120, follow_redirects=True) as client:
        boundary = await _dataset_boundary(client, table)
        with GeoParquetSnapshotWriter(
            raw_root=raw_root,
            dataset=table.dataset,
            source_url=table.source_url,
            output_crs=OUTPUT_CRS,
            snapshot_date=snapshot_date,
        ) as writer:
            async for batch in _iter_table_batches(
                client,
                table,
                boundary,
                page_size=page_size,
            ):
                payload = _feature_collection(batch.records, specification)
                await asyncio.to_thread(writer.write_geojson_batch, batch.number, payload)
                if progress is not None:
                    progress(batch)

            if writer.record_count != boundary.expected_records:
                raise SnapshotError(
                    f"Expected {boundary.expected_records:,} {table.dataset} rows but downloaded "
                    f"{writer.record_count:,}"
                )

            return writer.commit(
                expected_records=boundary.expected_records,
                expected_parts=ceil(boundary.expected_records / page_size),
                metadata={
                    "dataset_id": table.dataset_id,
                    "grain": list(table.grain),
                    "snapshot_key": table.key,
                    "maximum_key": boundary.maximum_key,
                    "page_size": page_size,
                    "source_geometry_field": specification.geometry_field,
                    "app_token_used": bool(headers),
                },
            )
