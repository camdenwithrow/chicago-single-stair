import json
from dataclasses import asdict, dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

BEDROOM_CATEGORIES = (
    "studio",
    "one_bedroom",
    "two_bedroom",
    "three_bedroom",
    "four_bedroom",
    "five_plus_bedroom",
)
ESTIMATE_PROFILE_IDS = ("conservative", "median", "progressive")


@dataclass(frozen=True, slots=True)
class Source:
    organization: str
    title: str
    url: str
    accessed_on: str
    note: str


@dataclass(frozen=True, slots=True)
class PolicyScenario:
    name: str
    jurisdiction: str
    status: str
    maximum_stories_above_grade: int
    maximum_units_per_story: int
    source_ids: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LotArchetype:
    name: str
    width_ft: float
    depth_ft: float
    match_width_min_ft: float
    match_width_max_ft: float
    match_depth_min_ft: float
    match_depth_max_ft: float
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EstimateProfile:
    name: str
    description: str
    two_stair_efficiency: float
    single_stair_efficiency: float
    efficiency_gain_percentage_points: float
    unit_size_method: str
    unit_sizes_sqft: dict[str, int]
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BedroomReporting:
    categories: tuple[str, ...]
    default_family_minimum_bedrooms: int
    reported_family_thresholds: tuple[int, ...]
    source_ids: tuple[str, ...]
    note: str


@dataclass(frozen=True, slots=True)
class ScenarioCatalog:
    config_version: str
    title: str
    default_policy_id: str
    default_estimate_id: str
    sources: dict[str, Source]
    policies: dict[str, PolicyScenario]
    lot_archetypes: dict[str, LotArchetype]
    estimate_profiles: dict[str, EstimateProfile]
    unit_size_evidence: dict[str, Any]
    bedroom_reporting: BedroomReporting

    def selection(
        self,
        *,
        policy_id: str | None = None,
        estimate_id: str | None = None,
    ) -> dict[str, Any]:
        selected_policy_id = policy_id or self.default_policy_id
        selected_estimate_id = estimate_id or self.default_estimate_id
        if selected_policy_id not in self.policies:
            raise ValueError(f"Unknown policy scenario: {selected_policy_id}")
        if selected_estimate_id not in self.estimate_profiles:
            raise ValueError(f"Unknown estimate profile: {selected_estimate_id}")

        policy = self.policies[selected_policy_id]
        estimate = self.estimate_profiles[selected_estimate_id]
        source_ids = set(policy.source_ids) | set(estimate.source_ids)
        for lot in self.lot_archetypes.values():
            source_ids.update(lot.source_ids)
        source_ids.update(self.bedroom_reporting.source_ids)
        return {
            "config_version": self.config_version,
            "policy_id": selected_policy_id,
            "policy": asdict(policy),
            "estimate_id": selected_estimate_id,
            "estimate": asdict(estimate),
            "lot_archetypes": {lot_id: asdict(lot) for lot_id, lot in self.lot_archetypes.items()},
            "bedroom_reporting": asdict(self.bedroom_reporting),
            "unit_size_evidence": self.unit_size_evidence,
            "sources": {
                source_id: asdict(self.sources[source_id]) for source_id in sorted(source_ids)
            },
        }


def _tuple(values: list[str]) -> tuple[str, ...]:
    return tuple(str(value) for value in values)


def _validate_sources(source_ids: tuple[str, ...], sources: dict[str, Source], owner: str) -> None:
    missing = sorted(set(source_ids) - sources.keys())
    if missing:
        raise ValueError(f"{owner} references unknown sources: {', '.join(missing)}")


