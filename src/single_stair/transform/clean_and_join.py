from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq

from single_stair.ingest.snapshot import GeoParquetSnapshotWriter, ParquetSnapshotWriter

WORKING_CRS = "EPSG:3435"
OUTPUT_CRS = "EPSG:4326"
TARGET_PART_RECORDS = 50_000


@dataclass(frozen=True, slots=True)
class StagedSnapshots:
    parcel_context: Path
    building_characteristics: Path

    @property
    def paths(self) -> tuple[Path, Path]:
        return self.parcel_context, self.building_characteristics


@dataclass(slots=True)
class JoinCounts:
    source_parcels: int = 0
    chicago_parcels: int = 0
    valid_pin: int = 0
    zoning: int = 0
    census_tract: int = 0
    ward: int = 0
    community_area: int = 0
    cta_station: int = 0
    metra_station: int = 0
    building_characteristics: int = 0

    def report(self) -> dict[str, Any]:
        counts = asdict(self)
        denominator = self.chicago_parcels
        rates = {
            key: round(value / denominator, 6) if denominator else 0.0
            for key, value in counts.items()
            if key not in {"source_parcels", "chicago_parcels"}
        }
        return {"counts": counts, "rates_of_chicago_parcels": rates}


ProgressCallback = Callable[[int, int, int], None]


def normalize_pin(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits if len(digits) == 14 else None


def parcel_pin(pin10: Any, unit: Any) -> str | None:
    if pin10 is None or pd.isna(pin10):
        return None
    base_digits = "".join(character for character in str(pin10) if character.isdigit())
    if len(base_digits) != 10:
        return None

    if unit is None or pd.isna(unit):
        unit_digits = "0000"
    else:
        try:
            unit_digits = f"{int(unit):04d}"
        except (TypeError, ValueError, OverflowError):
            return None
    return normalize_pin(base_digits + unit_digits)


def latest_snapshot(raw_root: Path, dataset: str) -> Path:
    dataset_root = raw_root / dataset
    snapshots = sorted(
        path
        for path in dataset_root.glob("snapshot_date=*")
        if path.is_dir() and (path / "manifest.json").is_file()
    )
    if not snapshots:
        raise FileNotFoundError(f"No complete raw snapshot found for {dataset}")
    return snapshots[-1]


def _parquet_parts(snapshot: Path) -> list[Path]:
    parts = sorted(snapshot.glob("part-*.parquet"))
    if not parts:
        raise FileNotFoundError(f"Snapshot contains no Parquet parts: {snapshot}")
    return parts


def _read_geoparquet(snapshot: Path) -> gpd.GeoDataFrame:
    frames = [gpd.read_parquet(part) for part in _parquet_parts(snapshot)]
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=frames[0].crs)


def _read_station_points(snapshot: Path) -> gpd.GeoDataFrame:
    tables = [pq.read_table(part).to_pandas() for part in _parquet_parts(snapshot)]
    frame = pd.concat(tables, ignore_index=True)
    longitude = pd.to_numeric(frame["stop_lon"], errors="raise")
    latitude = pd.to_numeric(frame["stop_lat"], errors="raise")
    return gpd.GeoDataFrame(
        frame,
        geometry=gpd.points_from_xy(longitude, latitude),
        crs=OUTPUT_CRS,
    ).to_crs(WORKING_CRS)


def _polygon_attributes(
    points: gpd.GeoDataFrame,
    polygons: gpd.GeoDataFrame,
    columns: list[str],
    *,
    tie_breaker: str,
) -> pd.DataFrame:
    joined = gpd.sjoin(
        points[["geometry"]],
        polygons[[*columns, "geometry"]],
        how="left",
        predicate="intersects",
    )
    joined["_source_index"] = joined.index
    joined = joined.sort_values(["_source_index", tie_breaker], na_position="last")
    joined = joined.drop_duplicates("_source_index", keep="first").set_index("_source_index")
    return joined[columns].reindex(points.index)


