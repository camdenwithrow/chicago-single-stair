1. Set up the project
- [x] Create the Python project
- [x] Add .env configuration for Socrata and Census API keys.
- [x] Create raw, staged, and final data directories.
- [x] Add tests and formatting.
- [ ] Add a basic GitHub Actions workflow.
2. Ingest the data
- [x] Download Cook County parcel geometry.
- [x] Download Cook County single- and small-multifamily building characteristics.
- [x] Download current Chicago zoning.
- [x] Download Chicago building permits.
- [x] Download city-owned land.
- [x] Download CTA and Metra station locations.
- [x] Download Census data on families, bedrooms, overcrowding, and rent burden.
- [x] Implement reusable dated GeoParquet snapshots with manifests and validation.
- [x] Save dated, unmodified source snapshots as Parquet.
3. Clean and join everything
- [x] Standardize parcel PINs.
- [x] Select the latest record for each parcel and building.
- [x] Spatially assign zoning, Census tract, ward, and community area.
- [x] Calculate distance from each parcel to transit.
- [x] Test how many records successfully join.
4. Define building scenarios
- [ ] Define 25-foot and 50-foot Chicago lot archetypes.
- [ ] Define maximum stories and units per floor.
- [ ] Define single-stair and two-stair efficiency assumptions.
- [ ] Define low, middle, and high average unit sizes.
- [ ] Define “family-sized” as 3+ bedrooms.
- [ ] Store all assumptions in versioned configuration files.
5. Calculate parcel opportunity
- [ ] Calculate existing built FAR.
- [ ] Calculate maximum FAR under current zoning.
- [ ] Estimate units under current zoning.
- [ ] Estimate units under a modest-upzoning scenario.
- [ ] Estimate 3+ bedroom units under each scenario.
- [ ] Flag vacant, city-owned, and underbuilt parcels.
- [ ] Flag results that require additional legal or site review.
6. Calculate family housing need
- [ ] Calculate renter households with children by Census tract.
- [ ] Calculate 3+ bedroom rental supply.
- [ ] Calculate the family-housing supply gap.
- [ ] Calculate overcrowding and rent burden.
- [ ] Identify neighborhoods with high need and low large-unit supply.
7. Combine opportunity with need
- [ ] Find candidate parcels in high-need neighborhoods.
- [ ] Find candidates within 0.5 miles of transit.
- [ ] Find candidates on city-owned land.
- [ ] Aggregate results by community area, ward, zoning class, and transit band.
- [ ] Compare current zoning, single-stair, and upzoning scenarios.
8. Create the permit baseline
- [ ] Parse stories and dwelling units from permit descriptions.
- [ ] Identify recent small multifamily permits.
- [ ] Validate a sample manually.
- [ ] Aggregate permits by year, neighborhood, size, cost, and review time.
- [ ] Save these metrics for future before-and-after comparisons.
9. Build the visualization
- [ ] Create an interactive candidate-parcel map.
- [ ] Add filters for scenario, transit distance, zoning, and ownership.
- [ ] Add neighborhood family-housing-gap charts.
- [ ] Add current-zoning versus upzoning comparisons.
- [ ] Add a simple 25-foot/50-foot lot feasibility simulator.
- [ ] Display assumptions and data limitations clearly.
10. Publish the portfolio project
- [ ] Write a short methodology report.
- [ ] Publish five to ten defensible findings.
- [ ] Add a data dictionary and architecture diagram.
- [ ] Automate data refreshes and tests.
- [ ] Publish the report and visualization.
- [ ] Add screenshots, setup instructions, and the live link to the README.


2:39 PM
