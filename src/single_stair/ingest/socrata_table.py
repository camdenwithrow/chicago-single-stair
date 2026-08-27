import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import date
from math import ceil
from pathlib import Path
from typing import Any, Literal

import httpx

from single_stair.ingest.snapshot import ParquetSnapshotWriter, SnapshotError
from single_stair.ingest.socrata import (
    SocrataResponseError,
    app_token_headers,
    integer_field,
    request_rows,
)

MAX_PAGE_SIZE = 50_000


@dataclass(frozen=True, slots=True)
class SocrataTable:
    dataset: str
    dataset_id: str
    domain: str
    key: str
    grain: tuple[str, ...]
    key_type: Literal["text", "number"] = "text"

    @property
    def source_url(self) -> str:
        return f"https://{self.domain}/resource/{self.dataset_id}.json"


@dataclass(frozen=True, slots=True)
class TableBoundary:
    expected_records: int
    maximum_key: str


@dataclass(frozen=True, slots=True)
class TableBatch:
    number: int
    total: int
    records: list[dict[str, Any]]
    first_key: str
    last_key: str


ProgressCallback = Callable[[TableBatch], None]


def _soql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _comparable_key(table: SocrataTable, value: str) -> str | int:
    if table.key_type == "text":
        return value
    try:
        return int(value)
    except ValueError as error:
        raise SocrataResponseError(
            f"{table.dataset} key {table.key} contains a non-numeric value"
        ) from error


def _soql_key(table: SocrataTable, value: str) -> str:
    return _soql_string(value) if table.key_type == "text" else str(_comparable_key(table, value))


async def _dataset_boundary(
    client: httpx.AsyncClient,
    table: SocrataTable,
) -> TableBoundary:
    rows = await request_rows(
        client,
        table.source_url,
        {
            "$select": (
                f"max({table.key}) as maximum_key,count(*) as expected_records,"
                f"count(distinct {table.key}) as distinct_keys"
            ),
            "$limit": 1,
        },
    )
    if len(rows) != 1 or rows[0].get("maximum_key") is None:
        raise SocrataResponseError(f"{table.dataset} contains no rows")

    expected_records = integer_field(rows[0], "expected_records")
    if integer_field(rows[0], "distinct_keys") != expected_records:
        raise SocrataResponseError(f"{table.dataset} key {table.key} is not unique")

    return TableBoundary(
        expected_records=expected_records,
        maximum_key=str(rows[0]["maximum_key"]),
    )


def _validate_page(
    records: list[dict[str, Any]],
    *,
    table: SocrataTable,
    previous_key: str | None,
    maximum_key: str,
) -> tuple[str, str]:
    keys = [str(record.get(table.key, "")) for record in records]
    if any(not key for key in keys):
        raise SocrataResponseError(f"{table.dataset} row is missing {table.key}")
    comparable_keys = [_comparable_key(table, key) for key in keys]
    if comparable_keys != sorted(comparable_keys) or len(keys) != len(set(keys)):
        raise SocrataResponseError(f"{table.dataset} keys are not unique and sorted")
    if (
        previous_key is not None and comparable_keys[0] <= _comparable_key(table, previous_key)
    ) or comparable_keys[-1] > _comparable_key(table, maximum_key):
        raise SocrataResponseError(f"{table.dataset} page exceeded its snapshot boundary")
    return keys[0], keys[-1]


async def _iter_table_batches(
    client: httpx.AsyncClient,
    table: SocrataTable,
    boundary: TableBoundary,
    *,
    page_size: int,
) -> AsyncIterator[TableBatch]:
    previous_key: str | None = None
    page_number = 0
    total_pages = ceil(boundary.expected_records / page_size)

    while previous_key is None or _comparable_key(table, previous_key) < _comparable_key(
        table, boundary.maximum_key
    ):
        predicates = [f"{table.key} <= {_soql_key(table, boundary.maximum_key)}"]
        if previous_key is not None:
            predicates.insert(0, f"{table.key} > {_soql_key(table, previous_key)}")

        records = await request_rows(
            client,
            table.source_url,
            {
                "$select": "*",
                "$where": " AND ".join(predicates),
                "$order": f"{table.key} ASC",
                "$limit": page_size,
            },
        )
        if not records:
            break

        first_key, last_key = _validate_page(
            records,
            table=table,
            previous_key=previous_key,
            maximum_key=boundary.maximum_key,
        )
        page_number += 1
        yield TableBatch(
            number=page_number,
            total=total_pages,
            records=records,
            first_key=first_key,
            last_key=last_key,
        )
        previous_key = last_key


async def ingest_socrata_table(
    table: SocrataTable,
    *,
    raw_root: Path = Path("data/raw"),
    snapshot_date: date | None = None,
    page_size: int = MAX_PAGE_SIZE,
    progress: ProgressCallback | None = None,
) -> Path:
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")

    headers = app_token_headers()
    async with httpx.AsyncClient(headers=headers, timeout=120, follow_redirects=True) as client:
        boundary = await _dataset_boundary(client, table)
        seen_keys: set[str] = set()

        with ParquetSnapshotWriter(
            raw_root=raw_root,
            dataset=table.dataset,
            source_url=table.source_url,
            output_crs=None,
            snapshot_date=snapshot_date,
        ) as writer:
            async for batch in _iter_table_batches(
                client,
                table,
                boundary,
                page_size=page_size,
            ):
                keys = {str(record[table.key]) for record in batch.records}
                if seen_keys & keys:
                    raise SnapshotError(f"{table.dataset} snapshot contains duplicate keys")
                seen_keys.update(keys)
                await asyncio.to_thread(writer.write_records_batch, batch.number, batch.records)
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
                    "app_token_used": bool(headers),
                },
            )