def _nearest_station(
    points: gpd.GeoDataFrame,
    stations: gpd.GeoDataFrame,
    *,
    prefix: str,
) -> pd.DataFrame:
    stop_id = f"{prefix}_stop_id"
    stop_name = f"{prefix}_stop_name"
    distance = f"{prefix}_distance_ft"
    station_fields = stations[["stop_id", "stop_name", "geometry"]].rename(
        columns={"stop_id": stop_id, "stop_name": stop_name}
    )
    joined = gpd.sjoin_nearest(
        points[["geometry"]],
        station_fields,
        how="left",
        distance_col=distance,
    )
    joined["_source_index"] = joined.index
    joined = joined.sort_values(["_source_index", distance, stop_id], na_position="last")
    joined = joined.drop_duplicates("_source_index", keep="first").set_index("_source_index")
    return joined[[stop_id, stop_name, distance]].reindex(points.index)


def _building_query(parts: list[Path]) -> tuple[str, list[str]]:
    paths = [str(path) for path in parts]
    source = "read_parquet(?, union_by_name=true, hive_partitioning=false)"
    query = f"""
        WITH normalized AS (
            SELECT
                * EXCLUDE (pin),
                CAST(pin AS VARCHAR) AS source_pin,
                regexp_replace(CAST(pin AS VARCHAR), '[^0-9]', '', 'g') AS pin
            FROM {source}
        ), ranked AS (
            SELECT
                *,
                row_number() OVER (
                    PARTITION BY pin, CAST(card AS VARCHAR)
                    ORDER BY try_cast(year AS DOUBLE) DESC
                ) AS record_rank
            FROM normalized
            WHERE length(pin) = 14
        )
        SELECT * EXCLUDE (record_rank)
        FROM ranked
        WHERE record_rank = 1
        ORDER BY pin, try_cast(card AS DOUBLE), CAST(card AS VARCHAR)
    """
    return query, paths


def stage_latest_building_characteristics(
    raw_snapshot: Path,
    *,
    staged_root: Path,
    snapshot_date: date | None,
) -> tuple[Path, Counter[str]]:
    query, paths = _building_query(_parquet_parts(raw_snapshot))
    connection = duckdb.connect()
    reader = connection.execute(query, [paths]).to_arrow_reader(batch_size=50_000)
    card_counts: Counter[str] = Counter()

    with ParquetSnapshotWriter(
        raw_root=staged_root,
        dataset="building_characteristics_latest",
        source_url=str(raw_snapshot),
        output_crs=None,
        snapshot_date=snapshot_date,
    ) as writer:
        for batch_number, batch in enumerate(reader, start=1):
            card_counts.update(str(value) for value in batch.column("pin").to_pylist())
            writer.write_arrow_batch(batch_number, batch)

        path = writer.commit(
            expected_records=writer.record_count,
            expected_parts=len(writer.parts),
            metadata={
                "source_snapshot": str(raw_snapshot),
                "grain": ["pin", "card"],
                "pin_format": "14 numeric digits",
                "latest_record_order": ["year DESC"],
                "unique_pins": len(card_counts),
            },
        )
    connection.close()
    return path, card_counts


def _prepare_layers(raw_root: Path) -> dict[str, gpd.GeoDataFrame]:
    zoning = _read_geoparquet(latest_snapshot(raw_root, "chicago_zoning")).rename(
        columns={"objectid": "zoning_objectid"}
    )
    census = _read_geoparquet(latest_snapshot(raw_root, "census_tract_geometry")).rename(
        columns={"GEOID": "census_tract_geoid"}
    )
    wards = _read_geoparquet(latest_snapshot(raw_root, "chicago_ward_boundaries"))
    communities = _read_geoparquet(
        latest_snapshot(raw_root, "chicago_community_area_boundaries")
    ).rename(
        columns={
            "area_num_1": "community_area_number",
            "community": "community_area_name",
        }
    )

    return {
        "zoning": zoning.to_crs(WORKING_CRS),
        "census": census.to_crs(WORKING_CRS),
        "wards": wards.to_crs(WORKING_CRS),
        "communities": communities.to_crs(WORKING_CRS),
        "cta": _read_station_points(latest_snapshot(raw_root, "cta_stations")),
        "metra": _read_station_points(latest_snapshot(raw_root, "metra_stations")),
    }


