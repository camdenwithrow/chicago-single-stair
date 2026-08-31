"""Zoning coverage screens, separate from the analytical unit-capacity scenarios."""

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

import duckdb
import geopandas as gpd
import pandas as pd


def current_zoning_config() -> dict[str, Any]:
    return json.loads(
        files("single_stair").joinpath("config/zoning_coverage.v1.json").read_text("utf-8")
    )


def current_zoning_tier(zone_class: Any) -> str | None:
    """Match the publisher's exact classes, not collapsed B/C capacity rule classes."""
    if not isinstance(zone_class, str):
        return None
    zone = zone_class.strip().upper()
    for tier, zones in current_zoning_config()["tiers"].items():
        if zone in zones:
            return tier
    return None


def read_coverage_parcels(source: Path) -> pd.DataFrame:
    """One row per source parcel objectid, including occupied/unmodeled parcels."""
    with duckdb.connect() as connection:
        frame = connection.execute(
            """
            SELECT objectid, pin, pinu, parceltype, centroid_lon, centroid_lat,
                   zone_class, zone_class AS zoning,
                   zoning_objectid, ward, community_area_number,
                   community_area_name AS community, analysis_lot_area_sqft,
                   current_zoning_unit_limit, lot_area_source, has_land_area_mismatch,
                   requires_legal_or_site_review, review_reasons,
                   parcel_geometry_area_sqft, transit_distance_ft,
                   nearest_transit_agency AS transit_agency,
                   is_city_owned AS city_owned, is_vacant AS vacant,
                   is_underbuilt AS underbuilt,
                   median_need_high_need_low_supply AS high_need_low_supply
            FROM read_parquet(?, union_by_name=true)
            """,
            [str(source / "part-*.parquet")],
        ).fetchdf()
    if frame["objectid"].isna().any() or frame["objectid"].duplicated().any():
        raise ValueError("Coverage requires unique, non-null parcel objectids; rebuild final data.")
    frame["zoning"] = frame["zoning"].astype("string").str.strip().str.upper()
    tier_by_zone = {
        zone: tier for tier, zones in current_zoning_config()["tiers"].items() for zone in zones
    }
    frame["tier"] = frame["zoning"].map(tier_by_zone)
    frame["current_single_stair"] = frame["tier"].notna()
    return frame


def coverage_parcel_geojson(parcels: pd.DataFrame, scenario_ids: list[str]) -> dict[str, Any]:
    """Export union of scenario coverage; preserve caller-added policy detail fields."""
    selected = parcels[parcels[scenario_ids].fillna(False).any(axis=1)].copy()
    selected = selected.dropna(subset=["centroid_lon", "centroid_lat"])
    points = gpd.GeoDataFrame(
        selected,
        geometry=gpd.points_from_xy(selected.centroid_lon, selected.centroid_lat),
        crs="EPSG:4326",
    ).set_index("objectid")
    points = points.drop(columns=["centroid_lon", "centroid_lat"])
    return json.loads(points.to_json())


def read_boundaries(source: Path) -> gpd.GeoDataFrame:
    parts = sorted(source.glob("part-*.parquet"))
    if not parts:
        raise FileNotFoundError(f"Boundary snapshot has no Parquet parts: {source}")
    frames = [gpd.read_parquet(part) for part in parts]
    return gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True), geometry="geometry", crs=frames[0].crs
    ).to_crs("EPSG:4326")


def coverage_area_geojson(
    boundaries: gpd.GeoDataFrame,
    parcels: pd.DataFrame,
    scenario_ids: list[str],
    *,
    boundary_key: str,
    parcel_key: str,
    boundary_name: str | None = None,
) -> dict[str, Any]:
    """Count selected source parcel records once by their existing centroid assignment."""
    columns = [boundary_key, "geometry"] + ([boundary_name] if boundary_name else [])
    areas = boundaries[columns].rename(columns={boundary_key: parcel_key}).copy()
    if boundary_name:
        areas = areas.rename(columns={boundary_name: "community_area_name"})
    areas[parcel_key] = areas[parcel_key].astype("string")
    for scenario in scenario_ids:
        selected = parcels.loc[parcels[scenario].fillna(False)].copy()
        selected[parcel_key] = selected[parcel_key].astype("string")
        counts = selected.groupby(parcel_key).size()
        areas[f"{scenario}_parcel_count"] = areas[parcel_key].map(counts).fillna(0).astype(int)
    return json.loads(areas.to_json(drop_id=True))


def current_zoning_geojson(source: Path) -> dict[str, Any]:
    """Use city-owned source geometry; reproduce selection only, not publisher assets."""
    zones = read_boundaries(source)
    zones["zoning"] = zones["zone_class"].astype("string").str.strip().str.upper()
    tiers = {
        zone: tier for tier, classes in current_zoning_config()["tiers"].items() for zone in classes
    }
    zones["tier"] = zones["zoning"].map(tiers)
    zones = zones[zones["tier"].notna()].copy()
    zones["current_single_stair"] = True
    zones["coverage_kind"] = "baseline_zoning"
    return json.loads(
        zones[["objectid", "zoning", "tier", "current_single_stair", "coverage_kind", "geometry"]]
        .set_index("objectid")
        .to_json()
    )
