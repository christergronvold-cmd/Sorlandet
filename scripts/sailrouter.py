#!/usr/bin/env python3
"""Weather routing for a square-rigged ship.

Given a wind forecast, a start, a goal and a departure time, work out the fastest
sailing route - which for a full-rigged ship means working out where she has to tack,
because she cannot point anywhere near the wind.

The method is isochrone routing. From the start, fan out across every heading she could
steer, advance each by one time step at the speed her sails would give in the wind
forecast for that place and hour, throw away all but the best position in each bearing
sector, and repeat. The frontier after n steps is the set of places she could reach in
n steps; the first one to reach the goal carries the fastest route back with it.

WHAT THIS IS NOT
----------------
The polar table below is an *estimate*. Sørlandet's real polars are not published, so the
numbers are built from what is known about her - 64 m, 1236 m² of sail, cruising around
5-8 knots, best runs low double figures - and from how square riggers behave in general:
useless close to the wind, fastest on a broad reach. Treat the routes as "this is where
the wind would push a ship like her", not as a navigator's plan.

She also does not always sail for speed. She is a school: there are sail drills, there is
standing on and off waiting for a berth, and on a leg with eleven days for a two-day
passage there is a great deal of time in hand. The fastest route is the interesting
comparison, not a prediction of what the master will choose.
"""

from __future__ import annotations

import heapq
import json
import math
import urllib.parse
import urllib.request
import ssl
from datetime import datetime, timedelta, timezone

# --------------------------------------------------------------------- the polar
# Rows are true wind angle in degrees (0 = wind dead ahead), columns are true wind speed
# in knots. Values are boat speed in knots. Below 60 degrees she cannot make progress at
# all under sail alone - that is the whole reason a square rigger tacks so widely.

TWA_ROWS = [0, 30, 50, 60, 70, 90, 110, 130, 150, 170, 180]
TWS_COLS = [4, 6, 8, 10, 14, 18, 24, 30, 40]

