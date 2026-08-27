import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Any

import duckdb
import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq

from single_stair.ingest.snapshot import GeoParquetSnapshotWriter
from single_stair.scenarios import BEDROOM_CATEGORIES, load_scenario_catalog
from single_stair.transform.clean_and_join import OUTPUT_CRS, WORKING_CRS, latest_snapshot

ProgressCallback = Callable[[int, int, int], None]


@dataclass(frozen=True, slots=True)
class ZoningRule:
    maximum_far: float
    minimum_lot_area_per_unit_sqft: float
    maximum_dwelling_units: int | None
    residential_permission: str
    upzoned_zone_class: str


@dataclass(frozen=True, slots=True)
class OpportunityConfig:
    config_version: str
    maximum_used_far_share: float
    minimum_unused_far: float
    maximum_land_area_relative_difference: float
    zoning_aliases: dict[str, str]
    business_commercial_residential_permissions: dict[str, str]
    zoning_rules: dict[str, ZoningRule]
    sources: dict[str, Any]
    notes: tuple[str, ...]


def load_opportunity_config(config_path: Path | None = None) -> OpportunityConfig:
    if config_path is None:
        config_file = files("single_stair").joinpath("config/parcel_opportunity.v1.json")
        payload = json.loads(config_file.read_text(encoding="utf-8"))
    else:
        payload = json.loads(config_path.read_text(encoding="utf-8"))

    thresholds = payload["underbuilt_thresholds"]
    rules = {
        zone_class: ZoningRule(**values) for zone_class, values in payload["zoning_rules"].items()
    }
    config = OpportunityConfig(
        config_version=payload["config_version"],
        maximum_used_far_share=float(thresholds["maximum_used_far_share"]),
        minimum_unused_far=float(thresholds["minimum_unused_far"]),
        maximum_land_area_relative_difference=float(
            thresholds["maximum_land_area_relative_difference"]
        ),
        zoning_aliases=payload["zoning_aliases"],
        business_commercial_residential_permissions=payload[
            "business_commercial_residential_permissions"
        ],
        zoning_rules=rules,
        sources=payload["sources"],
        notes=tuple(payload["notes"]),
    )
    if not 0 < config.maximum_used_far_share < 1:
        raise ValueError("Underbuilt FAR share must be between zero and one")
    if config.minimum_unused_far <= 0:
        raise ValueError("Minimum unused FAR must be positive")
    if not 0 < config.maximum_land_area_relative_difference < 1:
        raise ValueError("Land-area difference threshold must be between zero and one")
    if set(config.business_commercial_residential_permissions) != {
        "B1",
        "B2",
        "B3",
        "C1",
        "C2",
    }:
        raise ValueError("Business and commercial permissions are incomplete")
    for zone_class, rule in rules.items():
        if rule.maximum_far <= 0 or rule.minimum_lot_area_per_unit_sqft <= 0:
            raise ValueError(f"Zoning rule {zone_class} has invalid density values")
        if rule.upzoned_zone_class not in rules:
            raise ValueError(f"Zoning rule {zone_class} has an unknown upzoned class")
    return config


def normalize_zoning_class(value: Any, config: OpportunityConfig) -> str | None:
    if value is None or pd.isna(value):
        return None
    zone_class = config.zoning_aliases.get(str(value).strip().upper(), str(value).strip().upper())
    if zone_class in config.zoning_rules:
        return zone_class

    residential = re.fullmatch(r"(RS|RT|RM)-?(\d(?:\.5)?)", zone_class)
    if residential:
        candidate = f"{residential.group(1)}-{residential.group(2)}"
        return candidate if candidate in config.zoning_rules else None

    business = re.fullmatch(r"([BC])([123])-(1(?:\.5)?|2|3|5)", zone_class)
    if business:
        if business.group(1) == "C" and business.group(2) == "3":
            return None
        candidate = f"{business.group(1)}-{business.group(3)}"
        return candidate if candidate in config.zoning_rules else None
    return None


