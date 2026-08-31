/* global maplibregl, pmtiles */
"use strict";

const state = { candidates: null, zoningCoverage: null, wards: null, communityAreas: null, neighborhoods: [], comparisons: [], metadata: null, map: null, popup: null };
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

function areaCountField() { return `${scenarioField()}_parcel_count`; }
function scenarioLabel() { return state.metadata.map_scenarios.find((scenario) => scenario.id === scenarioField())?.label || scenarioField(); }
function tierLabel(tier) { return state.metadata.map_scenarios.find((scenario) => scenario.id === "current_single_stair")?.tier_labels?.[tier] || "BUILD-added parcel screen"; }
const tierColors = { res_7: "#c6dbef", res_10: "#6baed6", res_15: "#08519c", com_7: "#bdbdbd", com_15: "#7b3294" };

function updateScenarioNote() {
  $("map-scenario-note").textContent = state.metadata.map_scenarios.find((scenario) => scenario.id === scenarioField())?.description || "";
}

function overviewConfig() {
  return $("map-view").value === "community"
    ? { data: state.communityAreas, fillLayer: "community-fill", label: "community area" }
    : { data: state.wards, fillLayer: "ward-fill", label: "ward" };
}

function updateOverviewStyle() {
  const overview = overviewConfig();
  if (!state.map?.getLayer(overview.fillLayer)) return;
  const field = areaCountField();
  const values = overview.data.features.map((feature) => Number(feature.properties[field]) || 0);
  const maximum = Math.max(...values, 1);
  state.map.setPaintProperty(overview.fillLayer, "fill-color", [
    "case", ["==", ["get", field], null], "#bdbdbd",
    ["interpolate", ["linear"], ["get", field],
      0, "#f3f1eb", maximum * 0.25, "#c6dbef", maximum * 0.6, "#6baed6", maximum, "#08519c"]
  ]);
  const total = values.reduce((sum, value) => sum + value, 0);
  $("map-count").textContent = `${number.format(total)} selected parcel records assigned to ${overview.label}s`;
  $("map-legend").innerHTML = `<strong>Selected parcel records by ${overview.label}</strong><div class="legend-ramp"></div><span>0</span><span style="float:right">${number.format(maximum)}</span>`;
}

function updateMapView() {
  if (!state.map?.getLayer("ward-fill")) return;
  state.popup?.remove();
  updateScenarioNote();
  const mapView = $("map-view").value;
  const overviewView = mapView === "ward" || mapView === "community";
  for (const layer of ["ward-fill", "ward-outline"]) state.map.setLayoutProperty(layer, "visibility", mapView === "ward" ? "visible" : "none");
  for (const layer of ["community-fill", "community-outline"]) state.map.setLayoutProperty(layer, "visibility", mapView === "community" ? "visible" : "none");
  for (const layer of ["candidate-clusters", "candidate-points"]) state.map.setLayoutProperty(layer, "visibility", mapView === "parcel" ? "visible" : "none");
  for (const layer of ["zoning-fill", "zoning-outline"]) state.map.setLayoutProperty(layer, "visibility", mapView === "zoning" ? "visible" : "none");
  for (const label of document.querySelectorAll(".parcel-filter")) {
    label.classList.toggle("is-disabled", mapView !== "parcel");
    for (const control of label.querySelectorAll("select,input")) control.disabled = mapView !== "parcel";
  }
  $("map-legend").hidden = mapView === "parcel";
  if (overviewView) {
    updateOverviewStyle();
    state.map.fitBounds([[-87.95, 41.63], [-87.50, 42.03]], { padding: 24, duration: 0 });
  } else {
    applyFilters();
  }
}

function featureMatches(feature) {
  const p = feature.properties;
  if (p[scenarioField()] !== true) return false;
  if (Number($("transit-distance").value) < 999999 && (p.transit_distance_ft == null || !(p.transit_distance_ft <= Number($("transit-distance").value)))) return false;
  if ($("zoning").value !== "all" && p.zoning !== $("zoning").value) return false;
  if ($("ownership").value === "city" && !p.city_owned) return false;
  if ($("ownership").value === "private" && p.city_owned) return false;
  if ($("vacant").checked && !p.vacant) return false;
  if ($("underbuilt").checked && !p.underbuilt) return false;
  if ($("high-need").checked && !p.high_need_low_supply) return false;
  return true;
}

