# Plan: `app/` — local, fully offline map interface for my-osrm

## Context

The OSRM engines (Docker, ports 5000/5001/5002) and the isochrone/report-card scripts all exist, but
the only "interface" is the CLI plus static HTML that loads Leaflet and basemap tiles from the
internet. Max wants a browser app on BRONTOSAURUS that works with the network cable pulled: make
routes, isochrones, matrices, export them, and (extras chosen) run the report card visually, compare
engines, inspect the network, and bookmark any map by URL. It must be easy to keep adding tools.

Decisions already made with Max: FastAPI backend + MapLibre frontend (option 1), localhost only,
Protomaps PMTiles vector basemap for North America, all JS/fonts/sprites vendored (no CDN).

**Live caveat found during planning:** `na-ferry` is mid-rebuild (`data/osrm-na-ferry/` absent,
container exited). The app must treat every engine as optional and show health.

## Architecture (one paragraph)

`python -m app` starts uvicorn on `127.0.0.1:8080`. The server serves `app/web/` (static shell),
`/vendor` (MapLibre, pmtiles.js, fflate, Protomaps style, fonts, sprites), `/basemap`
(`data/basemap/*.pmtiles` via Starlette `StaticFiles`, whose `FileResponse` does HTTP Range in
Starlette 1.0), and `/api/<tool>` routers. The browser never talks to OSRM directly; `app/osrm.py`
proxies it so tests can fake it and the engine picker lives in one place. Tools are plugins: every
`app/tools/<id>.py` exports `meta` + `router`, every `app/web/tools/<id>.js` exports
`{id, title, init(ctx)}`; `GET /api/tools` lists ids and the frontend dynamic-imports them.

## File tree

```
app/
  __main__.py          python -m app -> uvicorn (127.0.0.1:8080)
  config.py            ENGINES {id: title, url, compose_service} from docker-compose ports; paths; version pins
  main.py              create_app(): include discovered routers, /api/tools, /api/jobs, static mounts
  registry.py          pkgutil over app.tools; validate meta.id == module name
  osrm.py              async httpx: route/table/nearest/trip/health; NoRoute -> 404 JSON
  jobs.py              ThreadPoolExecutor(2) job manager; progress, cancel Event, SSE + polling
  tools/               engines, route, isochrone, matrix, export, benchmarks, compare, inspector, places
  web/
    index.html         import map -> /vendor; #map + sidebar tabs
    app.js map.js layers.js state.js api.js panel.js style.css
    tools/<id>.js      one per tool
    vendor/            gitignored except MANIFEST.json (filled by setup)
  setup/
    __main__.py        python -m app.setup [--only stage] [--maxzoom N]; idempotent, resumable
    download.py        .part + Range resume + sha verify + os.replace
    vendor.py          pinned npm tarballs: maplibre-gl 6.13.0, pmtiles 4.5.0, fflate 0.8.2, @protomaps/basemaps 5.7.2
    pmtiles_cli.py     go-pmtiles v1.31.2 Windows zip -> data/bin/pmtiles.exe
    basemap.py         pmtiles extract <daily build> --bbox=-170,5,-50,72 [--maxzoom] -> data/basemap/
    assets.py          basemaps-assets main.zip -> fonts/ + sprites/v4/
    places.py          docker my-osrm-tools: osmium tags-filter place=city,town,village -> places_build.py -> data/app/places.sqlite (FTS5)
    ferries.py         docker my-osrm-tools: data/build/*/ferry-connectors.osm.pbf + ferry-report.json -> data/app/ferries.geojson
  tests/               conftest (MockTransport fake OSRM), fake_osrm.py, one test file per tool, test_static_range.py
  smoke.py             end-to-end against a real engine
  requirements.txt     fastapi uvicorn httpx numpy scipy matplotlib pillow pytest (all already installed on Py 3.14.3; no conda)
  README.md
```

Derived data goes under `data/app/`, `data/basemap/`, `data/bin/` (already gitignored by `data/*`).

## Tools (v1)

| id | Python | JS |
|---|---|---|
| engines | `GET /api/engines` configured + live health (1 s timeout) | picker + health badges; writes `engine` to hash |
| route | `POST /api/route {engine, coords[]}` steps=true, geometries=geojson | click to add A/stops/B, drag markers, line, hours/miles, turn list |
| isochrone | `POST /api/isochrone/jobs` -> job; `GET .../image.png` | point + hours + region/grid; progress bar (SSE); image overlay + legend; cancel |
| matrix | `POST /api/matrix` N×N; `/csv` | place list, sortable table, CSV download |
| export | `POST /api/export/gpx` | PNG snapshot (with ODbL attribution), GeoJSON of overlays, GPX |
| benchmarks | `GET /cases` (routes.toml); `POST /run {engine}` -> job | pass/fail routes drawn green/red, score, click a case to zoom |
| compare | `POST /api/compare {coords}` per healthy engine | overlaid colored lines + hours/miles table |
| inspector | `GET /nearest?engine&lon&lat`; `GET /ferries.geojson` | click -> node ids, way name, road class/mode; ferry connector overlay toggle |
| places | `GET /api/places?q=` FTS5 prefix, by population | search box with keyboard nav, fly-to, also accepts `lat, lon` |

