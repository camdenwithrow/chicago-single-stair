import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from single_stair.ingest.snapshot import (
    GeoParquetSnapshotWriter,
    ParquetSnapshotWriter,
    SnapshotError,
)
from single_stair.scenarios import BEDROOM_CATEGORIES, load_scenario_catalog
from single_stair.transform.clean_and_join import OUTPUT_CRS, latest_snapshot
from single_stair.transform.family_housing_need import ESTIMATE_PROFILES

ProgressCallback = Callable[[int, int, int], None]

CAPACITY_SCENARIOS = {
    "current_two_stair": "current_two_stair",
    "current_single_stair": "current_single_stair",
    "upzoned_single_stair": "upzoned_single_stair",
}
AGGREGATIONS = {
    "community_area": ("community_area_number", "community_area_name"),
    "ward": ("ward",),
    "zoning_class": ("canonical_zone_class",),
    "transit_band": ("transit_band_id", "transit_band_label"),
}
BASE_FLAG_COLUMNS = (
    "within_half_mile_transit",
    "is_city_owned",
    "is_underbuilt",
    "is_vacant",
    "requires_legal_or_site_review",
)


@dataclass(frozen=True, slots=True)
class TransitBand:
    id: str
    label: str
    maximum_distance_ft: float | None


@dataclass(frozen=True, slots=True)
class CombinedAnalysisConfig:
    config_version: str
    transit_bands: tuple[TransitBand, ...]
    half_mile_distance_ft: float
    scenario_order: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CombinedAnalysisSnapshots:
    parcels: Path
    summaries: tuple[Path, ...]

    @property
    def paths(self) -> tuple[Path, ...]:
        return (self.parcels, *self.summaries)


def load_combined_analysis_config(config_path: Path | None = None) -> CombinedAnalysisConfig:
    if config_path is None:
        config_file = files("single_stair").joinpath("config/combined_analysis.v1.json")
        payload = json.loads(config_file.read_text(encoding="utf-8"))
    else:
        payload = json.loads(config_path.read_text(encoding="utf-8"))

    config = CombinedAnalysisConfig(
        config_version=str(payload["config_version"]),
        transit_bands=tuple(TransitBand(**values) for values in payload["transit_bands"]),
        half_mile_distance_ft=float(payload["half_mile_distance_ft"]),
        scenario_order=tuple(payload["scenario_order"]),
        notes=tuple(payload["notes"]),
    )
    finite_maxima = [
        band.maximum_distance_ft
        for band in config.transit_bands
        if band.maximum_distance_ft is not None
    ]
    if not config.transit_bands or config.transit_bands[-1].maximum_distance_ft is not None:
        raise ValueError("The final transit band must be open-ended")
    if finite_maxima != sorted(finite_maxima) or any(value <= 0 for value in finite_maxima):
        raise ValueError("Transit band maximum distances must be positive and increasing")
    if config.half_mile_distance_ft not in finite_maxima:
        raise ValueError("Half-mile distance must be a configured transit-band boundary")
    if config.scenario_order != tuple(CAPACITY_SCENARIOS):
        raise ValueError("Configured scenario order does not match the capacity schema")
    return config


def _parquet_parts(snapshot: Path) -> list[Path]:
    parts = sorted(snapshot.glob("part-*.parquet"))
    if not parts:
        raise FileNotFoundError(f"Snapshot contains no Parquet parts: {snapshot}")
    return parts


def _read_need_snapshot(snapshot: Path) -> pd.DataFrame:
    columns = [
        "census_tract_geoid",
        "renter_households_with_children_share_pct",
        "renter_households_with_children_share_moe_pct",
        "renter_occupied_3_plus_bedroom_share_pct",
        "renter_occupied_3_plus_bedroom_share_moe_pct",
        "renter_overcrowding_rate_pct",
        "renter_overcrowding_rate_moe_pct",
        "renter_cost_burden_rate_pct",
        "renter_cost_burden_rate_moe_pct",
        "headline_estimates_reliable",
        "need_score_components_reliable",
        "need_ranking_eligible",
    ]
    for threshold in (2, 3, 4):
        columns.extend(
            f"family_housing_{threshold}_plus_gap_{profile}_estimate"
            for profile in ESTIMATE_PROFILES
        )
    for profile in ESTIMATE_PROFILES:
        columns.extend(
            [
                f"{profile}_need_score",
                f"{profile}_need_high_need_low_supply",
            ]
        )

    frames = [pq.read_table(part, columns=columns).to_pandas() for part in _parquet_parts(snapshot)]
    result = pd.concat(frames, ignore_index=True)
    if result["census_tract_geoid"].isna().any():
        raise SnapshotError("Family-housing need contains a missing Census tract GEOID")
    if result["census_tract_geoid"].duplicated().any():
        raise SnapshotError("Family-housing need contains duplicate Census tracts")
    return result


