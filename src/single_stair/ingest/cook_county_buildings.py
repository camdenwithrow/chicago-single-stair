import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import date
from math import ceil
from pathlib import Path
from typing import Any

import httpx

from single_stair.ingest.snapshot import ParquetSnapshotWriter, SnapshotError
from single_stair.ingest.socrata import (
    SocrataResponseError,
    app_token_headers,
    request_rows,
)
from single_stair.ingest.socrata import (
    integer_field as _integer_field,
)

DATASET_ID = "x54s-btds"
DATASET_URL = f"https://datacatalog.cookcountyil.gov/resource/{DATASET_ID}.json"
DEFAULT_PAGE_SIZE = 50_000


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


ProgressCallback = Callable[[BuildingCharacteristicsBatch], None]


async def _request_rows(
    client: httpx.AsyncClient,
    params: dict[str, str | int],
) -> list[dict[str, Any]]:
    return await request_rows(client, DATASET_URL, params)


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
    return app_token_headers()


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
