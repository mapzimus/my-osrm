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
3. **Logo and a question first.** Frame 1 is the subject's logo over the
   map with the question the video answers; the answer lands on the end card
   (section 6a; Max, 2026-10-16).
4. **Honest numbers.** On-screen stats match the data exactly, and the end
   card says what the number leaves out ("nonstop driving").

## 2. Canvas and export

| | |
|---|---|
| Main format | 1080 × 1920 (9:16), 30 fps, **one export per platform** |
| Platforms | `tiktok` and `reels` (Instagram); `K.use(platform)` sets the safe zones |
| Length | **65 s** (Max, 2026-10-13: "They should be 65s") |
| Encode | H.264 on the GPU (`h264_nvenc` p7, CQ 19), `yuv420p`, `+faststart`, AAC 192 kb/s; `-Cpu` = libx264 CRF 18 (§14) |
| Output | `experiments/<slug>/out/<slug>_<platform>.mp4` |

The two exports share the timeline, map and camera path. Only the layout moves:
each app covers a different part of the screen, and the camera fits the map to
that platform's band, so the two map pictures differ slightly and each is drawn
on its own.

## 3. Safe zones

TikTok, Reels and Shorts draw their own buttons and captions on top.

| Platform | HUD starts | "Now" band | No text below | Right rail | Why |
|---|---|---|---|---|---|
| `tiktok` | y = 150 | 1360 to 1470 | 1480 | 140 px | For You / Following tabs on top; caption, sound bar at the bottom |
| `reels` | y = 285 | 1380 to 1490 | 1500 | 120 px | The home feed shows reels cropped to 4:5 (y 285 to 1635); caption at the bottom |
| `classic` | y = 30 | 1520 to 1630 | 1640 | 140 px | The first video (college tour); title sits under TikTok's tabs |

The HUD is always 430 px tall from its start. The map fills the whole frame
behind everything; the camera centres the subject between the HUD and the
"now" band. The `classic` diagram:

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

- No text below the platform's floor, or in its right rail below y = 900.
- The camera keeps the moving subject at the centre of the map area,
  not the centre of the frame.

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
| 0 to 2.2 s | **Hook.** Whole map, every place already showing, the subject's logo and the opening question in big type (section 6a). The first frame is the thumbnail. |
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

## 6a. The first frame: logo + opening question

Standard since 2026-10-16 (Max: "Logos and opening questions", after the NFL Turf Wars opener).
Applies to Optimal Drives and Turf Wars.

**Logo.**
- The subject's official logo: the chain, brand, team or league (NFL shield, Red Sox B,
  Spirit wordmark). For a viewer request, the subject's logo; a public comment also shows as a comment card (a DM never).
- Sources, in order: ESPN's CDN for teams and leagues
  (`a.espncdn.com/i/teamlogos/<league>/500/<abbr>.png`), the brand site's own logo
  (firecrawl `branding` → `images.logo`), Wikipedia's file of the logo (Wikidata P154 or
  `File:<Name>_logo.svg`, the 1280 px thumbnail). At least ~400 px wide before resizing.
