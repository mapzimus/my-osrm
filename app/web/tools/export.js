// Export what's on the map: PNG snapshot, GeoJSON of every overlay, GPX of the route, share link.
import { h, btn, row, help, download, toast } from "../panel.js";

export const id = "export";
export const title = "Export";

export async function init(ctx) {
  const list = h("div", { class: "help" });
  ctx.panel.append(
    h("h2", {}, "Export"),
    help("Everything is rendered locally; exports are files on this PC."),
    row(btn("PNG snapshot", png, "primary"), btn("GeoJSON", geojson), btn("GPX route", gpx), btn("Copy link", link)),
    h("h3", {}, "on the map"),
    list,
  );

  function refresh() {
    const items = ctx.layers.list();
    list.replaceChildren(items.length ? h("ul", {}, ...items.map((o) => h("li", {}, `${o.id} (${o.kind})`))) : "nothing drawn yet");
  }

  async function png() {
    const canvas = ctx.map.getCanvas();
    const out = document.createElement("canvas");
    out.width = canvas.width; out.height = canvas.height;
    const g = out.getContext("2d");
    g.drawImage(canvas, 0, 0);
    // image overlays + markers are WebGL/DOM; markers aren't in the canvas, so stamp them
    for (const m of document.querySelectorAll(".marker")) {
      const r = m.getBoundingClientRect(), c = canvas.getBoundingClientRect();
      const s = canvas.width / c.width;
      const x = (r.left + r.width / 2 - c.left) * s, y = (r.bottom - c.top) * s;
      g.fillStyle = getComputedStyle(m).backgroundColor; g.strokeStyle = "#fff"; g.lineWidth = 2 * s;
      g.beginPath(); g.arc(x, y - 8 * s, 7 * s, 0, Math.PI * 2); g.fill(); g.stroke();
      g.fillStyle = "#fff"; g.font = `bold ${10 * s}px sans-serif`; g.textAlign = "center"; g.textBaseline = "middle";
      g.fillText(m.textContent, x, y - 8 * s);
    }
    const scale = canvas.width / canvas.getBoundingClientRect().width;
    const text = "© OpenStreetMap contributors · Protomaps · OSRM";
    g.font = `${11 * scale}px sans-serif`; g.textAlign = "right"; g.textBaseline = "bottom";
    const tw = g.measureText(text).width;
    g.fillStyle = "rgba(255,255,255,.75)"; g.fillRect(out.width - tw - 12 * scale, out.height - 18 * scale, tw + 12 * scale, 18 * scale);
    g.fillStyle = "#333"; g.fillText(text, out.width - 6 * scale, out.height - 3 * scale);
    out.toBlob((b) => download(`map_${stamp()}.png`, b), "image/png");
  }

  function geojson() {
    const feats = [];
    for (const o of ctx.layers.list()) {
      if (o.kind !== "geojson") continue;
      const fc = o.data.type === "FeatureCollection" ? o.data.features : [o.data.type === "Feature" ? o.data : { type: "Feature", properties: {}, geometry: o.data }];
      for (const f of fc) feats.push({ ...f, properties: { layer: o.id, ...(f.properties || {}) } });
    }
    if (!feats.length) return toast("Nothing to export yet", "bad");
    download(`map_${stamp()}.geojson`, JSON.stringify({ type: "FeatureCollection", features: feats }, null, 1), "application/geo+json");
  }

  async function gpx() {
    const route = ctx.layers.list().find((o) => o.id === "route");
    if (!route) return toast("Make a route first (Route tab)", "bad");
    const f = route.data.features[0];
    const wps = (f.properties.waypoints || []).map((n) => ({ name: n }));
    const coords = f.geometry.coordinates;
    const body = { name: f.properties.name || "route", geometry: f.geometry,
      waypoints: wps.map((w, i) => ({ ...w, location: i === 0 ? coords[0] : i === wps.length - 1 ? coords.at(-1) : coords[Math.floor(coords.length * i / (wps.length - 1))] })) };
    const r = await fetch("/api/export/gpx", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) return toast("GPX export failed", "bad");
    download(`route_${stamp()}.gpx`, await r.blob());
  }

  async function link() {
    try { await navigator.clipboard.writeText(location.href); toast("Link copied", "ok"); }
    catch { prompt("Copy this link", location.href); }
  }

  const stamp = () => new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "");
  return { activate: refresh };
}
