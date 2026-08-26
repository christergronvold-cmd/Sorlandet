#!/usr/bin/env python3
"""
Fetches the latest AIS position for the full-rigged ship Sørlandet (MMSI 257165000)
from aisstream.io, gets sea and land weather for that position from Open-Meteo, and
writes data/latest.json + data/track.json, which the web page reads.

Run by .github/workflows/update.yml every 20 minutes, or manually:

    export AISSTREAM_API_KEY=...      # free key from https://aisstream.io
    python3 scripts/update.py

Without a key the script runs in sample mode and writes made-up data, so you can
check that the page works before getting a key.
"""

from __future__ import annotations

import json
import math
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ------------------------------------------------------------------- settings

MMSI = os.environ.get("MMSI", "257165000")          # Sørlandet
SHIP_NAME = os.environ.get("SHIP_NAME", "Sørlandet")

# How long to listen to the AIS stream per run (seconds).
LISTEN_SECONDS = int(os.environ.get("LISTEN_SECONDS", "240"))

# Track thinning: full detail while it is fresh, coarser as it ages, so the
# file the page downloads stays small over a nine-month voyage.
THIN_RULES = [(7, 0), (30, 30), (120, 120), (10000, 360)]  # (days old, minutes between)

# A new track point is stored only if the ship has moved at least this many nautical
# miles, or at least this many minutes have passed since the previous point.
MIN_MOVE_NM = float(os.environ.get("MIN_MOVE_NM", "0.15"))
MIN_GAP_MIN = float(os.environ.get("MIN_GAP_MIN", "8"))

