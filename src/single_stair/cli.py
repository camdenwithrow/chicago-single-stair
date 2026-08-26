import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from single_stair.ingest.census import DEFAULT_ACS_YEAR, ingest_census_housing
from single_stair.ingest.chicago_city_land import ingest_chicago_city_owned_land
from single_stair.ingest.chicago_permits import ingest_chicago_building_permits
from single_stair.ingest.chicago_zoning import ZoningBatch, ingest_chicago_zoning
from single_stair.ingest.cook_county_buildings import (
    BuildingCharacteristicsBatch,
    ingest_cook_county_building_characteristics,
)
from single_stair.ingest.cook_county_parcels import (
    ParcelBatch,
    ingest_cook_county_parcels,
)
from single_stair.ingest.socrata_table import TableBatch
from single_stair.ingest.transit_stations import StationSnapshot, ingest_transit_stations


def _report_parcel_progress(batch: ParcelBatch, feature_count: int) -> None:
    print(f"Saved batch {batch.number:,}/{batch.total:,} ({feature_count:,} parcels)")


def _report_building_progress(batch: BuildingCharacteristicsBatch) -> None:
    print(f"Saved batch {batch.number:,}/{batch.total:,} ({len(batch.records):,} building records)")


def _report_zoning_progress(batch: ZoningBatch) -> None:
    print(f"Saved batch {batch.number:,}/{batch.total:,} ({len(batch.records):,} zoning polygons)")


def _report_permit_progress(batch: TableBatch) -> None:
    print(f"Saved batch {batch.number:,}/{batch.total:,} ({len(batch.records):,} permits)")


def _report_city_land_progress(batch: TableBatch) -> None:
    print(f"Saved batch {batch.number:,}/{batch.total:,} ({len(batch.records):,} properties)")


def _report_station_progress(snapshot: StationSnapshot) -> None:
    print(f"Saved {snapshot.records:,} {snapshot.agency} stations")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="single-stair")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest", help="Download a raw source snapshot")
    sources = ingest.add_subparsers(dest="source", required=True)

    sources.add_parser("cook-county-parcels", help="Ingest parcel geometry")
    buildings = sources.add_parser(
        "cook-county-buildings",
        help="Ingest single- and small-multifamily improvement characteristics",
    )
    buildings.add_argument(
        "--tax-year",
        type=int,
        help="Tax year to ingest; defaults to the latest available year",
    )
    sources.add_parser("chicago-zoning", help="Ingest current Chicago zoning polygons")
    sources.add_parser("chicago-building-permits", help="Ingest Chicago building permits")
    sources.add_parser("chicago-city-land", help="Ingest Chicago's city-owned land inventory")
    sources.add_parser("transit-stations", help="Ingest CTA and Metra station locations")
    census = sources.add_parser(
        "census-housing",
        help="Ingest ACS housing indicators and Cook County tract geometry",
    )
    census.add_argument(
        "--year",
        type=int,
        default=DEFAULT_ACS_YEAR,
        help=f"ACS 5-year vintage (default: {DEFAULT_ACS_YEAR})",
    )
    return parser


async def _run(arguments: argparse.Namespace) -> None:
    if arguments.source == "cook-county-parcels":
        snapshot_path = await ingest_cook_county_parcels(progress=_report_parcel_progress)
    elif arguments.source == "cook-county-buildings":
        snapshot_path = await ingest_cook_county_building_characteristics(
            tax_year=arguments.tax_year,
            progress=_report_building_progress,
        )
    elif arguments.source == "chicago-zoning":
        snapshot_path = await ingest_chicago_zoning(progress=_report_zoning_progress)
    elif arguments.source == "chicago-building-permits":
        snapshot_path = await ingest_chicago_building_permits(progress=_report_permit_progress)
    elif arguments.source == "chicago-city-land":
        snapshot_path = await ingest_chicago_city_owned_land(progress=_report_city_land_progress)
    elif arguments.source == "transit-stations":
        snapshots = await ingest_transit_stations(progress=_report_station_progress)
        snapshot_path = tuple(snapshot.path for snapshot in snapshots)
    else:
        census = await ingest_census_housing(year=arguments.year)
        snapshot_path = census.paths

    paths = snapshot_path if isinstance(snapshot_path, tuple) else (snapshot_path,)
    for path in paths:
        if not isinstance(path, Path):
            raise TypeError("Ingestion returned an invalid snapshot path")
        print(f"Completed snapshot: {path}")


def main(argv: Sequence[str] | None = None) -> None:
    arguments = _parser().parse_args(argv)
    asyncio.run(_run(arguments))