def _transit_band(
    distances: pd.Series,
    config: CombinedAnalysisConfig,
) -> tuple[pd.Series, pd.Series]:
    numeric = pd.to_numeric(distances, errors="coerce")
    if (numeric.dropna() < 0).any():
        raise SnapshotError("Transit distance cannot be negative")

    band_ids = pd.Series(pd.NA, index=distances.index, dtype="string")
    labels = pd.Series(pd.NA, index=distances.index, dtype="string")
    lower = 0.0
    for band in config.transit_bands:
        if band.maximum_distance_ft is None:
            selected = numeric > lower
        else:
            selected = numeric.ge(lower) & numeric.le(band.maximum_distance_ft)
        selected &= band_ids.isna()
        band_ids.loc[selected] = band.id
        labels.loc[selected] = band.label
        if band.maximum_distance_ft is not None:
            lower = band.maximum_distance_ft
    return band_ids, labels


def _required_capacity_columns() -> set[str]:
    return {
        f"{prefix}_{category}_capacity"
        for prefix in CAPACITY_SCENARIOS.values()
        for category in BEDROOM_CATEGORIES
    }


def enrich_opportunity_with_need(
    parcels: gpd.GeoDataFrame,
    need: pd.DataFrame,
    config: CombinedAnalysisConfig,
) -> gpd.GeoDataFrame:
    required = {
        "objectid",
        "census_tract_geoid",
        "transit_distance_ft",
        "is_city_owned",
        "is_underbuilt",
        "is_vacant",
        "requires_legal_or_site_review",
        "geometry",
        *_required_capacity_columns(),
    }
    missing = required - set(parcels.columns)
    if missing:
        raise SnapshotError(f"Parcel opportunity is missing columns: {sorted(missing)}")
    if parcels.crs is None:
        raise SnapshotError("Parcel opportunity does not declare a CRS")
    if parcels["objectid"].duplicated().any():
        raise SnapshotError("Parcel opportunity part contains duplicate object IDs")
    if need["census_tract_geoid"].duplicated().any():
        raise SnapshotError("Family-housing need contains duplicate Census tracts")

    result = parcels.merge(
        need,
        on="census_tract_geoid",
        how="left",
        validate="many_to_one",
        indicator="_need_join",
    )
    result["has_family_housing_need"] = result.pop("_need_join").eq("both")
    band_ids, band_labels = _transit_band(result["transit_distance_ft"], config)
    result["transit_band_id"] = band_ids
    result["transit_band_label"] = band_labels
    result["within_half_mile_transit"] = (
        pd.to_numeric(result["transit_distance_ft"], errors="coerce")
        .le(config.half_mile_distance_ft)
        .fillna(False)
    )

    for column in BASE_FLAG_COLUMNS[1:]:
        result[column] = result[column].astype("boolean").fillna(False)
    for profile in ESTIMATE_PROFILES:
        source = f"{profile}_need_high_need_low_supply"
        target = f"in_{profile}_high_need_low_supply_tract"
        result[target] = result[source].astype("boolean").fillna(False)
        result[f"{profile}_high_need_within_half_mile_transit"] = (
            result[target] & result["within_half_mile_transit"]
        )
        result[f"{profile}_high_need_city_owned"] = result[target] & result["is_city_owned"]

    modeled_columns = []
    for category in BEDROOM_CATEGORIES:
        baseline = f"current_two_stair_{category}_capacity"
        single_stair = f"current_single_stair_{category}_capacity"
        upzoned = f"upzoned_single_stair_{category}_capacity"
        result[f"single_stair_{category}_capacity_change"] = result[single_stair] - result[baseline]
        result[f"upzoning_{category}_capacity_change"] = result[upzoned] - result[single_stair]
        result[f"single_stair_increases_{category}_capacity"] = result[
            f"single_stair_{category}_capacity_change"
        ].gt(0)
        result[f"upzoning_increases_{category}_capacity"] = result[
            f"upzoning_{category}_capacity_change"
        ].gt(0)
        modeled_columns.extend([baseline, single_stair, upzoned])
    result["has_any_modeled_capacity"] = result[modeled_columns].notna().any(axis=1)
    return gpd.GeoDataFrame(result, geometry="geometry", crs=parcels.crs)


def _summary_flags() -> tuple[str, ...]:
    profile_flags = tuple(
        flag
        for profile in ESTIMATE_PROFILES
        for flag in (
            f"in_{profile}_high_need_low_supply_tract",
            f"{profile}_high_need_within_half_mile_transit",
            f"{profile}_high_need_city_owned",
        )
    )
    return (*BASE_FLAG_COLUMNS, *profile_flags)