USER_AGENT = os.environ.get(
    "CONTACT_UA", "sorlandet-tracker/1.0 (family project; contact: change-me@example.com)"
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LATEST = DATA / "latest.json"
TRACK = DATA / "track.json"
WIND = DATA / "wind.json"
WAVES = DATA / "waves.json"
HISTORY = DATA / "history.json"

# -------------------------------------------------------------------- helpers


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    s = s.strip().replace("Z", "+00:00")
    # aisstream sometimes sends nanoseconds: 2026-08-26 10:11:12.123456789 +0000 UTC
    s = s.replace(" +0000 UTC", "+00:00").replace(" ", "T", 1)
    if "." in s:
        head, _, tail = s.partition(".")
        frac = "".join(ch for ch in tail if ch.isdigit())[:6]
        rest = tail[len(frac):] if tail[len(frac):].startswith(("+", "-")) else "+00:00"
        s = f"{head}.{frac}{rest}"
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def nm_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in nautical miles."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371.0088 * math.asin(math.sqrt(h)) / 1.852


def get_json(url: str, timeout: int = 30) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
        print(f"  ! could not fetch {url.split('?')[0]}: {exc}", file=sys.stderr)
        return None


def read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# -------------------------------------------------------------- AIS position


def fetch_position_from_aisstream(api_key: str) -> tuple[dict | None, list[dict]]:
    """Listens on aisstream and collects EVERY position for our MMSI in the window.

    The ship transmits every few minutes in coastal waters. Sampling for a couple of
    minutes per run threw almost all of that away, so the window is now long and we
    keep the lot: the newest becomes the current position, the rest fill the track.

    Returns (newest position, all positions collected).
    """
    try:
        from websockets.sync.client import connect
    except ImportError:
        print("! missing the 'websockets' package (pip install websockets)", file=sys.stderr)
        return None, []

    subscribe = {
        "APIKey": api_key,
        "BoundingBoxes": [[[-90, -180], [90, 180]]],
        "FiltersShipMMSI": [MMSI],
    }

    collected: dict[str, dict] = {}          # keyed by timestamp, so duplicates fold
    static: dict = {}
    seen_total = seen_ours = 0
    deadline = now_utc() + timedelta(seconds=LISTEN_SECONDS)

    print(f"* listening on aisstream for up to {LISTEN_SECONDS}s ({LISTEN_SECONDS / 60:.0f} min) "
          f"for MMSI {MMSI} ...")
    try:
        with connect("wss://stream.aisstream.io/v0/stream",
                     open_timeout=20, ping_interval=20, ping_timeout=20) as ws:
            ws.send(json.dumps(subscribe))
            print("  connected and subscribed")
            while now_utc() < deadline:
                remaining = (deadline - now_utc()).total_seconds()
                if remaining <= 0:
                    break
                try:
                    raw = ws.recv(timeout=min(remaining, 120))
                except TimeoutError:
                    continue                  # quiet stretch - keep waiting, do not give up
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue

                if isinstance(msg, dict) and msg.get("error"):
                    print(f"! aisstream returned an error: {msg['error']}", file=sys.stderr)
                    break

                seen_total += 1
                meta = msg.get("MetaData") or {}
                if str(meta.get("MMSI", "")) != MMSI:
                    continue
                seen_ours += 1

                kind = msg.get("MessageType")
                body = (msg.get("Message") or {}).get(kind) or {}
                if kind == "ShipStaticData":
                    dest = (body.get("Destination") or "").strip()
                    if dest:
                        static["destination"] = dest

                lat = body.get("Latitude", meta.get("latitude"))
                lon = body.get("Longitude", meta.get("longitude"))
                if lat is None or lon is None:
                    continue
                try:
                    seen = iso(parse_iso(str(meta.get("time_utc") or "")))
                except Exception:
                    seen = iso(now_utc())

                collected[seen] = {
                    "lat": round(float(lat), 5),
                    "lon": round(float(lon), 5),
                    "sog_kn": body.get("Sog"),
                    "cog_deg": body.get("Cog"),
                    "heading_deg": None if body.get("TrueHeading") in (511, None) else body.get("TrueHeading"),
                    "nav_status": body.get("NavigationalStatus"),
                    "seen_utc": seen,
                    "source": "aisstream.io",
                }
    except Exception as exc:                  # network, TLS, dropped connection ...
        print(f"! error talking to aisstream: {exc}", file=sys.stderr)

    print(f"  messages seen: {seen_total} total, {seen_ours} for MMSI {MMSI}, "
          f"{len(collected)} distinct positions")
    if not collected:
        if seen_total == 0:
            print("  -> the stream sent nothing at all. Either the key was not accepted, "
                  "or no receiver reported any vessel in this window.")
        else:
            print("  -> no message for our MMSI in this window (normal outside coastal coverage)")
        return None, []

    ordered = [collected[t] for t in sorted(collected)]
    newest = ordered[-1]
    newest.update({k: v for k, v in static.items() if v})
    print(f"  -> newest position {newest['lat']}, {newest['lon']} at {newest['seen_utc']}")
    return newest, ordered


def demo_position() -> dict:
    """Sample position mid-Atlantic, so the page can be shown without a key."""
    return {
        "lat": 38.9,
        "lon": -32.4,
        "sog_kn": 6.4,
        "cog_deg": 71.5,
        "heading_deg": 74,
        "nav_status": 8,
        "seen_utc": iso(now_utc() - timedelta(hours=3)),
        "source": "demo",
        "destination": "PONTA DELGADA",
    }


# ------------------------------------------------------------------- weather


def fetch_weather(lat: float, lon: float) -> dict:
    """Sea and land weather for the position. Degrades gracefully if one is down."""
    weather: dict = {"fetched_utc": iso(now_utc())}

    marine = get_json(
        "https://marine-api.open-meteo.com/v1/marine?"
        + urllib.parse.urlencode(
            {
                "latitude": f"{lat:.4f}",
                "longitude": f"{lon:.4f}",
                "current": "wave_height,wave_direction,wave_period,swell_wave_height,"
                "swell_wave_period,wind_wave_height,sea_surface_temperature",
                "hourly": "wave_height",
                "forecast_days": "3",
                "timezone": "UTC",
            }
        )
    )
    if marine and isinstance(marine.get("current"), dict):
        cur = marine["current"]
        weather["sea"] = {
            "wave_height_m": cur.get("wave_height"),
            "wave_direction_deg": cur.get("wave_direction"),
            "wave_period_s": cur.get("wave_period"),
            "swell_height_m": cur.get("swell_wave_height"),
            "swell_period_s": cur.get("swell_wave_period"),
            "wind_wave_height_m": cur.get("wind_wave_height"),
            "sea_temp_c": cur.get("sea_surface_temperature"),
            "time_utc": cur.get("time"),
        }
        hourly = marine.get("hourly") or {}
        times, waves = hourly.get("time") or [], hourly.get("wave_height") or []
        weather["sea_forecast"] = [
            {"time_utc": t, "wave_height_m": w}
            for t, w in list(zip(times, waves))[:72:6]
            if w is not None
        ]

    air = get_json(
        "https://api.open-meteo.com/v1/forecast?"
        + urllib.parse.urlencode(
            {
                "latitude": f"{lat:.4f}",
                "longitude": f"{lon:.4f}",
                "current": "temperature_2m,apparent_temperature,wind_speed_10m,"
                "wind_direction_10m,wind_gusts_10m,pressure_msl,cloud_cover,"
                "precipitation,weather_code",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,wind_speed_10m_max",
                "wind_speed_unit": "ms",
                "forecast_days": "4",
                "timezone": "UTC",
            }
        )
    )
    if air and isinstance(air.get("current"), dict):
        cur = air["current"]
        weather["air"] = {
            "temp_c": cur.get("temperature_2m"),
            "feels_c": cur.get("apparent_temperature"),
            "wind_ms": cur.get("wind_speed_10m"),
            "gust_ms": cur.get("wind_gusts_10m"),
            "wind_direction_deg": cur.get("wind_direction_10m"),
            "pressure_hpa": cur.get("pressure_msl"),
            "cloud_pct": cur.get("cloud_cover"),
            "precip_mm": cur.get("precipitation"),
            "weather_code": cur.get("weather_code"),
            "time_utc": cur.get("time"),
        }
        daily = air.get("daily") or {}
        weather["air_forecast"] = [
            {
                "date": d,
                "weather_code": wc,
                "temp_max_c": tmax,
                "temp_min_c": tmin,
                "wind_max_ms": wmax,
            }
            for d, wc, tmax, tmin, wmax in zip(
                daily.get("time") or [],
                daily.get("weather_code") or [],
                daily.get("temperature_2m_max") or [],
                daily.get("temperature_2m_min") or [],
                daily.get("wind_speed_10m_max") or [],
            )
        ]

    return weather



# --------------------------------------------------------- wind and wave grids


GRID_CELLS = int(os.environ.get("GRID_CELLS", "5"))        # 5 x 5 points
GRID_SPAN_NM = float(os.environ.get("GRID_SPAN_NM", "360"))  # box side in nautical miles
GRID_HOURS = int(os.environ.get("GRID_HOURS", "24"))       # how far ahead
GRID_STEP = int(os.environ.get("GRID_STEP", "3"))          # hours between steps


def grid_around(lat: float, lon: float) -> tuple[list[str], list[str]]:
    """A grid that is the same size in nautical miles wherever the ship is.

    One degree of latitude is always 60 nm, but a degree of longitude shrinks by
    cos(latitude), so the longitude span is widened to match.
    """
    n = max(2, GRID_CELLS)
    half_lat = (GRID_SPAN_NM / 2) / 60
    cos_lat = max(0.15, math.cos(math.radians(lat)))       # guard near the poles
    half_lon = min(60.0, (GRID_SPAN_NM / 2) / (60 * cos_lat))

    lats, lons = [], []
    for i in range(n):
        for j in range(n):
            gl = max(-89.0, min(89.0, lat - half_lat + 2 * half_lat * i / (n - 1)))
            go = (lon - half_lon + 2 * half_lon * j / (n - 1) + 180) % 360 - 180
            lats.append(f"{gl:.3f}")
            lons.append(f"{go:.3f}")
    print(f"  grid: {n}x{n} over {GRID_SPAN_NM:.0f} x {GRID_SPAN_NM:.0f} nm "
          f"({2*half_lat:.1f}° lat x {2*half_lon:.1f}° lon at {lat:.1f}°)")
    return lats, lons


def _time_index(all_times: list[str]) -> tuple[list[int], list[str]]:
    now_hour = now_utc().strftime("%Y-%m-%dT%H:00")
    try:
        first = next(k for k, t in enumerate(all_times) if t >= now_hour)
    except StopIteration:
        first = 0
    idx = list(range(first, min(len(all_times), first + GRID_HOURS + 1), GRID_STEP))
    return idx, [all_times[k] for k in idx]


def _series(values: list, idx: list[int], nd: int | None) -> list:
    out = []
    for k in idx:
        v = values[k] if k < len(values) and values[k] is not None else None
        out.append(v if v is None else (int(v) if nd is None else round(v, nd)))
    return out


def fetch_grid(lat: float, lon: float, kind: str) -> dict | None:
    """Wind ('wind') or wave ('waves') forecast for the grid, in one API call."""
    lats, lons = grid_around(lat, lon)
    base, fields = (
        ("https://api.open-meteo.com/v1/forecast?", "wind_speed_10m,wind_direction_10m")
        if kind == "wind" else
        ("https://marine-api.open-meteo.com/v1/marine?", "wave_height,wave_direction,wave_period")
    )
    params = {
        "latitude": ",".join(lats),
        "longitude": ",".join(lons),
        "hourly": fields,
        "forecast_days": "3",
        "timezone": "UTC",
    }
    if kind == "wind":
        params["wind_speed_unit"] = "ms"

    data = get_json(base + urllib.parse.urlencode(params), timeout=45)
    if not data:
        return None

    blocks = data if isinstance(data, list) else [data]
    first = (blocks[0].get("hourly") or {}) if blocks else {}
    all_times = first.get("time") or []
    if not all_times:
        return None
    idx, times = _time_index(all_times)

    cells = []
    for b in blocks:
        h = b.get("hourly") or {}
        if kind == "wind":
            speed, direction = h.get("wind_speed_10m") or [], h.get("wind_direction_10m") or []
            if not speed:
                continue
            cell = {"ws": _series(speed, idx, 1), "wd": _series(direction, idx, None)}
        else:
            height, direction = h.get("wave_height") or [], h.get("wave_direction") or []
            period = h.get("wave_period") or []
            if not height or all(v is None for v in height):
                continue                                   # land cell - no sea state
            cell = {"hs": _series(height, idx, 1), "wd": _series(direction, idx, None),
                    "tp": _series(period, idx, 0)}
        cell["lat"] = round(float(b.get("latitude", 0)), 3)
        cell["lon"] = round(float(b.get("longitude", 0)), 3)
        cells.append(cell)

    if not cells:
        return None
    print(f"  -> {kind} grid: {len(cells)} points x {len(times)} time steps")
    return {"generated_utc": iso(now_utc()), "times": times,
            "step_hours": GRID_STEP, "cells": cells}


# --------------------------------------------------------- sun, moon, history


def fetch_sun_moon(lat: float, lon: float) -> dict | None:
    """Local time zone, sunrise and sunset where the ship is, plus the moon phase.

    Asking Open-Meteo with timezone=auto gives us the ship's own local time zone,
    which is what tells you when it is reasonable to call home.
    """
    data = get_json(
        "https://api.open-meteo.com/v1/forecast?"
        + urllib.parse.urlencode(
            {
                "latitude": f"{lat:.4f}",
                "longitude": f"{lon:.4f}",
                "daily": "sunrise,sunset,daylight_duration",
                "timezone": "auto",
                "forecast_days": "2",
            }
        )
    )
    out: dict = {"moon": moon_phase()}
    if data:
        daily = data.get("daily") or {}
        sunrise = (daily.get("sunrise") or [None])[0]
        sunset = (daily.get("sunset") or [None])[0]
        seconds = (daily.get("daylight_duration") or [None])[0]
        out.update({
            "timezone": data.get("timezone"),
            "timezone_abbreviation": data.get("timezone_abbreviation"),
            "utc_offset_seconds": data.get("utc_offset_seconds"),
            "sunrise_local": sunrise,
            "sunset_local": sunset,
            "daylight_hours": None if seconds is None else round(seconds / 3600, 1),
        })
        print(f"  -> ship local time zone {out.get('timezone')} "
              f"(UTC{out.get('utc_offset_seconds', 0) // 3600:+d}), "
              f"sunrise {sunrise}, sunset {sunset}")
    return out


def moon_phase(when: datetime | None = None) -> dict:
    """Moon age and illumination, computed locally - no API needed.

    Counts synodic months from a known new moon (6 Jan 2000, 18:14 UTC).
    Good to a few hours, which is plenty for a night watch.
    """
    when = when or now_utc()
    known_new = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    synodic = 29.530588853
    age = ((when - known_new).total_seconds() / 86400) % synodic
    illum = (1 - math.cos(2 * math.pi * age / synodic)) / 2
    names = [
        (1.85, "new moon"), (5.54, "waxing crescent"), (9.23, "first quarter"),
        (12.91, "waxing gibbous"), (16.61, "full moon"), (20.30, "waning gibbous"),
        (23.99, "last quarter"), (27.68, "waning crescent"), (30.0, "new moon"),
    ]
    name = next(n for limit, n in names if age < limit)
    return {"age_days": round(age, 1), "illumination": round(illum, 2), "phase": name}


def update_history(weather: dict, points: list) -> None:
    """Keeps one row per day: strongest wind and highest wave we have observed.

    The daily distance is not stored - the page works that out from the track, so
    there is only one source of truth for it.
    """
    today = now_utc().strftime("%Y-%m-%d")
    rows = read_json(HISTORY, {"days": []}).get("days") or []
    by_date = {r["date"]: r for r in rows}
    row = by_date.setdefault(today, {"date": today})

    wind = (weather.get("air") or {}).get("wind_ms")
    gust = (weather.get("air") or {}).get("gust_ms")
    wave = (weather.get("sea") or {}).get("wave_height_m")
    for key, value in (("max_wind_ms", wind), ("max_gust_ms", gust), ("max_wave_m", wave)):
        if value is not None:
            row[key] = max(value, row.get(key) or 0)

    if points:
        row["last_position_utc"] = points[-1]["t"]

    days = sorted(by_date.values(), key=lambda r: r["date"])[-400:]
    write_json(HISTORY, {"days": days})
    print(f"  -> history: {len(days)} days recorded")



def thin_track(points: list) -> list:
    """Keeps recent points as they are and spaces out the older ones."""
    if not points:
        return points
    now = now_utc()
    kept, last_t = [], {}
    for pt in points:
        try:
            t = parse_iso(pt["t"])
        except Exception:
            kept.append(pt)
            continue
        age_days = (now - t).total_seconds() / 86400
        spacing = next(m for d, m in THIN_RULES if age_days <= d)
        if spacing == 0:
            kept.append(pt)
            continue
        prev = last_t.get(spacing)
        if prev is None or (t - prev).total_seconds() / 60 >= spacing:
            kept.append(pt)
            last_t[spacing] = t
    if len(kept) != len(points):
        print(f"  -> thinned track from {len(points)} to {len(kept)} points")
    return kept


# ----------------------------------------------------------------------- main


def main() -> int:
    api_key = os.environ.get("AISSTREAM_API_KEY", "").strip()
    demo = not api_key

    track = read_json(TRACK, {"mmsi": MMSI, "ship": SHIP_NAME, "points": []})
    points = track.get("points") or []
    previous = read_json(LATEST, {})

    collected: list[dict] = []
    if demo:
        print("* no AISSTREAM_API_KEY - running in sample mode")
        position = demo_position()
        collected = [position]
    else:
        position, collected = fetch_position_from_aisstream(api_key)

    # No fresh AIS message: keep the last known position, but refresh the weather.
    fresh_fix = position is not None
    if position is None:
        position = (previous.get("position") or {}) if previous else {}
        if not position:
            print("! no position to show yet - exiting without writing files")
            return 0

    lat, lon = float(position["lat"]), float(position["lon"])
    weather = fetch_weather(lat, lon)

    # If the weather service is down, keep the previous weather instead of blanking
    # the cards on the page.
    if not weather.get("sea") and not weather.get("air"):
        earlier = (previous.get("weather") or {}) if previous else {}
        if earlier:
            weather = {**earlier, "stale": True}
            print("  -> no weather data, keeping the previous reading")

    if fresh_fix and collected:
        added = 0
        for fix in collected:
            lat_i, lon_i = float(fix["lat"]), float(fix["lon"])
            if points:
                last = points[-1]
                moved = nm_between((last["lat"], last["lon"]), (lat_i, lon_i))
                try:
                    gap = (parse_iso(fix["seen_utc"]) - parse_iso(last["t"])).total_seconds() / 60
                except Exception:
                    gap = MIN_GAP_MIN + 1
                if gap <= 0 or (moved < MIN_MOVE_NM and gap < MIN_GAP_MIN):
                    continue
            points.append({
                "t": fix["seen_utc"],
                "lat": lat_i,
                "lon": lon_i,
                "sog": fix.get("sog_kn"),
                "cog": fix.get("cog_deg"),
            })
            added += 1
        print(f"  -> added {added} of {len(collected)} collected positions to the track")
        points = thin_track(points)

    distance_nm = sum(
        nm_between((points[i - 1]["lat"], points[i - 1]["lon"]), (points[i]["lat"], points[i]["lon"]))
        for i in range(1, len(points))
    )

    sun = fetch_sun_moon(lat, lon)

    for kind, path in (("wind", WIND), ("waves", WAVES)):
        grid = fetch_grid(lat, lon, kind)
        if grid:
            write_json(path, grid)
        else:
            print(f"  -> no {kind} grid this run, keeping the previous one")

    write_json(TRACK, {"mmsi": MMSI, "ship": SHIP_NAME, "points": points})
    write_json(
        LATEST,
        {
            "ship": SHIP_NAME,
            "mmsi": MMSI,
            "imo": "5334561",
            "updated_utc": iso(now_utc()),
            "fresh_fix": fresh_fix,
            "demo": demo,
            "position": position,
            "weather": weather,
            "sun": sun or {},
            "stats": {
                "points": len(points),
                "distance_nm": round(distance_nm, 1),
                "first_point_utc": points[0]["t"] if points else None,
            },
        },
    )
    update_history(weather, points)
    print(f"* wrote {LATEST.name} and {TRACK.name} ({len(points)} points, {distance_nm:.0f} nm)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
