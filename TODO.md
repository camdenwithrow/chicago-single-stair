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
- [x] Define 25-foot and 50-foot Chicago lot archetypes.
- [x] Define maximum stories and units per floor.
- [x] Define single-stair and two-stair efficiency assumptions.
- [x] Define conservative, median, and progressive unit-size assumptions.
- [x] Use 3+ bedrooms as the headline family-sized measure and report 2+ and 4+.
- [x] Store all assumptions and their sources in versioned configuration files.
5. Calculate parcel opportunity
- [x] Calculate existing built FAR.
- [x] Calculate maximum FAR under current zoning.
- [x] Estimate units under current zoning.
- [x] Estimate units under a modest-upzoning scenario.
- [x] Estimate 3+ bedroom units under each scenario.
- [x] Flag vacant, city-owned, and underbuilt parcels.
- [x] Flag results that require additional legal or site review.
6. Calculate family housing need
- [x] Calculate renter households with children by Census tract.
- [x] Calculate 3+ bedroom rental supply.
- [x] Calculate the family-housing supply gap.
- [x] Calculate overcrowding and rent burden.
- [x] Identify neighborhoods with high need and low large-unit supply.
7. Combine opportunity with need
- [x] Find candidate parcels in high-need neighborhoods.
- [x] Find candidates within 0.5 miles of transit.
- [x] Find candidates on city-owned land.
- [x] Aggregate results by community area, ward, zoning class, and transit band.
- [x] Compare current zoning, single-stair, and upzoning scenarios.
8. Create the permit baseline
- [ ] Parse stories and dwelling units from permit descriptions.
- [ ] Identify recent small multifamily permits.
- [ ] Validate a sample manually.
- [ ] Aggregate permits by year, neighborhood, size, cost, and review time.
- [ ] Save these metrics for future before-and-after comparisons.
9. Build the visualization
** Keep the ui as simple as possible, do not embellish with over-styling, should
be purely utilitarian, presenting the information. Use protomaps api for map tiles**
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
