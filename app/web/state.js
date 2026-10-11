// URL-hash state: every map is a bookmarkable link.
//   #engine=na-ferry&flavor=dark&v=6.2/42.36/-71.06&tool=route&route=-71.06,42.36;-73.45,44.70
// Global keys: engine, flavor, v (view), tool. Each tool owns one key (its id) with a compact string.

const listeners = new Map();   // key -> Set(fn)
let data = parse(location.hash);
let timer = null;
let suppress = false;

function parse(hash) {
  const out = {};
  for (const [k, v] of new URLSearchParams(hash.replace(/^#/, ""))) out[k] = v;
  return out;
}

function write() {
  timer = null;
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(data)) if (v != null && v !== "") p.set(k, v);
  const next = "#" + p.toString().replace(/%2C/g, ",").replace(/%3B/g, ";").replace(/%2F/g, "/");
  if (next !== location.hash) { suppress = true; history.replaceState(null, "", next); suppress = false; }
}

export function get(key) { return data[key]; }

export function set(key, value, { silent = false } = {}) {
  if (data[key] === value) return;
  if (value == null || value === "") delete data[key]; else data[key] = String(value);
  if (!timer) timer = setTimeout(write, 250);
  if (!silent) for (const fn of listeners.get(key) || []) fn(data[key]);
}

export function on(key, fn) {
  if (!listeners.has(key)) listeners.set(key, new Set());
  listeners.get(key).add(fn);
  return () => listeners.get(key).delete(fn);
}

/** Per-tool namespace: one hash key named after the tool. */
export function ns(id) {
  return { get: () => data[id], set: (v, o) => set(id, v, o), on: (fn) => on(id, fn) };
}

window.addEventListener("hashchange", () => {
  if (suppress) return;
  const next = parse(location.hash);
  const keys = new Set([...Object.keys(data), ...Object.keys(next)]);
  data = next;
  for (const k of keys) for (const fn of listeners.get(k) || []) fn(data[k]);
});
