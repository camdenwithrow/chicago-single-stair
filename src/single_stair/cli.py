import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from single_stair.ingest.census import DEFAULT_ACS_YEAR, ingest_census_housing
from single_stair.ingest.chicago_boundaries import ingest_chicago_boundaries
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
from single_stair.scenarios import load_scenario_catalog
from single_stair.transform.clean_and_join import build_clean_and_join
from single_stair.transform.combine_opportunity_need import build_combined_opportunity_need
from single_stair.transform.family_housing_need import build_family_housing_need
from single_stair.transform.parcel_opportunity import build_parcel_opportunity


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


def _report_boundary_progress(dataset: str, batch: TableBatch) -> None:
    print(f"Saved batch {batch.number:,}/{batch.total:,} ({len(batch.records):,} {dataset})")


def _report_transform_progress(part: int, total: int, chicago_parcels: int) -> None:
    if part == total or part % 25 == 0:
        print(f"Processed parcel part {part:,}/{total:,} ({chicago_parcels:,} Chicago parcels)")


def _report_opportunity_progress(part: int, total: int, parcels: int) -> None:
    print(f"Calculated opportunity part {part:,}/{total:,} ({parcels:,} parcels)")


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
    sources.add_parser("chicago-boundaries", help="Ingest current ward and community boundaries")
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
    transform = commands.add_parser("transform", help="Build an analysis-ready staged dataset")
    transformations = transform.add_subparsers(dest="transformation", required=True)
    transformations.add_parser(
        "clean-and-join",
        help="Normalize keys, select latest records, and add spatial context",
    )
    opportunity = transformations.add_parser(
        "parcel-opportunity",
        help="Calculate zoning and bedroom-size capacity for each parcel",
    )
    opportunity.add_argument("--policy", default="chicago_proposed")
    opportunity.add_argument("--estimate", default="median")
    transformations.add_parser(
        "family-housing-need",
        help="Calculate tract-level family housing need and uncertainty",
    )
    combined = transformations.add_parser(
        "combine-opportunity-need",
        help="Join parcel opportunity to tract need and build comparison summaries",
    )
    combined.add_argument("--policy", default="chicago_proposed")
    combined.add_argument("--estimate", default="median")
    scenarios = commands.add_parser("scenarios", help="Inspect versioned building assumptions")
    scenario_commands = scenarios.add_subparsers(dest="scenario_command", required=True)
    show = scenario_commands.add_parser("show", help="Resolve a policy and estimate profile")
    show.add_argument("--policy", help="Policy scenario ID; defaults to Chicago proposed")
    show.add_argument(
        "--estimate",
        help="Estimate profile ID; defaults to median",
    )
    show.add_argument("--config", type=Path, help="Optional scenario configuration file")
    return parser


async def _run(arguments: argparse.Namespace) -> None:
    if arguments.command == "scenarios":
        catalog = load_scenario_catalog(arguments.config)
        print(
            json.dumps(
                catalog.selection(
                    policy_id=arguments.policy,
                    estimate_id=arguments.estimate,
                ),
                indent=2,
            )
        )
        return
    if arguments.command == "transform" and arguments.transformation == "parcel-opportunity":
        snapshot_path = await asyncio.to_thread(
            build_parcel_opportunity,
            policy_id=arguments.policy,
            estimate_id=arguments.estimate,
            progress=_report_opportunity_progress,
        )
    elif arguments.command == "transform" and arguments.transformation == "family-housing-need":
        snapshot_path = await asyncio.to_thread(build_family_housing_need)
    elif (
        arguments.command == "transform" and arguments.transformation == "combine-opportunity-need"
    ):
        combined = await asyncio.to_thread(
            build_combined_opportunity_need,
            policy_id=arguments.policy,
            estimate_id=arguments.estimate,
            progress=_report_opportunity_progress,
        )
        snapshot_path = combined.paths
    elif arguments.command == "transform":
        staged = await asyncio.to_thread(
            build_clean_and_join,
            progress=_report_transform_progress,
        )
        snapshot_path = staged.paths
    elif arguments.source == "cook-county-parcels":
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
    elif arguments.source == "chicago-boundaries":
        boundaries = await ingest_chicago_boundaries(progress=_report_boundary_progress)
        snapshot_path = boundaries.paths
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
