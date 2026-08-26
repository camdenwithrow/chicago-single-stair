import asyncio
import csv
import hashlib
import io
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from single_stair.ingest.download import request_bytes
from single_stair.ingest.snapshot import ParquetSnapshotWriter, SnapshotError


@dataclass(frozen=True, slots=True)
class GtfsFeed:
    dataset: str
    agency: str
    source_url: str
    station_location_type: str | None


CTA_FEED = GtfsFeed(
    dataset="cta_stations",
    agency="Chicago Transit Authority",
    source_url="https://www.transitchicago.com/downloads/sch_data/google_transit.zip",
    station_location_type="1",
)
METRA_FEED = GtfsFeed(
    dataset="metra_stations",
    agency="Metra",
    source_url="https://schedules.metrarail.com/gtfs/schedule.zip",
    station_location_type=None,
)


@dataclass(frozen=True, slots=True)
class StationSnapshot:
    agency: str
    path: Path
    records: int


ProgressCallback = Callable[[StationSnapshot], None]


def _station_records(archive: bytes, feed: GtfsFeed) -> tuple[list[dict[str, Any]], str]:
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            member = zipped.getinfo("stops.txt")
            with zipped.open(member) as raw_file:
                text_file = io.TextIOWrapper(raw_file, encoding="utf-8-sig", newline="")
                reader = csv.DictReader(text_file, skipinitialspace=True)
                if reader.fieldnames is None:
                    raise SnapshotError(f"{feed.agency} stops.txt has no header")
                reader.fieldnames = [field.strip() for field in reader.fieldnames]
                records = [
                    {str(key).strip(): value for key, value in record.items()}
                    for record in reader
                    if feed.station_location_type is None
                    or record.get("location_type") == feed.station_location_type
                ]
    except (KeyError, zipfile.BadZipFile) as error:
        raise SnapshotError(f"{feed.agency} GTFS archive does not contain stops.txt") from error

    required = {"stop_id", "stop_name", "stop_lat", "stop_lon"}
    stop_ids: list[str] = []
    for record in records:
        if not required.issubset(record) or any(not record[field] for field in required):
            raise SnapshotError(f"{feed.agency} station is missing an ID, name, or coordinate")
        stop_ids.append(str(record["stop_id"]))
        try:
            latitude = float(record["stop_lat"])
            longitude = float(record["stop_lon"])
        except (TypeError, ValueError) as error:
            raise SnapshotError(f"{feed.agency} station has an invalid coordinate") from error
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise SnapshotError(f"{feed.agency} station coordinate is outside valid bounds")

    if not records:
        raise SnapshotError(f"{feed.agency} GTFS feed contains no station records")
    if len(stop_ids) != len(set(stop_ids)):
        raise SnapshotError(f"{feed.agency} GTFS station stop_id values are not unique")

    member_timestamp = "-".join(
        [
            f"{member.date_time[0]:04d}",
            f"{member.date_time[1]:02d}",
            f"{member.date_time[2]:02d}",
        ]
    )
    return records, member_timestamp


async def ingest_gtfs_stations(
    feed: GtfsFeed,
    *,
    raw_root: Path = Path("data/raw"),
    snapshot_date: date | None = None,
) -> StationSnapshot:
    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
        archive = await request_bytes(client, feed.source_url)

    records, stops_file_date = await asyncio.to_thread(_station_records, archive, feed)
    with ParquetSnapshotWriter(
        raw_root=raw_root,
        dataset=feed.dataset,
        source_url=feed.source_url,
        output_crs=None,
        snapshot_date=snapshot_date,
    ) as writer:
        await asyncio.to_thread(writer.write_records_batch, 1, records)
        path = writer.commit(
            expected_records=len(records),
            expected_parts=1,
            metadata={
                "agency": feed.agency,
                "format": "GTFS Schedule",
                "source_member": "stops.txt",
                "source_member_date": stops_file_date,
                "source_archive_bytes": len(archive),
                "source_archive_sha256": hashlib.sha256(archive).hexdigest(),
                "source_filter": (
                    None
                    if feed.station_location_type is None
                    else f"location_type={feed.station_location_type}"
                ),
                "grain": ["stop_id"],
            },
        )
    return StationSnapshot(agency=feed.agency, path=path, records=len(records))


async def ingest_transit_stations(
    *,
    raw_root: Path = Path("data/raw"),
    snapshot_date: date | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[StationSnapshot, StationSnapshot]:
    snapshots = await asyncio.gather(
        ingest_gtfs_stations(CTA_FEED, raw_root=raw_root, snapshot_date=snapshot_date),
        ingest_gtfs_stations(METRA_FEED, raw_root=raw_root, snapshot_date=snapshot_date),
    )
    if progress is not None:
        for snapshot in snapshots:
            progress(snapshot)
    return snapshots
