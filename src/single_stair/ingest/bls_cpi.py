import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from single_stair.ingest.snapshot import ParquetSnapshotWriter, SnapshotError
from single_stair.ingest.socrata import (
    MAX_ATTEMPTS,
    RETRYABLE_STATUS_CODES,
    retry_delay_seconds,
)

BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
CHICAGO_CPI_SERIES_ID = "CUURS23ASA0"
DEFAULT_START_YEAR = 2015
MAX_UNREGISTERED_YEARS = 10


async def _request_bls(
    client: httpx.AsyncClient,
    *,
    start_year: int,
    end_year: int,
) -> Any:
    payload = {
        "seriesid": [CHICAGO_CPI_SERIES_ID],
        "startyear": str(start_year),
        "endyear": str(end_year),
    }
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response: httpx.Response | None = None
        try:
            response = await client.post(BLS_API_URL, json=payload)
        except httpx.TransportError:
            if attempt == MAX_ATTEMPTS:
                raise
        else:
            if response.status_code not in RETRYABLE_STATUS_CODES:
                response.raise_for_status()
                try:
                    return response.json()
                except ValueError as error:
                    raise SnapshotError("BLS returned invalid JSON") from error
            if attempt == MAX_ATTEMPTS:
                response.raise_for_status()
        await asyncio.sleep(retry_delay_seconds(response, attempt))
    raise RuntimeError("BLS download exhausted all retry attempts")


def _parse_bls_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("status") != "REQUEST_SUCCEEDED":
        raise SnapshotError("BLS CPI request did not succeed")
    try:
        series = payload["Results"]["series"]
    except (KeyError, TypeError) as error:
        raise SnapshotError("BLS CPI response is missing series data") from error
    if not isinstance(series, list) or len(series) != 1:
        raise SnapshotError("BLS CPI response must contain exactly one series")
    item = series[0]
    if item.get("seriesID") != CHICAGO_CPI_SERIES_ID or not isinstance(item.get("data"), list):
        raise SnapshotError("BLS CPI response contains an unexpected series")

    records = []
    keys: set[tuple[str, str, str]] = set()
    for original in item["data"]:
        if not isinstance(original, dict):
            raise SnapshotError("BLS CPI response contains a malformed observation")
        record = {"series_id": CHICAGO_CPI_SERIES_ID, **original}
        record["footnotes"] = json.dumps(record.get("footnotes", []), sort_keys=True)
        key = (str(record["series_id"]), str(record.get("year")), str(record.get("period")))
        if not key[1].isdigit() or not key[2].startswith("M"):
            raise SnapshotError("BLS CPI observation has an invalid year or period")
        if key in keys:
            raise SnapshotError("BLS CPI response contains duplicate observations")
        keys.add(key)
        records.append(record)
    if not records:
        raise SnapshotError("BLS CPI response contains no observations")
    return records


def _year_ranges(start_year: int, end_year: int) -> list[tuple[int, int]]:
    if start_year > end_year:
        raise ValueError("start_year must not be after end_year")
    ranges = []
    chunk_start = start_year
    while chunk_start <= end_year:
        chunk_end = min(chunk_start + MAX_UNREGISTERED_YEARS - 1, end_year)
        ranges.append((chunk_start, chunk_end))
        chunk_start = chunk_end + 1
    return ranges


async def ingest_chicago_cpi(
    *,
    raw_root: Path = Path("data/raw"),
    snapshot_date: date | None = None,
    start_year: int = DEFAULT_START_YEAR,
    end_year: int | None = None,
) -> Path:
    final_year = end_year or date.today().year
    ranges = _year_ranges(start_year, final_year)
    records = []
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        for range_start, range_end in ranges:
            payload = await _request_bls(client, start_year=range_start, end_year=range_end)
            records.extend(_parse_bls_payload(payload))
    keys = [(record["series_id"], record["year"], record["period"]) for record in records]
    if len(keys) != len(set(keys)):
        raise SnapshotError("BLS CPI chunks contain overlapping observations")
    records.sort(key=lambda record: (int(record["year"]), str(record["period"])))

    with ParquetSnapshotWriter(
        raw_root=raw_root,
        dataset="bls_chicago_cpi",
        source_url=BLS_API_URL,
        output_crs=None,
        snapshot_date=snapshot_date,
    ) as writer:
        writer.write_records_batch(1, records)
        return writer.commit(
            expected_records=len(records),
            expected_parts=1,
            metadata={
                "series_id": CHICAGO_CPI_SERIES_ID,
                "series_title": (
                    "All items in Chicago-Naperville-Elgin, IL-IN-WI, all urban consumers, "
                    "not seasonally adjusted"
                ),
                "base_period": "1982-84=100",
                "requested_start_year": start_year,
                "requested_end_year": final_year,
                "request_year_ranges": [list(year_range) for year_range in ranges],
                "grain": ["series_id", "year", "period"],
                "authentication_required": False,
                "owner": "U.S. Bureau of Labor Statistics",
            },
        )
