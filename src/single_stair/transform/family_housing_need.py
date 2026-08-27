import json
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq

from single_stair.ingest.snapshot import GeoParquetSnapshotWriter, SnapshotError
from single_stair.transform.clean_and_join import OUTPUT_CRS, latest_snapshot

ACS_DATASETS = {
    "B11005": "census_acs5_b11005",
    "B25115": "census_acs5_b25115",
    "B25042": "census_acs5_b25042",
    "B25014": "census_acs5_b25014",
    "B25070": "census_acs5_b25070",
}
ESTIMATE_PROFILES = ("conservative", "median", "progressive")
EXPECTED_SCORE_COMPONENTS = (
    "renter_households_with_children_share",
    "inverse_renter_occupied_3_plus_bedroom_share",
    "renter_overcrowding_rate",
    "renter_cost_burden_rate",
)


@dataclass(frozen=True, slots=True)
class FamilyHousingNeedConfig:
    config_version: str
    headline_bedroom_threshold: int
    reported_bedroom_thresholds: tuple[int, ...]
    high_need_percentile: float
    reliability_rule: str
    score_components: tuple[str, ...]
    sources: dict[str, Any]
    notes: tuple[str, ...]


def load_family_housing_need_config(
    config_path: Path | None = None,
) -> FamilyHousingNeedConfig:
    if config_path is None:
        config_file = files("single_stair").joinpath("config/family_housing_need.v1.json")
        payload = json.loads(config_file.read_text(encoding="utf-8"))
    else:
        payload = json.loads(config_path.read_text(encoding="utf-8"))

    config = FamilyHousingNeedConfig(
        config_version=str(payload["config_version"]),
        headline_bedroom_threshold=int(payload["headline_bedroom_threshold"]),
        reported_bedroom_thresholds=tuple(
            int(value) for value in payload["reported_bedroom_thresholds"]
        ),
        high_need_percentile=float(payload["high_need_percentile"]),
        reliability_rule=str(payload["reliability_rule"]),
        score_components=tuple(payload["score_components"]),
        sources=payload["sources"],
        notes=tuple(payload["notes"]),
    )
    if config.headline_bedroom_threshold != 3:
        raise ValueError("Version 1 family-housing need scoring requires a 3+ bedroom headline")
    if set(config.reported_bedroom_thresholds) - {2, 3, 4}:
        raise ValueError("Supported bedroom thresholds are 2, 3, and 4")
    if not 0.5 < config.high_need_percentile < 1:
        raise ValueError("High-need percentile must be between 0.5 and 1")
    if config.score_components != EXPECTED_SCORE_COMPONENTS:
        raise ValueError("Family-housing need score components do not match the implemented model")
    return config


def _parquet_parts(snapshot: Path) -> list[Path]:
    parts = sorted(snapshot.glob("part-*.parquet"))
    if not parts:
        raise FileNotFoundError(f"Snapshot contains no Parquet parts: {snapshot}")
    return parts


def _read_table(snapshot: Path) -> pd.DataFrame:
    return pd.concat(
        [pq.read_table(part).to_pandas() for part in _parquet_parts(snapshot)],
        ignore_index=True,
    )


def _snapshot_vintage(snapshot: Path) -> int:
    manifest_path = snapshot / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Snapshot is missing its manifest: {snapshot}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    try:
        return int(manifest["metadata"]["vintage"])
    except (KeyError, TypeError, ValueError) as error:
        raise SnapshotError(f"Snapshot does not declare a valid vintage: {snapshot}") from error


def _read_tract_geometry(snapshot: Path) -> gpd.GeoDataFrame:
    parts = [gpd.read_parquet(part) for part in _parquet_parts(snapshot)]
    crs = parts[0].crs
    if crs is None or any(frame.crs != crs for frame in parts):
        raise SnapshotError("Census tract geometry parts do not share a declared CRS")
    return gpd.GeoDataFrame(
        pd.concat(parts, ignore_index=True),
        geometry="geometry",
        crs=crs,
    )


