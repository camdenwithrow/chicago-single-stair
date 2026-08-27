import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from single_stair.ingest.socrata_geometry import (
    SocrataGeometryTable,
    ingest_socrata_geometry_table,
)
from single_stair.ingest.socrata_table import SocrataTable, TableBatch

WARD_BOUNDARIES = SocrataGeometryTable(
    table=SocrataTable(
        dataset="chicago_ward_boundaries",
        dataset_id="p293-wvbd",
        domain="data.cityofchicago.org",
        key="ward",
        grain=("ward",),
        key_type="number",
    ),
    geometry_field="the_geom",
    required_fields=("ward",),
)

COMMUNITY_AREA_BOUNDARIES = SocrataGeometryTable(
    table=SocrataTable(
        dataset="chicago_community_area_boundaries",
        dataset_id="igwz-8jzy",
        domain="data.cityofchicago.org",
        key="area_num_1",
        grain=("area_num_1",),
    ),
    geometry_field="the_geom",
    required_fields=("area_num_1", "community"),
)


@dataclass(frozen=True, slots=True)
class BoundarySnapshots:
    wards: Path
    community_areas: Path

    @property
    def paths(self) -> tuple[Path, Path]:
        return self.wards, self.community_areas


ProgressCallback = Callable[[str, TableBatch], None]


async def ingest_chicago_boundaries(
    *,
    raw_root: Path = Path("data/raw"),
    snapshot_date: date | None = None,
    progress: ProgressCallback | None = None,
) -> BoundarySnapshots:
    def callback(dataset: str):
        return None if progress is None else lambda batch: progress(dataset, batch)

    wards, community_areas = await asyncio.gather(
        ingest_socrata_geometry_table(
            WARD_BOUNDARIES,
            raw_root=raw_root,
            snapshot_date=snapshot_date,
            progress=callback("wards"),
        ),
        ingest_socrata_geometry_table(
            COMMUNITY_AREA_BOUNDARIES,
            raw_root=raw_root,
            snapshot_date=snapshot_date,
            progress=callback("community areas"),
        ),
    )
    return BoundarySnapshots(wards=wards, community_areas=community_areas)
