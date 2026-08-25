import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import date
from math import ceil
from pathlib import Path
from typing import Any

import httpx

from single_stair.ingest.snapshot import GeoParquetSnapshotWriter, SnapshotError
from single_stair.ingest.socrata import (
    SocrataResponseError,
    app_token_headers,
    integer_field,
    request_rows,
)

DATASET_ID = "dj47-wfun"
DATASET_URL = f"https://data.cityofchicago.org/resource/{DATASET_ID}.json"
DEFAULT_PAGE_SIZE = 2_000
OUTPUT_CRS = "EPSG:4326"


@dataclass(frozen=True, slots=True)
class ZoningBoundary:
    expected_records: int
    maximum_objectid: int


@dataclass(frozen=True, slots=True)
class ZoningBatch:
    number: int
    total: int
    records: list[dict[str, Any]]
    first_objectid: int
    last_objectid: int


ProgressCallback = Callable[[ZoningBatch], None]


async def _request_rows(
    client: httpx.AsyncClient,
    params: dict[str, str | int],
) -> list[dict[str, Any]]:
    return await request_rows(client, DATASET_URL, params)


async def _dataset_boundary(client: httpx.AsyncClient) -> ZoningBoundary:
    rows = await _request_rows(
        client,
        {
            "$select": "max(objectid) as maximum_objectid,count(*) as expected_records",
            "$limit": 1,
        },
    )
    if len(rows) != 1:
        raise SocrataResponseError("Chicago zoning dataset contains no rows")

    return ZoningBoundary(
        expected_records=integer_field(rows[0], "expected_records"),
        maximum_objectid=integer_field(rows[0], "maximum_objectid"),
    )


def _validate_page(
    records: list[dict[str, Any]],
    *,
    previous_objectid: int,
    maximum_objectid: int,
) -> tuple[int, int]:
    object_ids = [integer_field(record, "objectid") for record in records]
    if object_ids != sorted(object_ids) or len(object_ids) != len(set(object_ids)):
        raise SocrataResponseError("Chicago zoning objectid values are not unique and sorted")
    if object_ids[0] <= previous_objectid or object_ids[-1] > maximum_objectid:
        raise SocrataResponseError("Chicago zoning page exceeded its snapshot boundary")

    for record in records:
        if not record.get("objectid") or not record.get("zone_class"):
            raise SocrataResponseError("Chicago zoning row is missing objectid or zone_class")
        geometry = record.get("the_geom")
        if not isinstance(geometry, dict) or not geometry.get("type"):
            raise SocrataResponseError("Chicago zoning row is missing its geometry")

    return object_ids[0], object_ids[-1]


async def _iter_zoning_batches(
    client: httpx.AsyncClient,
    boundary: ZoningBoundary,
    *,
    page_size: int,
) -> AsyncIterator[ZoningBatch]:
    previous_objectid = 0
    page_number = 0
    total_pages = ceil(boundary.expected_records / page_size)

    while previous_objectid < boundary.maximum_objectid:
        records = await _request_rows(
            client,
            {
                "$select": "*",
                "$where": (
                    f"objectid > {previous_objectid} AND objectid <= {boundary.maximum_objectid}"
                ),
                "$order": "objectid ASC",
                "$limit": page_size,
            },
        )
        if not records:
            break

        first_objectid, last_objectid = _validate_page(
            records,
            previous_objectid=previous_objectid,
            maximum_objectid=boundary.maximum_objectid,
        )
        page_number += 1
        yield ZoningBatch(
            number=page_number,
            total=total_pages,
            records=records,
            first_objectid=first_objectid,
            last_objectid=last_objectid,
        )
        previous_objectid = last_objectid


def _feature_collection(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": record["the_geom"],
                "properties": {key: value for key, value in record.items() if key != "the_geom"},
            }
            for record in records
        ],
    }


async def ingest_chicago_zoning(
    *,
    raw_root: Path = Path("data/raw"),
    snapshot_date: date | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    progress: ProgressCallback | None = None,
) -> Path:
    if not 1 <= page_size <= 50_000:
        raise ValueError("page_size must be between 1 and 50000")

    headers = app_token_headers("CHICAGO_SOCRATA_APP_TOKEN")
    async with httpx.AsyncClient(headers=headers, timeout=120, follow_redirects=True) as client:
        boundary = await _dataset_boundary(client)
        seen_object_ids: set[int] = set()
        latest_edit: str | None = None

        with GeoParquetSnapshotWriter(
            raw_root=raw_root,
            dataset="chicago_zoning",
            source_url=DATASET_URL,
            output_crs=OUTPUT_CRS,
            snapshot_date=snapshot_date,
        ) as writer:
            async for batch in _iter_zoning_batches(client, boundary, page_size=page_size):
                object_ids = {integer_field(record, "objectid") for record in batch.records}
                if len(object_ids) != len(batch.records) or seen_object_ids & object_ids:
                    raise SnapshotError(
                        "Chicago zoning snapshot contains duplicate objectid values"
                    )
                seen_object_ids.update(object_ids)

                edit_dates = [
                    str(record["edit_date"])
                    for record in batch.records
                    if record.get("edit_date") is not None
                ]
                if edit_dates:
                    latest_edit = max(latest_edit or edit_dates[0], *edit_dates)

                payload = _feature_collection(batch.records)
                await asyncio.to_thread(writer.write_geojson_batch, batch.number, payload)
                if progress is not None:
                    progress(batch)

            if writer.record_count != boundary.expected_records:
                raise SnapshotError(
                    f"Expected {boundary.expected_records:,} zoning rows but downloaded "
                    f"{writer.record_count:,}"
                )

            return writer.commit(
                expected_records=boundary.expected_records,
                expected_parts=ceil(boundary.expected_records / page_size),
                metadata={
                    "dataset_id": DATASET_ID,
                    "grain": ["objectid"],
                    "maximum_objectid": boundary.maximum_objectid,
                    "page_size": page_size,
                    "source_geometry_field": "the_geom",
                    "latest_source_edit": latest_edit,
                    "app_token_used": bool(headers),
                },
            )
