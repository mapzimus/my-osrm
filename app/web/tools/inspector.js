// Click anywhere: what road did the engine snap to, and how fast does it think that road is?
import { fill, h, btn, row, help, status, fmtLL } from "../panel.js";

export const id = "inspector";
export const title = "Inspect";

export async function init(ctx) {
  const out = h("div");
  const ferryCb = h("input", { type: "checkbox", disabled: !ctx.cfg.ferries_overlay });
  let marker = null;

  ctx.panel.append(
    h("h2", {}, "Network inspector"),
    help("Click the map. Shows the nearest snapped road on the current engine, its OSM node ids and the speed the profile gave it."),
    row(h("label", {}, h("span", {}, ferryCb, " ferry + dock-connector overlay")),
        ctx.cfg.ferries_overlay ? null : h("span", { class: "help" }, "(run app.setup --only ferries)")),
    out,
  );

  ferryCb.onchange = async () => {
    if (!ferryCb.checked) return ctx.layers.remove("ferries");
    const fc = await ctx.api.guard(() => ctx.api.get("/api/inspector/ferries.geojson"), "ferries");
    if (fc) ctx.layers.setGeoJSON("ferries", fc, { layers: [
      { type: "line", filter: ["==", ["get", "kind"], "ferry"], paint: { "line-color": "#1f77b4", "line-width": 2.5, "line-dasharray": [2, 1.5] } },
      { type: "line", filter: ["==", ["get", "kind"], "connector"], paint: { "line-color": "#ff7f0e", "line-width": 3 } },
      { type: "circle", filter: ["==", ["geometry-type"], "Point"], paint: { "circle-radius": 4, "circle-color": ["case", ["==", ["get", "status"], "linked"], "#2e9e5b", "#d1495b"], "circle-stroke-color": "#fff", "circle-stroke-width": 1 } },
    ] });
  };

  async function inspect(lonlat) {
    if (marker) marker.remove();
    marker = ctx.marker(lonlat, "?", { draggable: true, onDrag: inspect, color: "#555" });
    status("inspecting…");
    const r = await ctx.api.guard(() => ctx.api.get("/api/inspector/nearest", { lon: lonlat[0], lat: lonlat[1], engine: ctx.engines.current() }), "inspect");
    status("");
    if (!r) { out.replaceChildren(); ctx.layers.remove("inspect"); return; }
    ctx.layers.setGeoJSON("inspect", { type: "FeatureCollection", features: [
      { type: "Feature", properties: { name: "snap" }, geometry: { type: "LineString", coordinates: [lonlat, r.waypoints[0].location] } },
      ...r.waypoints.map((w, i) => ({ type: "Feature", properties: { name: `snap ${i + 1}` }, geometry: { type: "Point", coordinates: w.location } })),
    ] }, { color: "#555", width: 2, fill: false });
    const w = r.waypoints[0];
    const p = r.probe;
    fill(out,
      h("h3", {}, "nearest road"),
      h("div", { class: "mono" }, `${w.name || "(unnamed)"}`),
      h("div", { class: "help" }, `snapped ${Math.round(w.snap_m)} m to ${fmtLL(w.location)}`),
      h("div", { class: "mono" }, `nodes ${w.nodes.join(" – ")}`),
      row(...w.nodes.map((n) => h("a", { href: `https://www.openstreetmap.org/node/${n}`, target: "_blank", class: "help", title: "opens OSM (needs internet)" }, `node ${n} ↗`))),
      p ? h("div", {}, h("h3", {}, "profile says"),
        h("div", {}, `${[p.ref, p.name].filter(Boolean).join(" · ") || "unnamed"} · mode ${p.mode}`),
        h("div", {}, `speed ≈ `, h("b", {}, p.mph == null ? "?" : `${p.mph} mph`), ` (${p.kmh ?? "?"} km/h)`),
      ) : h("p", { class: "help" }, "couldn't probe a route out of the snapped point"),
      r.waypoints.length > 1 ? h("div", {}, h("h3", {}, "other candidates"), ...r.waypoints.slice(1).map((x) => h("div", { class: "help" }, `${x.name || "(unnamed)"} · ${Math.round(x.snap_m)} m · nodes ${x.nodes.join("–")}`))) : null,
      h("p", { class: "help" }, `debug further:  python scripts/inspect_node.py ${w.nodes[0] ?? ""}`),
    );
    ctx.state.set(`${lonlat[0].toFixed(5)},${lonlat[1].toFixed(5)}`);
  }

  ctx.onMapClick(inspect);
  const saved = ctx.state.get();
  return {
    activate() { if (saved && !marker) { const ll = saved.split(",").map(Number); if (ll.every(Number.isFinite)) inspect(ll); } else if (marker) marker.addTo(ctx.map); },
    deactivate() { if (marker) marker.remove(); ctx.layers.remove("inspect"); },
    onPlace(lonlat) { ctx.map.flyTo({ center: lonlat, zoom: 13 }); inspect(lonlat); },
  };
}
