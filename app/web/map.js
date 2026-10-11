// MapLibre + local PMTiles basemap (Protomaps style, vendored fonts/sprites). Zero network.
import * as maplibregl from "maplibre-gl";
import { Protocol } from "pmtiles";
import { layers as pmLayers, namedFlavor } from "@protomaps/basemaps";
import * as state from "./state.js";
import * as overlays from "./layers.js";

const protocol = new Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

export const FLAVORS = ["light", "dark"];

export function buildStyle(basemapFile, flavor) {
  const flav = namedFlavor(flavor);
  const sources = {};
  const layers = [];
  if (basemapFile) {
    sources.protomaps = {
      type: "vector",
      url: `pmtiles://${location.origin}/basemap/${basemapFile}`,
      attribution: '© OpenStreetMap · Protomaps',
    };
    layers.push(...pmLayers("protomaps", flav, { lang: "en" }));
  } else {
    layers.push({ id: "bg", type: "background", paint: { "background-color": flav.background || "#e8e8e8" } });
  }
  return {
    version: 8,
    glyphs: `${location.origin}/vendor/basemaps-assets/fonts/{fontstack}/{range}.pbf`,
    sprite: `${location.origin}/vendor/basemaps-assets/sprites/v4/${flavor}`,
    sources, layers,
  };
}

export function parseView(v) {
  const m = (v || "").match(/^([\d.]+)\/(-?[\d.]+)\/(-?[\d.]+)$/);
  return m ? { zoom: +m[1], center: [+m[3], +m[2]] } : null;
}

export function create(container, basemapFile) {
  const flavor = FLAVORS.includes(state.get("flavor")) ? state.get("flavor") : (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  state.set("flavor", flavor, { silent: true });
  document.documentElement.dataset.theme = flavor;
  const view = parseView(state.get("v")) || { zoom: 5.2, center: [-74.5, 42.5] };
  const map = new maplibregl.Map({
    container,
    style: buildStyle(basemapFile, flavor),
    center: view.center, zoom: view.zoom,
    canvasContextAttributes: { preserveDrawingBuffer: true },   // PNG export
    attributionControl: false,
    maxZoom: 17,
  });
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "bottom-right");
  overlays.attach(map);

  map.on("moveend", () => {
    const c = map.getCenter();
    state.set("v", `${map.getZoom().toFixed(2)}/${c.lat.toFixed(4)}/${c.lng.toFixed(4)}`, { silent: true });
  });
  state.on("v", (v) => { const p = parseView(v); if (p) map.jumpTo(p); });

  map.setFlavor = (f) => {
    state.set("flavor", f, { silent: true });
    document.documentElement.dataset.theme = f;
    map.setStyle(buildStyle(basemapFile, f));
  };
  map.toggleFlavor = () => map.setFlavor(state.get("flavor") === "dark" ? "light" : "dark");
  return map;
}

export { maplibregl };