- Keep it in `<video>/assets/`. Crop the transparent padding (`getbbox()`); clear an
  opaque white box if the file has one (Spirit's did).
- Size: fits a 380-420 px box (wordmarks up to ~760 px wide), centred between the HUD and
  the hook text, from `MAP_TOP + 40`. Never over the chips, legend or header.
- It fades out with the hook text (same alpha).

**Opening question.**
- Two or three lines in the hook font, the question the video answers, in plain words:
  "Which NFL team is closest to the most Americans?" The last line in an accent colour.
  Auto-shrink the font until every line fits `W - 90`.
- The answer is not on the first frame; it lands on the end card (and in the caption).
- Tour videos ask the tour's question ("How long to drive to every Spirit Halloween?"),
  answered by the clock and the end card.
- Keep a darkening band behind the text so it reads over the busy finished map.

**The answer on the end card** (Max, 2026-10-16: "show the answer better"; built with the
dataviz skill, reference `experiments/nfl/video.py` `end_card`):
- A kicker naming the question ("MOST AMERICANS CLOSEST"), the winner's logo(s) and name, a
  hero figure in big type ("25.4 million people") and one plain sentence of what it means.
- Under it a short ranking: top 5 as horizontal bars, logo and name on the left, value in ink
  right of the bar. Emphasis colours: the winner gold `#C4870B`, the rest grey `#565E6E`
  (validated on the card surface; grey's sub-3:1 contrast is relieved by a label on every bar).
  Text is ink, never the series colour. Then a divider and the other extreme ("FEWEST").
- Sources and the sign-off at the bottom; the finished map below the card. Keep the card short
  enough that the map stays readable (top 8 made it a speck).

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
- Code: `reel_kit.py` (`BOAT`, `glyph`, `dashed`). The route's own ferry
  steps (`mode == "ferry"`, with the ferry's name) mark the crossing.

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
- Code: `reel_kit.py` (`PLANE`, `glyph`, `arc`). Pacing: a flight counts
  as several stops in `warp` so the plane stays on screen long enough to
  read. Reference: `experiments/lighthouses/` (Hawaii).

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

- **Silent by default** (Max, 2026-10-13: "I really don't like the dinging
  noise"). The kit's `soundtrack` (ticks, chimes, a finish chord) is opt-in
  only, e.g. `reel_video.py --sound`. Capitals (2026-10-14) uses it:
  `render.ps1 capitals -Sound`.
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

- [ ] First frame: the subject's logo and the opening question, readable as a thumbnail (6a).
- [ ] Nothing important under the bottom 280 px or the right-side buttons.
- [ ] The clock and the end card agree with `build.py`'s printed totals.
- [ ] Every famous callout is spelled right.
- [ ] `@mapzimus` visible all the way through; sign-off on the end card.
- [ ] Watched once with sound, once muted.

## 14. Rendering

Always render with the launcher (Max, 2026-10-14: "I want it done this way every time"):

```
.\experiments\render.ps1 <video> [-Platform reels] [-Parts 3] [-Sound]
                                 [-Workers 6] [-KeepEngine] [-Cpu] [-Only k]
```

- **Its own hidden process.** Never as a Claude Code background job: Claude Code
  kills those when RAM runs short. The launcher detaches and logs to
  `<video>/out/<platform>_render.log`; the last line is `ALL DONE` or `... FAILED`.
  Watch that file; don't hold the process.
- **Engine off while rendering.** The videos never query OSRM (routes are in
  `cache/tour.json`), so the launcher stops `my-osrm-na-ferry`, drops the WSL page
  cache, and restarts the container when done (only if it was running).
  `-KeepEngine` leaves it up.
- **Up to 6 workers, fitted to free RAM.** About 2 GB per worker, with 3 GB kept for
  the rest of the machine; the log says when it cut the count. Other big jobs (the
  DOK-tagger scorer takes ~9 GB) make it drop to 1–2 workers on its own.
- **GPU encode.** `reel_kit.encoder()` uses `h264_nvenc` (p7, hq, CQ 19) on the
  RTX 4070, which frees the CPU to draw frames. `-Cpu` (or `OD_ENCODER=x264`) goes back
  to libx264 slow CRF 18. Measured against libx264: 48.6 dB PSNR, lowest frame 43 dB,
  which looks the same.
- **Measured 2026-10-14** (walmart, TikTok, part 3 of 3, 650 frames):

  | Setup | Time |
  |---|---|
  | 1 worker, engine up, libx264 | ~565 s |
  | 6 workers, engine off, NVENC | 240 s |

- **In parts.** Each part (default 3, ~22 s of video each) is a separate Python run,
  so memory is released between parts. A crash costs one part; rerunning the same
  command skips the parts that already finished. `-Only k` renders just part k
  (trials); delete that part file afterwards, or the next full render skips it.
- **Join, then sound.** `--join N` stream-copies the parts (no re-encode, no quality
  loss), then `--sound` muxes the soundtrack if asked.
- **Stills first.** Check changes with `video.py --stills 12,30,64` (seconds) before
  any full render; a still costs seconds, a render costs half an hour.
- A `video.py` needs `--part k/n`, `--join n` and `--workers` for this (capitals is
  the reference). If ffmpeg dies, it reports ffmpeg's exit code and the frame.
