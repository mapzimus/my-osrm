// Overlay registry on top of MapLibre. Tools add GeoJSON / image overlays by id;
// the registry re-applies them after a basemap style swap and lists them for export.
import * as state from "./state.js";

const overlays = new Map();   // id -> {kind, data, opts}
let map = null;

export function attach(m) {
  map = m;
  map.on("style.load", () => { for (const [id, o] of overlays) apply(id, o); });
}

function firstSymbolLayer() {
  const layers = map.getStyle().layers || [];
  const sym = layers.find((l) => l.type === "symbol" && (l.id.includes("places") || l.id.includes("label")));
  return sym ? sym.id : undefined;
}

function apply(id, o) {
  if (!map.isStyleLoaded() && !map.loaded()) return;
  remove(id, false);
  const src = `ov-${id}`;
  if (o.kind === "geojson") {
    map.addSource(src, { type: "geojson", data: o.data });
    const specs = o.opts.layers || defaultLayers(o.opts);
    const before = o.opts.below === false ? undefined : firstSymbolLayer();
    for (let i = 0; i < specs.length; i++) {
      const spec = { id: `${src}-${i}`, source: src, ...specs[i] };
      map.addLayer(spec, before);
    }
  } else if (o.kind === "image") {
    map.addSource(src, { type: "image", url: o.data, coordinates: o.opts.coordinates });
    map.addLayer({ id: `${src}-0`, type: "raster", source: src, paint: { "raster-opacity": o.opts.opacity ?? 0.8, "raster-fade-duration": 0 } }, firstSymbolLayer());
  }
}

function defaultLayers({ color = "#d1495b", width = 4, opacity = 0.9, fill, circle }) {
  const out = [];
  if (fill !== false) out.push({ type: "fill", filter: ["==", ["geometry-type"], "Polygon"], paint: { "fill-color": color, "fill-opacity": fill?.opacity ?? 0.25 } });
  out.push({ type: "line", filter: ["in", ["geometry-type"], ["literal", ["LineString", "Polygon"]]],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": color, "line-width": width, "line-opacity": opacity } });
  if (circle !== false) out.push({ type: "circle", filter: ["==", ["geometry-type"], "Point"],
    paint: { "circle-radius": circle?.radius ?? 5, "circle-color": color, "circle-stroke-color": "#fff", "circle-stroke-width": 1.5 } });
  return out;
}

/** setGeoJSON(id, featureCollection, {color, width, opacity, fill, circle, layers, below}) */
export function setGeoJSON(id, data, opts = {}) {
  overlays.set(id, { kind: "geojson", data, opts });
  apply(id, overlays.get(id));
}

/** setImage(id, dataUrl, coordinates: [[w,n],[e,n],[e,s],[w,s]], {opacity}) */
export function setImage(id, url, coordinates, opts = {}) {
  overlays.set(id, { kind: "image", data: url, opts: { ...opts, coordinates } });
  apply(id, overlays.get(id));
}

export function remove(id, forget = true) {
  if (forget) overlays.delete(id);
  if (!map) return;
  const src = `ov-${id}`;
  for (const l of (map.getStyle()?.layers || [])) if (l.source === src) map.removeLayer(l.id);
  if (map.getSource(src)) map.removeSource(src);
}

export function removeByPrefix(prefix) {
  for (const id of [...overlays.keys()]) if (id.startsWith(prefix)) remove(id);
}

export function list() {
  return [...overlays].map(([id, o]) => ({ id, ...o }));
}

export function layerIds(id) {
  const src = `ov-${id}`;
  return (map.getStyle()?.layers || []).filter((l) => l.source === src).map((l) => l.id);
}

/** Fit the map to a GeoJSON object (FeatureCollection / Feature / Geometry). */
export function fit(geojson, padding = 60) {
  const b = bbox(geojson);
  if (!b) return;
  const side = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--side-w")) || 0;
  map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: { top: padding, bottom: padding, left: padding, right: padding }, duration: 600, maxZoom: 14 });
}

export function bbox(g) {
  let b = null;
  const add = ([x, y]) => { b = b ? [Math.min(b[0], x), Math.min(b[1], y), Math.max(b[2], x), Math.max(b[3], y)] : [x, y, x, y]; };
  const walk = (c) => { if (typeof c[0] === "number") add(c); else c.forEach(walk); };
  const geom = (gg) => { if (!gg) return; if (gg.type === "GeometryCollection") gg.geometries.forEach(geom); else walk(gg.coordinates); };
  if (g.type === "FeatureCollection") g.features.forEach((f) => geom(f.geometry));
  else if (g.type === "Feature") geom(g.geometry);
  else geom(g);
  return b;
}
