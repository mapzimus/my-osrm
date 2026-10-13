# Optimal Drives — video format

The house style for every **Optimal Drives** video by **@mapzimus**. One
look, one layout, one rhythm, so a viewer knows it's ours from the first frame.

The code side of this document is `experiments/reel_kit.py`: colours, fonts,
layout, timing, sound and the encoder all live there. A new video imports the
kit and only writes its own data and map logic. If this doc and the kit
disagree, fix whichever is wrong in the same commit.

Reference video: `experiments/college_tour/` (every college in New England).

---

## 1. What makes it ours

1. **Real drive times.** Every number comes from our own routing engine
   (OSRM on OpenStreetMap, North America, ferries included). Nothing estimated
   by straight lines, nothing hand-animated.
2. **The look.** Dark map, one amber trail, a ticking clock, stat chips, a
   closing stat card. Same every time.
3. **The answer first.** Frame 1 states the result. No slow build-up.
4. **Honest numbers.** On-screen stats match the data exactly, and the end
   card says what the number leaves out ("nonstop driving").

## 2. Canvas and export

| | |
|---|---|
| Main format | Reel, 1080 × 1920 (9:16), 30 fps |
| Length | 45 to 65 s |
| Encode | H.264, CRF 18, `yuv420p`, `+faststart`, AAC 192 kb/s |
| Output | `experiments/<slug>/out/<slug>_reel.mp4` |
| Optional | Feed cut 1080 × 1350 (4:5), same timeline |

## 3. Safe zones (reel)

TikTok, Reels and Shorts draw their own buttons and captions on top.

```
y = 0      ┌───────────────────────────┐
           │ series tag, title, stats, │  HUD band (0 to 460)
           │ chips                     │
y = 460    ├───────────────────────────┤
           │                           │
           │        map / action       │  keep the subject in the
           │                     ░░░░  │  centre; right 140 px of the
           │                     ░░░░  │  lower half is under the
y = 1520   ├───────────────────────────┤  like/comment buttons
           │ "now" label (1520 to 1630)│
y = 1640   ├───────────────────────────┤
           │ platform caption, no text │  bottom 280 px: map only
y = 1920   └───────────────────────────┘
```

- No text below y = 1640 or in the right 140 px below y = 900.
- The camera keeps the moving subject at the centre of the map area
  (y ≈ 990), not the centre of the frame.

## 4. Look

**Colours** (tokens in `reel_kit.py`):

| Token | RGB | Use |
|---|---|---|
| `WATER` | 13, 17, 23 | sea, lakes, background |
| `LAND` | 33, 38, 48 | land |
| `BORDER` | 58, 66, 80 | state and country lines, card outlines |
| `INK` | 236, 239, 244 | main text |
| `MUTED` | 150, 158, 172 | secondary text |
| `DOT` | 96, 104, 120 | places not reached yet |
| `ACCENT` | 255, 176, 59 | **the one accent**: trail, reached places, the key number, finished chips |

Amber means "done / reached / the answer". Nothing else gets amber.

**Fonts** (Windows built-ins):

| Role | Font | Size |
|---|---|---|
| Series tag | Segoe UI Bold, caps, spaced | 22 |
| Title | Segoe UI Bold | 54 |
| Subtitle | Segoe UI | 28 |
| Big stats, clock | Bahnschrift | 84 |
| Labels | Segoe UI Bold | 30 |
| Small text | Segoe UI | 24 |

## 5. Layout, top to bottom

1. **Series tag**: `OPTIMAL DRIVES`, amber, at the very top.
2. **Title**: the question, plain words. "Every college in New England".
3. **Subtitle**: one line on the method or scope.
4. **Stats row**: at most 3 numbers. The first is always the **clock**
   (hours driven, `h:mm`); the accent number is the count being chased.
5. **Chips**: progress per group (state, region). At most 6. A chip turns
   amber when its group is complete.
6. **Map** with a small `@mapzimus` watermark at its top left. Reposts strip
   captions, so the handle has to be inside the picture.
7. **"Now" label** in the safe band: kicker (`COLLEGE 42`, `START`,
   `FINISH`), name, place.

## 6. Timeline

| Time | Beat |
|---|---|
| 0 to 2.2 s | **Hook.** Whole map, every place already showing, the answer in big type: "212 colleges. 59.9 hours." The first frame works as the thumbnail. |
| 2.2 to 4 s | Hook fades, HUD appears, the camera dives to the start. |
| 4 s to end − 6 s | **The drive.** The camera follows; the clock runs. |
| end − 6 to end − 4 s | Pull back to the whole route. |
| last 4 s+ | **End card** (section 9). Hold still; this is the screenshot. |

