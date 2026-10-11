// tiny DOM helpers shared by every tool panel

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "html") el.innerHTML = v;
    else if (v === false || v == null) continue;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) el.append(c.nodeType ? c : document.createTextNode(String(c)));
  return el;
}

export const btn = (label, onClick, cls = "") => h("button", { class: `btn ${cls}`, onClick }, label);
export const row = (...kids) => h("div", { class: "row" }, ...kids);
export const help = (text) => h("p", { class: "help" }, text);

export function stat(items) {           // [[value, label], ...]
  return h("div", { class: "stat" }, items.map(([v, l]) => h("div", {}, h("b", {}, v), h("span", {}, l))));
}

export function progress() {
  const bar = h("i");
  const el = h("div", { class: "progress" }, bar);
  el.set = (p) => { bar.style.width = `${Math.round(100 * p)}%`; };
  return el;
}

/** Sortable table. cols: [{key, label, num, fmt}], rows: objects. onRow(row) optional. */
export function table(cols, rows, onRow) {
  const el = h("table", { class: "t" });
  let sortKey = null, dir = 1;
  const render = () => {
    const data = sortKey ? [...rows].sort((a, b) => (a[sortKey] > b[sortKey] ? 1 : a[sortKey] < b[sortKey] ? -1 : 0) * dir) : rows;
    el.replaceChildren(
      h("thead", {}, h("tr", {}, cols.map((c) => h("th", { class: c.num ? "num" : "", onClick: () => { dir = sortKey === c.key ? -dir : 1; sortKey = c.key; render(); } },
        c.label + (sortKey === c.key ? (dir > 0 ? " ▲" : " ▼") : ""))))),
      h("tbody", {}, data.map((r) => h("tr", { class: onRow ? "click" : "", onClick: onRow ? () => onRow(r) : null },
        cols.map((c) => { const v = c.fmt ? c.fmt(r[c.key], r) : r[c.key]; return h("td", { class: c.num ? "num" : "" }, v == null ? "" : v); })))),
    );
  };
  render();
  return el;
}

export function toast(msg, kind = "") {
  const el = h("div", { class: `toast ${kind}` }, msg);
  document.getElementById("toasts").append(el);
  setTimeout(() => el.remove(), kind === "bad" ? 9000 : 4000);
  return el;
}

export function status(msg) { document.getElementById("status").textContent = msg || ""; }

export function download(name, data, type = "application/octet-stream") {
  const blob = data instanceof Blob ? data : new Blob([data], { type });
  const a = h("a", { href: URL.createObjectURL(blob), download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

export const fmtH = (h) => (h == null || Number.isNaN(h)) ? "–" : h < 1 ? `${Math.round(h * 60)} min` : `${Math.floor(h)} h ${String(Math.round((h % 1) * 60)).padStart(2, "0")}`;
export const fmtMi = (mi) => mi == null ? "–" : mi < 10 ? `${mi.toFixed(1)} mi` : `${Math.round(mi).toLocaleString()} mi`;
export const fmtLL = ([lon, lat]) => `${lat.toFixed(4)}, ${lon.toFixed(4)}`;

/** Parse "lat, lon" or "lon, lat" (OSRM order if |first| > 90). Returns [lon, lat] or null. */
export function parseLatLon(text) {
  const m = String(text).trim().match(/^(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)$/);
  if (!m) return null;
  let a = +m[1], b = +m[2];
  if (Math.abs(a) > 90 && Math.abs(b) <= 90) return [a, b];       // lon, lat
  if (Math.abs(a) <= 90 && Math.abs(b) <= 180) return [b, a];      // lat, lon
  return null;
}