function applyFilters() {
  state.popup?.remove();
  updateScenarioNote();
  if ($("map-view").value === "zoning") {
    const features = state.zoningCoverage.features.filter((feature) => feature.properties[scenarioField()] === true);
    state.map?.getSource("zoning-coverage")?.setData({ type: "FeatureCollection", features });
    $("map-count").textContent = `${number.format(features.length)} selected map features · coverage, not legal eligibility`;
    const counts = new Map();
    for (const feature of features) { const tier = feature.properties.tier || "build_added"; counts.set(tier, (counts.get(tier) || 0) + 1); }
    $("map-legend").innerHTML = `<strong>Reference density groups</strong>${[...counts].map(([tier, count]) => `<div><span style="color:${tierColors[tier] || "#d95f0e"}">■</span> ${escapeHtml(tierLabel(tier))}: ${number.format(count)}</div>`).join("")}<div>Standard lot: 3,125 sq ft. Density, not built capacity.</div>`;
    return;
  }
  if ($("map-view").value !== "parcel") {
    updateOverviewStyle();
    return;
  }
  const features = state.candidates.features.filter(featureMatches);
  if (state.map?.getSource("candidates")) {
    state.map.getSource("candidates").setData({ type: "FeatureCollection", features });
  }
  $("map-count").textContent = `${number.format(features.length)} parcel records match`;
}