def aggregate_opportunity(
    parcels: pd.DataFrame,
    *,
    aggregation_type: str,
    grouping_columns: tuple[str, ...],
    policy_id: str,
    estimate_id: str,
) -> pd.DataFrame:
    required = {"objectid", *grouping_columns, *_required_capacity_columns(), *_summary_flags()}
    missing = required - set(parcels.columns)
    if missing:
        raise SnapshotError(f"Combined parcel data is missing columns: {sorted(missing)}")

    summaries: list[pd.DataFrame] = []
    for scenario_id, prefix in CAPACITY_SCENARIOS.items():
        for category in BEDROOM_CATEGORIES:
            capacity_column = f"{prefix}_{category}_capacity"
            baseline_column = f"current_two_stair_{category}_capacity"
            working = parcels[["objectid", *grouping_columns, *_summary_flags()]].copy()
            working["modeled_capacity_units"] = pd.to_numeric(
                parcels[capacity_column], errors="coerce"
            )
            baseline = pd.to_numeric(parcels[baseline_column], errors="coerce")
            working["incremental_capacity_vs_current_two_stair_units"] = (
                working["modeled_capacity_units"] - baseline
            )
            working["modeled_parcel"] = working["modeled_capacity_units"].notna().astype("int64")
            working["capacity_gain_parcel"] = (
                working["incremental_capacity_vs_current_two_stair_units"]
                .gt(0)
                .fillna(False)
                .astype("int64")
            )
            for flag in _summary_flags():
                selected = working[flag].astype("boolean").fillna(False)
                working[f"{flag}_parcel_count"] = selected.astype("int64")
                working[f"{flag}_modeled_capacity_units"] = working["modeled_capacity_units"].where(
                    selected
                )
                working[f"{flag}_incremental_capacity_units"] = working[
                    "incremental_capacity_vs_current_two_stair_units"
                ].where(selected)

            numeric_columns = [
                "modeled_capacity_units",
                "incremental_capacity_vs_current_two_stair_units",
                "modeled_parcel",
                "capacity_gain_parcel",
                *(
                    f"{flag}_{suffix}"
                    for flag in _summary_flags()
                    for suffix in (
                        "parcel_count",
                        "modeled_capacity_units",
                        "incremental_capacity_units",
                    )
                ),
            ]
            grouped = working.groupby(list(grouping_columns), dropna=False, observed=True)
            summary = grouped[numeric_columns].sum(min_count=1).reset_index()
            summary = summary.rename(
                columns={
                    "modeled_parcel": "modeled_parcel_count",
                    "capacity_gain_parcel": "capacity_gain_parcel_count",
                }
            )
            summary.insert(len(grouping_columns), "aggregation_type", aggregation_type)
            summary.insert(len(grouping_columns) + 1, "policy_scenario_id", policy_id)
            summary.insert(len(grouping_columns) + 2, "estimate_profile_id", estimate_id)
            summary.insert(len(grouping_columns) + 3, "capacity_scenario_id", scenario_id)
            summary.insert(len(grouping_columns) + 4, "bedroom_category", category)
            parcel_counts = grouped.size().reset_index(name="parcel_count")
            summary = summary.merge(
                parcel_counts,
                on=list(grouping_columns),
                how="left",
                validate="one_to_one",
            )
            summaries.append(summary)
    return pd.concat(summaries, ignore_index=True)


def _combine_partial_summaries(
    partials: list[pd.DataFrame],
    grouping_columns: tuple[str, ...],
) -> pd.DataFrame:
    if not partials:
        raise SnapshotError("No partial aggregation results were produced")
    keys = [
        *grouping_columns,
        "aggregation_type",
        "policy_scenario_id",
        "estimate_profile_id",
        "capacity_scenario_id",
        "bedroom_category",
    ]
    combined = pd.concat(partials, ignore_index=True)
    numeric_columns = [column for column in combined.columns if column not in keys]
    return (
        combined.groupby(keys, dropna=False, observed=True)[numeric_columns]
        .sum(min_count=1)
        .reset_index()
    )


def _write_summary(
    frame: pd.DataFrame,
    *,
    final_root: Path,
    dataset: str,
    source_snapshot: Path,
    snapshot_date: date | None,
    metadata: dict[str, Any],
) -> Path:
    with ParquetSnapshotWriter(
        raw_root=final_root,
        dataset=dataset,
        source_url=str(source_snapshot),
        output_crs=None,
        snapshot_date=snapshot_date,
    ) as writer:
        writer.write_arrow_batch(1, pa.Table.from_pandas(frame, preserve_index=False))
        return writer.commit(
            expected_records=len(frame),
            expected_parts=1,
            metadata=metadata,
        )


