# Optimal Drives: every Walmart — design

Date: 2026-10-14. Status: approved in chat, spec awaiting review.

## Goal

A 65 s Optimal Drives video (TikTok and Reels cuts, silent) of the fastest
trip to every Walmart store in the 50 states and Puerto Rico. The numbers on
the hook and end card must survive fact-checking in the comments.

## Rules (decided by Max)

- **Stores:** every Walmart store: Supercenters, Discount Stores,
  Neighborhood Markets. Not Sam's Club, not Walmart fuel stations or
  separately mapped pharmacies, not closed stores.
- **Coverage:** 50 states + Puerto Rico. Hawaii, Puerto Rico (and any store
  the road network can't reach) are flown to, with the amusement-parks rule:
  great-circle distance at 550 mph plus 2 h of airport time.
- **Time:** drive time only. No shopping, sleep or traffic.
- **Solver:** LKH-3 (approach A).

## Pipeline: `experiments/walmart/`

| Step | File | Output |
|---|---|---|
| Extract | `extract.sh`: osmium `brand:wikidata=Q483551` from `data/us-latest` and `data/puerto-rico-latest` | `cache/osm_*.geojson` |
| Inventory | `inventory.py`: keep store types above, drop the rest, merge duplicates within 150 m, one point per store | `cache/stores.json` |
| Matrix | `build.py`: drive time and distance, every store to every store, on the na-ferry engine, in 500-row chunks | `cache/matrix_t.npy`, `cache/matrix_d.npy` (float32) |
| Islands | `build.py`: stores with no road path to the main network become flight legs | in `tour.json` |
| Solve | `build.py`: LKH-3 in Docker, asymmetric TSP, free start and end | `cache/order.json` |
| Drive | `build.py`: route geometry per leg | `cache/tour.json` (the format `reel_kit` already reads) |
| Video | `video.py`, adapted from `amusement_parks/video.py` | `out/walmart_{tiktok,reels}.mp4` |

Every step caches its output and is skipped when the cache exists, so a
crash (memory, network) never repeats hours of work.

**Stop gate:** `inventory.py` prints its store count beside Walmart's
official US count. If OSM is short by more than 2 %, stop and report to Max
before building.

## Video (65 s)

- **Map:** every store a faint grey dot; it flashes blue when reached and
  stays lit.
- **Camera:** mostly the whole US. 3 to 4 short dives where stores are dense
  (Dallas, Atlanta, Florida). Hawaii, Puerto Rico and Alaska are shown with
  an inset or fly-to when the route reaches them.
- **Stats band:** clock, miles driven, "N of 4,6xx" stores, five region chips.
- **"Now" label:** state and that state's count, e.g.
  "TEXAS · 52 of 403 Walmarts". No individual store names.
- **Hook:** "4,6xx Walmarts. N hours."
- **End card:** hours, miles, stores, the state with the most stores, driving
  days at 8 h a day. Source: OpenStreetMap (date), routing: OSRM.
- Follows `docs/optimal-drives-format.md` (safe zones, colours, silent).

## Testing

- A 50-store run of the whole pipeline first. Its LKH-3 tour is checked by
  hand, and against OR-Tools on the same 50 stores.
- Checks on the full tour: each store exactly once; the leg times sum to the
  total; no driving leg over 12 h; flights only where no road path exists.
- Stills checked at hook, a dive, an island leg and the end card before the
  full render.

## Out of scope

Sam's Club; store opening hours; traffic; a sound track.
