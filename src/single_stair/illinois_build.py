"""Conservative parcel screening for the introduced Illinois BUILD middle-housing bill.

This is a zoning-allowance screen, not a capacity model. In particular it neither
renames Chicago zones nor attributes the proposed unit allowances to stair reform.
"""

import json
import math
from collections.abc import Mapping
from importlib.resources import files
from typing import Any, Literal, TypedDict

import pandas as pd

BuildCategory = Literal[
    "baseline", "screened_expansion", "review", "no_increase", "detached_only", "out_of_scope"
]
Eligibility = Literal["by_right", "conditional", "special_use", "unknown", "excluded"]


class BuildClassification(TypedDict):
    illinois_build: bool
    build_category: BuildCategory
    build_residential_eligibility: Eligibility
    build_existing_unit_comparator: int | None
    build_existing_unit_limit_basis: str
    build_minimum_units: int | None
    build_effective_unit_limit: int | None
    build_additional_unit_allowance: int | None
    build_review_reasons: list[str]


BUILD_PROPERTIES = tuple(BuildClassification.__annotations__)
PARCEL_REVIEW_BLOCKERS = frozenset(
    {
        "missing_pin",
        "unitized_parcel",
        "nonstandard_parcel_type",
        "inconsistent_assessor_land_area",
        "parcel_assessor_land_area_mismatch",
    }
)