function detailsHtml(rows, note) {
  return `<div class="map-popup"><dl>${rows.map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`).join("")}</dl><p>${escapeHtml(note)}</p></div>`;
}

function parcelDetailsHtml(properties) {
  const rows = [
    ["PIN", properties.pin || "Unavailable"], ["Community", properties.community || "Unavailable"],
    ["Current zoning", properties.zoning || "Unavailable"], ["Coverage", scenarioLabel()],
    ["Transit", properties.transit_distance_ft == null ? "Unavailable" : `${number.format(properties.transit_distance_ft)} ft · ${properties.transit_agency || "nearest"}`],
    ["City-owned", properties.city_owned ? "Yes" : "No"], ["Vacant flag", properties.vacant ? "Yes" : "No"],
    ["Underbuilt flag", properties.underbuilt ? "Yes" : "No"],
    ["Baseline selected district", properties.current_single_stair ? "Yes" : "No"]
  ];
  if (properties.tier) rows.push(["Reference density group", tierLabel(properties.tier)]);
  if (scenarioField() === "illinois_build") {
    if (properties.build_category) rows.push(["BUILD screen", properties.build_category.replaceAll("_", " ")]);
    if (properties.build_effective_unit_limit != null) rows.push(["Screened unit allowance", properties.build_effective_unit_limit]);
    if (properties.build_review_reasons) rows.push(["Review required", properties.build_review_reasons]);
  }
  return detailsHtml(rows, "Zoning coverage only, not legal eligibility or a construction forecast. Points are parcel centroids; parcel records are not unique development sites.");
}

function areaDetailsHtml(properties, geography) {
  const isWard = geography === "ward";
  const geographyLabel = isWard ? "Ward" : "Community area";
  const geographyValue = isWard ? properties.ward : properties.community_area_name;
  const count = properties[areaCountField()];
  return detailsHtml([
    [geographyLabel, geographyValue],
    ["Coverage", scenarioLabel()],
    ["Selected parcel records", count == null ? "Unavailable" : number.format(count)]
  ], "Counts use centroid assignments and all matching parcel records, including occupied sites. They are not additional units or unique development sites.");
}

function zoningDetailsHtml(properties) {
  return detailsHtml([
    ["Current zoning", properties.zoning || "Unavailable"],
    ["Coverage", properties.coverage_kind === "baseline_zoning" ? "Strong Towns selected district" : "BUILD-added parcel screen"],
    ["Reference group", tierLabel(properties.tier)]
  ], "Reference density uses a 3,125 sq ft standard lot, not actual unit capacity. Commercial ground-floor groups can have special-use exceptions. Local use, bulk, safety, and site requirements still need review.");
}

function bindFeaturePopup(layer, renderDetails, coordinates) {
  const show = (event) => {
    const feature = event.features?.[0];
    if (!feature) return;
    state.map.getCanvas().style.cursor = "pointer";
    state.popup
      .setLngLat(coordinates ? coordinates(feature, event) : event.lngLat)
      .setHTML(renderDetails(feature.properties))
      .addTo(state.map);
  };
  state.map.on("mouseenter", layer, show);
  state.map.on("mousemove", layer, show);
  state.map.on("click", layer, show);
  state.map.on("mouseleave", layer, () => {
    state.map.getCanvas().style.cursor = "";
    state.popup.remove();
  });
}

function initializeMap() {
  state.map = new maplibregl.Map({ container: "map", style: basemapStyle(), center: [-87.68, 41.84], zoom: 9.6, attributionControl: true });
  state.popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10 });
  state.map.addControl(new maplibregl.NavigationControl(), "top-left");
  state.map.on("load", () => {
    state.map.addSource("zoning-coverage", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    state.map.addLayer({ id: "zoning-fill", type: "fill", source: "zoning-coverage", paint: { "fill-color": ["match", ["get", "tier"], ...Object.entries(tierColors).flat(), "#d95f0e"], "fill-opacity": 0.6 } });
    state.map.addLayer({ id: "zoning-outline", type: "line", source: "zoning-coverage", paint: { "line-color": "#444", "line-width": 0.5 } });
    state.map.addSource("wards", { type: "geojson", data: state.wards });
    state.map.addLayer({ id: "ward-fill", type: "fill", source: "wards", paint: { "fill-color": "#c6dbef", "fill-opacity": 0.72 } });
    state.map.addLayer({ id: "ward-outline", type: "line", source: "wards", paint: { "line-color": "#444", "line-width": 1 } });
    state.map.addSource("community-areas", { type: "geojson", data: state.communityAreas });
    state.map.addLayer({ id: "community-fill", type: "fill", source: "community-areas", paint: { "fill-color": "#c6dbef", "fill-opacity": 0.72 } });
    state.map.addLayer({ id: "community-outline", type: "line", source: "community-areas", paint: { "line-color": "#444", "line-width": 1 } });
    state.map.addSource("candidates", { type: "geojson", data: { type: "FeatureCollection", features: [] }, cluster: true, clusterRadius: 35, clusterMaxZoom: 13 });
    state.map.addLayer({ id: "candidate-clusters", type: "circle", source: "candidates", filter: ["has", "point_count"], paint: { "circle-color": "#365f91", "circle-opacity": 0.75, "circle-radius": ["step", ["get", "point_count"], 9, 100, 14, 1000, 19] } });
    state.map.addLayer({ id: "candidate-points", type: "circle", source: "candidates", filter: ["!", ["has", "point_count"]], paint: { "circle-color": ["case", ["get", "city_owned"], "#7b3294", ["get", "vacant"], "#d95f0e", "#365f91"], "circle-opacity": 0.7, "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2, 15, 6], "circle-stroke-color": "#fff", "circle-stroke-width": 0.5 } });
    state.map.on("click", "candidate-clusters", async (event) => {
      const feature = state.map.queryRenderedFeatures(event.point, { layers: ["candidate-clusters"] })[0];
      const zoom = await state.map.getSource("candidates").getClusterExpansionZoom(feature.properties.cluster_id);
      state.map.easeTo({ center: feature.geometry.coordinates, zoom });
    });
    bindFeaturePopup("ward-fill", (properties) => areaDetailsHtml(properties, "ward"));
    bindFeaturePopup("community-fill", (properties) => areaDetailsHtml(properties, "community"));
    bindFeaturePopup("zoning-fill", zoningDetailsHtml);
    bindFeaturePopup(
      "candidate-points",
      parcelDetailsHtml,
      (feature) => feature.geometry.coordinates
    );
    updateMapView();
  });
  if (!window.SINGLE_STAIR_CONFIG?.protomapsUrl && !window.SINGLE_STAIR_CONFIG?.protomapsApiKey) {
    $("map-message").hidden = false;
    $("map-message").textContent = "Map data are shown without a basemap. Set PROTOMAPS_API_KEY or PROTOMAPS_URL in .env, then run: uv run --env-file .env single-stair visualize configure";
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
  $("scenario").innerHTML = state.metadata.map_scenarios.map((scenario) => `<option value="${escapeHtml(scenario.id)}">${escapeHtml(scenario.label)}</option>`).join("");
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
  const sources = state.metadata.map_scenarios.flatMap((scenario) => scenario.sources || []);
  const links = [...new Set(sources)].filter((url) => /^https:\/\//.test(url)).map((url) => `<li><a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(url)}</a></li>`).join("");
  $("methodology").innerHTML = `<p><strong>Map coverage:</strong> ${escapeHtml(state.metadata.map_definition)}</p><p><strong>Separate capacity charts:</strong> ${escapeHtml(state.metadata.policy_id)} / ${escapeHtml(state.metadata.estimate_id)}. These charts retain the earlier analytical capacity scenarios; they do not change with the map filter.</p><ul>${limitations}</ul><p>Map policy sources:</p><ul>${links}</ul>`;
}

async function main() {
  try {
    const metadataResponse = await fetch("data/metadata.json", { cache: "no-store" });
    if (!metadataResponse.ok) throw new Error("Run uv run single-stair visualize export to generate map data.");
    state.metadata = await metadataResponse.json();
    if (state.metadata.map_schema_version !== 2 || !state.metadata.map_scenarios?.length) throw new Error("Map export is outdated. Run uv run single-stair visualize export, then refresh.");
    [state.candidates, state.zoningCoverage, state.wards, state.communityAreas, state.neighborhoods, state.comparisons] = await Promise.all([
      fetchGzipJson("data/coverage_parcels.geojson.gz"), fetchGzipJson("data/zoning_coverage.geojson.gz"), fetch("data/coverage_wards.geojson").then((r) => r.json()), fetch("data/coverage_community_areas.geojson").then((r) => r.json()), fetch("data/neighborhoods.json").then((r) => r.json()),
      fetch("data/comparisons.json").then((r) => r.json())
    ]);
    populateControls(); initializeMap(); renderNeedChart(); renderComparison(); renderSimulator(); renderMethodology();
    $("status").textContent = `${number.format(state.metadata.map_parcel_count)} mapped parcel records · generated ${new Date(state.metadata.generated_at).toLocaleDateString()}`;
  } catch (error) {
    $("status").textContent = `Visualization data unavailable: ${error.message}`;
  }
}

main();
