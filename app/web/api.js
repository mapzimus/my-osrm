// fetch helpers + background-job follower
import { toast } from "./panel.js";

async function handle(r) {
  let data = null;
  try { data = await r.json(); } catch { /* not json */ }
  if (!r.ok) {
    const msg = (data && (data.detail || data.message)) || `${r.status} ${r.statusText}`;
    const err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    err.status = r.status;
    throw err;
  }
  return data;
}

export async function get(path, params) {
  const url = new URL(path, location.origin);
  for (const [k, v] of Object.entries(params || {})) if (v != null) url.searchParams.set(k, v);
  return handle(await fetch(url));
}

export async function post(path, body) {
  return handle(await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }));
}

export async function del(path) {
  return handle(await fetch(path, { method: "DELETE" }));
}

/** Follow a job via SSE (polling fallback). onUpdate({status,progress,message}); resolves with the result. */
export function follow(jobId, onUpdate) {
  return new Promise((resolve, reject) => {
    let finished = false;
    const finish = async (snap) => {
      if (finished) return;
      finished = true;
      if (snap.status === "done") {
        try { resolve((await get(`/api/jobs/${jobId}`, { result: true })).result); } catch (e) { reject(e); }
      } else if (snap.status === "cancelled") {
        const e = new Error("cancelled"); e.cancelled = true; reject(e);
      } else reject(new Error(snap.error || "job failed"));
    };
    const es = new EventSource(`/api/jobs/${jobId}/events`);
    es.onmessage = (ev) => {
      const snap = JSON.parse(ev.data);
      onUpdate && onUpdate(snap);
      if (["done", "failed", "cancelled"].includes(snap.status)) { es.close(); finish(snap); }
    };
    es.onerror = async () => {       // SSE dropped: poll instead
      es.close();
      while (!finished) {
        await new Promise((r) => setTimeout(r, 700));
        let snap;
        try { snap = await get(`/api/jobs/${jobId}`); } catch (e) { reject(e); return; }
        onUpdate && onUpdate(snap);
        if (["done", "failed", "cancelled"].includes(snap.status)) { finish(snap); return; }
      }
    };
  });
}

export function cancel(jobId) { return del(`/api/jobs/${jobId}`).catch(() => {}); }

/** Run fn, toast on error (unless cancelled). */
export async function guard(fn, label) {
  try { return await fn(); }
  catch (e) {
    if (e && e.cancelled) return undefined;
    toast(`${label ? label + ": " : ""}${e.message || e}`, "bad");
    console.error(e);
    return undefined;
  }
}