def _enrich_parcel_batch(
    frame: gpd.GeoDataFrame,
    layers: dict[str, gpd.GeoDataFrame],
    card_counts: Counter[str],
) -> gpd.GeoDataFrame:
    if frame.crs is None:
        raise ValueError("Parcel batch does not declare a CRS")
    frame = frame.to_crs(OUTPUT_CRS)
    frame.geometry = frame.geometry.make_valid()
    frame["pin"] = [
        parcel_pin(pin10, unit) for pin10, unit in zip(frame["pin10"], frame["pinu"], strict=True)
    ]

    projected = frame.to_crs(WORKING_CRS)
    centroids = gpd.GeoDataFrame(
        index=frame.index, geometry=projected.geometry.centroid, crs=WORKING_CRS
    )
    community = _polygon_attributes(
        centroids,
        layers["communities"],
        ["community_area_number", "community_area_name"],
        tie_breaker="community_area_number",
    )
    chicago_index = community.index[community["community_area_number"].notna()]
    if chicago_index.empty:
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs=OUTPUT_CRS)

    frame = frame.loc[chicago_index].copy()
    centroids = centroids.loc[chicago_index]
    community = community.loc[chicago_index]
    zoning = _polygon_attributes(
        centroids,
        layers["zoning"],
        ["zoning_objectid", "zoning_id", "zone_class"],
        tie_breaker="zoning_objectid",
    )
    census = _polygon_attributes(
        centroids,
        layers["census"],
        ["census_tract_geoid"],
        tie_breaker="census_tract_geoid",
    )
    ward = _polygon_attributes(
        centroids,
        layers["wards"],
        ["ward"],
        tie_breaker="ward",
    )
    cta = _nearest_station(centroids, layers["cta"], prefix="cta")
    metra = _nearest_station(centroids, layers["metra"], prefix="metra")

    output_columns = [
        column
        for column in (
            "objectid",
            "pin",
            "pin10",
            "pinu",
            "taxcode",
            "parceltype",
            "name",
            "last_edited_date",
            "geometry",
        )
        if column in frame.columns
    ]
    output = frame[output_columns].copy()
    centroid_wgs84 = centroids.to_crs(OUTPUT_CRS).geometry
    output["centroid_lon"] = centroid_wgs84.x
    output["centroid_lat"] = centroid_wgs84.y
    for attributes in (community, zoning, census, ward, cta, metra):
        for column in attributes.columns:
            output[column] = attributes[column]

    output["transit_distance_ft"] = output[["cta_distance_ft", "metra_distance_ft"]].min(axis=1)
    output["nearest_transit_agency"] = "CTA"
    metra_is_nearer = output["metra_distance_ft"] < output["cta_distance_ft"]
    output.loc[metra_is_nearer, "nearest_transit_agency"] = "Metra"
    output["building_card_count"] = output["pin"].map(card_counts).fillna(0).astype("int64")
    output["has_building_characteristics"] = output["building_card_count"] > 0
    return gpd.GeoDataFrame(output, geometry="geometry", crs=OUTPUT_CRS)


