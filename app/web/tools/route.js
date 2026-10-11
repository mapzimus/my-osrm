// A -> B (-> C ...) route: click the map to add stops, drag to move, see line + turn list.
import { fill, h, btn, row, help, stat, fmtH, fmtMi, toast, status } from "../panel.js";
import { pointList } from "../points.js";

export const id = "route";
export const title = "Route";

const ICONS = { ferry: "⛴", turn: "↪", depart: "●", arrive: "◉", merge: "⤴", "on ramp": "⤴", "off ramp": "⤵", fork: "⑂", roundabout: "↻", continue: "↑" };

export async function init(ctx) {
  let result = null;
  let timer = null;
  const pts = pointList(ctx, { onChange: schedule });
  const summary = h("div");
  const steps = h("div", { class: "steps" });
  const optBtn = btn("Best order", optimize, "small");
  const stateKey = ctx.state;

  ctx.panel.append(
    h("h2", {}, "Route"),
    help("Click the map (or search) to add A, B, C… Drag a marker to move it, click it to remove it."),
    pts.el,
    row(btn("Clear", () => { pts.clear(); }, "small"), btn("Reverse", () => pts.set([...pts.points].reverse()), "small"), optBtn),
    summary,
    steps,
  );

  function schedule() {
    stateKey.set(pts.serialize(), { silent: true });
    optBtn.disabled = pts.points.length < 3;
    clearTimeout(timer);
    timer = setTimeout(run, 150);
  }

  async function run() {
    if (pts.points.length < 2) { clear(); return; }
    status("routing…");
    const r = await ctx.api.guard(() => ctx.api.post("/api/route", { engine: ctx.engines.current(), coords: pts.coords() }), "route");
    status("");
    if (!r) { clear(true); return; }
    result = r;
    const names = pts.points.map((p, i) => p.name || `Stop ${i + 1}`);
    ctx.layers.setGeoJSON("route", {
      type: "FeatureCollection",
      features: [{ type: "Feature", properties: { name: `Route ${names[0]} → ${names.at(-1)}`, engine: r.engine, miles: r.miles, hours: r.hours, waypoints: names }, geometry: r.geometry }],
    }, { color: "#d1495b", width: 5 });
    const ferry = r.used_ferry ? h("div", {}, h("span", { class: "pass" }, "⛴ uses a ferry")) : null;
    fill(summary,
      stat([[fmtH(r.hours), "drive time"], [fmtMi(r.miles), "distance"], [`${Math.round(r.km)} km`, ""]]),
      ferry,
      r.legs.length > 1 ? h("div", { class: "help" }, r.legs.map((l, i) => `${names[i]} → ${names[i + 1]}: ${fmtH(l.hours)}, ${fmtMi(l.miles)}`).join(" · ")) : null,
      r.waypoints.some((w) => w.snap_m > 2000) ? h("div", { class: "fail" }, `⚠ a stop snapped ${Math.round(Math.max(...r.waypoints.map((w) => w.snap_m)) / 1000)} km to the nearest road on this engine`) : null,
    );
    renderSteps(r);
  }

  function renderSteps(r) {
    steps.replaceChildren(...r.steps.filter((s) => s.distance_m > 0 || s.type === "arrive").map((s) => {
      const icon = s.mode === "ferry" ? ICONS.ferry : ICONS[s.type] || ICONS.turn;
      const road = [s.ref, s.name].filter(Boolean).join(" · ") || (s.mode === "ferry" ? "ferry" : "unnamed road");
      const what = s.type === "arrive" ? "Arrive" : s.type === "depart" ? "Depart" : `${s.type}${s.modifier ? " " + s.modifier : ""}`;
      return h("div", { class: s.mode === "ferry" ? "ferry" : "", onClick: () => s.location && ctx.map.flyTo({ center: s.location, zoom: Math.max(ctx.map.getZoom(), 11) }) },
        `${icon} ${what} — ${road} `, h("small", {}, `${fmtMi(s.distance_m / 1609.344)}, ${fmtH(s.duration_s / 3600)}`));
    }));
  }

  async function optimize() {
    const r = await ctx.api.guard(() => ctx.api.post("/api/route/trip", { engine: ctx.engines.current(), coords: pts.coords(), roundtrip: false }), "best order");
    if (!r) return;
    const order = r.order;
    pts.set(order.map((i) => pts.points[i]));
    toast(`Reordered: ${fmtH(r.hours)}, ${fmtMi(r.miles)}`, "ok");
  }

  function clear(keepPoints = false) {
    result = null;
    ctx.layers.remove("route");
    summary.replaceChildren();
    steps.replaceChildren();
    if (!keepPoints) stateKey.set("", { silent: true });
  }

  ctx.onMapClick((lonlat) => pts.add(lonlat));
  ctx.engines.on(() => pts.points.length >= 2 && run());
  if (pts.parse(stateKey.get())) { /* restored from URL */ }
  stateKey.on((v) => { if (v !== pts.serialize()) { if (!v) pts.clear(); else pts.parse(v); } });

  return {
    activate() { pts.show(); if (result) ctx.layers.fit(result.geometry); },
    deactivate() { },
    onPlace(lonlat, name) { pts.add(lonlat, name); },
    current: () => result,
  };
}
