# Optimal Drives: every video so far

One line per video, newest first. `experiments/<slug>/` holds the code; renders live in
`experiments/<slug>/out/` (not in git) and the posted files in `Downloads\Videos <date>\`.
The standard is [optimal-drives-format.md](optimal-drives-format.md); the idea list is
[video-ideas.md](video-ideas.md). Add a row when a video ships.

## How a video gets made (the workflow, 2026-10-17)

| Step | Tool | Takes |
|---|---|---|
| 0. Data first: does the data support the claim? | a 15-min probe before building anything | 15 min |
| 1. Scaffold | `python experiments/new_video.py <slug> --subject "..."` | 10 s |
| 2. Inventory (what counts, what's left out) | `experiments/<slug>/inventory.py` → `cache/stores.json` | 15 min |
| 3. Tour (matrix, LKH order, flights) | `experiments/walmart/build.py --cache experiments/<slug>/cache` | 5 to 60 min |
| 4. Stills | `video.py --platform tiktok --stills 1,15,30,45,63` | 1 min |
| 5. Render, each platform | `.\experiments\render.ps1 <slug>` / `... -Platform reels -Shorts` | 10 to 20 min each |
| 6. Checklist, post | format doc section 13; captions from section 12 | 10 min |

Shared code: `experiments/reel_kit.py` (the look, the camera, the encoder) and
`experiments/reel_tour.py` (projection, basemap, route helpers, the command line). A video
file holds only its own data, `plan()`, `init()`, `render()` and its cards; the template is
`experiments/_template/`, the reference `experiments/chickfila/video.py`. Tests:
`python -m pytest experiments/tests`.

Rules that came from Max's reviews (all in the format doc): 65 s; silent unless asked; logo
and question on frame 1, the answer on the end card; shots that hold, zoom speed capped, the
car never leaves the frame; the camera settles before a flight moves; a label starts when
its place is reached; never render as a Claude Code background job; never build a tour while
a render runs (the launcher stops the engine).

## Tours ("every X", one drive)

| Date | Slug | What | Numbers | Request |
|---|---|---|---|---|
| 2026-10-17 | `market_basket` | Every Market Basket | in progress (inventory) | |
| 2026-10-17 | `chickfila` | Every Chick-fil-A: 48 states, DC, PR, Canada; HI and PR by plane. **The reference video.** | 3,415 · 984 h · 49,079 mi · 5 flights | parkermanh |
| 2026-10-17 | `shoprite` | Every ShopRite, 6 states; positions cross-checked against OSM and the Census geocoder | 275 · 56 h · 2,389 mi | Ian |
| 2026-10-17 | `big5` | Every NFL/MLB/NBA/NHL/MLS venue, real team logos, metro zooms (v5) | 154 teams · 135 buildings · 205 h · 12,405 mi | Rojbew |
| 2026-10-17 | `aw` | Every A&W, Alaska by the Alaska Highway | 386 · 464 h · 26,035 mi | RADZZ_4357 |
| 2026-10-16 | `bucees` | Every Buc-ee's | 57 · 106.6 h · 6,555 mi | |
| 2026-10-16 | `nhl` | Every NHL arena, Canada included; answer card of the longest drives | 32 · 162 h · 10,002 mi | mmmeltzer |
| 2026-10-16 | `meijer` | Every Meijer (locator + OSM merge) | 279 · 6 states | Goober2663 |
| 2026-10-16 | `route66_towns` | Every town on Route 66, Chicago to Santa Monica, with fun facts | 279 towns · 2,472 mi · 49.1 h | |
| 2026-10-16 | `spirit_halloween` | Every Spirit Halloween of the 2026 season, Bangor to Anchorage | 1,497 · 801 h | |
| 2026-10-16 | `white_castle` | Every White Castle | 336 · 15 states | |
| 2026-10-16 | `national_parks` | Every national park in the 50 states, Everglades first; itinerary export | 61 parks | a viewer |
| 2026-10-16 | `smallest_towns` | The smallest town in every state (v2: town-sign opener) | 52 towns · 4,024 people | |
| 2026-10-15 | `salem` | Every Salem in the lower 48, a bell per stop | 162 · 293 h · 16,371 mi | |
| 2026-10-15 | `culvers` | Every Culver's | 1,079 · 484 h · 25,874 mi | Liem Johnson |
| 2026-10-15 | `i90_towns` | Every town I-90 passes through, west to east (v2: I-90 shield, bigger names) | 3,090 mi · 46.8 h | |
| 2026-10-15 | `airports` | Every airport with passenger service in the lower 48 (idea 79) | 412 airports | |
| 2026-10-15 | `season` | One MLB team's 2026 season by car, home and away (Red Sox v2, Padres); roads coloured by passes | 162 games | |
| 2026-10-14 | `nba` | Every NBA arena | 30 · 146 h · 9,189 mi | @youraveragehornetsfan |
| 2026-10-14 | `roast_beef` | Every roast beef shop in Massachusetts | 92 · 11.3 h · 456 mi | |
| 2026-10-14 | `capitals` | Every state, province and territory capital (US, CA, MX); Honolulu finale; sound on | 95 | |
| 2026-10-14 | `walmart` | Every Walmart, 50 states and PR; HI and PR by plane. Its `build.py` is the tour builder every later video uses | 3,920 stores | |
| 2026-10-14 | `i95_towns` | Every town I-95 passes through, north to south | | |
| 2026-10-13 | `interstates` | Every 2-digit interstate end to end, in number order | | |
| 2026-10-13 | `amusement_parks` | Every amusement park in North America | | |
| 2026-10-13 | `lighthouses` | Every lighthouse you can drive to (lower 48, AK, HI); first ferries and flights | | |
| 2026-10-13 | `college_tour` | Every college in New England. The first video: the look, the sound | | |

## Turf Wars (every store leaves at once; roads take the colour of the closer one)

| Date | Slug | What | Answer |
|---|---|---|---|
| 2026-10-16 | `nfl` | Closest NFL stadium by drive time (idea 157); dataviz answer card | Giants/Jets 25.4 M people, Packers 3.3 M |
| 2026-10-16 | `wawa-vs-sheetz` | Wawa vs. Sheetz (idea 209); silent, ends on the last road | 1,249 Wawa + 872 Sheetz |
| 2026-10-14 | `dunkin-vs-starbucks` | Dunkin' vs. Starbucks flood (idea 203); v3 West Texas finale | 10,458 / 16,540 lower-48 |

## Drive-time maps and other formats

| Date | Slug | What |
|---|---|---|
| 2026-10-15 | `hospitals` | Every ER, lower 48 (idea 212): 15-min bands, "Every ER" reel |
| 2026-10-15 | `coasts` | Every road to its nearest shore, drawn like river basins (idea 224) |
| 2026-10-15 | `border_crossings` | The drive that crosses the same state border the most times (idea 40). **Parked.** |
| 2026-10-09 | `isochrone` | The Boston drive-time wave: regional, national and continental web maps; feed/reel videos; `reel_video.py` is the house-style cut |

## Videos the ideas list didn't have

A&W, Big 5, Chick-fil-A, ShopRite, Culver's, Meijer, White Castle, Spirit Halloween, roast beef,
I-90 and I-95 towns, the interstates, the coasts, one team's season, the airports (idea 79 was
"1 hour from every major airport"), the college tour. Most came from viewer comments: a public
comment opens the video as a card; a DM never does.
