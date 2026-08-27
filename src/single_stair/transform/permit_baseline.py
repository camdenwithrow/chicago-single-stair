import json
import re
from dataclasses import dataclass
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pyarrow as pa

from single_stair.ingest.snapshot import ParquetSnapshotWriter, SnapshotError
from single_stair.transform.clean_and_join import latest_snapshot

NEW_CONSTRUCTION = "PERMIT - NEW CONSTRUCTION"
ALTERATION = "PERMIT - RENOVATION/ALTERATION"
NUMBER_WORDS = {
    "ONE": 1,
    "TWO": 2,
    "THREE": 3,
    "FOUR": 4,
    "FIVE": 5,
    "SIX": 6,
    "SEVEN": 7,
    "EIGHT": 8,
    "NINE": 9,
    "TEN": 10,
    "ELEVEN": 11,
    "TWELVE": 12,
    "THIRTEEN": 13,
    "FOURTEEN": 14,
    "FIFTEEN": 15,
    "SIXTEEN": 16,
    "SEVENTEEN": 17,
    "EIGHTEEN": 18,
    "NINETEEN": 19,
    "TWENTY": 20,
}
NUMBER = r"(?:\d{1,3}|" + "|".join(NUMBER_WORDS) + r")"


@dataclass(frozen=True, slots=True)
class PermitParse:
    stories: int | None
    existing_units: int | None
    proposed_units: int | None
    net_added_units: int | None
    production_type: str
    confidence: str
    review_reason: str | None


def _number(value: str) -> int:
    return int(value) if value.isdigit() else NUMBER_WORDS[value]


def _first(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text)
    return _number(match.group(1)) if match else None


def parse_permit_description(permit_type: str | None, description: str | None) -> PermitParse:
    raw_description = description if isinstance(description, str) else ""
    text = re.sub(r"\s+", " ", raw_description.upper().replace("–", "-")).strip()
    if not text:
        return PermitParse(
            None, None, None, None, "unresolved", "unresolved", "missing_description"
        )

    stories = _first(rf"\b({NUMBER})(?:[ -]?1/2)?[ -]?(?:STORY|STORIES)\b", text)
    existing = _first(
        rf"\b(?:EXISTING|EXIST\.?|FROM)\s+(?:A\s+)?({NUMBER})\s*(?:DWELLING\s+)?(?:UNITS?|D\.?U\.?)\b",
        text,
    )
    proposed = _first(
        rf"\b(?:PROPOSED|TO|TOTAL(?:\s+OF)?)\s+(?:A\s+)?({NUMBER})\s*(?:TOTAL\s+)?(?:DWELLING\s+)?(?:UNITS?|D\.?U\.?)\b",
        text,
    )
    explicit_total = _first(
        rf"\b({NUMBER})\s+(?:TOTAL\s+)?(?:DWELLING\s+)?UNITS?(?:\s+UNITS?)?\s+(?:TOTAL\b|BUILDING\b)",
        text,
    )
    proposed = proposed or explicit_total
    dwelling = [
        _number(value)
        for value in re.findall(
            rf"\b({NUMBER})\s+(?:(?:RESIDENTIAL|TOTAL)\s+)?(?:DWELLING\s+)?(?:UNITS?|D\.?U\.?)\b",
            text,
        )
    ]
    flats = [_number(value) for value in re.findall(rf"\b({NUMBER})[ -]FLAT\b", text)]

    normalized_type = permit_type.replace("–", "-") if isinstance(permit_type, str) else ""
    if normalized_type == NEW_CONSTRUCTION:
        has_new_building = bool(
            re.search(r"\b(?:ERECT|NEW CONSTRUCTION|BUILDING \d+\s*-\s*NEW|NEW .*BUILDING)\b", text)
        )
        townhomes = _first(rf"\b({NUMBER})\s+NEW\s+TOWNHOMES?\b", text)
        candidates = dwelling + flats
        proposed = proposed or townhomes or (candidates[0] if candidates else None)
        if not has_new_building or proposed is None:
            return PermitParse(
                stories,
                None,
                proposed,
                proposed,
                "unresolved",
                "low",
                "new_construction_description_ambiguous",
            )
        return PermitParse(stories, 0, proposed, proposed, "new_construction", "high", None)

    if normalized_type == ALTERATION:
        if "NO CHANGE OF OCCUPANCY NUMBERS" in text and "SEE PERMIT" in text:
            return PermitParse(stories, existing, proposed, 0, "not_production", "high", None)
        adu_count = len(
            re.findall(
                r"\b(?:NEW|ADD(?:ITIONAL)?)\s+(?:A\.?D\.?U\.?|ACCESSORY DWELLING UNIT)\b", text
            )
        )
        explicit_add = _first(
            rf"\bADD(?:ING)?\s+({NUMBER})\s+(?:NEW\s+)?(?:DWELLING\s+)?UNITS?\b", text
        )
        net = proposed - existing if proposed is not None and existing is not None else None
        if net is None and (explicit_add or adu_count):
            net = explicit_add or adu_count
            if existing is not None:
                proposed = existing + net
        if net is not None and net > 0:
            return PermitParse(
                stories,
                existing,
                proposed,
                net,
                "unit_adding_alteration",
                "high" if proposed is not None else "medium",
                None if proposed is not None else "proposed_total_not_stated",
            )
        return PermitParse(stories, existing, proposed, net, "not_production", "medium", None)

    return PermitParse(stories, existing, proposed, None, "not_production", "high", None)


