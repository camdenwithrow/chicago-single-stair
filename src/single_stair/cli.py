import argparse
import asyncio
from collections.abc import Sequence

from single_stair.ingest.chicago_zoning import ZoningBatch, ingest_chicago_zoning
from single_stair.ingest.cook_county_buildings import (
    BuildingCharacteristicsBatch,
    ingest_cook_county_building_characteristics,
)
from single_stair.ingest.cook_county_parcels import (
    ParcelBatch,
    ingest_cook_county_parcels,
)


def _report_parcel_progress(batch: ParcelBatch, feature_count: int) -> None:
    print(f"Saved batch {batch.number:,}/{batch.total:,} ({feature_count:,} parcels)")


def _report_building_progress(batch: BuildingCharacteristicsBatch) -> None:
    print(f"Saved batch {batch.number:,}/{batch.total:,} ({len(batch.records):,} building records)")


def _report_zoning_progress(batch: ZoningBatch) -> None:
    print(f"Saved batch {batch.number:,}/{batch.total:,} ({len(batch.records):,} zoning polygons)")


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
    return parser


async def _run(arguments: argparse.Namespace) -> None:
    if arguments.source == "cook-county-parcels":
        snapshot_path = await ingest_cook_county_parcels(progress=_report_parcel_progress)
    elif arguments.source == "cook-county-buildings":
        snapshot_path = await ingest_cook_county_building_characteristics(
            tax_year=arguments.tax_year,
            progress=_report_building_progress,
        )
    else:
        snapshot_path = await ingest_chicago_zoning(progress=_report_zoning_progress)

    print(f"Completed snapshot: {snapshot_path}")


def main(argv: Sequence[str] | None = None) -> None:
    arguments = _parser().parse_args(argv)
    asyncio.run(_run(arguments))