def load_build_policy() -> dict[str, Any]:
    """Read the versioned evidence and fixed interpretation used by this screen."""
    resource = files("single_stair").joinpath("config/illinois_build.v1.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _true(value: object) -> bool:
    # NaN and strings such as "False" must not become truthy eligibility flags.
    return value is True or (not isinstance(value, str) and _number(value) == 1)


def build_minimum_units(area_sqft: object) -> int | None:
    """SB4060 §11-13.1-10(c) allowance, conditional on district/lot eligibility."""
    area = _number(area_sqft)
    if area is None or area <= 0:
        return None
    if area <= 2500:
        return 1
    if area <= 5000:
        return 4
    if area <= 7500:
        return 6
    return 8


def _zone(parcel: Mapping[str, object]) -> str:
    # The pipeline collapses B1/B2/B3 into B-<density>. Never use that collapsed
    # class to infer detached-house permission, which differs among the districts.
    raw = parcel.get("zone_class")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().upper()
    canonical = parcel.get("canonical_zone_class")
    if isinstance(canonical, str) and canonical.strip().upper().startswith(("RS-", "RT-", "RM-")):
        return canonical.strip().upper()
    return ""


def _eligibility(zone: str) -> Eligibility:
    if zone in {"RS-1", "RS-2", "RS-3"}:
        return "by_right"
    if zone in {"RT-3.5", "RT-4", "RT-4A", "RM-4.5", "RM-5", "RM-5.5", "RM-6", "RM-6.5"}:
        return "conditional"
    district, separator, density = zone.partition("-")
    if separator and density in {"1", "1.5", "2", "3", "5"}:
        if district == "B2":
            return "conditional"
        if district in {"B1", "B3", "C1", "C2"}:
            return "special_use"
        if district == "C3":
            return "excluded"
    # PD, downtown, industrial and unrecognized classes require their own legal
    # use-table review; lack of a modeled rule is not a finding of prohibition.
    return "unknown"


def classify_build_parcel(
    parcel: Mapping[str, object], *, is_baseline: bool = False
) -> BuildClassification:
    """Return JSON-safe screening fields for one tax parcel, preserving baseline.

    Required for an automatic addition: a known RS district, >2,500 sqft, a
    known existing unit limit below the proposal, and conventional non-unitized
    parcel evidence without known lot-area conflicts. Existing building counts
    and floor-area efficiency do not affect this classification. Numeric fields
    on review rows describe a conditional scenario, not an established right.
    """
    eligibility = _eligibility(_zone(parcel))
    allowance = build_minimum_units(parcel.get("analysis_lot_area_sqft"))
    current = _number(parcel.get("current_zoning_unit_limit"))
    if current is None or current < 0 or not current.is_integer():
        current = None
    comparator_basis = "source_modeled_zoning_limit" if current is not None else "unknown"
    if eligibility == "by_right" and current == 0:
        # An undersized RS lot can produce floor(area / minimum_lot_area) == 0
        # upstream. Compare to the district's one-detached-dwelling ceiling,
        # without claiming the existing tax parcel is a legal buildable lot.
        current = 1.0
        comparator_basis = "rs_detached_district_ceiling"
    effective = (
        max(int(current), allowance)
        if current is not None and allowance is not None and eligibility != "excluded"
        else None
    )
    increase = effective - int(current) if effective is not None else None
    reasons: list[str] = []
    if eligibility == "conditional":
        reasons.append("detached_use_preservation_and_transit_review")
    elif eligibility == "special_use":
        reasons.append("detached_use_special_approval_review")
    elif eligibility == "unknown":
        reasons.append("unmapped_detached_house_permission")
    if allowance is None:
        reasons.append("missing_or_invalid_lot_area")
    if current is None:
        reasons.append("missing_or_invalid_current_unit_limit")
    unit = _number(parcel.get("pinu"))
    parcel_type = _number(parcel.get("parceltype"))
    if unit is None:
        reasons.append("missing_parcel_unit_identifier")
    elif unit != 0:
        reasons.append("unitized_parcel")
    if parcel_type is None:
        reasons.append("missing_parcel_type")
    elif parcel_type != 1:
        reasons.append("nonstandard_parcel_type")
    if _true(parcel.get("has_land_area_mismatch")):
        reasons.append("parcel_assessor_land_area_mismatch")
    previous = parcel.get("review_reasons")
    previous_reasons = previous.split(";") if isinstance(previous, str) else []
    reasons.extend(reason for reason in previous_reasons if reason in PARCEL_REVIEW_BLOCKERS)
    if _true(parcel.get("requires_legal_or_site_review")) and not any(previous_reasons):
        reasons.append("unspecified_existing_site_review")
    reasons = list(dict.fromkeys(reasons))

    category: BuildCategory
    if is_baseline:
        category = "baseline"
    elif eligibility == "excluded":
        category = "out_of_scope"
    elif reasons:
        category = "review"
    elif allowance == 1:
        category = "detached_only"
    elif increase is not None and increase > 0:
        category = "screened_expansion"
    else:
        category = "no_increase"
    return {
        "illinois_build": is_baseline or category == "screened_expansion",
        "build_category": category,
        "build_residential_eligibility": eligibility,
        "build_existing_unit_comparator": int(current) if current is not None else None,
        "build_existing_unit_limit_basis": comparator_basis,
        "build_minimum_units": allowance if eligibility != "excluded" else None,
        "build_effective_unit_limit": effective,
        "build_additional_unit_allowance": increase,
        "build_review_reasons": reasons,
    }


def enrich_build_parcels(frame: pd.DataFrame) -> pd.DataFrame:
    """Copy a parcel-grain frame and append BUILD fields without changing geometry.

    Source: final parcel-opportunity table plus caller-supplied
    ``current_single_stair`` membership. Destination: map coverage export.
    Grain/keys: unchanged; no aggregation or deduplication occurs here.
    """
    columns = (
        "zone_class",
        "canonical_zone_class",
        "analysis_lot_area_sqft",
        "current_zoning_unit_limit",
        "pinu",
        "parceltype",
        "has_land_area_mismatch",
        "requires_legal_or_site_review",
        "review_reasons",
        "current_single_stair",
    )
    available = [column for column in columns if column in frame]
    classifications = [
        classify_build_parcel(row, is_baseline=_true(row.get("current_single_stair")))
        for values in frame[available].itertuples(index=False, name=None)
        for row in [dict(zip(available, values, strict=True))]
    ]
    output = frame.copy()
    for field in BUILD_PROPERTIES:
        values = [row[field] for row in classifications]
        if field in {
            "build_existing_unit_comparator",
            "build_minimum_units",
            "build_effective_unit_limit",
            "build_additional_unit_allowance",
        }:
            output[field] = pd.array(values, dtype="Int64")
        elif field == "illinois_build":
            output[field] = pd.array(values, dtype=bool)
        else:
            output[field] = pd.Series(values, index=frame.index, dtype=object)
    return output