def _permit_era(issue_date: pd.Timestamp) -> str:
    if issue_date < pd.Timestamp("2024-05-01"):
        return "legacy"
    if issue_date < pd.Timestamp("2024-09-19"):
        return "2024_transition"
    return "current"


def _size_band(units: Any) -> str | None:
    if pd.isna(units):
        return None
    value = int(units)
    for lower, upper in ((2, 2), (3, 4), (5, 8), (9, 12), (13, 20)):
        if lower <= value <= upper:
            return str(lower) if lower == upper else f"{lower}-{upper}"
    return "outside_2-20"


def _read_permits(snapshot: Path, start_year: int) -> pd.DataFrame:
    pattern = str(snapshot / "part-*.parquet")
    query = """
        SELECT id, permit_ AS permit_number, permit_status, permit_milestone,
               replace(permit_type, '–', '-') AS permit_type, review_type,
               application_start_date, issue_date, processing_time, street_number,
               street_direction, street_name, work_type, work_description, reported_cost,
               total_fee, community_area, census_tract, ward, latitude, longitude
        FROM read_parquet(?, union_by_name=true)
        WHERE try_cast(issue_date AS DATE) >= make_date(?, 1, 1)
    """
    return duckdb.connect().execute(query, [pattern, start_year]).fetchdf()


def _annual_cpi(snapshot: Path) -> pd.DataFrame:
    pattern = str(snapshot / "part-*.parquet")
    query = """
        SELECT cast(year AS INTEGER) issue_year,
               avg(try_cast(value AS DOUBLE)) annual_cpi,
               count(try_cast(value AS DOUBLE)) cpi_month_count
        FROM read_parquet(?, union_by_name=true)
        GROUP BY 1
    """
    return duckdb.connect().execute(query, [pattern]).fetchdf()


def _community_names(snapshot: Path) -> pd.DataFrame:
    pattern = str(snapshot / "part-*.parquet")
    query = """
        SELECT DISTINCT try_cast(area_num_1 AS INTEGER) community_area,
               community AS community_area_name
        FROM read_parquet(?, union_by_name=true)
        WHERE area_num_1 IS NOT NULL
    """
    return duckdb.connect().execute(query, [pattern]).fetchdf()


def _summarize(frame: pd.DataFrame, dimensions: list[str]) -> pd.DataFrame:
    grouped = frame.groupby(dimensions, dropna=False, observed=True)
    summary = grouped.agg(
        permit_count=("permit_id", "size"),
        dwelling_units_permitted=("net_added_units", "sum"),
        median_review_days=("review_days", "median"),
        median_reported_cost_nominal=("reported_cost_nominal", "median"),
        total_reported_cost_nominal=("reported_cost_nominal", "sum"),
        median_reported_cost_2025_dollars=("reported_cost_2025_dollars", "median"),
        total_reported_cost_2025_dollars=("reported_cost_2025_dollars", "sum"),
        missing_review_time_count=("review_days", lambda values: int(values.isna().sum())),
        missing_cost_count=("reported_cost_nominal", lambda values: int(values.isna().sum())),
    ).reset_index()
    return summary


