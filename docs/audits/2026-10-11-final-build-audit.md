# my-osrm final-build audit: findings and fixes

## Context

The overnight run (2026-10-10 23:53) got through every PBF stage and stopped at `osrm-extract` with no
engine written (`data/osrm-na-ferry/` has the PBFs and `.done` markers but no `na-ferry.osrm.*`). Before
the final build, Max asked for an audit of the whole Docker build path. This plan lists what I verified
and the fixes, ordered by routing impact. Another session ("OSRM setup and optimization") is writing the
master plan and already covers car.lua and parts of the data pipeline; overlaps are marked.

Current state checked: compose config valid; 27 app tests pass; `us-major`/`na-major` running
(3.4 + 3.7 GB RAM of the 24 GB WSL cap); D: has 1.2 TB free; C: is 92 % full (81 GB free, Docker's
WSL vhdx + 24 GB swap live there).

## HIGH

### 1. Turn restrictions are completely lost (data pipeline, not covered by the other session)
- **Evidence**: `osmium fileinfo -e data/osrm-na-ferry/na-ferry.osm.pbf` → `Number of relations: 0`.
  Same for `data/osrm-major/us-major.osm.pbf`. `use_turn_restrictions = true` in car.lua has nothing to use.
- **Mechanism**: `osmium tags-filter w/highway=...` keeps matching ways plus objects *they* reference
  (nodes). Relations that reference the ways (`type=restriction`) are not pulled in.
- **Fix**: add `r/type=restriction` as a second filter expression in `MAJOR_FILTER` usage in
  `scripts/build_ferry_engine.py:171` (pass it as an extra arg after `MAJOR_FILTER`), and in
  `scripts/filter_pbf.sh:270`, `scripts/build_na_osrm.sh:366`. Verified on Puerto Rico: 589 relations
  come back; cost is +1.5 % ways (189 residential, 48 service, 22 unclassified stubs that are
  restriction members). Also apply to the north filter? No: `NORTH_FILTER` ways get restrictions via the
  same relations already pulled by the major filter only if a major way is a member; acceptable.
- **Rebuild impact**: every country's `*-major.osm.pbf` must be regenerated → delete those files and their
  `.done` markers, then merge → extract rerun (≈ 30–45 min of osmium before extract starts).

### 2. Real car ferries dropped by `car_ferries.py` (overlaps the other session's "stranded famous ferries")
- **Evidence** from `data/osrm-na-ferry/ferries/ferry-filter-report.json` (771 dropped):
  - "foot passengers, silent on cars" rule (619) drops: **South Baymouth–Tobermory (Chi-Cheemaun)**,
    **Seattle–Bremerton (WSF)**, **Milwaukee–Muskegon (Lake Express)**, Oxford–Bellevue MD,
    Green River Ferry (Mammoth Cave), Valley View Ferry KY, likely Kennebecasis Island NB and one
    Hyannis–Nantucket way (Steamship Authority carries cars; Hy-Line does not).
  - "rail/freight/tour name" rule (146): `"rail "` substring matches **"Trail "** (West Coast Trail,
    Tutka Trail, freezeout Trail — passenger-only today, but the match is accidental);
    "Fox Lake Access Peace River Barge" is a vehicle barge.
- **Fix** in `scripts/car_ferries.py`:
  - word-boundary regex for `FREIGHT_WORDS` (`\brail\b`, `\bbarge\b` …) instead of substring;
  - add an explicit override table (`scripts/ferry_overrides.toml` or a dict): `keep = [way ids]` /
    `drop = [way ids]` consulted before the heuristics; seed it with the ferries above;
  - write the "foot-silent" drops to a separate review list so false drops are visible each build.
- Add report-card cases to `benchmarks/routes.toml`: Tobermory → South Baymouth (Manitoulin),
  Seattle → Bremerton, Milwaukee → Muskegon (max_miles well under the land detour).

### 3. Compose healthcheck never passes (`docker-compose.yml:15`)
- **Evidence**: `docker inspect` health log: `/bin/sh: -77.0,38.9: not found` — CMD-SHELL splits the URL
  at `;`. Both running engines show `(unhealthy)`. Second bug: inside the Alpine image `localhost`
  resolves to `::1` and osrm-routed listens on IPv4 only → `Connection refused` even with the URL fixed.
- **Fix**: exec form with 127.0.0.1:
  `test: ["CMD", "wget", "-qO-", "http://127.0.0.1:5000/route/v1/driving/-77.0,38.9;-77.0,38.9"]`
  (verified: this exact wget returns `{"code":"Ok"…}` inside the container). Keep `start_period`, raise
  it to 120s: na-ferry loads ~6 GB. Affects `app/osrm.py` health dots only indirectly (app probes HTTP
  itself), but `restart: unless-stopped` + unhealthy is what Docker Desktop shows.

## MEDIUM

