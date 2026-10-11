// Drive-time bands from one origin, computed as a background job on the server.
import { h, btn, row, help, progress, toast, status, fmtH, fmtLL } from "../panel.js";

export const id = "isochrone";
export const title = "Isochrone";

export async function init(ctx) {
  const presets = await ctx.api.get("/api/isochrone/presets").catch(() => ({ regions: {}, docker: false }));
  let origin = null, marker = null, job = null, result = null;

  const nameIn = h("input", { type: "text", value: "Origin", style: "width:120px" });
  const regionSel = h("select", {}, ...Object.keys(presets.regions).map((k) => h("option", { value: k }, k)), h("option", { value: "custom" }, "custom (map view)"));
  const hoursIn = h("input", { type: "number", min: 0, max: 200, value: 0, title: "0 = auto (every hour it takes)" });
  const stepIn = h("input", { type: "number", min: 0.01, max: 1, step: 0.01, value: 0.05, title: "grid spacing, degrees" });
  const fillCb = h("input", { type: "checkbox", checked: presets.docker });
  const flightsCb = h("input", { type: "checkbox" });
  const originEl = h("span", { class: "help" }, "click the map to set the origin");
  const bar = progress();
  const msg = h("div", { class: "help" });
  const runBtn = btn("Compute", run, "primary");
  const cancelBtn = btn("Cancel", () => job && ctx.api.cancel(job.id), "small");
  const legend = h("div");
  const cities = h("div");
  const exclBoxes = (presets.countries || []).map((c) => h("label", { title: `skip grid points inside ${c.label}` },
    h("input", { type: "checkbox", value: c.code, disabled: !presets.countries_ready, onChange: save }), ` ${c.label}`));
  const excluded = () => exclBoxes.map((l) => l.firstChild).filter((cb) => cb.checked).map((cb) => cb.value);
  const exclRow = row(h("label", {}, "skip"), ...exclBoxes,
    presets.countries_ready ? null : h("span", { class: "help" }, "(run app.setup --only countries)"));
  const customRow = row(h("label", {}, "step°"), stepIn, h("span", { class: "help" }, "bbox = current map view"));
  customRow.hidden = true;
  regionSel.onchange = () => { customRow.hidden = regionSel.value !== "custom"; };
  bar.hidden = true; cancelBtn.hidden = true;

  ctx.panel.append(
    h("h2", {}, "Isochrone"),
    help("Everywhere reachable from one point, banded by hour. Regional ≈ 1–3 min; national ≈ 10+ min."),
    row(h("label", {}, "origin"), originEl),
    row(h("label", {}, "name"), nameIn, h("label", {}, "region"), regionSel),
    customRow,
    row(h("label", {}, "max hours"), hoursIn, h("label", {}, h("span", {}, fillCb, " land fill")), h("label", { title: "fly to Hawaii / Puerto Rico from BOS (experiment)" }, h("span", {}, flightsCb, " flights"))),
    exclRow,
    row(runBtn, cancelBtn),
    bar, msg, legend, cities,
  );

  function setOrigin(lonlat, name) {
    origin = lonlat;
    if (name) nameIn.value = name;
    originEl.textContent = fmtLL(lonlat);
    if (marker) marker.remove();
    marker = ctx.marker(lonlat, "●", { onDrag: (ll) => setOrigin(ll) });
    save();
  }

  function save() {
    if (!origin) return;
    ctx.state.set([`${origin[0].toFixed(5)},${origin[1].toFixed(5)}`, regionSel.value, hoursIn.value || 0, encodeURIComponent(nameIn.value), excluded().join("+")].join("|"));
  }

  async function run() {
    if (!origin) return toast("Click the map to set an origin first", "bad");
    save();
    const body = { engine: ctx.engines.current(), name: nameIn.value || "Origin", origin, hours: +hoursIn.value || null, fill: fillCb.checked, flights: flightsCb.checked, exclude: excluded() };
    if (regionSel.value === "custom") {
      const b = ctx.map.getBounds();
      body.bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
      body.step = +stepIn.value;
    } else body.region = regionSel.value;
    runBtn.disabled = true; bar.hidden = false; cancelBtn.hidden = false; bar.set(0);
    job = await ctx.api.guard(() => ctx.api.post("/api/isochrone/jobs", body), "isochrone");
    if (!job) { runBtn.disabled = false; bar.hidden = true; cancelBtn.hidden = true; return; }
    const res = await ctx.api.guard(() => ctx.api.follow(job.id, (s) => { bar.set(s.progress); msg.textContent = s.message || s.status; status(`isochrone ${Math.round(100 * s.progress)}%`); }), "isochrone");
    runBtn.disabled = false; bar.hidden = true; cancelBtn.hidden = true; status("");
    if (!res) { msg.textContent = "cancelled or failed"; return; }
    show(res);
  }

  function show(res) {
    result = res;
    msg.textContent = `${res.hours} hour bands · engine ${res.engine}` + (res.exclude?.length ? ` · skipped ${res.exclude.join(", ")}` : "");
    const [[s, w], [n, e]] = res.bounds;          // Leaflet order from isochrone.py
    ctx.layers.setImage("isochrone", res.png_url, [[w, n], [e, n], [e, s], [w, s]], { opacity: 0.8 });
    const items = res.colors.map((c, i) => [h("span", { class: "sw", style: `background:${c}` }), `≤ ${i + 1} h`, h("span", { class: "a" }, `${Math.round(res.areas[i] / 1000).toLocaleString()}k km²`)]).flat();
    legend.replaceChildren(h("h3", {}, "legend"), h("div", { class: "legend" }, ...items));
    const rows = res.cities.filter((c) => c.hours != null).sort((a, b) => a.hours - b.hours);
    cities.replaceChildren(h("h3", {}, "cities"), h("div", { class: "steps" }, ...rows.map((c) =>
      h("div", { onClick: () => ctx.map.flyTo({ center: [c.lon, c.lat], zoom: 8 }) }, `${fmtH(c.hours)}  `, h("small", {}, c.name)))));
    ctx.map.fitBounds([[w, s], [e, n]], { padding: 40, duration: 800 });
  }

  ctx.onMapClick((lonlat) => setOrigin(lonlat));
  const saved = ctx.state.get();
  if (saved) {
    const [o, region, hours, name, excl] = saved.split("|");
    // restore the settings first: setOrigin() calls save(), which reads them back
    for (const cb of exclBoxes.map((l) => l.firstChild)) cb.checked = (excl || "").split("+").includes(cb.value);
    if (region && [...regionSel.options].some((op) => op.value === region)) regionSel.value = region;
    hoursIn.value = hours || 0;
    customRow.hidden = regionSel.value !== "custom";
    const ll = o.split(",").map(Number);
    if (ll.length === 2 && ll.every(Number.isFinite)) setOrigin(ll, decodeURIComponent(name || "Origin"));
  }

  return {
    activate() { if (marker) marker.addTo(ctx.map); },
    deactivate() { },
    onPlace(lonlat, name) { setOrigin(lonlat, name); ctx.map.flyTo({ center: lonlat, zoom: Math.max(ctx.map.getZoom(), 8) }); },
    current: () => result,
  };
}