def build_combined_opportunity_need(
    *,
    final_root: Path = Path("data/final"),
    snapshot_date: date | None = None,
    policy_id: str = "chicago_proposed",
    estimate_id: str = "median",
    config_path: Path | None = None,
    progress: ProgressCallback | None = None,
) -> CombinedAnalysisSnapshots:
    scenario_catalog = load_scenario_catalog()
    scenario_catalog.selection(policy_id=policy_id, estimate_id=estimate_id)
    config = load_combined_analysis_config(config_path)
    opportunity_dataset = f"parcel_opportunity_{policy_id}_{estimate_id}"
    opportunity_snapshot = latest_snapshot(final_root, opportunity_dataset)
    need_snapshot = latest_snapshot(final_root, "family_housing_need")
    need = _read_need_snapshot(need_snapshot)
    opportunity_parts = _parquet_parts(opportunity_snapshot)
    combined_dataset = f"parcel_opportunity_with_need_{policy_id}_{estimate_id}"
    partial_summaries: dict[str, list[pd.DataFrame]] = {
        aggregation: [] for aggregation in AGGREGATIONS
    }
    need_join_count = 0
    seen_objectids: set[Any] = set()

    with GeoParquetSnapshotWriter(
        raw_root=final_root,
        dataset=combined_dataset,
        source_url=str(opportunity_snapshot),
        output_crs=OUTPUT_CRS,
        snapshot_date=snapshot_date,
    ) as writer:
        for part_number, part in enumerate(opportunity_parts, start=1):
            parcels = gpd.read_parquet(part)
            required_scenario_columns = {"policy_scenario_id", "estimate_profile_id"}
            missing_scenario_columns = required_scenario_columns - set(parcels.columns)
            if missing_scenario_columns:
                raise SnapshotError(
                    "Parcel opportunity is missing scenario columns: "
                    f"{sorted(missing_scenario_columns)}"
                )
            source_policy_ids = set(parcels["policy_scenario_id"].dropna().astype(str))
            source_estimate_ids = set(parcels["estimate_profile_id"].dropna().astype(str))
            if source_policy_ids != {policy_id} or source_estimate_ids != {estimate_id}:
                raise SnapshotError("Parcel opportunity scenario does not match the requested IDs")
            objectids = set(parcels["objectid"].dropna())
            if len(objectids) != len(parcels) or seen_objectids.intersection(objectids):
                raise SnapshotError("Parcel opportunity contains missing or duplicate object IDs")
            seen_objectids.update(objectids)
            output = enrich_opportunity_with_need(parcels, need, config)
            writer.write_geodataframe_batch(part_number, output)
            need_join_count += int(output["has_family_housing_need"].sum())
            for aggregation_type, grouping_columns in AGGREGATIONS.items():
                partial_summaries[aggregation_type].append(
                    aggregate_opportunity(
                        output,
                        aggregation_type=aggregation_type,
                        grouping_columns=grouping_columns,
                        policy_id=policy_id,
                        estimate_id=estimate_id,
                    )
                )
            if progress is not None:
                progress(part_number, len(opportunity_parts), writer.record_count)

        parcel_path = writer.commit(
            expected_records=writer.record_count,
            expected_parts=len(opportunity_parts),
            metadata={
                "grain": ["objectid"],
                "key": "objectid",
                "policy_scenario_id": policy_id,
                "estimate_profile_id": estimate_id,
                "building_scenario_config_version": scenario_catalog.config_version,
                "config_version": config.config_version,
                "source_snapshots": {
                    "parcel_opportunity": str(opportunity_snapshot),
                    "family_housing_need": str(need_snapshot),
                },
                "family_housing_need_join_count": need_join_count,
                "family_housing_need_join_rate": (
                    need_join_count / writer.record_count if writer.record_count else 0
                ),
                "neutral_research_design": True,
                "ownership": {"derived_dataset": "single-stair project"},
                "limitations": list(config.notes),
            },
        )

    summary_paths = []
    for aggregation_type, grouping_columns in AGGREGATIONS.items():
        summary = _combine_partial_summaries(partial_summaries[aggregation_type], grouping_columns)
        dataset = f"opportunity_need_by_{aggregation_type}_{policy_id}_{estimate_id}"
        summary_paths.append(
            _write_summary(
                summary,
                final_root=final_root,
                dataset=dataset,
                source_snapshot=parcel_path,
                snapshot_date=snapshot_date,
                metadata={
                    "grain": [
                        *grouping_columns,
                        "capacity_scenario_id",
                        "bedroom_category",
                    ],
                    "grouping_columns": list(grouping_columns),
                    "policy_scenario_id": policy_id,
                    "estimate_profile_id": estimate_id,
                    "config_version": config.config_version,
                    "capacity_scenarios": list(config.scenario_order),
                    "bedroom_categories": list(BEDROOM_CATEGORIES),
                    "source_snapshot": str(parcel_path),
                    "ownership": {"derived_dataset": "single-stair project"},
                    "limitations": list(config.notes),
                },
            )
        )
    return CombinedAnalysisSnapshots(parcels=parcel_path, summaries=tuple(summary_paths))
