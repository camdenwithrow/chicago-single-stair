# Chicago Single-Stair Housing Analysis

A data-engineering project that combines Cook County parcel and building data with Chicago
zoning, transit, permits, public land, and Census housing indicators.

## Setup

```bash
uv sync
```

Cook County's parcel and assessor APIs are public. An optional Socrata app token can be supplied
through `COOK_SOCRATA_APP_TOKEN` to receive identified-client rate limits.

## Raw ingestion

```bash
uv run single-stair ingest cook-county-parcels
uv run single-stair ingest cook-county-buildings --tax-year 2025
```

Omit `--tax-year` to use the latest year exposed by the Assessor dataset. The latest tax year may
still be provisional, so published analysis should always identify the selected year.

Successful ingestions create immutable snapshots under `data/raw/<dataset>/snapshot_date=YYYY-MM-DD/`.
Each snapshot contains Parquet parts and a manifest with source, grain, request boundary, record
counts, and checksums. Generated data is intentionally excluded from Git.

The building-characteristics source is improvement-level, uniquely identified by PIN, tax year,
and card. It covers Assessor single- and multi-family classes with fewer than seven units; it is not
a complete inventory of condominium or large commercial buildings.

## Checks

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run python -m unittest discover -s tests -v
```