POLAR = {
    #        4kn  6kn  8kn 10kn 14kn 18kn 24kn 30kn 40kn
    0:     [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    30:    [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    50:    [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    60:    [0.4, 0.8, 1.4, 2.0, 2.9, 3.4, 3.6, 3.4, 2.6],
    70:    [0.7, 1.4, 2.2, 3.0, 4.1, 4.8, 5.0, 4.7, 3.6],
    90:    [1.2, 2.2, 3.3, 4.4, 6.0, 7.0, 7.6, 7.2, 5.6],
    110:   [1.5, 2.7, 4.0, 5.2, 7.1, 8.4, 9.4, 9.0, 7.0],
    130:   [1.6, 2.9, 4.2, 5.5, 7.5, 9.0, 10.2, 9.8, 7.6],
    150:   [1.5, 2.7, 3.9, 5.1, 7.0, 8.5, 9.8, 9.5, 7.4],
    170:   [1.2, 2.2, 3.2, 4.2, 5.8, 7.1, 8.3, 8.1, 6.4],
    180:   [1.1, 2.0, 3.0, 3.9, 5.4, 6.6, 7.8, 7.6, 6.0],
}

NO_GO = 58.0          # degrees off the true wind she cannot sail inside of
MAX_SPEED = 13.0      # a sanity ceiling; she has done better, briefly, running


def _interp(x: float, xs: list, ys: list) -> float:
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(1, len(xs)):
        if x <= xs[i]:
            f = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
            return ys[i - 1] + (ys[i] - ys[i - 1]) * f
    return ys[-1]


_SPEED_CACHE: dict[tuple[int, int], float] = {}


# A polar learned from her own sailing, if one has been built. It is consulted first and
# only where it has enough hours behind it; everything else falls through to the table
# below. See polar.py - the point is that this can be switched on from the first day, when
# it changes nothing, and grows into the truth over a season.
_LEARNED = None


def use_learned_polar(speed_fn) -> None:
    """Give the router a speed(twa, tws) that prefers measured figures."""
    global _LEARNED
    _LEARNED = speed_fn
    _SPEED_CACHE.clear()


def boat_speed(twa: float, tws: float) -> float:
    """Speed through the water at a true wind angle and speed, in knots."""
    twa = abs(((twa + 180) % 360) - 180)          # fold onto 0..180
    if twa < NO_GO:
        return 0.0
    if _LEARNED is not None:
        key = (int(twa + 0.5), int(tws * 2 + 0.5))
        hit = _SPEED_CACHE.get(key)
        if hit is None:
            hit = min(MAX_SPEED, max(0.0, float(_LEARNED(twa, tws))))
            _SPEED_CACHE[key] = hit
        return hit
    key = (int(twa + 0.5), int(tws * 2 + 0.5))
    hit = _SPEED_CACHE.get(key)
    if hit is not None:
        return hit
    per_row = [_interp(tws, TWS_COLS, POLAR[r]) for r in TWA_ROWS]
    out = min(MAX_SPEED, max(0.0, _interp(twa, TWA_ROWS, per_row)))
    _SPEED_CACHE[key] = out
    return out


def best_vmg_angle(tws: float, to_wind: bool = True) -> tuple[float, float]:
    """The angle that makes best progress dead upwind (or dead downwind), and its VMG."""
    best_a, best_v = None, -1.0
    lo, hi = (NO_GO, 100) if to_wind else (100, 180)
    a = lo
    while a <= hi:
        v = boat_speed(a, tws) * (math.cos(math.radians(a)) if to_wind
                                  else -math.cos(math.radians(a)))
        if v > best_v:
            best_a, best_v = a, v
        a += 1
    return best_a, best_v


# ------------------------------------------------------------------ geometry
R_NM = 3440.065


def nm_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R_NM * math.asin(math.sqrt(h))


def bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def step_from(pos: tuple[float, float], course: float, dist_nm: float) -> tuple[float, float]:
    lat1, lon1 = math.radians(pos[0]), math.radians(pos[1])
    d = dist_nm / R_NM
    c = math.radians(course)
    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(c))
    lon2 = lon1 + math.atan2(math.sin(c) * math.sin(d) * math.cos(lat1),
                             math.cos(d) - math.sin(lat1) * math.sin(lat2))
    return (math.degrees(lat2), (math.degrees(lon2) + 540) % 360 - 180)


# ---------------------------------------------------------------- wind field


class WindField:
    """Wind on a coarse lat/lon/time lattice, with linear interpolation between nodes.

    One Open-Meteo call covers the whole box for the whole passage, so routing does not
    need a request per trial position - which is what would make this unaffordable.
    """

    def __init__(self, lats: list[float], lons: list[float], times: list[datetime],
                 speed: dict, direction: dict):
        self.lats, self.lons, self.times = lats, lons, times
        self.speed, self.direction = speed, direction

    @staticmethod
    def _span(lo: float, hi: float, step: float) -> list[float]:
        n = max(2, int(math.ceil((hi - lo) / step)) + 1)
        return [lo + (hi - lo) * i / (n - 1) for i in range(n)]

    @classmethod
    def fetch(cls, box: tuple[float, float, float, float], depart: datetime, hours: int,
              get_json, step_deg: float = 1.5, step_h: int = 3,
              max_nodes: int = 90) -> "WindField | None":
        """box is (lat_min, lat_max, lon_min, lon_max), padded by the caller."""
        lat_min, lat_max, lon_min, lon_max = box
        lats = cls._span(lat_min, lat_max, step_deg)
        lons = cls._span(lon_min, lon_max, step_deg)
        while len(lats) * len(lons) > max_nodes and step_deg < 12:
            step_deg *= 1.5
            lats = cls._span(lat_min, lat_max, step_deg)
            lons = cls._span(lon_min, lon_max, step_deg)

        nodes = [(la, lo) for la in lats for lo in lons]
        past_days = 0
        days = max(2, min(16, hours // 24 + 2))
        params = {
            "latitude": ",".join(f"{la:.3f}" for la, _ in nodes),
            "longitude": ",".join(f"{lo:.3f}" for _, lo in nodes),
            "hourly": "wind_speed_10m,wind_direction_10m",
            "wind_speed_unit": "kn",
            "forecast_days": str(days),
            "timezone": "UTC",
        }
        data = get_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params),
                        timeout=60)
        if not data:
            return None
        blocks = data if isinstance(data, list) else [data]
        if len(blocks) < len(nodes):
            return None

        raw_times = (blocks[0].get("hourly") or {}).get("time") or []
        if not raw_times:
            return None
        stamps = [datetime.strptime(t, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
                  for t in raw_times]
        keep = [i for i, t in enumerate(stamps)
                if depart - timedelta(hours=step_h) <= t <= depart + timedelta(hours=hours + step_h)
                and t.hour % step_h == 0]
        if len(keep) < 2:
            keep = list(range(0, len(stamps), step_h))
        times = [stamps[i] for i in keep]

        speed: dict = {}
        direction: dict = {}
        for k, (la, lo) in enumerate(nodes):
            h = blocks[k].get("hourly") or {}
            ws, wd = h.get("wind_speed_10m") or [], h.get("wind_direction_10m") or []
            if not ws:
                return None
            speed[(round(la, 3), round(lo, 3))] = [ws[i] if i < len(ws) else None for i in keep]
            direction[(round(la, 3), round(lo, 3))] = [wd[i] if i < len(wd) else None for i in keep]
        return cls([round(x, 3) for x in lats], [round(x, 3) for x in lons],
                   times, speed, direction)

    def _bracket(self, values: list[float], v: float) -> tuple[int, int, float]:
        if v <= values[0]:
            return 0, 0, 0.0
        if v >= values[-1]:
            return len(values) - 1, len(values) - 1, 0.0
        for i in range(1, len(values)):
            if v <= values[i]:
                span = values[i] - values[i - 1]
                return i - 1, i, (0.0 if span == 0 else (v - values[i - 1]) / span)
        return len(values) - 1, len(values) - 1, 0.0

    def at(self, lat: float, lon: float, when: datetime) -> tuple[float, float]:
        """Wind speed in knots and direction in degrees (the way it comes FROM)."""
        i0, i1, fi = self._bracket(self.lats, lat)
        j0, j1, fj = self._bracket(self.lons, lon)
        k0, k1, fk = self._bracket([t.timestamp() for t in self.times], when.timestamp())

        def node(i, j, k):
            key = (self.lats[i], self.lons[j])
            s = (self.speed.get(key) or [None])[k] if k < len(self.speed.get(key) or []) else None
            d = (self.direction.get(key) or [None])[k] if k < len(self.direction.get(key) or []) else None
            return s, d

        # Average the eight surrounding nodes, with directions summed as vectors so that
        # 350 and 10 degrees average to 0 rather than to 180.
        sx = sy = ssum = wsum = 0.0
        for i, wi in ((i0, 1 - fi), (i1, fi if i1 != i0 else 0.0)):
            for j, wj in ((j0, 1 - fj), (j1, fj if j1 != j0 else 0.0)):
                for k, wk in ((k0, 1 - fk), (k1, fk if k1 != k0 else 0.0)):
                    w = wi * wj * wk
                    if w <= 0:
                        continue
                    s, d = node(i, j, k)
                    if s is None or d is None:
                        continue
                    r = math.radians(d)
                    sx += w * math.sin(r) * s
                    sy += w * math.cos(r) * s
                    ssum += w * s
                    wsum += w
        if wsum <= 0:
            return 0.0, 0.0
        speed = ssum / wsum
        if abs(sx) < 1e-9 and abs(sy) < 1e-9:
            return speed, 0.0
        return speed, (math.degrees(math.atan2(sx, sy)) + 360) % 360


# ----------------------------------------------------------------- the router


def isochrone_route(start: tuple[float, float], goal: tuple[float, float],
                    depart: datetime, field: WindField,
                    is_sea=None, step_h: float = 3.0, max_hours: float = 240.0,
                    headings: int = 36, sectors: int = 90,
                    arrive_within_nm: float = 12.0):
    """The fastest sailing route from start to goal, or None if she cannot get there.

    Returns {"points": [{lat, lon, t, twa, tws, sog, course}], "hours": float,
             "tacks": [indices into points]}
    """
    if nm_between(start, goal) <= arrive_within_nm:
        return {"points": [{"lat": start[0], "lon": start[1], "t": depart}], "hours": 0.0,
                "tacks": []}

    # frontier entries: (lat, lon, hours, parent index, course steered, tws, twa, sog)
    nodes = [{"lat": start[0], "lon": start[1], "h": 0.0, "parent": None,
              "course": None, "tws": None, "twa": None, "sog": None}]
    frontier = [0]
    steps = int(max_hours / step_h)

    for _ in range(steps):
        candidates = []
        for idx in frontier:
            n = nodes[idx]
            when = depart + timedelta(hours=n["h"])
            tws, wdir = field.at(n["lat"], n["lon"], when)
            if tws <= 0:
                continue
            for hi in range(headings):
                course = hi * (360.0 / headings)
                twa = ((course - wdir + 180) % 360) - 180
                sog = boat_speed(twa, tws)
                if sog <= 0.05:
                    continue
                dist = sog * step_h
                pos = step_from((n["lat"], n["lon"]), course, dist)
                if is_sea and not is_sea(pos[0], pos[1]):
                    continue
                candidates.append((pos, n["h"] + step_h, idx, course, tws, twa, sog))

        if not candidates:
            break

        # Keep the position that has got furthest towards the goal in each bearing sector,
        # measured from the start. This is what stops the fan exploding.
        best: dict[int, tuple] = {}
        for cand in candidates:
            pos = cand[0]
            sector = int(bearing(start, pos) / (360.0 / sectors)) % sectors
            gain = nm_between(start, goal) - nm_between(pos, goal)
            prev = best.get(sector)
            if prev is None or gain > prev[0]:
                best[sector] = (gain, cand)

        frontier = []
        arrived = None
        for _, cand in best.values():
            pos, hours, parent, course, tws, twa, sog = cand
            nodes.append({"lat": pos[0], "lon": pos[1], "h": hours, "parent": parent,
                          "course": course, "tws": tws, "twa": twa, "sog": sog})
            here = len(nodes) - 1
            frontier.append(here)
            if nm_between(pos, goal) <= arrive_within_nm:
                if arrived is None or hours < nodes[arrived]["h"]:
                    arrived = here

        if arrived is not None:
            return _unwind(nodes, arrived, depart, goal)

    # Never reached it: give back the best effort so the page can still draw something.
    if not frontier:
        return None
    closest = min(frontier, key=lambda i: nm_between((nodes[i]["lat"], nodes[i]["lon"]), goal))
    out = _unwind(nodes, closest, depart, goal)
    out["reached"] = False
    return out


def _unwind(nodes: list, end: int, depart: datetime, goal: tuple) -> dict:
    chain = []
    i = end
    while i is not None:
        chain.append(nodes[i])
        i = nodes[i]["parent"]
    chain.reverse()

    points = []
    for n in chain:
        points.append({
            "lat": round(n["lat"], 4), "lon": round(n["lon"], 4),
            "t": (depart + timedelta(hours=n["h"])).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "hours": round(n["h"], 1),
            "course": None if n["course"] is None else round(n["course"]),
            "twa": None if n["twa"] is None else round(n["twa"]),
            "tws": None if n["tws"] is None else round(n["tws"], 1),
            "sog": None if n["sog"] is None else round(n["sog"], 1),
        })

    # A tack or a gybe: the wind crosses from one side to the other.
    tacks = []
    for k in range(2, len(points)):
        a, b = points[k - 1]["twa"], points[k]["twa"]
        if a is None or b is None:
            continue
        if a * b < 0 and abs(a) + abs(b) > 40:
            tacks.append(k)

    return {"points": points, "hours": round(chain[-1]["h"], 1), "tacks": tacks,
            "reached": True}