def _with_geoid(frame: pd.DataFrame, table: str) -> pd.DataFrame:
    required = {"state", "county", "tract"}
    if not required.issubset(frame.columns):
        raise SnapshotError(f"ACS {table} snapshot is missing geography keys")
    result = frame.copy()
    result["census_tract_geoid"] = (
        result["state"].astype("string").str.zfill(2)
        + result["county"].astype("string").str.zfill(3)
        + result["tract"].astype("string").str.zfill(6)
    )
    if result["census_tract_geoid"].duplicated().any():
        raise SnapshotError(f"ACS {table} snapshot contains duplicate tracts")
    return result


def _numeric(frame: pd.DataFrame, columns: list[str], table: str) -> pd.DataFrame:
    missing = set(columns) - set(frame.columns)
    if missing:
        raise SnapshotError(f"ACS {table} snapshot is missing columns: {sorted(missing)}")
    result = frame[columns].apply(pd.to_numeric, errors="coerce").astype("Float64")
    return result.mask(result < 0)


def _sum(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    return frame[columns].sum(axis=1, min_count=len(columns))


def _root_sum_square(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    squared = frame[columns].pow(2)
    return squared.sum(axis=1, min_count=len(columns)).pow(0.5)


def _rate(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return (100 * numerator / denominator.where(denominator > 0)).clip(lower=0, upper=100)


def _rate_moe(
    numerator: pd.Series,
    numerator_moe: pd.Series,
    denominator: pd.Series,
    denominator_moe: pd.Series,
) -> pd.Series:
    proportion = numerator / denominator.where(denominator > 0)
    radicand = numerator_moe.pow(2) - proportion.pow(2) * denominator_moe.pow(2)
    fallback = numerator_moe.pow(2) + proportion.pow(2) * denominator_moe.pow(2)
    standard_error = radicand.where(radicand >= 0, fallback).pow(0.5)
    return (100 * standard_error / denominator.where(denominator > 0)).clip(lower=0, upper=100)


def _add_count(
    output: pd.DataFrame,
    source: pd.DataFrame,
    name: str,
    estimate_columns: list[str],
) -> None:
    output[f"{name}_estimate"] = _sum(source, estimate_columns)
    output[f"{name}_moe"] = _root_sum_square(
        source, [column.removesuffix("E") + "M" for column in estimate_columns]
    )


def _add_rate(
    output: pd.DataFrame,
    name: str,
    numerator: str,
    denominator: str,
) -> None:
    output[f"{name}_pct"] = _rate(
        output[f"{numerator}_estimate"], output[f"{denominator}_estimate"]
    )
    output[f"{name}_moe_pct"] = _rate_moe(
        output[f"{numerator}_estimate"],
        output[f"{numerator}_moe"],
        output[f"{denominator}_estimate"],
        output[f"{denominator}_moe"],
    )


def _interval(point: pd.Series, moe: pd.Series, profile: str) -> pd.Series:
    if profile == "conservative":
        return (point - moe).clip(lower=0)
    if profile == "progressive":
        return point + moe
    return point


def _rate_interval(point: pd.Series, moe: pd.Series, profile: str, *, inverse: bool) -> pd.Series:
    direction = -1 if profile == "conservative" else 1
    if inverse:
        direction *= -1
    if profile == "median":
        direction = 0
    return (point + direction * moe).clip(lower=0, upper=100)


def _reference_percentile(
    values: pd.Series,
    reference: pd.Series,
    *,
    inverse: bool = False,
) -> pd.Series:
    ordered = sorted(float(value) for value in reference.dropna())
    if not ordered:
        return pd.Series(pd.NA, index=values.index, dtype="Float64")

    def percentile(value: Any) -> float | None:
        if pd.isna(value):
            return None
        lower = bisect_left(ordered, float(value))
        upper = bisect_right(ordered, float(value))
        if inverse:
            average_rank = ((len(ordered) - upper + 1) + (len(ordered) - lower)) / 2
        else:
            average_rank = ((lower + 1) + upper) / 2
        return average_rank / len(ordered)

    return values.map(percentile).astype("Float64")


def _calculate_rankings(output: pd.DataFrame, config: FamilyHousingNeedConfig) -> None:
    threshold = config.headline_bedroom_threshold
    supply_rate = f"renter_occupied_{threshold}_plus_bedroom_share"
    output["renter_households_with_children_estimate_reliable"] = (
        output["renter_households_with_children_moe"]
        <= output["renter_households_with_children_estimate"]
    ).fillna(False)
    output[f"renter_occupied_{threshold}_plus_bedroom_units_estimate_reliable"] = (
        output[f"renter_occupied_{threshold}_plus_bedroom_units_moe"]
        <= output[f"renter_occupied_{threshold}_plus_bedroom_units_estimate"]
    ).fillna(False)
    output["renter_overcrowded_units_estimate_reliable"] = (
        output["renter_overcrowded_units_moe"] <= output["renter_overcrowded_units_estimate"]
    ).fillna(False)
    output["renter_cost_burdened_households_estimate_reliable"] = (
        output["renter_cost_burdened_households_moe"]
        <= output["renter_cost_burdened_households_estimate"]
    ).fillna(False)
    output["headline_estimates_reliable"] = (
        output["renter_households_with_children_estimate_reliable"]
        & output[f"renter_occupied_{threshold}_plus_bedroom_units_estimate_reliable"]
    )
    output["need_score_components_reliable"] = (
        output["headline_estimates_reliable"]
        & output["renter_overcrowded_units_estimate_reliable"]
        & output["renter_cost_burdened_households_estimate_reliable"]
    )

    reference_children = output["renter_households_with_children_share_pct"]
    reference_supply = output[f"{supply_rate}_pct"]
    reference_overcrowding = output["renter_overcrowding_rate_pct"]
    reference_burden = output["renter_cost_burden_rate_pct"]
    output["need_ranking_eligible"] = (
        pd.concat(
            [
                reference_children,
                reference_supply,
                reference_overcrowding,
                reference_burden,
            ],
            axis=1,
        )
        .notna()
        .all(axis=1)
    )

    for profile in ESTIMATE_PROFILES:
        children = _rate_interval(
            output["renter_households_with_children_share_pct"],
            output["renter_households_with_children_share_moe_pct"],
            profile,
            inverse=False,
        )
        supply = _rate_interval(
            output[f"{supply_rate}_pct"],
            output[f"{supply_rate}_moe_pct"],
            profile,
            inverse=True,
        )
        overcrowding = _rate_interval(
            output["renter_overcrowding_rate_pct"],
            output["renter_overcrowding_rate_moe_pct"],
            profile,
            inverse=False,
        )
        burden = _rate_interval(
            output["renter_cost_burden_rate_pct"],
            output["renter_cost_burden_rate_moe_pct"],
            profile,
            inverse=False,
        )

        child_percentile = _reference_percentile(children, reference_children)
        low_supply_percentile = _reference_percentile(supply, reference_supply, inverse=True)
        overcrowding_percentile = _reference_percentile(overcrowding, reference_overcrowding)
        burden_percentile = _reference_percentile(burden, reference_burden)
        prefix = f"{profile}_need"
        output[f"{prefix}_children_percentile"] = child_percentile
        output[f"{prefix}_low_large_unit_supply_percentile"] = low_supply_percentile
        output[f"{prefix}_overcrowding_percentile"] = overcrowding_percentile
        output[f"{prefix}_cost_burden_percentile"] = burden_percentile
        output[f"{prefix}_score"] = pd.concat(
            [
                child_percentile,
                low_supply_percentile,
                overcrowding_percentile,
                burden_percentile,
            ],
            axis=1,
        ).mean(axis=1, skipna=False)
        output[f"{prefix}_high_need_low_supply"] = (
            (child_percentile >= config.high_need_percentile)
            & (low_supply_percentile >= config.high_need_percentile)
            & output["headline_estimates_reliable"]
        )


def calculate_family_housing_need(
    acs_tables: dict[str, pd.DataFrame],
    tract_geometry: gpd.GeoDataFrame,
    chicago_tract_geoids: set[str],
    config: FamilyHousingNeedConfig,
) -> gpd.GeoDataFrame:
    required_tables = set(ACS_DATASETS)
    if set(acs_tables) != required_tables:
        raise ValueError(f"Expected ACS tables {sorted(required_tables)}")
    if "GEOID" not in tract_geometry or "geometry" not in tract_geometry:
        raise SnapshotError("Census tract geometry is missing GEOID or geometry")
    if tract_geometry.crs is None:
        raise SnapshotError("Census tract geometry does not declare a CRS")

    prepared: dict[str, pd.DataFrame] = {}
    for table, frame in acs_tables.items():
        keyed = _with_geoid(frame, table)
        value_columns = [column for column in keyed if column.startswith(f"{table}_")]
        numeric = _numeric(keyed, value_columns, table)
        numeric.insert(0, "census_tract_geoid", keyed["census_tract_geoid"])
        prepared[table] = numeric.set_index("census_tract_geoid")

    common_geoids = set.intersection(
        chicago_tract_geoids,
        *(set(frame.index.astype(str)) for frame in prepared.values()),
        set(tract_geometry["GEOID"].astype(str)),
    )
    if not common_geoids:
        raise SnapshotError("No Chicago Census tracts join across the ACS inputs")

    index = pd.Index(sorted(common_geoids), name="census_tract_geoid")
    output = pd.DataFrame(index=index)
    b11005 = prepared["B11005"].reindex(index)
    b25115 = prepared["B25115"].reindex(index)
    b25042 = prepared["B25042"].reindex(index)
    b25014 = prepared["B25014"].reindex(index)
    b25070 = prepared["B25070"].reindex(index)

    _add_count(output, b11005, "households", ["B11005_001E"])
    _add_count(output, b11005, "households_with_people_under_18", ["B11005_002E"])
    _add_count(output, b25115, "renter_households", ["B25115_015E"])
    _add_count(
        output,
        b25115,
        "renter_households_with_children",
        ["B25115_018E", "B25115_022E", "B25115_025E"],
    )
    _add_rate(
        output,
        "renter_households_with_children_share",
        "renter_households_with_children",
        "renter_households",
    )

    _add_count(output, b25042, "renter_occupied_units", ["B25042_009E"])
    bedroom_columns = {
        0: "B25042_010E",
        1: "B25042_011E",
        2: "B25042_012E",
        3: "B25042_013E",
        4: "B25042_014E",
        5: "B25042_015E",
    }
    for bedrooms, column in bedroom_columns.items():
        label = "studio" if bedrooms == 0 else f"{bedrooms}_bedroom"
        if bedrooms == 5:
            label = "5_plus_bedroom"
        _add_count(output, b25042, f"renter_occupied_{label}_units", [column])
    for threshold in config.reported_bedroom_thresholds:
        columns = [column for bedrooms, column in bedroom_columns.items() if bedrooms >= threshold]
        supply = f"renter_occupied_{threshold}_plus_bedroom_units"
        _add_count(output, b25042, supply, columns)
        _add_rate(
            output,
            f"renter_occupied_{threshold}_plus_bedroom_share",
            supply,
            "renter_occupied_units",
        )
        balance = output["renter_households_with_children_estimate"] - output[f"{supply}_estimate"]
        balance_moe = (
            output["renter_households_with_children_moe"].pow(2) + output[f"{supply}_moe"].pow(2)
        ).pow(0.5)
        output[f"family_housing_{threshold}_plus_balance_estimate"] = balance
        output[f"family_housing_{threshold}_plus_balance_moe"] = balance_moe
        for profile in ESTIMATE_PROFILES:
            output[f"family_housing_{threshold}_plus_gap_{profile}_estimate"] = _interval(
                balance, balance_moe, profile
            ).clip(lower=0)

    _add_count(output, b25014, "renter_occupancy_measured_units", ["B25014_008E"])
    _add_count(
        output,
        b25014,
        "renter_overcrowded_units",
        ["B25014_011E", "B25014_012E", "B25014_013E"],
    )
    _add_count(
        output,
        b25014,
        "renter_severely_overcrowded_units",
        ["B25014_012E", "B25014_013E"],
    )
    _add_rate(
        output,
        "renter_overcrowding_rate",
        "renter_overcrowded_units",
        "renter_occupancy_measured_units",
    )
    _add_rate(
        output,
        "renter_severe_overcrowding_rate",
        "renter_severely_overcrowded_units",
        "renter_occupancy_measured_units",
    )

    _add_count(
        output,
        b25070,
        "renter_households_with_computed_rent_burden",
        [f"B25070_{number:03d}E" for number in range(2, 11)],
    )
    _add_count(
        output,
        b25070,
        "renter_cost_burdened_households",
        [f"B25070_{number:03d}E" for number in range(7, 11)],
    )
    _add_count(output, b25070, "renter_severely_cost_burdened_households", ["B25070_010E"])
    _add_rate(
        output,
        "renter_cost_burden_rate",
        "renter_cost_burdened_households",
        "renter_households_with_computed_rent_burden",
    )
    _add_rate(
        output,
        "renter_severe_cost_burden_rate",
        "renter_severely_cost_burdened_households",
        "renter_households_with_computed_rent_burden",
    )
    _calculate_rankings(output, config)

    geometry = tract_geometry.copy()
    geometry["census_tract_geoid"] = geometry["GEOID"].astype(str)
    geometry = geometry.set_index("census_tract_geoid").reindex(index)
    if geometry.geometry.isna().any():
        raise SnapshotError("A joined Chicago Census tract is missing geometry")
    result = geometry[["geometry"]].join(output).reset_index()
    return gpd.GeoDataFrame(result, geometry="geometry", crs=tract_geometry.crs).to_crs(OUTPUT_CRS)


def _chicago_tract_geoids(parcel_snapshot: Path) -> set[str]:
    geoids: set[str] = set()
    for part in _parquet_parts(parcel_snapshot):
        table = pq.read_table(part, columns=["census_tract_geoid"])
        geoids.update(value for value in table.column(0).to_pylist() if value)
    if not geoids:
        raise SnapshotError("Parcel context contains no Census tract assignments")
    return geoids


def build_family_housing_need(
    *,
    raw_root: Path = Path("data/raw"),
    staged_root: Path = Path("data/staged"),
    final_root: Path = Path("data/final"),
    snapshot_date: date | None = None,
    config_path: Path | None = None,
) -> Path:
    config = load_family_housing_need_config(config_path)
    source_snapshots = {
        table: latest_snapshot(raw_root, dataset) for table, dataset in ACS_DATASETS.items()
    }
    tract_snapshot = latest_snapshot(raw_root, "census_tract_geometry")
    parcel_snapshot = latest_snapshot(staged_root, "parcel_context")
    vintages = {_snapshot_vintage(snapshot) for snapshot in source_snapshots.values()}
    if len(vintages) != 1:
        raise SnapshotError("ACS source snapshots do not use the same vintage")
    acs_vintage = vintages.pop()
    acs_tables = {table: _read_table(path) for table, path in source_snapshots.items()}
    geometry = _read_tract_geometry(tract_snapshot)
    chicago_geoids = _chicago_tract_geoids(parcel_snapshot)
    output = calculate_family_housing_need(acs_tables, geometry, chicago_geoids, config)

    with GeoParquetSnapshotWriter(
        raw_root=final_root,
        dataset="family_housing_need",
        source_url=f"https://api.census.gov/data/{acs_vintage}/acs/acs5",
        output_crs=OUTPUT_CRS,
        snapshot_date=snapshot_date,
    ) as writer:
        writer.write_geodataframe_batch(1, output)
        return writer.commit(
            expected_records=len(output),
            expected_parts=1,
            metadata={
                "grain": ["census_tract_geoid"],
                "geographic_scope": "Chicago tracts represented in the staged parcel context",
                "config_version": config.config_version,
                "acs_vintage": acs_vintage,
                "headline_bedroom_threshold": config.headline_bedroom_threshold,
                "reported_bedroom_thresholds": list(config.reported_bedroom_thresholds),
                "high_need_percentile": config.high_need_percentile,
                "score_components": list(config.score_components),
                "score_method": (
                    "Equal-weight mean of four percentile ranks benchmarked against the "
                    "median Chicago tract distribution"
                ),
                "source_snapshots": {
                    **{table: str(path) for table, path in source_snapshots.items()},
                    "tract_geometry": str(tract_snapshot),
                    "parcel_context": str(parcel_snapshot),
                },
                "sources": config.sources,
                "reliability_rule": config.reliability_rule,
                "ownership": {
                    "source_data": "U.S. Census Bureau",
                    "derived_dataset": "single-stair project",
                },
                "limitations": list(config.notes),
            },
        )