def _validation_metrics(permits: pd.DataFrame) -> pd.DataFrame:
    resource = files("single_stair").joinpath("config/permit_validation.v1.csv")
    with resource.open("rb") as validation_file:
        labels = pd.read_csv(validation_file, dtype={"permit_id": "string"})
    reviewed = labels.merge(
        permits[["permit_id", "is_small_multifamily_production"]],
        on="permit_id",
        how="left",
        validate="one_to_one",
    )
    if reviewed["is_small_multifamily_production"].isna().any():
        missing = reviewed.loc[reviewed["is_small_multifamily_production"].isna(), "permit_id"]
        raise SnapshotError(
            f"Validation permits are absent from the source snapshot: {missing.tolist()}"
        )
    expected = reviewed["expected_small_multifamily_production"].astype(bool)
    actual = reviewed["is_small_multifamily_production"].astype(bool)
    reviewed["correct"] = expected.eq(actual)
    reviewed["true_positive"] = expected & actual
    reviewed["false_positive"] = ~expected & actual
    reviewed["false_negative"] = expected & ~actual
    reviewed["true_negative"] = ~expected & ~actual

    rows = []
    for scope, group in [("overall", reviewed), *reviewed.groupby("stratum", sort=True)]:
        true_positive = int(group["true_positive"].sum())
        false_positive = int(group["false_positive"].sum())
        false_negative = int(group["false_negative"].sum())
        precision_denominator = true_positive + false_positive
        recall_denominator = true_positive + false_negative
        rows.append(
            {
                "validation_scope": scope,
                "sample_size": len(group),
                "correct_count": int(group["correct"].sum()),
                "accuracy": float(group["correct"].mean()),
                "precision": (
                    true_positive / precision_denominator if precision_denominator else None
                ),
                "recall": true_positive / recall_denominator if recall_denominator else None,
                "true_positive": true_positive,
                "false_positive": false_positive,
                "false_negative": false_negative,
                "true_negative": int(group["true_negative"].sum()),
            }
        )
    return pd.DataFrame(rows)


def _write_dataset(
    frame: pd.DataFrame,
    *,
    root: Path,
    dataset: str,
    snapshot_date: date | None,
    metadata: dict[str, Any],
) -> Path:
    table = pa.Table.from_pandas(frame, preserve_index=False)
    with ParquetSnapshotWriter(
        raw_root=root,
        dataset=dataset,
        source_url="derived from raw Chicago permits and BLS CPI snapshots",
        output_crs=None,
        snapshot_date=snapshot_date,
    ) as writer:
        writer.write_arrow_batch(1, table)
        return writer.commit(expected_records=len(frame), expected_parts=1, metadata=metadata)


