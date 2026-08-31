"""Map export helpers for BUILD additions and the complete screening audit."""

import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

from single_stair.illinois_build import BUILD_PROPERTIES, load_build_policy


def build_map_scenario() -> dict[str, Any]:
    policy = load_build_policy()
    return {
        "id": "illinois_build",
        "label": "With IL BUILD (proposed)",
        "description": (
            "Reference zoning plus screened RS parcel additions under proposed BUILD. "
            "Additional housing permissions are not proven single-stair benefits; "
            "unresolved sites are excluded and recorded in the screening audit."
        ),
        "reviewed_on": policy["researched_on"],
        "sources": [source["url"] for source in policy["sources"]],
        "tier_labels": {"build_added": "BUILD-added parcel screen"},
    }


def potential_build_review(coverage: pd.DataFrame) -> pd.Series:
    """Unresolved potential additions, excluding baseline and unassessed districts."""
    return (
        ~coverage["current_single_stair"]
        & coverage["build_category"].eq("review")
        & coverage["build_residential_eligibility"].isin(["by_right", "conditional", "special_use"])
        & coverage["build_minimum_units"].gt(1)
        & coverage["build_additional_unit_allowance"].gt(0)
    ).fillna(False)


def build_screening_summary(coverage: pd.DataFrame) -> dict[str, Any]:
    return {
        "total_parcel_records": len(coverage),
        "baseline_parcel_records": int(coverage.current_single_stair.sum()),
        "screened_additional_parcel_records": int(
            coverage.build_category.eq("screened_expansion").sum()
        ),
        "potential_additions_requiring_review": int(potential_build_review(coverage).sum()),
        "unassessed_district_parcel_records": int(
            (
                coverage.build_residential_eligibility.eq("unknown")
                & ~coverage.current_single_stair
            ).sum()
        ),
        "categories": {
            str(key): int(value) for key, value in coverage.build_category.value_counts().items()
        },
        "audit_file": "build_screening.parquet",
        "interpretation": (
            "Counts are tax-parcel records, not unique zoning lots, dwelling units, "
            "net housing additions, or guaranteed building-code eligibility."
        ),
    }


def write_build_audit(coverage: pd.DataFrame, output: Path) -> None:
    """Preserve every decision, including excluded/unknown rows; key is objectid."""
    columns = [
        "objectid",
        "pin",
        "zone_class",
        "ward",
        "community_area_number",
        "analysis_lot_area_sqft",
        "current_zoning_unit_limit",
        "current_single_stair",
        *BUILD_PROPERTIES,
    ]
    coverage[columns].to_parquet(output, index=False)


def build_addition_geojson(source: Path, coverage: pd.DataFrame) -> dict[str, Any]:
    """Union screened parcel footprints by ward/district, not entire zoning districts.

    Shared parcel edges otherwise obscure or disappear at overview zoom. Exact unions
    remove those interior edges without buffering or adding unscreened land. Individual
    parcel decisions remain available in parcel detail and the complete audit.
    """
    additions = coverage.loc[coverage.build_category.eq("screened_expansion")].copy()
    if additions.empty:
        return {"type": "FeatureCollection", "features": []}
    parts = sorted(source.glob("part-*.parquet"))
    if not parts:
        raise FileNotFoundError(f"Parcel snapshot has no Parquet parts: {source}")
    selected = []
    for part in parts:
        geometry = gpd.read_parquet(part, columns=["objectid", "geometry"])
        selected.append(geometry.loc[geometry.objectid.isin(additions.objectid)])
    geometry = gpd.GeoDataFrame(pd.concat(selected, ignore_index=True), crs=selected[0].crs)
    if geometry.objectid.duplicated().any() or set(geometry.objectid) != set(additions.objectid):
        raise ValueError("BUILD polygon export requires one geometry per screened parcel objectid")
    geometry = geometry.merge(
        additions[["objectid", "zoning", "ward"]],
        on="objectid",
        validate="one_to_one",
    )
    if geometry.geometry.isna().any() or geometry.geometry.is_empty.any():
        raise ValueError("BUILD addition has missing parcel geometry; rebuild source data")
    if not geometry.geometry.is_valid.all():
        raise ValueError("BUILD addition has invalid parcel geometry; repair source data first")
    geometry["ward"] = geometry.ward.fillna("Unassigned")
    geometry = geometry.dissolve(
        by=["zoning", "ward"], aggfunc={"objectid": "count"}, as_index=False
    ).rename(columns={"objectid": "parcel_count"})
    geometry = geometry.to_crs("EPSG:4326")
    geometry["current_single_stair"] = False
    geometry["illinois_build"] = True
    geometry["coverage_kind"] = "build_added_footprint"
    geometry["tier"] = "build_added"
    geometry["objectid"] = "build:" + geometry.zoning + ":" + geometry.ward.astype(str)
    return json.loads(geometry.set_index("objectid").to_json())
