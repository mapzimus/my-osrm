// Place search panel (the top search box uses the same index). Fly to a place, or send it to the active tool.
import { h, help, table } from "../panel.js";

export const id = "places";
export const title = "Places";

export async function init(ctx) {
  const input = h("input", { type: "text", placeholder: "city, town or village", style: "width:100%" });
  const out = h("div");
  let timer = null;
  ctx.panel.append(
    h("h2", {}, "Places"),
    ctx.cfg.places_index ? help("Offline index of OSM cities, towns and villages. Tip: press / anywhere to jump to the search box.")
      : help("No place index yet. Build it once (needs Docker + the raw extracts in data/):  python -m app.setup --only places"),
    input, out,
  );
  input.oninput = () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) return out.replaceChildren();
    timer = setTimeout(async () => {
      const rows = await ctx.api.get("/api/places", { q, limit: 30 }).catch(() => []);
      out.replaceChildren(table([
        { key: "name", label: "name" }, { key: "region", label: "region" }, { key: "kind", label: "kind" },
        { key: "population", label: "pop", num: true, fmt: (v) => v ? v.toLocaleString() : "" },
      ], rows, (r) => ctx.map.flyTo({ center: [r.lon, r.lat], zoom: r.kind === "city" ? 10 : 12 })));
    }, 120);
  };
  return {};
}
