import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
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
    first_pin: str
    first_card: Decimal
    last_pin: str
    last_card: Decimal


@dataclass(frozen=True, slots=True)
class DatasetBoundary:
    tax_year: int
    expected_records: int
    maximum_pin: str
    maximum_card: Decimal


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
            "$select": "pin,card",
            "$where": f"year = {tax_year}",
            "$order": "pin DESC,card DESC",
            "$limit": 1,
        },
    )
    if len(upper_rows) != 1:
        raise SocrataResponseError(f"No building characteristics found for tax year {tax_year}")

    maximum_pin = str(upper_rows[0].get("pin", ""))
    maximum_card = _decimal_field(upper_rows[0], "card")
    if not maximum_pin:
        raise SocrataResponseError(f"Building characteristics for {tax_year} have no PIN key")
    counts = await _request_rows(
        client,
        {
            "$select": "count(*) as expected_records",
            "$where": f"year = {tax_year} AND pin <= '{maximum_pin}'",
            "$limit": 1,
        },
    )
    if len(counts) != 1:
        raise SocrataResponseError(f"Could not count building characteristics for {tax_year}")

    return DatasetBoundary(
        tax_year=tax_year,
        expected_records=_integer_field(counts[0], "expected_records"),
        maximum_pin=maximum_pin,
        maximum_card=maximum_card,
    )


def _decimal_field(record: dict[str, Any], field: str) -> Decimal:
    try:
        return Decimal(str(record.get(field)))
    except (InvalidOperation, TypeError) as error:
        raise SocrataResponseError(f"Socrata response contained an invalid {field}") from error


def _validate_page(
    records: list[dict[str, Any]],
    *,
    tax_year: int,
    previous_key: tuple[str, Decimal] | None,
    maximum_key: tuple[str, Decimal],
) -> tuple[tuple[str, Decimal], tuple[str, Decimal]]:
    keys = [(str(record.get("pin", "")), _decimal_field(record, "card")) for record in records]
    if any(not pin for pin, _card in keys):
        raise SocrataResponseError("Building-characteristics row is missing its PIN key")
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise SocrataResponseError(
            "Building-characteristics PIN/card keys are not unique and sorted"
        )
    if (previous_key is not None and keys[0] <= previous_key) or keys[-1] > maximum_key:
        raise SocrataResponseError("Building-characteristics page exceeded its snapshot boundary")

    for record in records:
        if _integer_field(record, "year") != tax_year:
            raise SocrataResponseError("Building-characteristics page contained another tax year")
        if not record.get("pin") or record.get("card") is None:
            raise SocrataResponseError(
                "Building-characteristics row is missing its PIN or card key"
            )

    return keys[0], keys[-1]


async def _iter_building_batches(
    client: httpx.AsyncClient,
    boundary: DatasetBoundary,
    *,
    page_size: int,
) -> AsyncIterator[BuildingCharacteristicsBatch]:
    previous_key: tuple[str, Decimal] | None = None
    page_number = 0
    total_pages = ceil(boundary.expected_records / page_size)
    maximum_key = (boundary.maximum_pin, boundary.maximum_card)

    while previous_key is None or previous_key < maximum_key:
        predicates = [f"year = {boundary.tax_year}", f"pin <= '{boundary.maximum_pin}'"]
        if previous_key is not None:
            previous_pin, previous_card = previous_key
            predicates.append(
                f"(pin > '{previous_pin}' OR (pin = '{previous_pin}' AND card > {previous_card}))"
            )
        records = await _request_rows(
            client,
            {
                "$select": "*",
                "$where": " AND ".join(predicates),
                "$order": "pin ASC,card ASC",
                "$limit": page_size,
            },
        )
        if not records:
            break

        first_key, last_key = _validate_page(
            records,
            tax_year=boundary.tax_year,
            previous_key=previous_key,
            maximum_key=maximum_key,
        )
        page_number += 1
        yield BuildingCharacteristicsBatch(
            number=page_number,
            total=total_pages,
            tax_year=boundary.tax_year,
            records=records,
            first_pin=first_key[0],
            first_card=first_key[1],
            last_pin=last_key[0],
            last_card=last_key[1],
        )
        previous_key = last_key


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
                    "maximum_pin": boundary.maximum_pin,
                    "maximum_card": str(boundary.maximum_card),
                    "page_size": page_size,
                    "app_token_used": bool(_socrata_headers()),
                },
            )
