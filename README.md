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
the CLI, or load `.env` with `uv run --env-file .env single-stair ...` (an environment-file
runner such as dotenvx also works).

## Raw ingestion

```bash
uv run single-stair ingest cook-county-parcels
uv run single-stair ingest cook-county-buildings --tax-year 2025
uv run single-stair ingest chicago-zoning
uv run single-stair ingest chicago-boundaries
uv run single-stair ingest chicago-building-permits
uv run single-stair ingest chicago-city-land
uv run single-stair ingest transit-stations
uv run single-stair ingest census-housing --year 2024
uv run single-stair ingest bls-chicago-cpi
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
- Chicago wards: one current ward polygon per numeric `ward` from dataset `p293-wvbd`.
- Chicago community areas: one current community-area polygon per `area_num_1` from dataset
  `igwz-8jzy`.
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
- Chicago-area CPI: one monthly observation per year and period from BLS series `CUURS23ASA0`,
  used to express permit costs in constant 2025 dollars. No API key is required.

Socrata tables use keyset pagination against a fixed upper key and validate the final count. GTFS
and Census boundary manifests include the downloaded archive's SHA-256 checksum. API credentials
are never written to requests in manifests or to Parquet files.

## Staged transformation

After all raw sources have been ingested, build the analysis-ready parcel table:

```bash
uv run single-stair transform clean-and-join
```

This creates two dated datasets under `data/staged/`:

- `building_characteristics_latest`: one latest-tax-year record per Assessor `(pin, card)`.
- `parcel_context`: one row per Cook County parcel `objectid` whose centroid falls within a
  Chicago community area. It includes a standardized 14-digit PIN, the parcel centroid, current
  zoning, Census tract, ward, community area, nearest CTA and Metra stations, and joined building
  card counts.

Spatial calculations use Illinois StatePlane East (EPSG:3435). Boundary assignments and transit
distances are measured from each parcel centroid; distances are stored in US survey feet. The
output geometry is converted back to WGS84 (EPSG:4326) for mapping. Each staged snapshot includes
a manifest with source snapshots, row counts, join coverage, and the transformation method. Invalid
source parcel geometry is repaired in the staged layer, and output is compacted into parts of
approximately 50,000 rows to avoid carrying the raw API's small-file layout into analytics.

## Checks

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run python -m unittest discover -s tests -v
```

## Building scenarios

Inspect the default Chicago-proposal and median-estimate assumptions with:

```bash
uv run single-stair scenarios show
```

Select the proposed Illinois alternative and a different evidence-backed estimate level with:

```bash
uv run single-stair scenarios show --policy illinois_sb4061 --estimate conservative
```

The versioned scenario configuration separates fixed policy limits from analytical estimates.
Chicago's proposal is the default and allows up to five stories above grade and four units per
story; the Illinois SB4061 alternative allows up to six stories and four units per story if
enacted. Both are proposals, not current legal entitlements.

The estimate profiles apply a common interpretation throughout the model:

- `conservative` uses the smallest observed stair-efficiency gain and spacious unit assumptions.
- `median` uses the median prototype efficiency gain and weighted median AHS size ranges.
- `progressive` uses the largest observed efficiency gain and IHDA minimum unit sizes.

Every profile reports studio through five-plus-bedroom units. The headline family-sized measure
is three-plus bedrooms, with two-plus and four-plus results retained for comparison. Sources,
sample sizes, fallbacks, and limitations are stored with the assumptions in
`src/single_stair/config/building_scenarios.v1.json`. These scenarios are analytical capacity
tests, not architectural designs or determinations that a parcel is legally buildable.

## Parcel opportunity

After building the staged parcel context, calculate the default Chicago-proposal and median
opportunity snapshot with:

```bash
uv run single-stair transform parcel-opportunity
```

Use `--policy` and `--estimate` to materialize another configured combination. The output is one
GeoParquet row per parcel under `data/final/parcel_opportunity_<policy>_<estimate>/`. It includes
existing and allowed FAR, current- and upzoned-density limits, city ownership, conservative vacancy
and underbuilt flags, and independent capacity estimates for every bedroom size under current
two-stair, current single-stair, and modest-upzoning single-stair conditions.

The modest-upzoning scenario advances supported residential districts by one density step and B/C
districts by one dash suffix. It is an analytical comparison rather than a rezoning recommendation.
Unsupported zoning, conditional residential uses, missing Assessor coverage, unitized parcels, and
nonstandard parcel types are retained with explicit review reasons. Missing small-residential
building characteristics are never treated as proof that a parcel is vacant.
Existing building area uses the Assessor tieback proration when a building is allocated across tax
records, and material disagreement between Assessor and mapped parcel area is flagged for review.

## Family housing need

Build the tract-level family-housing-need snapshot after ingesting Census housing data and creating
the staged parcel context:

```bash
uv run single-stair transform family-housing-need
```

The output is one GeoParquet row per Chicago Census tract under
`data/final/family_housing_need/`. It reports renter families with own children under 18, occupied
renter units from studio through five-plus bedrooms, 2+/3+/4+ bedroom supply and gap measures,
overcrowding, severe overcrowding, rent burden, and severe rent burden. Estimates retain derived
90% margins of error. The project owns this derived dataset; the U.S. Census Bureau owns the five
source tables and tract geometry. Its grain and primary key are one row per `census_tract_geoid`.

