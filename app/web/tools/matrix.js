// N x N drive-time matrix (OSRM /table).
import { h, btn, row, help, table, download, status, fmtH, fmtMi } from "../panel.js";
import { pointList } from "../points.js";

export const id = "matrix";
export const title = "Matrix";

export async function init(ctx) {
  const pts = pointList(ctx, { max: 400, color: "#1f77b4", onChange: () => ctx.state.set(pts.serialize()), label: (i) => String(i + 1) });
  const out = h("div");
  const paste = h("textarea", { placeholder: "paste points, one per line:  name, lat, lon   (or just lat, lon)" });
  let last = null, what = "hours";

  ctx.panel.append(
    h("h2", {}, "Drive-time matrix"),
    help("Click the map or search to add points, then compute every-to-every drive time."),
    pts.el,
    row(btn("Compute", run, "primary"), btn("Clear", () => { pts.clear(); out.replaceChildren(); }, "small"),
        btn("hours / miles", () => { what = what === "hours" ? "miles" : "hours"; if (last) render(last); }, "small")),
    h("h3", {}, "bulk add"),
    paste,
    row(btn("Add pasted points", addPasted, "small")),
    out,
  );

  function addPasted() {
    for (const line of paste.value.split(/\n/)) {
      const parts = line.split(/[,\t]/).map((s) => s.trim()).filter(Boolean);
      if (parts.length < 2) continue;
      const nums = parts.slice(-2).map(Number);
      if (!nums.every(Number.isFinite)) continue;
      const name = parts.length > 2 ? parts.slice(0, -2).join(", ") : "";
      const lonlat = Math.abs(nums[0]) > 90 ? [nums[0], nums[1]] : [nums[1], nums[0]];
      pts.add(lonlat, name);
    }
    paste.value = "";
  }

  async function run() {
    if (pts.points.length < 2) return;
    status("computing matrix…");
    const names = pts.points.map((p, i) => p.name || `P${i + 1}`);
    last = await ctx.api.guard(() => ctx.api.post("/api/matrix", { engine: ctx.engines.current(), coords: pts.coords(), names }), "matrix");
    status("");
    if (last) render(last);
  }

  function render(m) {
    const grid = what === "hours" ? m.hours : m.miles;
    const fmt = what === "hours" ? fmtH : fmtMi;
    const cols = [{ key: "_n", label: what }, ...m.names.map((n, j) => ({ key: `c${j}`, label: n, num: true, fmt: (v) => v == null ? "–" : fmt(v) }))];
    const rows = m.names.map((n, i) => Object.fromEntries([["_n", n], ...grid[i].map((v, j) => [`c${j}`, v])]));
    out.replaceChildren(
      h("h3", {}, `${m.names.length} × ${m.names.length} · ${m.engine}`),
      h("div", { style: "overflow-x:auto" }, table(cols, rows)),
      row(btn("CSV hours", () => csv("hours"), "small"), btn("CSV miles", () => csv("miles"), "small")),
    );
  }

  async function csv(kind) {
    const names = pts.points.map((p, i) => p.name || `P${i + 1}`);
    const r = await fetch(`/api/matrix/csv?what=${kind}`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ engine: ctx.engines.current(), coords: pts.coords(), names }) });
    download(`matrix_${kind}.csv`, await r.blob());
  }

  ctx.onMapClick((lonlat) => pts.add(lonlat));
  pts.parse(ctx.state.get());
  pts.hide();
  return {
    activate() { pts.show(); },
    deactivate() { pts.hide(); },
    onPlace(lonlat, name) { pts.add(lonlat, name); },
  };
}
