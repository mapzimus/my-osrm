# my-osrm

A self-hosted [OSRM](https://github.com/Project-OSRM/osrm-backend) routing engine for driving in the US, Canada and Mexico,
with ferries, running in Docker. Extracted from [mapzimus/optitrek](https://github.com/mapzimus/optitrek),
where it powered the road-trip optimizer.

## Quick start

```bash
docker compose up -d                      # US + Canada + Mexico + ferries on :5002
python benchmarks/report_card.py --url http://127.0.0.1:5002 --engine na
```

Grand Isle VT → Plattsburgh NY over the Lake Champlain ferry:
`http://127.0.0.1:5002/route/v1/driving/-73.3510,44.7206;-73.4529,44.6995?overview=false`

## Engines

| Engine | Compose service | Port | Size | Report card |
|---|---|---|---|---|
| **US + Canada + Mexico major roads + ferries** | `na-ferry` (default) | 5002 | ~6 GB | **10/10** (incl. drive-time bands) |
| US + Canada major roads | `na-major` (`--profile major`) | 5001 | ~6 GB | 4/8 |
| US major roads | `us-major` (`--profile major`) | 5000 | ~5 GB | 2/6 |
| Full US, every road | `us-full` (`--profile full`) | 5003 | ~75 GB | needs 64+ GB RAM: cloud VM only |

"Major roads" = `motorway, trunk, primary, secondary, tertiary` + their `_link` ramps. North of 55°N
(Alaska, northern Canada) the engine also keeps `unclassified` roads: that tag means lowest public road
class, not unpaved, and up north they are often the only road. Winter/ice roads are excluded, so routes
model summer driving. Raw extracts: US/Canada from Geofabrik May 2026, Mexico October 2026. Alaska and Hawaii are included
(Seattle → Anchorage needs the Canada engines; each Hawaiian island is its own network).

## How the ferry engine works

The major-roads filter strips the small service roads that link ferry docks to the highway network, so
ferries were stranded islands. `scripts/ferry_connectors.py` keeps every car ferry plus, for each dock,
the shortest *legal* path out and in (one-way aware) to the major network. It also keeps small-road
islands whole and opens `access=private` ferry staging lanes. `scripts/build_ferry_engine.py` merges
that into the US + Canada + Mexico major roads and builds. Adding a country is one row in its `COUNTRIES` table.

## Drive times

`profiles/car.lua` is OSRM's stock car profile with two changes. It skips winter/ice roads, and `speed_reduction` goes 0.8 → 0.95. Stock
OSRM multiplies every posted limit by 0.8, which made interstates ~52 mph and DC → Boston 9.2 h.
The report card's control cases check drive times against real-world bands.

## Layout

| Path | What |
|---|---|
| `app/` | Offline browser interface: routes, isochrones, matrices, report card, engine comparison, inspector, exports. `python -m app.setup` once, then `python -m app` → http://127.0.0.1:8080. See [app/README.md](app/README.md) |
| `benchmarks/` | Report card: known trips with known answers (ferries, border shortcuts, controls) |
| `profiles/car.lua` | Calibrated car profile, mounted over `/opt/car.lua` in every build |
| `scripts/build_ferry_engine.py` | Build the recommended engine (runs from Windows; only needs docker) |
| `scripts/ferry_connectors.py` | Ferry + dock-connector extraction (runs in the `tools/` image) |
| `scripts/filter_pbf.sh` | Tag-filter a full PBF down to major roads |
| `scripts/build_osrm.sh`, `build_na_osrm.sh` | Build the older single-extract / no-ferry engines |
| `scripts/inspect_node.py` | Debug: list every way touching an OSM node |
| `tools/Dockerfile` | `my-osrm-tools`: osmium + pyosmium + shapely |

All Docker images are pinned by digest (OSRM v26.5.0, osmium 1.19.0). `data/` (~110 GB) is gitignored.

## Roadmap

1. ~~Import from optitrek~~
2. ~~Audit & improve~~: ferries, Canada, pinned images, calibrated drive times
3. **Experiments**
