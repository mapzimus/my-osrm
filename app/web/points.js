// Shared "list of map points" widget: click-to-add markers, draggable, removable, serializable.
import { h, btn, fmtLL } from "./panel.js";

const LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

/**
 * pointList(ctx, {max, color, onChange, label(i, n)})
 * returns {points: [{lonlat, name}], add, remove, clear, set, el, serialize, parse}
 */
export function pointList(ctx, { max = 50, color, onChange, label = (i, n) => (n <= 26 ? LABELS[i] : String(i + 1)), compact = false } = {}) {
  const points = [];
  const markers = [];
  const el = h("ul", { class: "pts" });

  const fire = () => { render(); onChange && onChange(points); };

  function render() {
    for (const m of markers) m.remove();
    markers.length = 0;
    points.forEach((p, i) => {
      const m = ctx.marker(p.lonlat, label(i, points.length), {
        color,
        onDrag: (ll) => { p.lonlat = ll; p.name = ""; fire(); },
        onClick: () => remove(i),
      });
      markers.push(m);
    });
    el.replaceChildren(...points.map((p, i) => h("li", {},
      h("span", { class: "tag", style: color ? `background:${color}` : "" }, label(i, points.length)),
      h("span", { title: fmtLL(p.lonlat) }, p.name || fmtLL(p.lonlat)),
      h("button", { class: "btn small x", title: "remove", onClick: () => remove(i) }, "×"))));
  }

  function add(lonlat, name = "") {
    if (points.length >= max) { points.pop(); }
    points.push({ lonlat: [+lonlat[0], +lonlat[1]], name });
    fire();
  }
  function remove(i) { points.splice(i, 1); fire(); }
  function clear() { points.length = 0; fire(); }
  function set(arr) { points.length = 0; for (const p of arr) points.push({ lonlat: p.lonlat, name: p.name || "" }); fire(); }
  function serialize() { return points.map((p) => `${p.lonlat[0].toFixed(5)},${p.lonlat[1].toFixed(5)}`).join(";"); }
  function parse(str) {
    if (!str) return false;
    const arr = str.split(";").map((s) => s.split(",").map(Number)).filter((a) => a.length === 2 && a.every(Number.isFinite));
    if (!arr.length) return false;
    set(arr.map((lonlat) => ({ lonlat })));
    return true;
  }
  function hide() { for (const m of markers) m.remove(); }
  function show() { render(); }

  return { points, add, remove, clear, set, el, serialize, parse, hide, show, coords: () => points.map((p) => p.lonlat) };
}