def stage_parcel_context(
    raw_snapshot: Path,
    *,
    raw_root: Path,
    staged_root: Path,
    snapshot_date: date | None,
    card_counts: Counter[str],
    progress: ProgressCallback | None = None,
) -> Path:
    layers = _prepare_layers(raw_root)
    counts = JoinCounts()
    seen_object_ids: set[int] = set()

    with GeoParquetSnapshotWriter(
        raw_root=staged_root,
        dataset="parcel_context",
        source_url=str(raw_snapshot),
        output_crs=OUTPUT_CRS,
        snapshot_date=snapshot_date,
    ) as writer:
        output_batch_number = 0
        buffered_frames: list[gpd.GeoDataFrame] = []
        buffered_records = 0
        parts = _parquet_parts(raw_snapshot)
        for part_number, part in enumerate(parts, start=1):
            source = gpd.read_parquet(part)
            counts.source_parcels += len(source)
            object_ids = {int(value) for value in source["objectid"]}
            if seen_object_ids & object_ids:
                raise ValueError("Parcel snapshot contains duplicate objectid values")
            seen_object_ids.update(object_ids)

            output = _enrich_parcel_batch(source, layers, card_counts)
            if output.empty:
                if progress is not None:
                    progress(part_number, len(parts), counts.chicago_parcels)
                continue
            buffered_frames.append(output)
            buffered_records += len(output)

            counts.chicago_parcels += len(output)
            counts.valid_pin += int(output["pin"].notna().sum())
            counts.zoning += int(output["zone_class"].notna().sum())
            counts.census_tract += int(output["census_tract_geoid"].notna().sum())
            counts.ward += int(output["ward"].notna().sum())
            counts.community_area += int(output["community_area_number"].notna().sum())
            counts.cta_station += int(output["cta_stop_id"].notna().sum())
            counts.metra_station += int(output["metra_stop_id"].notna().sum())
            counts.building_characteristics += int(output["has_building_characteristics"].sum())
            if buffered_records >= TARGET_PART_RECORDS:
                output_batch_number += 1
                writer.write_geodataframe_batch(
                    output_batch_number,
                    gpd.GeoDataFrame(
                        pd.concat(buffered_frames, ignore_index=True),
                        geometry="geometry",
                        crs=OUTPUT_CRS,
                    ),
                )
                buffered_frames = []
                buffered_records = 0
            if progress is not None:
                progress(part_number, len(parts), counts.chicago_parcels)

        if buffered_frames:
            output_batch_number += 1
            writer.write_geodataframe_batch(
                output_batch_number,
                gpd.GeoDataFrame(
                    pd.concat(buffered_frames, ignore_index=True),
                    geometry="geometry",
                    crs=OUTPUT_CRS,
                ),
            )

        return writer.commit(
            expected_records=writer.record_count,
            expected_parts=len(writer.parts),
            metadata={
                "source_snapshot": str(raw_snapshot),
                "grain": ["objectid"],
                "scope": "parcel centroids intersecting a Chicago community area",
                "pin_format": "pin10 plus four-digit unit, 14 numeric digits",
                "working_crs": WORKING_CRS,
                "distance_method": "parcel centroid to station point",
                "distance_unit": "US survey feet",
                "target_part_records": TARGET_PART_RECORDS,
                "join_quality": counts.report(),
                "source_snapshots": {
                    name: str(latest_snapshot(raw_root, dataset))
                    for name, dataset in {
                        "zoning": "chicago_zoning",
                        "census_tracts": "census_tract_geometry",
                        "wards": "chicago_ward_boundaries",
                        "community_areas": "chicago_community_area_boundaries",
                        "cta_stations": "cta_stations",
                        "metra_stations": "metra_stations",
                    }.items()
                },
            },
        )


def build_clean_and_join(
    *,
    raw_root: Path = Path("data/raw"),
    staged_root: Path = Path("data/staged"),
    snapshot_date: date | None = None,
    progress: ProgressCallback | None = None,
) -> StagedSnapshots:
    building_source = latest_snapshot(raw_root, "cook_county_building_characteristics")
    parcel_source = latest_snapshot(raw_root, "cook_county_parcels")
    building_path, card_counts = stage_latest_building_characteristics(
        building_source,
        staged_root=staged_root,
        snapshot_date=snapshot_date,
    )
    parcel_path = stage_parcel_context(
        parcel_source,
        raw_root=raw_root,
        staged_root=staged_root,
        snapshot_date=snapshot_date,
        card_counts=card_counts,
        progress=progress,
    )
    return StagedSnapshots(
        parcel_context=parcel_path,
        building_characteristics=building_path,
    )