def _district_family(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    match = re.fullmatch(r"([BC][123])-(?:1(?:\.5)?|2|3|5)", str(value).strip().upper())
    return match.group(1) if match else None


def _residential_permission(
    source_zone_class: Any,
    canonical_zone_class: str | None,
    config: OpportunityConfig,
) -> str | None:
    if canonical_zone_class is None or pd.isna(canonical_zone_class):
        return None
    district_family = _district_family(source_zone_class)
    if district_family is not None:
        return config.business_commercial_residential_permissions[district_family]
    return config.zoning_rules[canonical_zone_class].residential_permission


def _upzoned_zone_class(
    source_zone_class: Any,
    canonical_zone_class: str | None,
    config: OpportunityConfig,
) -> str | None:
    if canonical_zone_class is None or pd.isna(canonical_zone_class):
        return None
    upzoned_rule_class = config.zoning_rules[canonical_zone_class].upzoned_zone_class
    district_family = _district_family(source_zone_class)
    if district_family is None:
        return upzoned_rule_class
    target_dash = upzoned_rule_class.split("-", maxsplit=1)[1]
    return f"{district_family}-{target_dash}"


def _parquet_parts(snapshot: Path) -> list[Path]:
    parts = sorted(snapshot.glob("part-*.parquet"))
    if not parts:
        raise FileNotFoundError(f"Snapshot contains no Parquet parts: {snapshot}")
    return parts


def _building_summary(snapshot: Path) -> pd.DataFrame:
    paths = [str(path) for path in _parquet_parts(snapshot)]
    query = """
        SELECT
            pin,
            count(*) AS assessor_building_records,
            sum(try_cast(char_bldg_sf AS DOUBLE)) AS existing_building_sqft_unprorated,
            sum(
                try_cast(char_bldg_sf AS DOUBLE) *
                CASE
                    WHEN try_cast(tieback_proration_rate AS DOUBLE) > 0
                        AND try_cast(tieback_proration_rate AS DOUBLE) < 1
                    THEN try_cast(tieback_proration_rate AS DOUBLE)
                    ELSE 1
                END
            ) AS existing_building_sqft,
            bool_or(
                try_cast(tieback_proration_rate AS DOUBLE) > 0
                AND try_cast(tieback_proration_rate AS DOUBLE) < 1
            ) AS has_prorated_building_area,
            max(try_cast(char_land_sf AS DOUBLE)) AS assessor_land_sqft,
            count(DISTINCT try_cast(char_land_sf AS DOUBLE)) AS assessor_land_area_values,
            sum(
                CASE
                    WHEN char_use = 'Single-Family' THEN 1
                    WHEN char_apts = 'Two' THEN 2
                    WHEN char_apts = 'Three' THEN 3
                    WHEN char_apts = 'Four' THEN 4
                    WHEN char_apts = 'Five' THEN 5
                    WHEN char_apts = 'Six' THEN 6
                    ELSE NULL
                END
            ) AS assessor_existing_units
        FROM read_parquet(?, union_by_name=true, hive_partitioning=false)
        GROUP BY pin
    """
    connection = duckdb.connect()
    try:
        return connection.execute(query, [paths]).fetchdf().set_index("pin")
    finally:
        connection.close()


def _city_owned_summary(snapshot: Path) -> pd.DataFrame:
    tables = [pq.read_table(part).to_pandas() for part in _parquet_parts(snapshot)]
    frame = pd.concat(tables, ignore_index=True)
    frame["pin"] = frame["pin"].map(
        lambda value: "".join(character for character in str(value) if character.isdigit())
    )
    frame = frame.loc[frame["pin"].str.len() == 14].copy()
    frame["is_city_owned"] = frame["property_status"].eq("Owned by City")
    return (
        frame.sort_values(["pin", "id"])
        .drop_duplicates("pin", keep="last")
        .set_index("pin")[["is_city_owned", "property_status", "managing_organization"]]
        .rename(
            columns={
                "property_status": "city_land_status",
                "managing_organization": "city_managing_organization",
            }
        )
    )


def _rule_values(
    zone_classes: pd.Series,
    config: OpportunityConfig,
    attribute: str,
) -> pd.Series:
    values = {
        zone_class: getattr(rule, attribute) for zone_class, rule in config.zoning_rules.items()
    }
    return zone_classes.map(values)


def _zoning_unit_limit(
    parcel_area_sqft: pd.Series,
    minimum_lot_area: pd.Series,
    maximum_dwelling_units: pd.Series,
) -> pd.Series:
    raw_limit = parcel_area_sqft / minimum_lot_area
    limit = pd.Series(pd.NA, index=parcel_area_sqft.index, dtype="Int64")
    available = raw_limit.notna()
    limit.loc[available] = raw_limit.loc[available].map(math.floor).astype("Int64")
    fixed = pd.to_numeric(maximum_dwelling_units, errors="coerce").astype("Int64")
    has_fixed = fixed.notna() & limit.notna()
    limit.loc[has_fixed] = pd.concat([limit.loc[has_fixed], fixed.loc[has_fixed]], axis=1).min(
        axis=1
    )
    return limit


def _capacity(
    gross_floor_area_sqft: pd.Series,
    efficiency: float,
    unit_size_sqft: int,
    zoning_unit_limit: pd.Series,
    building_unit_limit: int,
) -> pd.Series:
    raw_area_limit = gross_floor_area_sqft * efficiency / unit_size_sqft
    area_limit = pd.Series(pd.NA, index=gross_floor_area_sqft.index, dtype="Int64")
    available = raw_area_limit.notna()
    area_limit.loc[available] = raw_area_limit.loc[available].map(math.floor).astype("Int64")
    candidates = pd.concat(
        [
            area_limit,
            zoning_unit_limit,
            pd.Series(building_unit_limit, index=gross_floor_area_sqft.index, dtype="Int64"),
        ],
        axis=1,
    )
    complete = candidates.notna().all(axis=1)
    result = pd.Series(pd.NA, index=gross_floor_area_sqft.index, dtype="Int64")
    result.loc[complete] = candidates.loc[complete].min(axis=1).astype("Int64")
    return result


def _review_reasons(frame: pd.DataFrame) -> pd.Series:
    output: list[str] = []
    for row in frame.itertuples(index=False):
        reasons: list[str] = []
        if row.canonical_zone_class is None or pd.isna(row.canonical_zone_class):
            reasons.append("unsupported_or_nonresidential_zoning")
        elif row.residential_permission != "by_right":
            reasons.append("residential_use_conditions")
        if pd.isna(row.pin):
            reasons.append("missing_pin")
        if pd.isna(row.assessor_building_records):
            reasons.append("building_characteristics_unavailable")
        if pd.notna(row.pinu) and float(row.pinu) != 0:
            reasons.append("unitized_parcel")
        if pd.notna(row.parceltype) and float(row.parceltype) != 1:
            reasons.append("nonstandard_parcel_type")
        if pd.notna(row.assessor_land_area_values) and row.assessor_land_area_values > 1:
            reasons.append("inconsistent_assessor_land_area")
        if row.has_land_area_mismatch:
            reasons.append("parcel_assessor_land_area_mismatch")
        output.append(";".join(reasons))
    return pd.Series(output, index=frame.index, dtype="string")


def enrich_parcel_opportunity(
    parcels: gpd.GeoDataFrame,
    *,
    building_summary: pd.DataFrame,
    city_owned_summary: pd.DataFrame,
    opportunity_config: OpportunityConfig,
    policy_id: str,
    estimate_id: str,
) -> gpd.GeoDataFrame:
    if parcels.crs is None:
        raise ValueError("Parcel context does not declare a CRS")
    scenarios = load_scenario_catalog()
    if policy_id not in scenarios.policies:
        raise ValueError(f"Unknown policy scenario: {policy_id}")
    if estimate_id not in scenarios.estimate_profiles:
        raise ValueError(f"Unknown estimate profile: {estimate_id}")
    policy = scenarios.policies[policy_id]
    estimate = scenarios.estimate_profiles[estimate_id]

    frame = parcels.copy()
    frame["parcel_geometry_area_sqft"] = frame.to_crs(WORKING_CRS).geometry.area
    frame = frame.join(building_summary, on="pin")
    frame = frame.join(city_owned_summary, on="pin")
    frame["is_city_owned"] = frame["is_city_owned"].fillna(False).astype(bool)
    valid_assessor_area = frame["assessor_land_sqft"].gt(0)
    frame["analysis_lot_area_sqft"] = frame["parcel_geometry_area_sqft"]
    frame.loc[valid_assessor_area, "analysis_lot_area_sqft"] = frame.loc[
        valid_assessor_area, "assessor_land_sqft"
    ]
    frame["lot_area_source"] = "parcel_geometry"
    frame.loc[valid_assessor_area, "lot_area_source"] = "assessor_building_characteristics"
    frame["land_area_relative_difference"] = (
        frame["parcel_geometry_area_sqft"] - frame["assessor_land_sqft"]
    ).abs() / frame["assessor_land_sqft"]
    frame["has_land_area_mismatch"] = (
        frame["land_area_relative_difference"]
        .gt(opportunity_config.maximum_land_area_relative_difference)
        .fillna(False)
    )
    frame["canonical_zone_class"] = frame["zone_class"].map(
        lambda value: normalize_zoning_class(value, opportunity_config)
    )
    frame["upzoned_rule_class"] = _rule_values(
        frame["canonical_zone_class"], opportunity_config, "upzoned_zone_class"
    )
    frame["upzoned_zone_class"] = [
        _upzoned_zone_class(source, canonical, opportunity_config)
        for source, canonical in zip(
            frame["zone_class"], frame["canonical_zone_class"], strict=True
        )
    ]
    frame["residential_permission"] = [
        _residential_permission(source, canonical, opportunity_config)
        for source, canonical in zip(
            frame["zone_class"], frame["canonical_zone_class"], strict=True
        )
    ]
    frame["current_maximum_far"] = _rule_values(
        frame["canonical_zone_class"], opportunity_config, "maximum_far"
    )
    frame["upzoned_maximum_far"] = _rule_values(
        frame["upzoned_rule_class"], opportunity_config, "maximum_far"
    )
    current_mla = _rule_values(
        frame["canonical_zone_class"], opportunity_config, "minimum_lot_area_per_unit_sqft"
    )
    upzoned_mla = _rule_values(
        frame["upzoned_rule_class"], opportunity_config, "minimum_lot_area_per_unit_sqft"
    )
    current_fixed_units = _rule_values(
        frame["canonical_zone_class"], opportunity_config, "maximum_dwelling_units"
    )
    upzoned_fixed_units = _rule_values(
        frame["upzoned_rule_class"], opportunity_config, "maximum_dwelling_units"
    )
    frame["current_zoning_unit_limit"] = _zoning_unit_limit(
        frame["analysis_lot_area_sqft"], current_mla, current_fixed_units
    )
    frame["upzoned_zoning_unit_limit"] = _zoning_unit_limit(
        frame["analysis_lot_area_sqft"], upzoned_mla, upzoned_fixed_units
    )
    frame["current_maximum_floor_area_sqft"] = (
        frame["analysis_lot_area_sqft"] * frame["current_maximum_far"]
    )
    frame["upzoned_maximum_floor_area_sqft"] = (
        frame["analysis_lot_area_sqft"] * frame["upzoned_maximum_far"]
    )
    frame["existing_built_far"] = frame["existing_building_sqft"] / frame["analysis_lot_area_sqft"]
    frame["unused_current_far"] = frame["current_maximum_far"] - frame["existing_built_far"]
    frame["used_current_far_share"] = frame["existing_built_far"] / frame["current_maximum_far"]
    frame["is_underbuilt"] = (
        frame["used_current_far_share"].le(opportunity_config.maximum_used_far_share)
        & frame["unused_current_far"].ge(opportunity_config.minimum_unused_far)
    ).fillna(False)
    frame["is_vacant"] = frame["assessor_building_records"].notna() & frame[
        "existing_building_sqft"
    ].le(0)
    frame["vacancy_status"] = "building_characteristics_unavailable"
    frame.loc[frame["assessor_building_records"].notna(), "vacancy_status"] = "improved"
    frame.loc[frame["is_vacant"], "vacancy_status"] = "zero_assessed_building_area"

    building_unit_limit = policy.maximum_stories_above_grade * policy.maximum_units_per_story
    for category in BEDROOM_CATEGORIES:
        unit_size = estimate.unit_sizes_sqft[category]
        frame[f"current_two_stair_{category}_capacity"] = _capacity(
            frame["current_maximum_floor_area_sqft"],
            estimate.two_stair_efficiency,
            unit_size,
            frame["current_zoning_unit_limit"],
            building_unit_limit,
        )
        frame[f"current_single_stair_{category}_capacity"] = _capacity(
            frame["current_maximum_floor_area_sqft"],
            estimate.single_stair_efficiency,
            unit_size,
            frame["current_zoning_unit_limit"],
            building_unit_limit,
        )
        frame[f"upzoned_single_stair_{category}_capacity"] = _capacity(
            frame["upzoned_maximum_floor_area_sqft"],
            estimate.single_stair_efficiency,
            unit_size,
            frame["upzoned_zoning_unit_limit"],
            building_unit_limit,
        )

    frame["policy_scenario_id"] = policy_id
    frame["estimate_profile_id"] = estimate_id
    frame["modeled_maximum_stories"] = policy.maximum_stories_above_grade
    frame["modeled_maximum_units_per_story"] = policy.maximum_units_per_story
    frame["modeled_maximum_building_units"] = building_unit_limit
    frame["upzoning_requires_map_amendment"] = frame["canonical_zone_class"].notna() & frame[
        "upzoned_rule_class"
    ].ne(frame["canonical_zone_class"])
    frame["review_reasons"] = _review_reasons(frame)
    frame["requires_legal_or_site_review"] = frame["review_reasons"].str.len().gt(0)
    return gpd.GeoDataFrame(frame, geometry="geometry", crs=parcels.crs)


def build_parcel_opportunity(
    *,
    raw_root: Path = Path("data/raw"),
    staged_root: Path = Path("data/staged"),
    final_root: Path = Path("data/final"),
    snapshot_date: date | None = None,
    policy_id: str = "chicago_proposed",
    estimate_id: str = "median",
    opportunity_config_path: Path | None = None,
    progress: ProgressCallback | None = None,
) -> Path:
    opportunity_config = load_opportunity_config(opportunity_config_path)
    parcel_snapshot = latest_snapshot(staged_root, "parcel_context")
    building_snapshot = latest_snapshot(staged_root, "building_characteristics_latest")
    city_snapshot = latest_snapshot(raw_root, "chicago_city_owned_land")
    buildings = _building_summary(building_snapshot)
    city_owned = _city_owned_summary(city_snapshot)
    parcel_parts = _parquet_parts(parcel_snapshot)
    dataset = f"parcel_opportunity_{policy_id}_{estimate_id}"

    with GeoParquetSnapshotWriter(
        raw_root=final_root,
        dataset=dataset,
        source_url=str(parcel_snapshot),
        output_crs=OUTPUT_CRS,
        snapshot_date=snapshot_date,
    ) as writer:
        for part_number, part in enumerate(parcel_parts, start=1):
            parcels = gpd.read_parquet(part)
            output = enrich_parcel_opportunity(
                parcels,
                building_summary=buildings,
                city_owned_summary=city_owned,
                opportunity_config=opportunity_config,
                policy_id=policy_id,
                estimate_id=estimate_id,
            )
            writer.write_geodataframe_batch(part_number, output)
            if progress is not None:
                progress(part_number, len(parcel_parts), writer.record_count)

        scenarios = load_scenario_catalog()
        return writer.commit(
            expected_records=writer.record_count,
            expected_parts=len(parcel_parts),
            metadata={
                "grain": ["objectid"],
                "policy_scenario_id": policy_id,
                "estimate_profile_id": estimate_id,
                "building_scenario_config_version": scenarios.config_version,
                "opportunity_config_version": opportunity_config.config_version,
                "source_snapshots": {
                    "parcel_context": str(parcel_snapshot),
                    "building_characteristics_latest": str(building_snapshot),
                    "chicago_city_owned_land": str(city_snapshot),
                },
                "underbuilt_definition": {
                    "maximum_used_far_share": opportunity_config.maximum_used_far_share,
                    "minimum_unused_far": opportunity_config.minimum_unused_far,
                    "maximum_land_area_relative_difference": (
                        opportunity_config.maximum_land_area_relative_difference
                    ),
                },
                "capacity_method": (
                    "minimum of FAR area, zoning density, and selected policy envelope"
                ),
                "existing_far_method": (
                    "Assessor building area prorated by positive fractional tieback rate, "
                    "divided by Assessor land area when available or parcel geometry area"
                ),
                "bedroom_capacity_grain": (
                    "independent capacity by bedroom size, not a unit-mix allocation"
                ),
                "zoning_sources": opportunity_config.sources,
                "limitations": list(opportunity_config.notes),
            },
        )
