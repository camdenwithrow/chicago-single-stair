import asyncio
import hashlib
import io
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import geopandas as gpd
import httpx

from single_stair.ingest.download import SourceResponseError, request_bytes, request_json
from single_stair.ingest.snapshot import (
    GeoParquetSnapshotWriter,
    ParquetSnapshotWriter,
    SnapshotError,
)

DEFAULT_ACS_YEAR = 2024
ILLINOIS_FIPS = "17"
COOK_COUNTY_FIPS = "031"


@dataclass(frozen=True, slots=True)
class AcsTable:
    group: str
    topic: str
    estimates: tuple[str, ...]

    @property
    def variables(self) -> tuple[str, ...]:
        return tuple(
            variable
            for estimate in self.estimates
            for variable in (f"{self.group}_{estimate}E", f"{self.group}_{estimate}M")
        )


ACS_TABLES = (
    AcsTable("B11005", "households_with_children", ("001", "002", "003")),
    AcsTable(
        "B25115",
        "renter_families_with_children",
        ("015", "016", "018", "022", "025"),
    ),
    AcsTable("B25042", "renter_bedrooms", ("009", "010", "011", "012", "013", "014", "015")),
    AcsTable("B25014", "renter_overcrowding", ("008", "009", "010", "011", "012", "013")),
    AcsTable(
        "B25070",
        "gross_rent_as_percent_of_income",
        ("001", "002", "003", "004", "005", "006", "007", "008", "009", "010", "011"),
    ),
)


@dataclass(frozen=True, slots=True)
class CensusSnapshots:
    acs_tables: tuple[Path, ...]
    tract_geometry: Path

    @property
    def paths(self) -> tuple[Path, ...]:
        return (*self.acs_tables, self.tract_geometry)


def _census_api_key() -> str:
    key = os.environ.get("CENSUS_API_KEY")
    if not key:
        raise RuntimeError(
            "CENSUS_API_KEY is required; request one from api.census.gov/data/key_signup.html"
        )
    return key


def _records_from_census_payload(payload: Any, table: AcsTable) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[0], list):
        raise SnapshotError(f"ACS {table.group} response is not a header plus data rows")

    header = payload[0]
    required = {"NAME", *table.variables, "state", "county", "tract"}
    if not all(isinstance(field, str) for field in header) or not required.issubset(header):
        raise SnapshotError(f"ACS {table.group} response is missing requested fields")

    records: list[dict[str, Any]] = []
    geography_keys: set[tuple[str, str, str]] = set()
    for row in payload[1:]:
        if not isinstance(row, list) or len(row) != len(header):
            raise SnapshotError(f"ACS {table.group} response contains a malformed row")
        record = dict(zip(header, row, strict=True))
        geography_key = (
            str(record["state"]),
            str(record["county"]),
            str(record["tract"]),
        )
        if geography_key in geography_keys:
            raise SnapshotError(f"ACS {table.group} contains a duplicate tract")
        geography_keys.add(geography_key)
        records.append(record)

    if not records:
        raise SnapshotError(f"ACS {table.group} returned no Cook County tracts")
    return records


async def _ingest_acs_table(
    client: httpx.AsyncClient,
    table: AcsTable,
    *,
    year: int,
    api_key: str,
    raw_root: Path,
    snapshot_date: date | None,
) -> Path:
    source_url = f"https://api.census.gov/data/{year}/acs/acs5"
    params = [
        ("get", ",".join(("NAME", *table.variables))),
        ("for", "tract:*"),
        ("in", f"state:{ILLINOIS_FIPS}"),
        ("in", f"county:{COOK_COUNTY_FIPS}"),
        ("key", api_key),
    ]
    try:
        payload = await request_json(client, source_url, params=params)
    except SourceResponseError as error:
        if "Key" in str(error):
            message = "Census API authentication failed; verify CENSUS_API_KEY in your environment"
        else:
            message = "Census API returned an unexpected response"
        raise RuntimeError(message) from None
    except httpx.HTTPError:
        # Suppress the original exception because Census credentials are carried in the query URL.
        raise RuntimeError(
            "Census API request failed; verify connectivity and CENSUS_API_KEY"
        ) from None
    records = _records_from_census_payload(payload, table)

    dataset = f"census_acs5_{table.group.lower()}"
    with ParquetSnapshotWriter(
        raw_root=raw_root,
        dataset=dataset,
        source_url=source_url,
        output_crs=None,
        snapshot_date=snapshot_date,
    ) as writer:
        await asyncio.to_thread(writer.write_records_batch, 1, records)
        return writer.commit(
            expected_records=len(records),
            expected_parts=1,
            metadata={
                "vintage": year,
                "survey": "ACS 5-year detailed tables",
                "group": table.group,
                "topic": table.topic,
                "variables": list(table.variables),
                "geography": {
                    "level": "tract",
                    "state_fips": ILLINOIS_FIPS,
                    "county_fips": COOK_COUNTY_FIPS,
                },
                "grain": ["state", "county", "tract"],
                "api_key_used": True,
            },
        )


