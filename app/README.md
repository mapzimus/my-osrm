# app/ — offline map interface

A browser app on this PC that talks only to `127.0.0.1`: the OSRM engines in
Docker, a local Protomaps basemap, vendored MapLibre. Pull the network cable
and everything still works.

```bash
python -m app.setup          # once: vendor JS, basemap (~34 GB), fonts, place index, ferry overlay
docker compose up -d         # the routing engine(s)
python -m app                # http://127.0.0.1:8080
```

`python -m app.setup --only basemap --maxzoom 10` gives a 1 GB basemap in
about a minute if you want to start before the full one finishes. The app
prefers `data/basemap/north-america.pmtiles` and falls back to the `-z10` file.

## Tools

| Tab | What | API |
|---|---|---|
| Engines | pick which container every tool uses; health dots | `GET /api/engines` |
| Route | click A, B, C…; drag markers; turn list; "Best order" (OSRM `/trip`) | `POST /api/route`, `/api/route/trip` |
| Isochrone | drive-time bands from one point; preset regions or the current view; background job with progress | `POST /api/isochrone/jobs` |
| Matrix | N×N drive times, sortable, CSV | `POST /api/matrix`, `/csv` |
| Export | PNG snapshot (with attribution), GeoJSON of everything drawn, GPX of the route, copy link | `POST /api/export/gpx` |
| Compare | same trip on every running engine, overlaid, with a time table | `POST /api/compare` |
| Inspect | click: snapped node/way, road class, speed the profile assigned; ferry + dock-connector overlay | `GET /api/inspector/nearest` |
| Report card | `benchmarks/routes.toml` run as a job, pass/fail routes drawn on the map | `POST /api/benchmarks/run` |
| Places | offline city/town/village search (also the top search box; press `/`) | `GET /api/places?q=` |

Every map is a URL: `#engine=na-ferry&flavor=dark&v=6.2/42.36/-71.06&tool=route&route=-71.06,42.36;-73.45,44.70`.

## Adding a tool

Two files, nothing to register:

```
app/tools/<id>.py          meta = ToolMeta(id="<id>", title="…", order=…); router = APIRouter()
app/web/tools/<id>.js      export const id, title; export async function init(ctx) { … return {activate, deactivate, onPlace} }
```

`ctx` gives you `map` (MapLibre), `api` (get/post/follow a job), `state` (your own URL-hash key),
`layers` (overlay registry that survives basemap style swaps), `engines`, `panel` (your `<section>`),
`onMapClick(fn)` (only fires while your tab is active) and `marker(lngLat, label, opts)`.
Slow work goes through `app/jobs.py`: `request.app.state.jobs.submit(kind, fn)` and the JS follows it
with `ctx.api.follow(jobId, onProgress)`.

## Layout

| Path | What |
|---|---|
| `main.py`, `registry.py`, `osrm.py`, `jobs.py`, `config.py` | app factory, tool discovery, OSRM client, background jobs, paths/pins |
| `tools/` | one Python router per tool |
| `web/` | `index.html` (import map), `app.js` (boot), `map.js`, `layers.js`, `state.js`, `panel.js`, `points.js`, `tools/*.js` |
| `web/vendor/` | gitignored; filled by `app.setup` (MANIFEST.json records versions) |
| `setup/` | idempotent stages: `vendor`, `cli`, `assets`, `basemap`, `ferries`, `places` |
| `tests/` | pytest with a fake OSRM: `python -m pytest app/tests` |
| `smoke.py` | against a real engine: `python app/smoke.py --url http://127.0.0.1:5002` |

Derived data lives in `data/app/` (places.sqlite, ferries.geojson), `data/basemap/`, `data/bin/`.

## Notes

- Basemap: Protomaps daily build, North America bbox, z0–15. Dailies expire after a week;
  `data/basemap/BUILD.txt` records which one you have. Re-run `--only basemap` after deleting the
  file to refresh (set `BASEMAP_BUILD` in `config.py` to pin one).
- The ferry overlay reads `data/build/<cc>/ferry-connectors.osm.pbf`; delete `data/app/ferries.geojson`
  and re-run `--only ferries` after a ferry rebuild adds a country.
- Isochrone land fill shells out to the `my-osrm-tools` image (as the CLI does); untick "land fill" if
  Docker isn't running.
- MapLibre needs WebGL to draw; a hidden browser pane (e.g. a background tab) stalls rendering until shown.
