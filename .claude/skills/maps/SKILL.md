---
name: maps
description: Make a drive-time map, route, isochrone or distance table from Max's local OSRM engine and send it as an image. Use whenever Max asks (often from his phone over Remote Control) for a route, drive time, "how far is X from Y", "what can I reach in N hours", a matrix of drive times, or a map picture, anywhere in the US, Canada, Mexico or Central America.
---

# Maps from the local engine

Everything runs on BRONTOSAURUS, offline. Max is often on his phone: lead with the
answer in one or two lines, then send the picture. Don't paste JSON.

## 0. Is it up? (5 s)

```bash
curl -s http://127.0.0.1:8080/api/engines
```

- No answer: the app is down. `pwsh -File app/autostart.ps1 status`, then
  `Start-ScheduledTask -TaskName 'my-osrm map app'` (PowerShell). Log: `logs/app.log`.
- `na-ferry` shows `"healthy": false`: `docker compose up -d na-ferry`, wait ~60 s.
  Don't start other engines while a build runs (RAM). Other sessions sometimes stop
  the engine for builds: if a build is running, say so and wait.

## 1. Places to coordinates

```bash
curl -s "http://127.0.0.1:8080/api/places?q=plattsburgh&limit=3"
```

Returns name, region, country, lon, lat, sorted by population. OSRM order is **lon,lat**.
If a name is ambiguous (Portland), pick the obvious one and say which.

## 2. Numbers without a picture

```bash
# route (2+ points, in visit order)
curl -s -X POST http://127.0.0.1:8080/api/route -H "Content-Type: application/json" \
  -d '{"engine":"na-ferry","coords":[[-71.0589,42.3601],[-73.4529,44.6995]]}'
# -> miles, hours, used_ferry, legs, steps (turn list)

# best visiting order for 3+ stops
curl -s -X POST http://127.0.0.1:8080/api/route/trip -H "Content-Type: application/json" \
  -d '{"engine":"na-ferry","coords":[[...],[...],[...]],"roundtrip":false}'

# N x N drive times (hours) and miles
curl -s -X POST http://127.0.0.1:8080/api/matrix -H "Content-Type: application/json" \
  -d '{"engine":"na-ferry","coords":[[...],[...]],"names":["A","B"]}'
```

Times include a 30 min boarding wait per ferry and ~20 min per border crossing.

## 3. A picture

Every map in the app is a URL, so: build the link, open it in the Playwright browser,
screenshot, send.

| Map | URL (after `http://127.0.0.1:8080/`) |
|---|---|
| Route | `#engine=na-ferry&tool=route&route=LON,LAT;LON,LAT[;...]` (zooms to fit) |
| Isochrone setup | `#engine=na-ferry&tool=isochrone&isochrone=LON,LAT\|REGION\|HOURS\|NAME\|SKIP` |
| Compare engines | `#tool=compare&compare=LON,LAT;LON,LAT` |
| Any view | add `&v=ZOOM/LAT/LON`, `&flavor=dark` |

Isochrone fields: REGION = `regional` (1-3 min, Northeast), `national` (10+ min),
`continental`, or `custom` (uses the current view). HOURS = 0 for auto. SKIP = countries
to leave out, joined by `-`: `US-CA-MX-CAM`, or empty. NAME: letters, digits, spaces.

Steps (Playwright MCP tools; the Claude browser pane can't render WebGL while hidden):

1. `browser_resize` 1280 x 800, then `browser_navigate` to the URL.
2. Route: wait ~3 s. Isochrone: click `#panel-isochrone button.primary` (Compute), then
   poll with `browser_evaluate` until `#panel-isochrone .legend` exists (national can take
   10+ min; tell Max it's running).
3. `browser_take_screenshot` with `filename: ".playwright-mcp/<name>.png"` (only paths
   inside the repo are allowed).
4. `SendUserFile` with that PNG, `display: "render"`, a one-line caption (time, distance,
   ferry or not).

Also good from the panel text (`browser_evaluate` on `#panel-route .stat` or
`#panel-isochrone .legend`) for the caption.

## 4. Open it live on the phone

If Max wants to pan around himself, the app is reachable over Tailscale once it's
logged in on both devices: `tailscale serve status` shows the https address. Never
bind the app to 0.0.0.0 or open a firewall port; Tailscale serve keeps it private.

## Don't

- Edit `scripts/`, `profiles/`, `benchmarks/` or rebuild engines from a maps request:
  those belong to the build sessions.
- Commit without `git branch --show-current` first.