**Pacing.** Video time is not proportional to drive time. Half the speed
follows the clock, half follows the stops, so dense clusters slow down and
empty highways speed up. The clock always shows the true drive time, it just
runs at a varying speed.

**Camera.** Follow the subject with smoothing (no jitter). Zoom out on long
empty stretches, zoom in on clusters. No cuts; every move is continuous.

## 7. Labels and callouts

- Each place lights up amber with a short pop when reached. Draw dots at
  the road-snapped point (OSRM `waypoints[].location`), not the address,
  so the pop lands on the trail.
- **Famous places get a callout**: a bigger label on the map for about 1 s.
  Pick 10 to 25 names people will recognise. They drive the comments ("my
  school was #143").
- Shorten long names consistently (`University of Connecticut` → `UConn`).

## 8. Ferries and flights

When a route leaves the road, the move has to show it. The viewer should
never see the trail jump across water.

**Ferries** (whenever the engine's route uses one; `used_ferry` / the
step's `mode == "ferry"`):

- The trail switches to a **dashed amber line** for the crossing, with a
  small white **boat** marker moving along the ferry line at the sailing speed.
- The car marker waits at the dock first: the **30-min boarding wait** runs
  on the clock with the car standing still. Then the boat sails.
- The "now" label shows the kicker `FERRY` and the route name
  (e.g. `Cape May–Lewes Ferry`, `1 h 25 min`).
- Sound: a low two-note horn as the boat leaves.
- Code: `experiments/isochrone/ferries_anim.py` (`load`, `along`, `draw`).
  Ferry lines come from `data/app/ferries.geojson`.

**Flights** (only where no road or ferry reaches: Hawaii, Bermuda, roadless
Alaska, islands; the end card says "flown").

- A white **plane** silhouette flies a **curved arc** (bowed upward), nose
  pointing along the path, leaving a thin grey trail that fades about
  1.5 s after landing. Long flights get a larger plane.
- The camera pulls out enough to keep both airports on screen, then
  settles back in on the arrival.
- Clock: flight time at a stated cruise speed plus a fixed airport
  overhead, both written on the end card ("+ 2 flights, 550 mph, 2 h airport time").
- Kicker `FLIGHT`, label `BOS → HNL`.
- Sound: a soft rising whoosh over the flight.
- Code: `experiments/isochrone/animate.py` (`draw_plane`, `arc`) and
  `experiments/isochrone/flights.py` (`plan`, `great_circle`).

When a third video needs these, move them into `reel_kit.py` so they look
the same everywhere.

## 9. End card

At most 4 stat rows, then the footer:

1. The headline number (accent colour), e.g. `59.9 hours` *of driving, nonstop*.
2. Distance and count.
3. A typical-step number (average between stops, longest hop).
4. **The real-life number**: what it takes as an actual trip, with the
   assumptions in the label ("20 min per campus, 8 h sleep a night: 8.2 days").

Footer: start and finish, data sources, then the sign-off
`Optimal Drives · @mapzimus`.

## 10. Sound

- Built into the video: a soft tick each time a place is reached, a brighter
  chime when a chip completes, a low chord at the finish.
- **No music in the file.** Add trending or library audio in the app when
  posting; platforms reward their own audio, and it avoids rights problems.

## 11. Data honesty

- Name the data source on the end card (e.g. `IPEDS 2024`).
- Define what counts and apply it the same way everywhere. Colleges: active,
  degree-granting, not system offices, not online-only.
- Say what the number leaves out (stops, sleep, traffic).
- Ferries count with their boarding wait; border crossings with their delay.

## 12. Post template

```
Optimal Drives: <the question>
<the answer in one line>. Real drive times on every road.
Comment a place and I'll map it.
#maps #geography #roadtrip #<region>
```

## 13. Before posting

- [ ] First frame states the answer and reads well as a thumbnail.
- [ ] Nothing important under the bottom 280 px or the right-side buttons.
- [ ] The clock and the end card agree with `build.py`'s printed totals.
- [ ] Every famous callout is spelled right.
- [ ] `@mapzimus` visible all the way through; sign-off on the end card.
- [ ] Watched once with sound, once muted.