async def _ingest_tract_geometry(
    client: httpx.AsyncClient,
    *,
    year: int,
    raw_root: Path,
    snapshot_date: date | None,
) -> Path:
    source_url = (
        f"https://www2.census.gov/geo/tiger/TIGER{year}/TRACT/tl_{year}_{ILLINOIS_FIPS}_tract.zip"
    )
    archive = await request_bytes(client, source_url)
    frame = await asyncio.to_thread(gpd.read_file, io.BytesIO(archive))
    required = {"STATEFP", "COUNTYFP", "TRACTCE", "GEOID", "geometry"}
    if not required.issubset(frame.columns):
        raise SnapshotError("Census tract geometry is missing required source fields")
    frame = frame.loc[
        (frame["STATEFP"] == ILLINOIS_FIPS) & (frame["COUNTYFP"] == COOK_COUNTY_FIPS)
    ].copy()
    if frame.empty or frame["GEOID"].duplicated().any():
        raise SnapshotError("Cook County tract geometry is empty or contains duplicate GEOIDs")
    if not frame.geometry.geom_type.isin({"Polygon", "MultiPolygon"}).all():
        raise SnapshotError("Census tract geometry contains a non-polygon feature")
    if frame.crs is None:
        raise SnapshotError("Census tract geometry does not declare a CRS")

    output_crs = frame.crs.to_string()
    with GeoParquetSnapshotWriter(
        raw_root=raw_root,
        dataset="census_tract_geometry",
        source_url=source_url,
        output_crs=output_crs,
        snapshot_date=snapshot_date,
    ) as writer:
        await asyncio.to_thread(writer.write_geodataframe_batch, 1, frame)
        return writer.commit(
            expected_records=len(frame),
            expected_parts=1,
            metadata={
                "vintage": year,
                "boundary_product": "TIGER/Line Census Tracts",
                "source_archive_bytes": len(archive),
                "source_archive_sha256": hashlib.sha256(archive).hexdigest(),
                "source_scope": "Illinois",
                "snapshot_filter": {"STATEFP": ILLINOIS_FIPS, "COUNTYFP": COOK_COUNTY_FIPS},
                "grain": ["GEOID"],
            },
        )


async def ingest_census_housing(
    *,
    raw_root: Path = Path("data/raw"),
    snapshot_date: date | None = None,
    year: int = DEFAULT_ACS_YEAR,
) -> CensusSnapshots:
    if not 2009 <= year <= DEFAULT_ACS_YEAR:
        raise ValueError(f"year must be between 2009 and {DEFAULT_ACS_YEAR}")

    api_key = _census_api_key()
    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
        acs_paths = []
        for table in ACS_TABLES:
            acs_paths.append(
                await _ingest_acs_table(
                    client,
                    table,
                    year=year,
                    api_key=api_key,
                    raw_root=raw_root,
                    snapshot_date=snapshot_date,
                )
            )
        tract_path = await _ingest_tract_geometry(
            client,
            year=year,
            raw_root=raw_root,
            snapshot_date=snapshot_date,
        )

    return CensusSnapshots(acs_tables=tuple(acs_paths), tract_geometry=tract_path)
