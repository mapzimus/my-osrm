# Europe engine — build plan (2026-10-17)

Goal: a second engine, `eu-ferry`, on port 5004. Same pipeline as `na-ferry`
(major roads + car ferries + border crossings), built on a Google Cloud VM
with `scripts/cloud_build.ps1`, then served on BRONTOSAURUS.

## Sizing (taginfo probe, 2026-10-17)

| | North America | Europe |
|---|---|---|
| Raw extract | ~20 GB (7 files) | 32.8 GB (`europe-latest.osm.pbf`, one file) |
| Major-road ways | 5.2 M | 9.4 M (1.8x) |
| Engine on disk | 8.4 GB | ~15 GB (estimate) |
| RAM serving | 4.5 GB | ~8 GB (estimate) |

Both engines together (~12.5 GB) just fit under the 14 GB WSL cap. Run one
at a time during renders.

## Cloud run

- VM: `e2-highmem-16` (128 GB) instead of `-8`. Europe is 1.8x the roads
  and the extra hour or so costs less than a failed run. ~$0.85/h.
- Disk: 300 GB pd-balanced (33 GB raw + intermediate files).
- Expected cost: ~2 h VM + ~15 GB download to the PC ≈ $4-5. Delete the VM
  as soon as `fetch` finishes.

## Code changes, in order

1. **`build_ferry_engine.py`: a `--region na|eu` switch.** `NAME`, `OUT`,
   `COUNTRIES` and the far-north setting move into a per-region table. Today
   they are hard-coded to `na-ferry` / `us`, `ca`.
2. **`update_data.py`**: download source for `eu`
   (`https://download.geofabrik.de/europe-latest.osm.pbf`).
3. **Far north**: keep `unclassified` roads north of ~62°N in Europe
   (northern Norway, Sweden, Finland, Iceland), same reason as Alaska.
4. **`car_ferries.py` keep-list for Europe.** The current drop rule matches
   `balearia` and `passenger`. Baleària is Spain's main *car* ferry line.
   Add the big car operators (Stena, DFDS, P&O, Brittany, Irish Ferries,
   Color Line, Fjord Line, Tallink/Silja, Viking, Finnlines, Moby, Tirrenia,
   GNV, Grimaldi, Minoan, Anek, Blue Star, CalMac, Wightlink, Red Funnel,
   Smyril, Fjord1, Norled).
5. **Channel Tunnel.** Le Shuttle is `route=shuttle_train`, not a highway,
   so the major-roads filter drops it. Then London → Paris only works by
   ferry. Keep shuttle-train ways (car.lua already has a speed for them).
6. **Border delays.** car.lua charges 1200 s (20 min) at every national
   border. Inside Schengen there is no stop. Needs a per-border rule
   (Schengen ↔ Schengen ≈ 0; UK, Ireland, Balkans, Turkey, Belarus keep a
   delay). **Max decides the numbers.**
7. **Cloud scripts**: `cloud_build.ps1 -Region eu` (VM size, `fetch` target
   `data/osrm-eu-ferry`); `cloud_remote.sh` takes the region and runs the
   matching report card.
8. **`docker-compose.yml`**: `eu-ferry` service on `127.0.0.1:5004`.
9. **Report card for Europe** (`benchmarks/`): known trips with known
   answers:
   - London → Paris (tunnel and Dover–Calais)
   - Copenhagen → Malmö (Øresund bridge)
   - Oslo → Bergen (fjord ferries)
   - Piraeus → Crete (overnight ferry)
   - Rome → Palermo (Messina strait)
   - Berlin → Munich (control drive-time band)

## Check before the cloud run

- Run steps 1-6 on a small country locally first (Denmark, 472 MB: ferries,
  bridges, a Schengen border) through `benchmarks/mini_engine.py`-style
  data. This catches bugs for free instead of on a $0.85/h VM.
- Does `europe-latest` include European Russia? Check `europe.poly` before
  promising Moscow routes.
