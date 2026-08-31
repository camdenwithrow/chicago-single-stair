// Run with: node --test tests/test_map_filters.cjs
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function appContext() {
  const controls = Object.fromEntries([
    ["scenario", "current_single_stair"], ["transit-distance", "999999"],
    ["zoning", "all"], ["ownership", "all"], ["map-view", "parcel"],
    ["vacant", ""], ["underbuilt", ""], ["high-need", ""], ["status", ""]
  ].map(([id, value]) => [id, { value, checked: false }]));
  const context = vm.createContext({
    Intl, document: { getElementById: (id) => controls[id] },
    fetch: () => new Promise(() => {}),
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8"), context);
  return { context, controls };
}

test("parcel screen uses boolean coverage, not capacity or vacancy", () => {
  const { context, controls } = appContext();
  context.parcel = { properties: { current_single_stair: true, vacant: false, transit_distance_ft: null } };
  assert.equal(vm.runInContext("featureMatches(parcel)", context), true);
  controls["transit-distance"].value = "2640";
  assert.equal(vm.runInContext("featureMatches(parcel)", context), false);
  controls["transit-distance"].value = "999999";
  controls.vacant.checked = true;
  assert.equal(vm.runInContext("featureMatches(parcel)", context), false);
  controls.vacant.checked = false;
  context.parcel.properties.current_single_stair = 12;
  assert.equal(vm.runInContext("featureMatches(parcel)", context), false);
});

test("second scenario membership works without changing map code", () => {
  const { context, controls } = appContext();
  controls.scenario.value = "illinois_build";
  context.parcel = { properties: { current_single_stair: false, illinois_build: true } };
  assert.equal(vm.runInContext("featureMatches(parcel)", context), true);
  assert.equal(vm.runInContext("areaCountField()", context), "illinois_build_parcel_count");
});

test("legacy exports fail with actionable regeneration instruction", async () => {
  const { context, controls } = appContext();
  context.fetch = async () => ({ ok: true, json: async () => ({ candidate_count: 12 }) });
  await vm.runInContext("main()", context);
  assert.match(controls.status.textContent, /outdated.*uv run single-stair visualize export/);
});

test("BUILD rejects baseline-only exports with regeneration instruction", async () => {
  const { context, controls } = appContext();
  context.fetch = async () => ({ ok: true, json: async () => ({map_schema_version: 2, map_scenarios: [{id: "current_single_stair"}]}) });
  await vm.runInContext("main()", context);
  assert.match(controls.status.textContent, /outdated for BUILD.*visualize export/);
});

test("BUILD parcel details accept serialized review arrays and distinguish comparison basis", () => {
  const { context, controls } = appContext();
  controls.scenario.value = "illinois_build";
  vm.runInContext("state.metadata = {map_scenarios: [{id:'illinois_build',label:'With IL BUILD (proposed)'}]}", context);
  context.properties = {build_category: "screened_expansion", build_effective_unit_limit: 4, build_existing_unit_comparator: 1, build_existing_unit_limit_basis: "rs_detached_district_ceiling", build_review_reasons: "[]"};
  let html = vm.runInContext("parcelDetailsHtml(properties)", context);
  assert.match(html, /source lot-area formula returned zero/);
  assert.doesNotMatch(html, /Review required/);
  context.properties.build_review_reasons = '["detached_use_preservation_and_transit_review"]';
  html = vm.runInContext("parcelDetailsHtml(properties)", context);
  assert.match(html, /detached use preservation and transit review/);
});

test("BUILD overview popup describes footprint group counts, not a parcel allowance", () => {
  const { context } = appContext();
  context.properties = {coverage_kind: "build_added_footprint", zoning: "RS-3", ward: "1", parcel_count: 120};
  const html = vm.runInContext("zoningDetailsHtml(properties)", context);
  assert.match(html, /120/);
  assert.match(html, /ward\/district group/);
  assert.match(html, /Switch to Parcel detail/);
  assert.doesNotMatch(html, /Proposed middle-housing allowance/);
});

test("detail loads are deferred, shared by concurrent requests, and cached", async () => {
  const { context } = appContext();
  let calls = 0;
  let resolve;
  context.fetchGzipJson = () => { calls++; return new Promise((done) => { resolve = done; }); };
  assert.equal(calls, 0);
  const first = vm.runInContext("loadMapDetail('zoning')", context);
  const second = vm.runInContext("loadMapDetail('zoning')", context);
  assert.equal(calls, 1);
  resolve({type: "FeatureCollection", features: []});
  await Promise.all([first, second]);
  await vm.runInContext("loadMapDetail('zoning')", context);
  assert.equal(calls, 1);
});

test("failed detail downloads can be retried", async () => {
  const { context } = appContext();
  context.fetchGzipJson = async () => { throw new Error("offline"); };
  await assert.rejects(vm.runInContext("loadMapDetail('zoning')", context), /offline/);
  context.fetchGzipJson = async () => ({type: "FeatureCollection", features: []});
  await vm.runInContext("loadMapDetail('zoning')", context);
  assert.equal(vm.runInContext("state.zoningCoverage.features.length", context), 0);
});
