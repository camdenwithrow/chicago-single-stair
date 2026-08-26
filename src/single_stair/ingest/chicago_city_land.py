from datetime import date
from pathlib import Path

from single_stair.ingest.socrata_table import (
    ProgressCallback,
    SocrataTable,
    ingest_socrata_table,
)

CITY_OWNED_LAND = SocrataTable(
    dataset="chicago_city_owned_land",
    dataset_id="aksk-kvfp",
    domain="data.cityofchicago.org",
    key="id",
    grain=("id",),
)


async def ingest_chicago_city_owned_land(
    *,
    raw_root: Path = Path("data/raw"),
    snapshot_date: date | None = None,
    page_size: int = 50_000,
    progress: ProgressCallback | None = None,
) -> Path:
    return await ingest_socrata_table(
        CITY_OWNED_LAND,
        raw_root=raw_root,
        snapshot_date=snapshot_date,
        page_size=page_size,
        progress=progress,
    )
