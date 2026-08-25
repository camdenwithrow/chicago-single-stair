import asyncio
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import date
from math import ceil
from pathlib import Path
from typing import Any

import httpx

from single_stair.ingest.snapshot import ParquetSnapshotWriter, SnapshotError

DATASET_ID = "x54s-btds"
DATASET_URL = f"https://datacatalog.cookcountyil.gov/resource/{DATASET_ID}.json"
DEFAULT_PAGE_SIZE = 50_000
MAX_ATTEMPTS = 5
MAX_RETRY_DELAY_SECONDS = 30.0
RETRYABLE_STATUS_CODES = frozenset({429, *range(500, 600)})


@dataclass(frozen=True, slots=True)
class BuildingCharacteristicsBatch:
    number: int
    total: int
    tax_year: int
    records: list[dict[str, Any]]
    first_sid: int
    last_sid: int


@dataclass(frozen=True, slots=True)
class DatasetBoundary:
    tax_year: int
    expected_records: int
    maximum_sid: int


class SocrataResponseError(RuntimeError):
    """Raised when the Cook County Socrata API returns an invalid response."""


ProgressCallback = Callable[[BuildingCharacteristicsBatch], None]


def _retry_delay_seconds(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return min(float(retry_after), MAX_RETRY_DELAY_SECONDS)
            except ValueError:
                pass

    return min(2 ** (attempt - 1), MAX_RETRY_DELAY_SECONDS)


async def _request_rows(
    client: httpx.AsyncClient,
    params: dict[str, str | int],
) -> list[dict[str, Any]]:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response: httpx.Response | None = None

        try:
            response = await client.get(DATASET_URL, params=params)
        except httpx.TransportError:
            if attempt == MAX_ATTEMPTS:
                raise
        else:
            if response.status_code not in RETRYABLE_STATUS_CODES:
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list) or not all(
                    isinstance(record, dict) for record in payload
                ):
                    raise SocrataResponseError("Socrata response was not a list of records")
                return payload

            if attempt == MAX_ATTEMPTS:
                response.raise_for_status()

        await asyncio.sleep(_retry_delay_seconds(response, attempt))

    raise RuntimeError("Building-characteristics request exhausted all retry attempts")


def _integer_field(record: dict[str, Any], field: str) -> int:
    value = record.get(field)
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise SocrataResponseError(f"Socrata response contained an invalid {field}") from error


async def _latest_tax_year(client: httpx.AsyncClient) -> int:
    rows = await _request_rows(client, {"$select": "max(year) as tax_year", "$limit": 1})
    if len(rows) != 1:
        raise SocrataResponseError("Could not determine the latest building tax year")
    return _integer_field(rows[0], "tax_year")


async def _dataset_boundary(client: httpx.AsyncClient, tax_year: int) -> DatasetBoundary:
    upper_rows = await _request_rows(
        client,
        {
            "$select": ":sid",
            "$where": f"year = {tax_year}",
            "$order": ":sid DESC",
            "$limit": 1,
        },
    )
    if len(upper_rows) != 1:
        raise SocrataResponseError(f"No building characteristics found for tax year {tax_year}")

    maximum_sid = _integer_field(upper_rows[0], ":sid")
    counts = await _request_rows(
        client,
        {
            "$select": "count(*) as expected_records",
            "$where": f"year = {tax_year} AND :sid <= {maximum_sid}",
            "$limit": 1,
        },
    )
    if len(counts) != 1:
        raise SocrataResponseError(f"Could not count building characteristics for {tax_year}")

    return DatasetBoundary(
        tax_year=tax_year,
        expected_records=_integer_field(counts[0], "expected_records"),
        maximum_sid=maximum_sid,
    )


def _validate_page(
    records: list[dict[str, Any]],
    *,
    tax_year: int,
    previous_sid: int,
    maximum_sid: int,
) -> tuple[int, int]:
    sids = [_integer_field(record, ":sid") for record in records]
    if sids != sorted(sids) or len(sids) != len(set(sids)):
        raise SocrataResponseError("Building-characteristics :sid values are not unique and sorted")
    if sids[0] <= previous_sid or sids[-1] > maximum_sid:
        raise SocrataResponseError("Building-characteristics page exceeded its snapshot boundary")

    for record in records:
        if _integer_field(record, "year") != tax_year:
            raise SocrataResponseError("Building-characteristics page contained another tax year")
        if not record.get("pin") or record.get("card") is None:
            raise SocrataResponseError(
                "Building-characteristics row is missing its PIN or card key"
            )

    return sids[0], sids[-1]


async def _iter_building_batches(
    client: httpx.AsyncClient,
    boundary: DatasetBoundary,
    *,
    page_size: int,
) -> AsyncIterator[BuildingCharacteristicsBatch]:
    previous_sid = 0
    page_number = 0
    total_pages = ceil(boundary.expected_records / page_size)

    while previous_sid < boundary.maximum_sid:
        where = (
            f"year = {boundary.tax_year} AND :sid > {previous_sid} "
            f"AND :sid <= {boundary.maximum_sid}"
        )
        records = await _request_rows(
            client,
            {
                "$select": ":sid,*",
                "$where": where,
                "$order": ":sid ASC",
                "$limit": page_size,
            },
        )
        if not records:
            break

        first_sid, last_sid = _validate_page(
            records,
            tax_year=boundary.tax_year,
            previous_sid=previous_sid,
            maximum_sid=boundary.maximum_sid,
        )
        page_number += 1
        yield BuildingCharacteristicsBatch(
            number=page_number,
            total=total_pages,
            tax_year=boundary.tax_year,
            records=records,
            first_sid=first_sid,
            last_sid=last_sid,
        )
        previous_sid = last_sid


def _socrata_headers() -> dict[str, str]:
    app_token = os.environ.get("COOK_SOCRATA_APP_TOKEN")
    return {"X-App-Token": app_token} if app_token else {}


async def ingest_cook_county_building_characteristics(
    *,
    raw_root: Path = Path("data/raw"),
    snapshot_date: date | None = None,
    tax_year: int | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    progress: ProgressCallback | None = None,
) -> Path:
    if not 1 <= page_size <= DEFAULT_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {DEFAULT_PAGE_SIZE}")

    async with httpx.AsyncClient(
        headers=_socrata_headers(),
        timeout=120,
        follow_redirects=True,
    ) as client:
        selected_tax_year = tax_year or await _latest_tax_year(client)
        boundary = await _dataset_boundary(client, selected_tax_year)

        with ParquetSnapshotWriter(
            raw_root=raw_root,
            dataset="cook_county_building_characteristics",
            source_url=DATASET_URL,
            output_crs=None,
            snapshot_date=snapshot_date,
        ) as writer:
            async for batch in _iter_building_batches(
                client,
                boundary,
                page_size=page_size,
            ):
                await asyncio.to_thread(writer.write_records_batch, batch.number, batch.records)
                if progress is not None:
                    progress(batch)

            if writer.record_count != boundary.expected_records:
                raise SnapshotError(
                    f"Expected {boundary.expected_records:,} building rows but downloaded "
                    f"{writer.record_count:,}"
                )

            return writer.commit(
                expected_records=boundary.expected_records,
                expected_parts=ceil(boundary.expected_records / page_size),
                metadata={
                    "dataset_id": DATASET_ID,
                    "grain": ["pin", "year", "card"],
                    "tax_year": selected_tax_year,
                    "maximum_sid": boundary.maximum_sid,
                    "page_size": page_size,
                    "app_token_used": bool(_socrata_headers()),
                },
            )
