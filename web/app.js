/* global maplibregl, pmtiles */
"use strict";

const state = { candidates: null, wards: null, neighborhoods: [], comparisons: [], metadata: null, map: null };
const $ = (id) => document.getElementById(id);
const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);

async function fetchGzipJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Could not load ${url}`);
  if (!("DecompressionStream" in window)) throw new Error("This browser cannot open gzip data.");
  const stream = response.body.pipeThrough(new DecompressionStream("gzip"));
  return new Response(stream).json();
}

function basemapStyle() {
  const apiKey = window.SINGLE_STAIR_CONFIG?.protomapsApiKey || "";
  if (apiKey) return `https://api.protomaps.com/styles/v5/light/en.json?key=${encodeURIComponent(apiKey)}`;
  const url = window.SINGLE_STAIR_CONFIG?.protomapsUrl || "";
  const style = { version: 8, sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": "#f3f1eb" } }] };
  if (!url) return style;
  if (url.endsWith(".pmtiles")) {
    const protocol = new pmtiles.Protocol();
    maplibregl.addProtocol("pmtiles", protocol.tile);
    style.sources.protomaps = { type: "vector", url: `pmtiles://${url}`, attribution: '<a href="https://protomaps.com">Protomaps</a> © <a href="https://openstreetmap.org">OpenStreetMap</a>' };
  } else {
    style.sources.protomaps = { type: "vector", tiles: [url], minzoom: 0, maxzoom: 15, attribution: '<a href="https://protomaps.com">Protomaps</a> © <a href="https://openstreetmap.org">OpenStreetMap</a>' };
  }
  style.layers.push(
    { id: "earth", type: "fill", source: "protomaps", "source-layer": "earth", paint: { "fill-color": "#f3f1eb" } },
    { id: "water", type: "fill", source: "protomaps", "source-layer": "water", paint: { "fill-color": "#cbdce6" } },
    { id: "roads", type: "line", source: "protomaps", "source-layer": "roads", paint: { "line-color": "#bbb", "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.3, 15, 2] } }
  );
  return style;
}

function scenarioField() { return $("scenario").value; }

function wardCapacityField() { return `${scenarioField()}_modeled_capacity_units`; }
function wardGainField() { return `${scenarioField()}_incremental_capacity_vs_current_two_stair_units`; }

function updateWardStyle() {
  if (!state.map?.getLayer("ward-fill")) return;
  const field = wardGainField();
  const values = state.wards.features.map((feature) => Number(feature.properties[field]) || 0);
  const maximum = Math.max(...values, 1);
  state.map.setPaintProperty("ward-fill", "fill-color", [
    "case", ["==", ["get", field], null], "#bdbdbd",
    ["interpolate", ["linear"], ["get", field],
      0, "#f3f1eb", maximum * 0.25, "#c6dbef", maximum * 0.6, "#6baed6", maximum, "#08519c"]
  ]);
  const total = values.reduce((sum, value) => sum + value, 0);
  $("map-count").textContent = `${number.format(total)} additional modeled 3-bedroom units citywide`;
  $("map-legend").innerHTML = `<strong>Additional 3-bedroom capacity</strong><div class="legend-ramp"></div><span>0</span><span style="float:right">${number.format(maximum)}</span><div>Gray: not modeled</div>`;
}

function updateMapView() {
  if (!state.map?.getLayer("ward-fill")) return;
  const wardView = $("map-view").value === "ward";
  for (const layer of ["ward-fill", "ward-outline"]) state.map.setLayoutProperty(layer, "visibility", wardView ? "visible" : "none");
  for (const layer of ["candidate-clusters", "candidate-points"]) state.map.setLayoutProperty(layer, "visibility", wardView ? "none" : "visible");
  for (const label of document.querySelectorAll(".parcel-filter")) {
    label.classList.toggle("is-disabled", wardView);
    for (const control of label.querySelectorAll("select,input")) control.disabled = wardView;
  }
  $("map-legend").hidden = !wardView;
  if (wardView) {
    updateWardStyle();
    state.map.fitBounds([[-87.95, 41.63], [-87.50, 42.03]], { padding: 24, duration: 0 });
  } else {
    applyFilters();
  }
}

function featureMatches(feature) {
  const p = feature.properties;
  if (!(Number(p[scenarioField()]) > 0)) return false;
  if (Number($("transit-distance").value) < 999999 && !(p.transit_distance_ft <= Number($("transit-distance").value))) return false;
  if ($("zoning").value !== "all" && p.zoning !== $("zoning").value) return false;
  if ($("ownership").value === "city" && !p.city_owned) return false;
  if ($("ownership").value === "private" && p.city_owned) return false;
  if ($("vacant").checked && !p.vacant) return false;
  if ($("underbuilt").checked && !p.underbuilt) return false;
  if ($("high-need").checked && !p.high_need_low_supply) return false;
  return true;
}

function applyFilters() {
  if ($("map-view").value === "ward") {
    updateWardStyle();
    return;
  }
  const features = state.candidates.features.filter(featureMatches);
  if (state.map?.getSource("candidates")) {
    state.map.getSource("candidates").setData({ type: "FeatureCollection", features });
  }
  $("map-count").textContent = `${number.format(features.length)} parcels match`;
}

function parcelDetails(properties) {
  const rows = [
    ["PIN", properties.pin || "Unavailable"], ["Community", properties.community || "Unavailable"],
    ["Current zoning", properties.zoning || "Unsupported"], ["Upzoned class", properties.upzoned_zoning || "Not modeled"],
    ["Transit", properties.transit_distance_ft == null ? "Unavailable" : `${number.format(properties.transit_distance_ft)} ft · ${properties.transit_agency || "nearest"}`],
    ["City-owned", properties.city_owned ? "Yes" : "No"], ["Vacant flag", properties.vacant ? "Yes" : "No"],
    ["Underbuilt flag", properties.underbuilt ? "Yes" : "No"], ["Current / two stair", properties.current_two_stair ?? "—"],
    ["Current / single stair", properties.current_single_stair ?? "—"], ["Upzoned / single stair", properties.upzoned_single_stair ?? "—"],
    ["Legal/site review", properties.requires_review ? properties.review_reasons || "Required" : "No automated reason"]
  ];
  $("parcel-details").innerHTML = `<dl>${rows.map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`).join("")}</dl><p><small>Capacities use the 3-bedroom archetype. A map point is a parcel centroid.</small></p>`;
}

function wardDetails(properties) {
  const scenarioNames = {
    current_two_stair: "Current zoning · two stair",
    current_single_stair: "Current zoning · single stair",
    upzoned_single_stair: "Modest upzoning · single stair"
  };
  const capacity = properties[wardCapacityField()];
  const gain = properties[wardGainField()];
  const displayMetric = (value) => value == null ? "Not modeled" : number.format(value);
  $("parcel-details").innerHTML = `<dl><dt>Ward</dt><dd>${escapeHtml(properties.ward)}</dd><dt>Scenario</dt><dd>${escapeHtml(scenarioNames[scenarioField()])}</dd><dt>Modeled 3-bedroom capacity</dt><dd>${displayMetric(capacity)}</dd><dt>Additional vs. current two stair</dt><dd>${displayMetric(gain)}</dd></dl><p><small>Ward totals aggregate parcel-level analytical capacity. Select “Parcel detail” to inspect individual sites.</small></p>`;
}

function initializeMap() {
  state.map = new maplibregl.Map({ container: "map", style: basemapStyle(), center: [-87.68, 41.84], zoom: 9.6, attributionControl: true });
  state.map.addControl(new maplibregl.NavigationControl(), "top-right");
  state.map.on("load", () => {
    state.map.addSource("wards", { type: "geojson", data: state.wards });
    state.map.addLayer({ id: "ward-fill", type: "fill", source: "wards", paint: { "fill-color": "#c6dbef", "fill-opacity": 0.72 } });
    state.map.addLayer({ id: "ward-outline", type: "line", source: "wards", paint: { "line-color": "#444", "line-width": 1 } });
    state.map.addSource("candidates", { type: "geojson", data: { type: "FeatureCollection", features: [] }, cluster: true, clusterRadius: 35, clusterMaxZoom: 13 });
    state.map.addLayer({ id: "candidate-clusters", type: "circle", source: "candidates", filter: ["has", "point_count"], paint: { "circle-color": "#365f91", "circle-opacity": 0.75, "circle-radius": ["step", ["get", "point_count"], 9, 100, 14, 1000, 19] } });
    state.map.addLayer({ id: "candidate-points", type: "circle", source: "candidates", filter: ["!", ["has", "point_count"]], paint: { "circle-color": ["case", ["get", "city_owned"], "#7b3294", ["get", "vacant"], "#d95f0e", "#365f91"], "circle-opacity": 0.7, "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2, 15, 6], "circle-stroke-color": "#fff", "circle-stroke-width": 0.5 } });
    state.map.on("click", "candidate-clusters", async (event) => {
      const feature = state.map.queryRenderedFeatures(event.point, { layers: ["candidate-clusters"] })[0];
      const zoom = await state.map.getSource("candidates").getClusterExpansionZoom(feature.properties.cluster_id);
      state.map.easeTo({ center: feature.geometry.coordinates, zoom });
    });
    state.map.on("click", "candidate-points", (event) => parcelDetails(event.features[0].properties));
    state.map.on("click", "ward-fill", (event) => wardDetails(event.features[0].properties));
    state.map.on("mouseenter", "ward-fill", () => { state.map.getCanvas().style.cursor = "pointer"; });
    state.map.on("mouseleave", "ward-fill", () => { state.map.getCanvas().style.cursor = ""; });
    state.map.on("mouseenter", "candidate-points", () => { state.map.getCanvas().style.cursor = "pointer"; });
    state.map.on("mouseleave", "candidate-points", () => { state.map.getCanvas().style.cursor = ""; });
    updateMapView();
  });
  if (!window.SINGLE_STAIR_CONFIG?.protomapsUrl && !window.SINGLE_STAIR_CONFIG?.protomapsApiKey) {
    $("map-message").hidden = false;
    $("map-message").textContent = "Ward and parcel data are shown without a basemap. Add a domain-restricted Protomaps API key or self-hosted tile URL in config.js.";
  }
}

function renderNeedChart() {
  const field = `gap_${$("need-estimate").value}`;
  const rows = [...state.neighborhoods].filter((row) => row[field] != null).sort((a, b) => b[field] - a[field]).slice(0, 15);
  const max = Math.max(...rows.map((row) => row[field]), 1);
  $("need-chart").innerHTML = rows.map((row) => `<span>${escapeHtml(row.community_area_name)}</span><div class="bar-track"><div class="bar" style="width:${Math.max(0, row[field]) / max * 100}%"></div></div><span class="bar-value">${number.format(row[field])}</span>`).join("");
}

function renderComparison() {
  const community = $("comparison-community").value;
  const rows = state.comparisons.filter((row) => String(row.community_area_number) === community);
  const names = { current_two_stair: "Current zoning · two stair", current_single_stair: "Current zoning · single stair", upzoned_single_stair: "Modest upzoning · single stair" };
  $("comparison-chart").innerHTML = rows.map((row) => `<article><h3>${names[row.capacity_scenario_id] || row.capacity_scenario_id}</h3><strong>${number.format(row.modeled_capacity_units || 0)}</strong><span>modeled 3-bedroom units</span><p>${number.format(row.incremental_capacity_vs_current_two_stair_units || 0)} versus current two-stair</p></article>`).join("");
}

function renderSimulator() {
  const width = Number($("lot-width").value), depth = Number($("lot-depth").value), far = Number($("lot-far").value);
  const estimate = state.metadata.scenarios.estimate_profiles[$("estimate-profile").value];
  const policy = state.metadata.scenarios.policies[$("policy-profile").value];
  const unitSize = estimate.unit_sizes_sqft[$("bedrooms").value];
  const grossArea = width * depth * far;
  const buildingLimit = policy.maximum_stories_above_grade * policy.maximum_units_per_story;
  const twoStair = Math.max(0, Math.min(Math.floor(grossArea * estimate.two_stair_efficiency / unitSize), buildingLimit));
  const singleStair = Math.max(0, Math.min(Math.floor(grossArea * estimate.single_stair_efficiency / unitSize), buildingLimit));
  $("simulator-results").innerHTML = `<div><strong>${number.format(grossArea)}</strong>gross sq ft</div><div><strong>${twoStair}</strong>two-stair units</div><div><strong>${singleStair}</strong>single-stair units</div><div><strong>${singleStair - twoStair}</strong>modeled difference</div>`;
}

function populateControls() {
  const zones = [...new Set(state.candidates.features.map((feature) => feature.properties.zoning).filter(Boolean))].sort();
  $("zoning").insertAdjacentHTML("beforeend", zones.map((zone) => `<option value="${escapeHtml(zone)}">${escapeHtml(zone)}</option>`).join(""));
  const communities = [...new Map(state.comparisons.map((row) => [String(row.community_area_number), row.community_area_name])).entries()].sort((a, b) => Number(a[0]) - Number(b[0]));
  $("comparison-community").innerHTML = communities.map(([id, name]) => `<option value="${escapeHtml(id)}">${escapeHtml(name)}</option>`).join("");
  $("comparison-community").value = communities.find((entry) => entry[1] === "LOGAN SQUARE")?.[0] || communities[0]?.[0];
  $("map-view").addEventListener("change", updateMapView);
  $("scenario").addEventListener("change", applyFilters);
  for (const id of ["transit-distance", "zoning", "ownership", "vacant", "underbuilt", "high-need"]) $(id).addEventListener("change", applyFilters);
  $("need-estimate").addEventListener("change", renderNeedChart);
  $("comparison-community").addEventListener("change", renderComparison);
  for (const id of ["lot-width", "lot-depth", "lot-far", "bedrooms", "estimate-profile", "policy-profile"]) $(id).addEventListener("input", renderSimulator);
}

function renderMethodology() {
  const limitations = state.metadata.limitations.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  $("methodology").innerHTML = `<p><strong>Candidate definition:</strong> ${escapeHtml(state.metadata.candidate_definition)}.</p><p><strong>Default analysis:</strong> ${escapeHtml(state.metadata.policy_id)} / ${escapeHtml(state.metadata.estimate_id)}.</p><ul>${limitations}</ul><p>Protomaps provides map context from OpenStreetMap-derived tiles. Candidate points and analytical values come from the project pipeline.</p>`;
}

async function main() {
  try {
    [state.candidates, state.wards, state.neighborhoods, state.comparisons, state.metadata] = await Promise.all([
      fetchGzipJson("data/candidates.geojson.gz"), fetch("data/wards.geojson").then((r) => r.json()), fetch("data/neighborhoods.json").then((r) => r.json()),
      fetch("data/comparisons.json").then((r) => r.json()), fetch("data/metadata.json").then((r) => r.json())
    ]);
    populateControls(); initializeMap(); renderNeedChart(); renderComparison(); renderSimulator(); renderMethodology();
    $("status").textContent = `${number.format(state.metadata.candidate_count)} candidate parcels · generated ${new Date(state.metadata.generated_at).toLocaleDateString()}`;
  } catch (error) {
    $("status").textContent = `Visualization data unavailable: ${error.message}`;
  }
}

main();
