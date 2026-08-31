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
