// Boot: load config + tool manifest, build the map, mount each tool's panel.
import * as api from "./api.js";
import * as state from "./state.js";
import * as layers from "./layers.js";
import * as mapmod from "./map.js";
import { h, toast, status, parseLatLon } from "./panel.js";

const cfg = await api.get("/api/config");
const manifest = await api.get("/api/tools");

// Prefer the full-zoom basemap, else the quick z10 one, else none (grey map, still works).
const basemap = cfg.basemaps.includes("north-america.pmtiles") ? "north-america.pmtiles"
  : cfg.basemaps.sort().reverse()[0] || null;
if (!basemap) toast("No basemap yet: run  python -m app.setup --only basemap --maxzoom 10", "bad");

const map = mapmod.create("map", basemap);
document.getElementById("flavor").onclick = () => map.toggleFlavor();

// ---- engines (shared) --------------------------------------------------------
const engines = {
  list: cfg.engines, health: {},
  current() { return state.get("engine") || cfg.default_engine; },
  get(id) { return cfg.engines.find((e) => e.id === (id || this.current())); },
  set(id) { state.set("engine", id); },
  on(fn) { return state.on("engine", fn); },
  async refresh() {
    const list = await api.get("/api/engines").catch(() => []);
    for (const e of list) this.health[e.id] = e.healthy;
    const dot = document.getElementById("engine-dot");
    const ok = this.health[this.current()];
    dot.className = ok ? "ok" : "bad";
    dot.title = `${this.current()}: ${ok ? "up" : "down"}`;
    return list;
  },
};
if (!cfg.engines.some((e) => e.id === engines.current())) state.set("engine", cfg.default_engine, { silent: true });
engines.on(() => engines.refresh());
engines.refresh();
setInterval(() => engines.refresh(), 20000);

// ---- tools -------------------------------------------------------------------
const tabs = document.getElementById("tabs");
const panels = document.getElementById("panels");
const tools = new Map();
let active = null;

function makeCtx(meta) {
  const section = h("section", { id: `panel-${meta.id}`, hidden: true });
  panels.append(section);
  const clickHandlers = new Set();
  const ctx = {
    id: meta.id, map, api, state: state.ns(meta.id), globalState: state, layers, engines, cfg,
    maplibregl: mapmod.maplibregl,
    panel: section,
    onMapClick(fn) { clickHandlers.add(fn); return () => clickHandlers.delete(fn); },
    _clicks: clickHandlers,
    activate() { activate(meta.id); },
    isActive: () => active === meta.id,
    marker(lngLat, label, { color, draggable = true, onDrag, onClick } = {}) {
      const el = h("div", { class: "marker", style: color ? `background:${color}` : "" }, h("span", {}, label || ""));
      const m = new mapmod.maplibregl.Marker({ element: el, draggable, anchor: "bottom", offset: [0, 2] }).setLngLat(lngLat).addTo(map);
      if (onDrag) m.on("dragend", () => onDrag(m.getLngLat().toArray()));
      if (onClick) el.addEventListener("click", (ev) => { ev.stopPropagation(); onClick(m); });
      return m;
    },
  };
  return ctx;
}

function activate(id) {
  if (active === id) return;
  const prev = tools.get(active);
  if (prev) { prev.panel.hidden = true; prev.impl.deactivate && prev.impl.deactivate(); }
  active = id;
  const cur = tools.get(id);
  cur.panel.hidden = false;
  cur.impl.activate && cur.impl.activate();
  for (const b of tabs.children) b.classList.toggle("active", b.dataset.id === id);
  state.set("tool", id, { silent: true });
}

for (const meta of manifest) {
  const ctx = makeCtx(meta);
  let impl;
  try {
    const mod = await import(meta.js);
    impl = (await mod.init(ctx)) || {};
  } catch (e) {
    console.error(`tool ${meta.id} failed to load`, e);
    ctx.panel.append(h("p", { class: "help" }, `failed to load: ${e.message}`));
    impl = {};
  }
  tools.set(meta.id, { meta, ctx, impl, panel: ctx.panel });
  tabs.append(h("button", { "data-id": meta.id, title: meta.title, onClick: () => activate(meta.id) }, meta.icon ? `${meta.icon} ${meta.title}` : meta.title));
}

map.on("click", (ev) => {
  const t = tools.get(active);
  if (!t) return;
  for (const fn of t.ctx._clicks) fn(ev.lngLat.toArray(), ev);
});

const wanted = state.get("tool");
activate(tools.has(wanted) ? wanted : (manifest.find((m) => m.id !== "engines") || manifest[0]).id);
state.on("tool", (id) => { if (id && tools.has(id)) activate(id); });

// ---- search box (places tool fills it; fallback: lat, lon) -----------------------
const search = document.getElementById("search");
const results = document.getElementById("search-results");
let searchIdx = -1, searchItems = [];
window.searchPick = null;     // tools set this to receive the picked place while they're active

function showResults(items) {
  searchItems = items; searchIdx = -1;
  results.replaceChildren(...items.map((it, i) => h("li", { onMousedown: (e) => { e.preventDefault(); pick(i); } },
    h("span", {}, it.name), h("span", { class: "kind" }, it.kind))));
  results.hidden = items.length === 0;
}
function pick(i) {
  const it = searchItems[i];
  if (!it) return;
  results.hidden = true; search.value = it.name; search.blur();
  const handler = tools.get(active)?.impl.onPlace;
  if (handler) handler(it.lonlat, it.name); else map.flyTo({ center: it.lonlat, zoom: Math.max(map.getZoom(), 10) });
}
let searchTimer = null;
search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = search.value.trim();
  const ll = parseLatLon(q);
  if (ll) return showResults([{ name: `${ll[1].toFixed(5)}, ${ll[0].toFixed(5)}`, kind: "coordinates", lonlat: ll }]);
  if (q.length < 2 || !cfg.places_index) return showResults([]);
  searchTimer = setTimeout(async () => {
    const r = await api.get("/api/places", { q, limit: 12 }).catch(() => []);
    showResults(r.map((p) => ({ name: p.label, kind: p.kind, lonlat: [p.lon, p.lat] })));
  }, 120);
});
search.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    searchIdx = Math.max(0, Math.min(searchItems.length - 1, searchIdx + (e.key === "ArrowDown" ? 1 : -1)));
    [...results.children].forEach((li, i) => li.classList.toggle("active", i === searchIdx));
  } else if (e.key === "Enter") { e.preventDefault(); pick(searchIdx < 0 ? 0 : searchIdx); }
  else if (e.key === "Escape") { results.hidden = true; search.blur(); }
});
search.addEventListener("blur", () => setTimeout(() => (results.hidden = true), 150));
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement !== search && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) { e.preventDefault(); search.focus(); search.select(); }
});
if (!cfg.places_index) search.placeholder = "lat, lon  (place search: run app.setup --only places)";

status(basemap ? `basemap: ${basemap}` : "no basemap");
window.app = { map, state, layers, engines, tools, cfg };