def _validate_catalog(catalog: ScenarioCatalog) -> None:
    if catalog.default_policy_id not in catalog.policies:
        raise ValueError("Default policy does not exist")
    if catalog.default_estimate_id not in catalog.estimate_profiles:
        raise ValueError("Default estimate does not exist")
    if tuple(catalog.estimate_profiles) != ESTIMATE_PROFILE_IDS:
        raise ValueError("Estimate profiles must be conservative, median, and progressive")
    if catalog.bedroom_reporting.categories != BEDROOM_CATEGORIES:
        raise ValueError("Bedroom reporting categories do not match the supported schema")
    if catalog.bedroom_reporting.default_family_minimum_bedrooms not in (
        catalog.bedroom_reporting.reported_family_thresholds
    ):
        raise ValueError("Default family threshold must also be reported")

    for policy_id, policy in catalog.policies.items():
        if policy.maximum_stories_above_grade < 1 or policy.maximum_units_per_story < 1:
            raise ValueError(f"Policy {policy_id} has an invalid building envelope")
        _validate_sources(policy.source_ids, catalog.sources, f"Policy {policy_id}")

    for lot_id, lot in catalog.lot_archetypes.items():
        if not lot.match_width_min_ft <= lot.width_ft <= lot.match_width_max_ft:
            raise ValueError(f"Lot {lot_id} width falls outside its match range")
        if not lot.match_depth_min_ft <= lot.depth_ft <= lot.match_depth_max_ft:
            raise ValueError(f"Lot {lot_id} depth falls outside its match range")
        _validate_sources(lot.source_ids, catalog.sources, f"Lot {lot_id}")

    for estimate_id, estimate in catalog.estimate_profiles.items():
        if not 0 < estimate.two_stair_efficiency <= estimate.single_stair_efficiency <= 1:
            raise ValueError(f"Estimate {estimate_id} has invalid floor-area efficiency")
        actual_gain = round(
            (estimate.single_stair_efficiency - estimate.two_stair_efficiency) * 100,
            6,
        )
        if actual_gain != estimate.efficiency_gain_percentage_points:
            raise ValueError(f"Estimate {estimate_id} has an inconsistent efficiency gain")
        if tuple(estimate.unit_sizes_sqft) != BEDROOM_CATEGORIES:
            raise ValueError(f"Estimate {estimate_id} has invalid bedroom categories")
        sizes = tuple(estimate.unit_sizes_sqft.values())
        if any(size <= 0 for size in sizes) or sizes != tuple(sorted(sizes)):
            raise ValueError(f"Estimate {estimate_id} unit sizes must be positive and ordered")
        _validate_sources(estimate.source_ids, catalog.sources, f"Estimate {estimate_id}")

    conservative = catalog.estimate_profiles["conservative"]
    median = catalog.estimate_profiles["median"]
    progressive = catalog.estimate_profiles["progressive"]
    efficiencies = tuple(
        profile.single_stair_efficiency for profile in (conservative, median, progressive)
    )
    if efficiencies != tuple(sorted(efficiencies)):
        raise ValueError("Single-stair efficiency must increase with opportunity level")
    for category in BEDROOM_CATEGORIES:
        sizes = tuple(
            profile.unit_sizes_sqft[category] for profile in (conservative, median, progressive)
        )
        if sizes != tuple(sorted(sizes, reverse=True)):
            raise ValueError(f"Unit size for {category} must decrease with opportunity level")

    _validate_sources(
        catalog.bedroom_reporting.source_ids,
        catalog.sources,
        "Bedroom reporting",
    )


def load_scenario_catalog(config_path: Path | None = None) -> ScenarioCatalog:
    if config_path is None:
        config_file = files("single_stair").joinpath("config/building_scenarios.v1.json")
        payload = json.loads(config_file.read_text(encoding="utf-8"))
    else:
        payload = json.loads(config_path.read_text(encoding="utf-8"))

    sources = {source_id: Source(**values) for source_id, values in payload["sources"].items()}
    policies: dict[str, PolicyScenario] = {}
    for policy_id, original_values in payload["policies"].items():
        values = original_values.copy()
        source_ids = _tuple(values.pop("source_ids"))
        notes = _tuple(values.pop("notes"))
        policies[policy_id] = PolicyScenario(**values, source_ids=source_ids, notes=notes)

    lots: dict[str, LotArchetype] = {}
    for lot_id, original_values in payload["lot_archetypes"].items():
        values = original_values.copy()
        source_ids = _tuple(values.pop("source_ids"))
        lots[lot_id] = LotArchetype(**values, source_ids=source_ids)

    estimates: dict[str, EstimateProfile] = {}
    for estimate_id, original_values in payload["estimate_profiles"].items():
        values = original_values.copy()
        unit_sizes_sqft = {
            category: int(size) for category, size in values.pop("unit_sizes_sqft").items()
        }
        source_ids = _tuple(values.pop("source_ids"))
        estimates[estimate_id] = EstimateProfile(
            **values,
            unit_sizes_sqft=unit_sizes_sqft,
            source_ids=source_ids,
        )
    reporting_values = payload["bedroom_reporting"].copy()
    categories = _tuple(reporting_values.pop("categories"))
    reported_family_thresholds = tuple(
        int(value) for value in reporting_values.pop("reported_family_thresholds")
    )
    reporting_source_ids = _tuple(reporting_values.pop("source_ids"))
    reporting = BedroomReporting(
        **reporting_values,
        categories=categories,
        reported_family_thresholds=reported_family_thresholds,
        source_ids=reporting_source_ids,
    )
    catalog = ScenarioCatalog(
        config_version=payload["config_version"],
        title=payload["title"],
        default_policy_id=payload["defaults"]["policy"],
        default_estimate_id=payload["defaults"]["estimate"],
        sources=sources,
        policies=policies,
        lot_archetypes=lots,
        estimate_profiles=estimates,
        unit_size_evidence=payload["unit_size_evidence"],
        bedroom_reporting=reporting,
    )
    _validate_catalog(catalog)
    return catalog