def build_permit_baseline(
    *,
    raw_root: Path = Path("data/raw"),
    final_root: Path = Path("data/final"),
    snapshot_date: date | None = None,
) -> tuple[Path, ...]:
    config = json.loads(
        files("single_stair").joinpath("config/permit_baseline.v1.json").read_text(encoding="utf-8")
    )
    permits_source = latest_snapshot(raw_root, "chicago_building_permits")
    cpi_source = latest_snapshot(raw_root, "bls_chicago_cpi")
    community_source = latest_snapshot(raw_root, "chicago_community_area_boundaries")
    permits = _read_permits(permits_source, int(config["baseline_start_year"]))
    permits["issue_date"] = pd.to_datetime(permits["issue_date"], errors="coerce")
    permits["application_start_date"] = pd.to_datetime(
        permits["application_start_date"], errors="coerce"
    )
    permits = permits.loc[permits["issue_date"].notna()].copy()
    permits["issue_year"] = permits["issue_date"].dt.year.astype("int16")
    permits["baseline_period"] = permits["issue_year"].map(
        lambda year: "complete" if year <= int(config["last_complete_year"]) else "partial"
    )
    permits["permit_data_era"] = permits["issue_date"].map(_permit_era)
    parsed = [
        parse_permit_description(permit_type, description)
        for permit_type, description in zip(
            permits["permit_type"], permits["work_description"], strict=True
        )
    ]
    permits["stories"] = [item.stories for item in parsed]
    permits["existing_dwelling_units"] = [item.existing_units for item in parsed]
    permits["proposed_dwelling_units"] = [item.proposed_units for item in parsed]
    permits["net_added_units"] = [item.net_added_units for item in parsed]
    permits["production_type"] = [item.production_type for item in parsed]
    permits["parser_confidence"] = [item.confidence for item in parsed]
    permits["parser_review_reason"] = [item.review_reason for item in parsed]
    permits["is_small_multifamily_production"] = (
        permits["production_type"].isin(["new_construction", "unit_adding_alteration"])
        & permits["proposed_dwelling_units"].between(
            int(config["small_multifamily_min_units"]),
            int(config["small_multifamily_max_units"]),
        )
        & permits["net_added_units"].gt(0)
    )
    permits["size_band"] = permits["proposed_dwelling_units"].map(_size_band)
    calculated = (permits["issue_date"] - permits["application_start_date"]).dt.days
    permits["published_processing_days"] = pd.to_numeric(
        permits["processing_time"], errors="coerce"
    )
    permits["review_days"] = calculated.where(calculated.ge(0))
    permits["processing_time_matches_source"] = (
        (permits["review_days"] - permits["published_processing_days"]).abs().le(1)
    )
    permits["reported_cost_nominal"] = pd.to_numeric(
        permits["reported_cost"], errors="coerce"
    ).where(lambda values: values > 0)
    cpi = _annual_cpi(cpi_source)
    permits = permits.merge(cpi, on="issue_year", how="left", validate="many_to_one")
    reference_year = int(config["reference_dollar_year"])
    reference = cpi.loc[cpi["issue_year"] == reference_year, "annual_cpi"]
    if len(reference) != 1 or pd.isna(reference.iloc[0]):
        raise SnapshotError(f"CPI snapshot lacks a usable {reference_year} annual average")
    permits["reported_cost_2025_dollars"] = (
        permits["reported_cost_nominal"] * float(reference.iloc[0]) / permits["annual_cpi"]
    )
    permits["community_area"] = pd.to_numeric(permits["community_area"], errors="coerce")
    permits = permits.merge(
        _community_names(community_source), on="community_area", how="left", validate="many_to_one"
    )
    permits = permits.rename(columns={"id": "permit_id"})

    production = permits.loc[permits["is_small_multifamily_production"]].copy()
    summaries = {
        "permit_baseline_by_year": _summarize(
            production,
            ["issue_year", "baseline_period", "permit_data_era", "production_type", "size_band"],
        ),
        "permit_baseline_by_neighborhood": _summarize(
            production,
            [
                "issue_year",
                "baseline_period",
                "community_area",
                "community_area_name",
                "production_type",
                "size_band",
            ],
        ),
        "permit_baseline_by_review_type": _summarize(
            production,
            ["issue_year", "baseline_period", "review_type", "production_type", "size_band"],
        ),
        "permit_baseline_validation": _validation_metrics(permits),
    }
    common = {
        "config_version": config["config_version"],
        "sources": [str(permits_source), str(cpi_source), str(community_source)],
        "owner": "project transform; source owners are City of Chicago and U.S. BLS",
    }
    paths = [
        _write_dataset(
            permits,
            root=final_root,
            dataset="permit_baseline_records",
            snapshot_date=snapshot_date,
            metadata={**common, "grain": ["permit_id"], "primary_key": ["permit_id"]},
        )
    ]
    for dataset, summary in summaries.items():
        dimension_count = 1 if dataset == "permit_baseline_validation" else len(summary.columns) - 9
        paths.append(
            _write_dataset(
                summary,
                root=final_root,
                dataset=dataset,
                snapshot_date=snapshot_date,
                metadata={
                    **common,
                    "grain": list(summary.columns[:dimension_count]),
                    **(
                        {
                            "method": (
                                "Manual review of a deterministic, stratified 100-permit sample"
                            ),
                            "label_file": "permit_validation.v1.csv",
                        }
                        if dataset == "permit_baseline_validation"
                        else {}
                    ),
                },
            )
        )
    return tuple(paths)