Cross-cutting: `state.js` URL hash (`#engine=na-ferry&flavor=dark&v=6.2/42.36/-71.06&tool=route&route=...`),
basemap flavor toggle light/dark via `map.setStyle(next, {transformStyle})` with `layers.js` re-applying overlays.

## Reuse (do not rewrite)

- `experiments/isochrone/isochrone.py`: `grid`, `drive_hours`, `land_mask`, `fill_land`, `band_png`, `band_areas`, `city_times`, `REGIONS`. Minimal edits: add optional `progress`/`cancel` callbacks to `drive_hours` (replace the two prints), let `compute_hours` accept a `(bbox, step)` tuple, extract `build_payload(...) -> dict` out of `main()`, add `fill=False` path (land_mask shells out to Docker). Import the same way `animate.py` does (`sys.path.insert(0, HERE)`); note the sibling `import flights`.
- `benchmarks/report_card.py`: `route()` and `grade()` used verbatim by the benchmarks tool.
- `scripts/build_ferry_engine.py`: copy its Docker invocation and sentinel/idempotency pattern for `app/setup`.
- `experiments/isochrone/template.html`: reuse its CSS variables/palette for `style.css`.

## Gotchas to honor

- MapLibre 6: `canvasContextAttributes: {preserveDrawingBuffer: true}` at construction (needed for PNG export).
- `pmtiles` ESM bare-imports `fflate` -> import map + vendored fflate; a test scans vendored JS for uncovered bare imports.
- No GZip middleware on `/basemap`; never replace the pmtiles file while running (write `.part`, rename, restart).
- Basemap size: planet z0-15 is ~120 GB; NA at z15 estimated 20-35 GB, roughly halving per zoom dropped. First run `--maxzoom 10` (~1 GB, minutes) so the app is usable today; full z15 extract runs overnight. Daily builds expire after a week: pin the build id in config and expect to re-pin.
- Engines may all be down (na-ferry is rebuilding now): compare/benchmarks skip unhealthy engines rather than error.
- Windows: pathlib, `os.replace`, sqlite connection per request, uvicorn spawn-safe `__main__` guard; add `*.js *.css *.html text eol=lf` to `.gitattributes`.

## Ordered steps (each verifiable)

1. Scaffold `app/` (config, registry, main, index shell, requirements). Verify: `python -m app` serves the page; `pytest tests/test_registry.py`.
2. `osrm.py` + fake OSRM fixture + `engines` tool. Verify: pytest; `/api/engines` lists down engines gracefully.
3. `setup/vendor.py` + import map + `map.js` with blank style. Verify: map renders, console clean, vendor scan passes.
4. `setup/pmtiles_cli.py`, `setup/assets.py`, `setup/basemap.py --maxzoom 10`, `/basemap` mount. Verify: NA basemap renders with labels; Network tab shows 206s; `test_static_range.py`.
5. Flavor toggle + `state.js` hash. Verify: reload restores view/flavor/engine.
6. `route` tool. Verify: `test_route.py`; Grand Isle -> Plattsburgh shows a ferry step (once an engine is up).
7. `inspector` tool + `setup/ferries.py`. Verify: click a dock shows node/way; overlay toggles.
8. `matrix` tool + CSV. Verify: `test_matrix.py`.
9. `export` tool. Verify: `test_export.py`; PNG not blank.
10. `jobs.py` + SSE; isochrone.py refactor; `isochrone` tool. Verify: CLI output unchanged (diff HTML size before/after); `test_isochrone.py`; a regional run shows live progress.
11. `benchmarks` tool as a job. Verify: `test_benchmarks.py`; score matches CLI.
12. `compare` tool. Verify: `test_compare.py`.
13. `places` setup + search box. Verify: `test_places.py`; "Plattsburgh" resolves.
14. Kick off full z15 basemap extract (background, hours); `app/README.md`; `.gitattributes`; `smoke.py` against :5002.

Commit after each step. Step 0 (housekeeping): copy this plan to `docs/superpowers/specs/2026-10-10-offline-map-app-design.md` so the repo carries the spec.

## Verification (end to end)

- `pytest app/tests` green with the fake OSRM.
- With `docker compose up -d` healthy: `python app/smoke.py --url http://127.0.0.1:5002` passes (health, 3 report-card cases, nearest on a ferry dock, regional isochrone job).
- Manual offline check: disable Wi-Fi, open `http://127.0.0.1:8080`, pan/zoom the basemap, make a route, an isochrone, export a PNG. Browser Network tab shows only 127.0.0.1 requests.
