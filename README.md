# my-osrm

A self-hosted [OSRM](https://github.com/Project-OSRM/osrm-backend) routing engine for US (and US+Canada) driving,
running in Docker. Extracted from [mapzimus/optitrek](https://github.com/mapzimus/optitrek), where it powered the
road-trip optimizer.

## What's here

| Network | Data dir | Port | Size | Runs on BRONTOSAURUS (32 GB RAM)? |
|---|---|---|---|---|
| Full US, every road | `data/osrm/us-latest.osrm` | 5000 | ~75 GB | **No.** Needs 64+ GB RAM (BSOD on 2026-05-21). Cloud VM only |
| US major roads | `data/osrm-major/us-major.osrm` | 5000 | ~5 GB | Yes |
| US + Canada major roads | `data/osrm-major-na/north-america-major.osrm` | 5001 | ~6 GB | Yes |

| **US + Canada major roads + ferries** (recommended) | `data/osrm-na-ferry/na-ferry.osrm` | 5002 | ~6 GB | Yes. Report card **8/8** |

The ferry build adds every car ferry plus the shortest legal road path in *and* out of each dock
(`scripts/ferry_connectors.py`), then builds with `scripts/build_ferry_engine.py`.

| Report card (`benchmarks/`) | US major | US+CA major | US+CA + ferries |
|---|---|---|---|
| Score | 2/6 | 4/8 | **8/8** |

"Major roads" = `motorway, trunk, primary, secondary, tertiary` + their `_link` ramps (see `scripts/filter_pbf.sh`).
Raw extracts (`data/us-latest.osm.pbf`, `data/canada-latest.osm.pbf`) are Geofabrik downloads from May 2026.

`data/` is gitignored. The built artifacts were copied from `G:\dev\optitrek\data` on 2026-10-08.

## Scripts (bash, run from WSL Ubuntu)

| Script | Does |
|---|---|
| `scripts/filter_pbf.sh` | Tag-filter a full PBF down to major roads |
| `scripts/build_osrm.sh` | extract → partition → customize (idempotent) |
| `scripts/build_na_osrm.sh` | Download Canada, filter, merge with US-major, build |
| `scripts/smoke_test_na_engine.sh` | Start both engines, compare cross-border routes |
| `scripts/test_border_routing.sh` | Probe border-shortcut cases on the US-only engine |

## Quick start (US major roads)

```bash
docker compose --profile major up -d osrm-major
curl "http://localhost:5000/route/v1/driving/-77.0365,38.8977;-71.0589,42.3601?overview=false"
```

## Roadmap

1. **Import** (done): engine files + data copied from optitrek, verbatim.
2. **Audit & improve**: get it running, pin versions, de-optitrek the names, verify data integrity.
3. **Experiments**: fun stuff on top of the engine.
