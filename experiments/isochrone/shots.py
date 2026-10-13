"""Shot list for the camera video: wave clock + camera path + Hawaii inset.

Video seconds drive everything. Each keyframe pins the wave clock (hours
since leaving Boston) and the camera (centre lon/lat, width of the view in
km) at one moment; between keyframes both glide on monotone cubic curves
(PCHIP: no overshoot, so the clock never runs backwards and the camera never
swings past its mark). Zoom is interpolated in log-width so it feels even.

"END" in the hours column means the slowest reachable place (t_end);
"FULL" in the camera columns means the whole continent, fitted to the box.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.interpolate import PchipInterpolator

EARTH_KM = 6371.0
DURATION = 45.0

# (video s, wave hours, lon, lat, km wide) -- reel-first (9:16, map box 1020x900)
KEYS = [
    (0.0,    0.0, -71.3, 42.6,   900),     # New England close, Boston pulse
    (3.5,    4.5, -69.0, 38.5,  2600),     # widen: the Bermuda flight lands
    (8.5,   12.0, -76.0, 30.0,  4300),     # East + Miami island hops
    (13.0,  18.0, -84.0, 36.0,  5000),     # Hawaii lands (inset), Chicago/Atlanta
    (17.5,  32.0, -97.0, 41.0,  5600),     # glide west with the front
    (22.0,  50.0, -110.0, 43.0, 6400),     # Rockies to the West Coast
    (26.5,  72.0, -112.0, 38.0, 8200),     # Mexico to Panama, Alaska
    (30.0,  92.0, -128.0, 49.0, 8600),     # Prudhoe, Panama done; swing to Alaska
    (33.5, 110.0, -157.0, 52.4, 1900),     # Alaska Peninsula: the ferry chain
    (37.0,  "END", -163.5, 52.4, 1050),    # Dutch Harbor lights up
    (39.0,  "END", -150.0, 57.0, 5200),    # zoom out first, over Alaska...
    (41.5,  "END", "FULL", "FULL", "FULL"),  # ...then settle on the whole continent
    (DURATION, "END", "FULL", "FULL", "FULL"),
]

# Hawaii inset: only while the Honolulu flight lands, and in the finale.
HAWAII = [(0, 0), (10.5, 0), (11.5, 1), (15.0, 1), (16.0, 0), (38.0, 0), (40.0, 1), (DURATION, 1)]


class Shots:
    def __init__(self, proj, t_end: float, full: tuple[float, float, float], keys=KEYS, hawaii=HAWAII):
        """full: (cx, cy, width) of the whole-continent view, projected units."""
        secs = np.array([k[0] for k in keys], dtype=float)
        hours = np.array([t_end if k[1] == "END" else k[1] for k in keys], dtype=float)
        hours = np.minimum(hours, t_end)
        cx, cy, lw = [], [], []
        for _, _, lon, lat, km in keys:
            if lon == "FULL":
                x, y, w = full
            else:
                x, y = (float(v) for v in proj.forward(np.array(lon), np.array(lat)))
                w = km / EARTH_KM
            cx.append(x)
            cy.append(y)
            lw.append(math.log(w))
        self.duration = float(secs[-1])
        self._t = PchipInterpolator(secs, hours)
        self._x = PchipInterpolator(secs, cx)
        self._y = PchipInterpolator(secs, cy)
        self._w = PchipInterpolator(secs, lw)
        self._h = np.array(hawaii, dtype=float)

    def hours(self, s: float) -> float:
        return float(self._t(min(max(s, 0.0), self.duration)))

    def view(self, s: float) -> tuple[float, float, float]:
        s = min(max(s, 0.0), self.duration)
        return float(self._x(s)), float(self._y(s)), math.exp(float(self._w(s)))

    def hawaii(self, s: float) -> float:
        return float(np.interp(s, self._h[:, 0], self._h[:, 1]))