### 4. Memory contention during the build
- Two engines are running now (7 GB). `osrm-extract` on the 764 MB merged PBF with 6 threads peaks
  ~10–14 GB; WSL cap is 24 GB. Run `docker compose --profile major stop` before the build, and have
  `scripts/overnight.ps1` do that first. Also `docker compose stop na-ferry` before wiping the output
  dir: the old container restart-looped 61 times after `OUT` was emptied (`update_data.py` does stop it;
  `overnight.ps1` does not).

### 5. `.done` markers don't track inputs (overlaps the other session's ".done markers" item)
- `stage()` in `build_ferry_engine.py:152` skips whenever sentinel + marker exist, regardless of whether
  an input changed (new country, filter change). Fix: skip only if the marker is newer than every
  input path passed to the stage (add an `inputs: list[Path]` arg, compare mtimes). This makes fix #1's
  rebuild automatic instead of hand-deleting files.

### 6. Overnight script hygiene (`scripts/overnight.ps1`)
- Commits mp4/gif/png binaries into git every run (repo bloat; the working tree already shows 4 modified
  binaries). Keep outputs out of git or commit only `report_card.txt` + html.
- Hard-coded `Co-Authored-By: Claude Opus 5.5` in the commit message; parametrize or drop.
- Log captured nothing from the extract because output is filtered with `Select-String -NotMatch
  '\[info\]'` — keep OSRM `[info]` lines in the log file and filter only the console.

### 7. Docs drift
- README and compose comments say "US + Canada (+ Mexico)", "Report card 8/8"; the build now has 7
  regions (us, ca, mx, cam, pr, bm, gl) and the README table says 10/10. README claims "all Docker
  images pinned by digest" but `tools/Dockerfile:70` uses `python:3.12-slim` by tag. Pin it, and note the
  Central America Geofabrik extract also brings the whole Caribbean (Guadeloupe, BVI, Curaçao ferries
  appear in the reports) — harmless islands, but say so.

## LOW

### 8. Git hygiene
- `scripts/__pycache__/*.pyc` (3 files) are tracked despite `.gitignore`: `git rm --cached -r scripts/__pycache__`.
- `experiments/isochrone/out/overnight.log` is untracked and will be committed by the overnight step's
  globs? (no: globs are html/mp4/gif/png) — add `*.log` under `out/` to `.gitignore`.

### 9. Border crossings (overlaps the other session's "border double-crossings")
- 540 marked nodes; multi-lane plazas mark each lane way ("All Travelers Lane" ×15, "Ready Lane" ×10).
  A car passes one lane, so no double charge there. Roads that run *along* the boundary line would be
  marked by `intersects`; use `crosses` predicate to skip touching/collinear cases.

### 10. Two osmium images
- `filter_pbf.sh` / `build_na_osrm.sh` use `iboates/osmium` (1.7 GB); the main build uses
  `my-osrm-tools` (316 MB). Point the legacy scripts at `my-osrm-tools` so one image builds everything.

## Who does what (agreed with the "OSRM setup and optimization" session)

That session has folded all 10 findings into its master plan
(`C:\Users\mhowe\.claude\plans\next-i-want-to-cozy-cupcake.md`) and will apply every fix under
`scripts/`, `profiles/` and `experiments/isochrone/`, plus two of its own (domestic ferries charged
customs via territorial-sea lines; ungate version tie-break). To avoid collisions, **this session only
edits**: `docker-compose.yml` (#3 healthcheck, stale comments), `README.md` (#7), `tools/Dockerfile`
(#7 digest pin), `.gitignore` + `git rm --cached` (#8), `benchmarks/routes.toml` (the three new ferry
cases from #2), and `app/` if anything there needs it. Nothing in `scripts/` or `profiles/` from here.

## Order of work for the final build

1. Fixes #1, #2, #3 (code), #5 (freshness), #8.
2. `docker compose --profile major stop`; delete `data/*/…-major.osm.pbf` + `.done`, `data/osrm-na-ferry/na-*`.
3. `python scripts/build_ferry_engine.py` (expect ~45 min osmium + extract/partition/customize ~40–60 min).
4. `docker compose up -d na-ferry`; wait for `(healthy)`.

## Verification

- `docker run --rm -v D:/REPOS/my-osrm/data:/data my-osrm-tools osmium fileinfo -e /data/osrm-na-ferry/na-ferry.osm.pbf | grep relations` → non-zero.
- `docker inspect my-osrm-na-ferry --format '{{.State.Health.Status}}'` → `healthy`.
- `python benchmarks/report_card.py --url http://127.0.0.1:5002 --engine na` → all cases pass incl. the three new ferry cases.
- `python app/smoke.py --engine na-ferry`; `python -m pytest app/tests`.
- Spot-check a known turn restriction (e.g. a no-left-turn at a trunk intersection) via `/route` before vs after.
