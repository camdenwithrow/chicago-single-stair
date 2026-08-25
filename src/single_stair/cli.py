import asyncio

from single_stair.ingest.cook_county_parcels import ParcelBatch, ingest_cook_county_parcels


def _report_progress(batch: ParcelBatch, feature_count: int) -> None:
    print(f"Saved batch {batch.number:,}/{batch.total:,} ({feature_count:,} parcels)")


async def _run() -> None:
    snapshot_path = await ingest_cook_county_parcels(progress=_report_progress)
    print(f"Completed snapshot: {snapshot_path}")


def main() -> None:
    asyncio.run(_run())
