// Same trip on every engine that's up, overlaid.
import { h, btn, row, help, table, status, fmtH, fmtMi } from "../panel.js";
import { pointList } from "../points.js";

export const id = "compare";
export const title = "Compare";

export async function init(ctx) {
  const pts = pointList(ctx, { max: 2, color: "#7a3fb0", onChange: schedule, label: (i) => "AB"[i] });
  const out = h("div");
  let timer = null;

  ctx.panel.append(
    h("h2", {}, "Compare engines"),
    help("Click A and B. Every running engine routes the same trip; lines are colored per engine."),
    pts.el,
    row(btn("Clear", () => { pts.clear(); out.replaceChildren(); ctx.layers.removeByPrefix("compare-"); }, "small")),
    out,
  );

  function schedule() { ctx.state.set(pts.serialize()); clearTimeout(timer); timer = setTimeout(run, 150); }

  async function run() {
    ctx.layers.removeByPrefix("compare-");
    if (pts.points.length < 2) { out.replaceChildren(); return; }
    status("comparing…");
    const r = await ctx.api.guard(() => ctx.api.post("/api/compare", { coords: pts.coords() }), "compare");
    status("");
    if (!r) return;
    r.results.forEach((e, i) => {
      if (e.status !== "ok") return;
      ctx.layers.setGeoJSON(`compare-${e.engine}`, { type: "FeatureCollection", features: [{ type: "Feature", properties: { name: `${e.engine} route`, engine: e.engine, hours: e.hours, miles: e.miles }, geometry: e.geometry }] },
        { color: e.color, width: 6 - i * 1.2, opacity: 0.85 });
    });
    const rows = r.results.map((e) => ({ ...e, ferry: e.used_ferry ? "⛴" : "" }));
    out.replaceChildren(
      table([
        { key: "engine", label: "engine", fmt: (v, e) => h("span", {}, h("span", { class: "sw", style: `background:${e.color}` }), v) },
        { key: "hours", label: "time", num: true, fmt: (v, e) => e.status === "ok" ? fmtH(v) : e.status },
        { key: "miles", label: "miles", num: true, fmt: (v) => v == null ? "" : fmtMi(v) },
        { key: "ferry", label: "" },
      ], rows),
      r.skipped.length ? h("p", { class: "help" }, `not running: ${r.skipped.join(", ")}`) : null,
    );
  }

  ctx.onMapClick((lonlat) => pts.add(lonlat));
  pts.parse(ctx.state.get());
  pts.hide();
  return {
    activate() { pts.show(); if (pts.points.length === 2) run(); },
    deactivate() { pts.hide(); ctx.layers.removeByPrefix("compare-"); },
    onPlace(lonlat, name) { pts.add(lonlat, name); },
  };
}
