# Chicago Single-Stair Housing Analysis

A data-engineering project that combines Cook County parcel and building data with Chicago
zoning, transit, permits, public land, and Census housing indicators.

## Setup

```bash
uv sync
```

Copy `.env.example` to `.env` and replace only the credentials you use. `SOCRATA_APP_TOKEN` is
optional and is shared by the Cook County and Chicago public-data requests. The Socrata secret
token is intentionally not used: these are anonymous, read-only SODA requests that only require
the app token for identified-client rate limits. The Census API requires `CENSUS_API_KEY`; request a key from
[api.census.gov](https://api.census.gov/data/key_signup.html). Export the variables before running
the CLI, or use an environment-file runner such as dotenvx.

## Raw ingestion

```bash
uv run single-stair ingest cook-county-parcels
uv run single-stair ingest cook-county-buildings --tax-year 2025
uv run single-stair ingest chicago-zoning
uv run single-stair ingest chicago-building-permits
uv run single-stair ingest chicago-city-land
uv run single-stair ingest transit-stations
uv run single-stair ingest census-housing --year 2024
```

Omit `--tax-year` to use the latest year exposed by the Assessor dataset. The latest tax year may
still be provisional, so published analysis should always identify the selected year.

Successful ingestions create immutable snapshots under `data/raw/<dataset>/snapshot_date=YYYY-MM-DD/`.
Each snapshot contains Parquet parts and a manifest with source, grain, request boundary, record
counts, and checksums. Generated data is intentionally excluded from Git.

The building-characteristics source is improvement-level, uniquely identified by PIN, tax year,
and card. It covers Assessor single- and multi-family classes with fewer than seven units; it is not
a complete inventory of condominium or large commercial buildings.

The ingestion sources and raw data contracts are:

- Chicago zoning: one polygon per source `objectid` from dataset `dj47-wfun`.
- Chicago building permits: one permit database record per unique string `id` from `ydr8-5enu`.
  The key is deliberately not cast to an integer because the source includes prefixed IDs.
- City-owned land: one inventory property per unique `id` from `aksk-kvfp`, retaining source PIN,
  ownership attributes, coordinates, and location object.
- CTA stations: `location_type=1` parent-station rows from the official static GTFS `stops.txt`.
- Metra stations: every row from the official static GTFS `stops.txt`; Metra publishes stations
  directly rather than platform/parent records.
- Census housing: one row per Cook County tract for each selected ACS 5-year detailed table. Both
  estimates and margins of error are retained for `B11005` and `B25115` (families and children),
  `B25042` (bedrooms by tenure), `B25014` (occupants per room), and `B25070` (rent burden). A
  separate Cook County full-resolution TIGER/Line tract-geometry snapshot retains the native
  Census boundary CRS and includes water-only tract 9900 so its keys align with the ACS tables.

Socrata tables use keyset pagination against a fixed upper key and validate the final count. GTFS
and Census boundary manifests include the downloaded archive's SHA-256 checksum. API credentials
are never written to requests in manifests or to Parquet files.

## Checks

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run python -m unittest discover -s tests -v
```
