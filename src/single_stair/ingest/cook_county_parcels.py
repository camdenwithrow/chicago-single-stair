import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import date
from itertools import batched
from math import ceil
from pathlib import Path
from typing import Any

import httpx

from single_stair.ingest.snapshot import GeoParquetSnapshotWriter, SnapshotError

PARCEL_URL = (
    "https://gis.cookcountyil.gov/hosting/rest/services/Hosted/Parcel/FeatureServer/0/query"
)
MAX_PAGE_SIZE = 2_000
DEFAULT_CONCURRENCY = 4
MAX_ATTEMPTS = 5
MAX_RETRY_DELAY_SECONDS = 30.0
RETRYABLE_STATUS_CODES = frozenset({429, *range(500, 600)})
SOURCE_CRS = "EPSG:3435"
OUTPUT_CRS = "EPSG:4326"


@dataclass(frozen=True, slots=True)
class ParcelBatch:
    number: int
    total: int
    object_ids: tuple[int, ...]
    payload: dict[str, Any]


class ArcGISResponseError(RuntimeError):
    """Raised when ArcGIS returns an error inside a successful HTTP response."""


ProgressCallback = Callable[[ParcelBatch, int], None]


def _retry_delay_seconds(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return min(float(retry_after), MAX_RETRY_DELAY_SECONDS)
            except ValueError:
                pass

    return min(2 ** (attempt - 1), MAX_RETRY_DELAY_SECONDS)


def _arcgis_error_code(payload: dict[str, Any]) -> int | None:
    error = payload.get("error")
    if not isinstance(error, dict):
        return None

    code = error.get("code")
    return code if isinstance(code, int) else None


async def _request_json(
    client: httpx.AsyncClient,
    method: str,
    **kwargs: Any,
) -> dict[str, Any]:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response: httpx.Response | None = None

        try:
            response = await client.request(method, PARCEL_URL, **kwargs)
        except httpx.TransportError:
            if attempt == MAX_ATTEMPTS:
                raise
        else:
            if response.status_code not in RETRYABLE_STATUS_CODES:
                response.raise_for_status()
                payload = response.json()
                error_code = _arcgis_error_code(payload)

                if "error" not in payload:
                    return payload
                if error_code not in RETRYABLE_STATUS_CODES:
                    raise ArcGISResponseError(payload["error"])

            if attempt == MAX_ATTEMPTS:
                response.raise_for_status()
                raise ArcGISResponseError(response.json().get("error", response.text))

        await asyncio.sleep(_retry_delay_seconds(response, attempt))

    raise RuntimeError("Parcel request exhausted all retry attempts")


async def _fetch_object_ids(client: httpx.AsyncClient) -> list[int]:
    payload = await _request_json(
        client,
        "GET",
        params={"where": "1=1", "returnIdsOnly": "true", "f": "json"},
    )
    object_ids = payload.get("objectIds")

    if not isinstance(object_ids, list) or not all(isinstance(value, int) for value in object_ids):
        raise ArcGISResponseError("ArcGIS response did not contain a valid objectIds list")
    if len(object_ids) != len(set(object_ids)):
        raise ArcGISResponseError("ArcGIS response contained duplicate object IDs")

    return sorted(object_ids)


async def _fetch_parcel_batch(
    client: httpx.AsyncClient,
    object_ids: tuple[int, ...],
) -> dict[str, Any]:
    return await _request_json(
        client,
        "POST",
        data={
            "objectIds": ",".join(map(str, object_ids)),
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": 4326,
            "f": "geojson",
        },
    )


async def iter_parcel_batches(
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    page_size: int = MAX_PAGE_SIZE,
) -> AsyncIterator[ParcelBatch]:
    """Yield a stable snapshot of Cook County parcels in bounded concurrent batches."""
    if not 1 <= concurrency <= 10:
        raise ValueError("concurrency must be between 1 and 10")
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")

    limits = httpx.Limits(
        max_connections=concurrency,
        max_keepalive_connections=concurrency,
    )

    async with httpx.AsyncClient(
        timeout=120,
        follow_redirects=True,
        limits=limits,
    ) as client:
        object_ids = await _fetch_object_ids(client)
        total_batches = ceil(len(object_ids) / page_size)
        batch_iterator = iter(enumerate(batched(object_ids, page_size), start=1))
        pending: dict[asyncio.Task[dict[str, Any]], tuple[int, tuple[int, ...]]] = {}

        def schedule_next_batch() -> bool:
            try:
                batch_number, object_id_batch = next(batch_iterator)
            except StopIteration:
                return False

            task = asyncio.create_task(_fetch_parcel_batch(client, object_id_batch))
            pending[task] = (batch_number, object_id_batch)
            return True

        for _ in range(min(concurrency, total_batches)):
            schedule_next_batch()

        try:
            while pending:
                completed, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)

                for task in completed:
                    batch_number, object_id_batch = pending.pop(task)
                    yield ParcelBatch(
                        number=batch_number,
                        total=total_batches,
                        object_ids=object_id_batch,
                        payload=task.result(),
                    )
                    schedule_next_batch()
        finally:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)


def _validate_parcel_batch(batch: ParcelBatch) -> int:
    features = batch.payload.get("features")
    if not isinstance(features, list):
        raise SnapshotError(f"Batch {batch.number} does not contain a features list")

    returned_ids: list[int] = []
    for feature in features:
        properties = feature.get("properties") if isinstance(feature, dict) else None
        object_id = properties.get("objectid") if isinstance(properties, dict) else None
        if not isinstance(object_id, int):
            raise SnapshotError(f"Batch {batch.number} contains an invalid objectid")
        returned_ids.append(object_id)

    if len(returned_ids) != len(set(returned_ids)):
        raise SnapshotError(f"Batch {batch.number} contains duplicate object IDs")
    if set(returned_ids) != set(batch.object_ids):
        missing = len(set(batch.object_ids) - set(returned_ids))
        unexpected = len(set(returned_ids) - set(batch.object_ids))
        raise SnapshotError(
            f"Batch {batch.number} object IDs do not match the request "
            f"({missing} missing, {unexpected} unexpected)"
        )

    return len(returned_ids)


async def ingest_cook_county_parcels(
    *,
    raw_root: Path = Path("data/raw"),
    snapshot_date: date | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    page_size: int = MAX_PAGE_SIZE,
    progress: ProgressCallback | None = None,
) -> Path:
    requested_records = 0
    expected_parts: int | None = None

    with GeoParquetSnapshotWriter(
        raw_root=raw_root,
        dataset="cook_county_parcels",
        source_url=PARCEL_URL,
        output_crs=OUTPUT_CRS,
        snapshot_date=snapshot_date,
    ) as writer:
        async for batch in iter_parcel_batches(concurrency=concurrency, page_size=page_size):
            if expected_parts is None:
                expected_parts = batch.total
            elif batch.total != expected_parts:
                raise SnapshotError("Parcel batch count changed during ingestion")

            feature_count = _validate_parcel_batch(batch)
            await asyncio.to_thread(writer.write_geojson_batch, batch.number, batch.payload)
            requested_records += len(batch.object_ids)

            if progress is not None:
                progress(batch, feature_count)

        if expected_parts is None:
            raise SnapshotError("Cook County returned no parcel batches")

        return writer.commit(
            expected_records=requested_records,
            expected_parts=expected_parts,
            metadata={
                "source_object_id_count": requested_records,
                "downloaded_record_count": writer.record_count,
                "page_size": page_size,
                "concurrency": concurrency,
                "where": "1=1",
                "out_fields": "*",
                "return_geometry": True,
                "source_crs": SOURCE_CRS,
            },
        )