Three need views expose ACS uncertainty: `conservative` uses the lower bound, `median` uses the
published point estimate, and `progressive` uses the upper bound. Each view includes percentile
components and an equal-weight need score. The high-need/low-supply flag requires both the renter
family-with-children share and inverse 3+ bedroom share to rank in Chicago's highest quartile, and
requires the headline count margins of error not to exceed their estimates.

This is a relative screening measure, not a determination that a household occupies an unsuitable
unit. The bedroom inventory includes occupied renter units only and does not measure current
availability, affordability, or condition. Definitions, official Census sources, uncertainty
methods, and limitations are versioned in
`src/single_stair/config/family_housing_need.v1.json`.

## Combined opportunity and need

After creating the parcel-opportunity and family-housing-need snapshots, combine them with:

```bash
uv run single-stair transform combine-opportunity-need
```

Use `--policy` and `--estimate` to select a previously materialized parcel-opportunity snapshot.
The command writes one GeoParquet row per source parcel to
`data/final/parcel_opportunity_with_need_<policy>_<estimate>/` and separate long-form Parquet
summaries for community area, ward, canonical zoning class, and transit band.

The parcel output is deliberately neutral: every source parcel remains in the research universe.
Need, half-mile transit proximity, city ownership, vacancy, underbuilt status, review requirements,
and capacity changes are independent fields rather than inputs to a composite recommendation. This
allows the visualization to show how results change as each screen is applied or removed.

Each summary compares current-zoning two-stair, current-zoning single-stair, and modest-upzoning
single-stair capacity for studio through five-plus-bedroom archetypes. Transit bands are 0-0.25,
0.25-0.5, 0.5-1, and more than 1 mile. Tract-level Census values repeated on parcels are for
filtering only and must not be summed; neighborhood need totals remain at their authoritative tract
grain in `family_housing_need`. The versioned combined-analysis configuration records this contract
and its limitations.

## Permit baseline

After ingesting permits, community-area boundaries, and Chicago-area CPI, build the baseline with:

```bash
uv run single-stair transform permit-baseline
```

The record-level output has one row per issued permit from 2015 onward. Rules parse stories,
existing and proposed dwelling units, and net added units from the public work description while
retaining confidence and review-reason fields. The 2–20-unit headline includes both new
construction and unit-adding alterations, and keeps those production types separate. It does not
count ordinary work merely because a description says it affects dwelling units.

Separate final datasets summarize permit count, net units, nominal and 2025-dollar reported cost,
and review time by year/size, community area, and review type. Completed years are 2015–2025;
2026 is retained as partial. A permit-system-era dimension identifies the 2024 source transition.
Review time is recalculated from application start to issue date, with negative durations excluded
and agreement with the published processing field retained for auditing.

The classification was manually checked against a deterministic, stratified 100-permit sample in
`src/single_stair/config/permit_validation.v1.csv`; validation metrics are materialized in
`permit_baseline_validation`. Counts describe permits issued, not construction completions, and
reported costs are applicant values rather than independently verified project costs. Parser
assumptions, official sources, and limitations are versioned in
`src/single_stair/config/permit_baseline.v1.json`.

## Visualization

Export browser-ready data from the latest analytical snapshots and serve the static app locally:

```bash
uv run single-stair visualize export
uv run --env-file .env single-stair visualize configure
python3 -m http.server 8000 --directory web
```

Then open `http://localhost:8000`. Generated files under `web/data/` are excluded from Git. The
candidate GeoJSON contains centroids for every modeled parcel independently flagged vacant,
underbuilt, or city-owned; it is gzip-compressed to approximately 4 MB for browser delivery. This
is a transparent research universe rather than a ranked recommendation list.

The map defaults to an official ward-boundary overview of additional modeled 3-bedroom capacity
under current zoning with single-stair construction, compared with the current two-stair baseline.
Switch to the community-area overview for the same metrics across Chicago's 77 community areas, or
to parcel detail for the existing scenario, transit-distance, zoning, ownership, vacancy,
underbuilt, and family-need filters. The dashboard also includes neighborhood 3+ bedroom gap bars,
community-area capacity comparisons, hover details anchored to map features, and an
assumption-driven lot simulator. Map filters remain in a compact overlay on the map. The
simulator defaults to a 25-by-125-foot lot; change the width to 50 feet for the second documented
archetype.

Set `PROTOMAPS_API_KEY` in the root `.env` to use the hosted Protomaps v5 light style. Alternatively,
set `PROTOMAPS_URL` to a self-hosted PMTiles archive or compatible ZXY vector endpoint. When both
are set, the API key takes precedence. Both settings are optional; leave them empty for analytical
geometry without a basemap.

The `visualize configure` command generates git-ignored `web/config.js` with only these two public
map settings. It does not require data snapshots and does not export Census or Socrata credentials.
Run it again after changing the environment, then hard-refresh the page. The data export command
does not change map configuration. In CI, inject the same variables and run
`uv run single-stair visualize configure` before publishing `web/`; serve only `web/`, never the
repository root or `.env`. Use `--output path/to/config.js` for another static output directory.

This keeps credentials out of Git, not out of the browser: the generated map key is still public.
Restrict it to the deployed domains in the Protomaps account. Existing installations should move
the values from the old hand-edited `web/config.js` into `.env` before regenerating it; absent
variables generate empty values rather than preserving stale keys. See the official
[Protomaps hosted API](https://protomaps.com/api)
and [MapLibre integration](https://docs.protomaps.com/pmtiles/maplibre) documentation.
