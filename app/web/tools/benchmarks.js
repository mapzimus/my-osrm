// Report card on the map: every benchmark trip drawn green (pass) or red (fail).
import { h, btn, row, help, progress, table, status, stat, fmtH, fmtMi } from "../panel.js";

export const id = "benchmarks";
export const title = "Report card";

const COLOR = { PASS: "#2e9e5b", FAIL: "#d1495b", ERROR: "#d9900b", SKIP: "#9aa3b5" };

export async function init(ctx) {
  const out = h("div");
  const bar = progress(); bar.hidden = true;
  const msg = h("div", { class: "help" });
  const runBtn = btn("Run report card", run, "primary");
  let last = null;

  ctx.panel.append(
    h("h2", {}, "Report card"),
    help("Runs every case in benchmarks/routes.toml against the current engine (~30 s) and draws each trip."),
    row(runBtn, btn("Show cases", showCases, "small")),
    bar, msg, out,
  );

  async function showCases() {
    const cases = await ctx.api.guard(() => ctx.api.get("/api/benchmarks/cases"), "cases");
    if (!cases) return;
    draw(cases.map((c) => ({ ...c, result: "SKIP", status: "" })));
    render({ rows: cases.map((c) => ({ ...c, result: "", status: "" })) });
  }

  async function run() {
    runBtn.disabled = true; bar.hidden = false; bar.set(0);
    const job = await ctx.api.guard(() => ctx.api.post("/api/benchmarks/run", { engine: ctx.engines.current() }), "report card");
    if (!job) { runBtn.disabled = false; bar.hidden = true; return; }
    const res = await ctx.api.guard(() => ctx.api.follow(job.id, (s) => { bar.set(s.progress); msg.textContent = s.message; status(`report card ${Math.round(100 * s.progress)}%`); }), "report card");
    runBtn.disabled = false; bar.hidden = true; status("");
    if (!res) return;
    last = res;
    msg.textContent = "";
    draw(res.rows);
    render(res);
  }

  function draw(rows) {
    const feats = rows.map((r) => ({
      type: "Feature",
      properties: { name: r.name, result: r.result, miles: r.miles, hours: r.hours, color: COLOR[r.result] || COLOR.SKIP },
      geometry: r.geometry || { type: "LineString", coordinates: [r.from, r.to] },
    }));
    ctx.layers.setGeoJSON("benchmarks", { type: "FeatureCollection", features: feats }, { layers: [
      { type: "line", layout: { "line-cap": "round" }, paint: { "line-color": ["get", "color"], "line-width": 3.5, "line-opacity": 0.85,
        "line-dasharray": ["case", ["==", ["get", "result"], "SKIP"], ["literal", [1, 2]], ["literal", [1, 0]]] } },
    ] });
    ctx.layers.fit({ type: "FeatureCollection", features: feats }, 30);
  }

  function render(res) {
    const rows = res.rows;
    out.replaceChildren(
      res.scored != null ? stat([[`${res.passed}/${res.scored}`, "score"], [res.engine, "engine"]]) : null,
      table([
        { key: "result", label: "", fmt: (v) => h("span", { class: (v || "").toLowerCase() }, v === "PASS" ? "✓" : v === "FAIL" ? "✗" : v === "SKIP" ? "–" : v) },
        { key: "name", label: "case" },
        { key: "kind", label: "kind" },
        { key: "hours", label: "time", num: true, fmt: (v, r) => r.status === "ok" ? fmtH(v) : r.status },
        { key: "miles", label: "mi", num: true, fmt: (v, r) => v == null ? "" : `${fmtMi(v)}${r.used_ferry ? " ⛴" : ""}` },
      ], rows, (r) => ctx.layers.fit(r.geometry || { type: "LineString", coordinates: [r.from, r.to] }, 80)),
      h("p", { class: "help" }, "Click a row to zoom. Dashed = not scored on this engine."),
    );
  }

  return {
    activate() { if (last) draw(last.rows); },
    deactivate() { ctx.layers.remove("benchmarks"); },
  };
}
