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
IMO = os.environ.get("IMO", "5334561")              # Sørlandet
SHIP_NAME = os.environ.get("SHIP_NAME", "Sørlandet")

# The foundation runs its own position page, linked from fullriggeren.no under
# "Follow the ship". It serves a plain public JSON list of the last ~500 fixes -
# no key, and whitelisted to this ship's IMO. Set FOUNDATION_URL="" to switch off.
FOUNDATION_URL = os.environ.get(
    "FOUNDATION_URL", f"https://raptor.warrisk.tech/position/{IMO}")

# How long to listen to the AIS stream per run (seconds).
LISTEN_SECONDS = int(os.environ.get("LISTEN_SECONDS", "240"))

# Track thinning: full detail while it is fresh, coarser as it ages, so the
# file the page downloads stays small over a nine-month voyage.
THIN_RULES = [(7, 2), (30, 30), (120, 120), (10000, 360)]  # (days old, minutes between)

# A new track point is stored only if the ship has moved at least this many nautical
# miles, or at least this many minutes have passed since the previous point.
MIN_MOVE_NM = float(os.environ.get("MIN_MOVE_NM", "0.15"))
MIN_GAP_MIN = float(os.environ.get("MIN_GAP_MIN", "8"))

# How far back the rewind slider can reach. Open-Meteo serves past hours from the same
# endpoints through past_days, so this needs no separate historical API and no key.
WAKE_HOURS = int(os.environ.get("WAKE_HOURS", "48"))

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


def today_iso() -> str:
    return now_utc().strftime("%Y-%m-%d")


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


def bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Initial great-circle course from a to b, in degrees true."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


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


def write_json(path: Path, payload, compact: bool = False) -> None:
    """Write JSON. compact=True drops the indentation, which is 28 % of track.json -
    worth it for the one file every phone downloads and nobody reads by hand."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    else:
        body = json.dumps(payload, ensure_ascii=False, indent=1)
    path.write_text(body + "\n", encoding="utf-8")


# --------------------------------------------------- the foundation's own feed


def fetch_from_foundation() -> list[dict]:
    """Read the foundation's own position list.

    It answers with an array of [timestamp, GeoJSON Point] pairs - roughly 500 fixes,
    about one every seven minutes, a rolling window of the last two to three days.
    Speed and course are not published, so they are computed from consecutive fixes.
    """
    if not FOUNDATION_URL:
        return []

    raw = get_json(FOUNDATION_URL, timeout=30)
    if raw is not None and not isinstance(raw, list):
        print("  ! the foundation's feed did not answer with a list of positions")
        return []
    if not raw:
        return []

    fixes: list[dict] = []
    for row in raw:
        try:
            when, geom = row[0], row[1]
            lon, lat = float(geom["coordinates"][0]), float(geom["coordinates"][1])
            stamp = iso(parse_iso(when))
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        fixes.append({"lat": lat, "lon": lon, "sog_kn": None, "cog_deg": None,
                      "heading_deg": None, "seen_utc": stamp, "source": "foundation"})

    fixes.sort(key=lambda f: f["seen_utc"])

    # Speed and course from the step between neighbours, so the page still has both.
    for i in range(1, len(fixes)):
        a, b = fixes[i - 1], fixes[i]
        try:
            hours = (parse_iso(b["seen_utc"]) - parse_iso(a["seen_utc"])).total_seconds() / 3600
        except Exception:
            continue
        if not 0 < hours <= 3:
            continue
        dist = nm_between((a["lat"], a["lon"]), (b["lat"], b["lon"]))
        speed = dist / hours
        if speed <= 25:
            b["sog_kn"] = round(speed, 1)
            b["cog_deg"] = round(bearing((a["lat"], a["lon"]), (b["lat"], b["lon"])))

    if fixes:
        print(f"  -> foundation feed: {len(fixes)} positions, newest {fixes[-1]['seen_utc']}")
    return fixes


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
                    # Message 5 also carries the voyage fields the crew set by hand. These
                    # are what a tracking site turns into "destination changed" events.
                    draught = body.get("MaximumStaticDraught")
                    if draught:
                        static["draught_m"] = round(float(draught), 1)
                    eta = body.get("Eta") or {}
                    if isinstance(eta, dict) and eta.get("Month"):
                        static["eta_text"] = (
                            f"{int(eta.get('Day') or 0):02d}.{int(eta.get('Month') or 0):02d} "
                            f"{int(eta.get('Hour') or 0):02d}:{int(eta.get('Minute') or 0):02d}")

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


# ------------------------------------------------- BarentsWatch live AIS (Norway)
# The Norwegian Coastal Administration's own AIS feed. Far denser than a volunteer
# network while the ship is in Norwegian waters, and silent once she leaves them -
# so it runs alongside aisstream rather than replacing it.

BW_TOKEN_URL = "https://id.barentswatch.no/connect/token"
BW_SSE = "https://live.ais.barentswatch.no/live/v1/sse/combined"
BW_LATEST = "https://live.ais.barentswatch.no/live/v1/latest/combined"


def bw_token() -> str | None:
    cid = os.environ.get("BW_CLIENT_ID", "").strip()
    secret = os.environ.get("BW_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        return None
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials", "client_id": cid,
        "client_secret": secret, "scope": "ais",
    }).encode()
    req = urllib.request.Request(BW_TOKEN_URL, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context()) as r:
            return json.loads(r.read())["access_token"]
    except Exception as exc:
        print(f"  ! BarentsWatch token failed: {exc}", file=sys.stderr)
        return None


def _bw_point(msg: dict) -> dict | None:
    lat = msg.get("latitude")
    lon = msg.get("longitude")
    t = msg.get("msgtime") or msg.get("msgTime")
    if lat is None or lon is None or not t:
        return None
    try:
        seen = iso(parse_iso(str(t)))
    except Exception:
        return None
    return {
        "lat": round(float(lat), 5),
        "lon": round(float(lon), 5),
        "sog_kn": msg.get("speedOverGround"),
        "cog_deg": msg.get("courseOverGround"),
        "heading_deg": None if msg.get("trueHeading") in (511, None) else msg.get("trueHeading"),
        "nav_status": msg.get("navigationalStatus"),
        "destination": (msg.get("destination") or "").strip() or None,
        "draught_m": msg.get("draught"),
        "seen_utc": seen,
        "source": "barentswatch",
    }


def collect_from_barentswatch(seconds: int, into: dict, lock) -> None:
    """Listens to the BarentsWatch SSE stream, filtered to our MMSI."""
    tok = bw_token()
    if not tok:
        print("* BarentsWatch: no credentials, skipping (aisstream alone)")
        return

    body = json.dumps({"mmsi": [int(MMSI)], "modelType": "Simple", "modelFormat": "Json"}).encode()
    req = urllib.request.Request(BW_SSE, data=body, headers={
        "Authorization": f"bearer {tok}", "Content-Type": "application/json",
        "Accept": "text/event-stream", "User-Agent": USER_AGENT})

    deadline = now_utc() + timedelta(seconds=seconds)
    got = 0
    print(f"* BarentsWatch: opening the live stream for MMSI {MMSI} ...")
    try:
        with urllib.request.urlopen(req, timeout=60, context=ssl.create_default_context()) as r:
            for raw in r:
                if now_utc() >= deadline:
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    msg = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if str(msg.get("mmsi", "")) != MMSI:
                    continue
                pt = _bw_point(msg)
                if pt:
                    with lock:
                        into[pt["seen_utc"]] = pt
                    got += 1
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:160]
        except Exception:
            pass
        print(f"  ! BarentsWatch stream: HTTP {exc.code} {exc.reason} {detail}", file=sys.stderr)
    except Exception as exc:
        print(f"  ! BarentsWatch stream: {exc}", file=sys.stderr)
    print(f"* BarentsWatch: {got} positions from the Coastal Administration feed")


def bw_latest_position() -> dict | None:
    """One-shot fallback: the newest position BarentsWatch has, within 24 hours."""
    tok = bw_token()
    if not tok:
        return None
    body = json.dumps({"mmsi": [int(MMSI)], "modelType": "Simple", "modelFormat": "Json"}).encode()
    req = urllib.request.Request(BW_LATEST, data=body, headers={
        "Authorization": f"bearer {tok}", "Content-Type": "application/json",
        "Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=45, context=ssl.create_default_context()) as r:
            rows = json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        print(f"  ! BarentsWatch latest: {exc}", file=sys.stderr)
        return None
    rows = rows if isinstance(rows, list) else [rows]
    points = [pt for pt in (_bw_point(m) for m in rows if isinstance(m, dict)) if pt]
    if points:
        newest = sorted(points, key=lambda p: p["seen_utc"])[-1]
        print(f"  -> BarentsWatch latest: {newest['lat']}, {newest['lon']} at {newest['seen_utc']}")
        return newest
    return None


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

    air = get_json(
        "https://api.open-meteo.com/v1/forecast?"
        + urllib.parse.urlencode(
            {
                "latitude": f"{lat:.4f}",
                "longitude": f"{lon:.4f}",
                "current": "temperature_2m,apparent_temperature,wind_speed_10m,"
                "wind_direction_10m,wind_gusts_10m,pressure_msl,cloud_cover,"
                "precipitation,weather_code",
                "wind_speed_unit": "ms",
                "forecast_days": "1",
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

    return weather



# --------------------------------------------------------- wind and wave grids


GRID_CELLS = int(os.environ.get("GRID_CELLS", "5"))        # 5 x 5 points
GRID_SPAN_NM = float(os.environ.get("GRID_SPAN_NM", "360"))  # box side in nautical miles
GRID_HOURS = int(os.environ.get("GRID_HOURS", "72"))       # how far ahead
GRID_STEP = int(os.environ.get("GRID_STEP", "1"))          # hours between steps


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
    """Time steps for the grid: WAKE_HOURS behind us as well as GRID_HOURS ahead.

    The slider on the page runs across the whole span, so the same arrows that show the
    forecast can be wound back to the weather she actually sailed through.
    """
    back_hour = (now_utc() - timedelta(hours=WAKE_HOURS)).strftime("%Y-%m-%dT%H:00")
    try:
        first = next(k for k, t in enumerate(all_times) if t >= back_hour)
    except StopIteration:
        first = 0
    span = WAKE_HOURS + GRID_HOURS
    idx = list(range(first, min(len(all_times), first + span + 1), GRID_STEP))
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
        ("https://api.open-meteo.com/v1/forecast?",
         "wind_speed_10m,wind_direction_10m,weather_code,temperature_2m")
        if kind == "wind" else
        ("https://marine-api.open-meteo.com/v1/marine?", "wave_height,wave_direction,wave_period")
    )
    params = {
        "latitude": ",".join(lats),
        "longitude": ",".join(lons),
        "hourly": fields,
        "past_days": str(max(1, min(7, (WAKE_HOURS + 23) // 24))),
        "forecast_days": str(max(2, min(16, (GRID_HOURS + 47) // 24))),
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
            cell = {"ws": _series(speed, idx, 1), "wd": _series(direction, idx, None),
                    "wc": _series(h.get("weather_code") or [], idx, None),
                    "t2": _series(h.get("temperature_2m") or [], idx, 0)}
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


def civil_place(ports: list) -> dict | None:
    """The port whose clock belongs on the page: the one she is in, else the next."""
    for q in ports:
        if q.get("arrive") and q.get("depart") and q["arrive"] <= today_iso() <= q["depart"]:
            return q
    return next_port(ports)


def port_timezone(lat: float, lon: float) -> str | None:
    """The IANA zone name ashore at a place, e.g. Europe/London.

    The name and not the offset, so the page can work out the time for any date - summer
    time included - rather than freezing today's offset into the file.
    """
    data = get_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
        "current": "temperature_2m", "timezone": "auto", "forecast_days": "1"}), timeout=25)
    return (data or {}).get("timezone")


def fetch_sun_moon(lat: float, lon: float) -> dict | None:
    """Sunrise, sunset and the moon where the ship is, plus the clock ashore.

    Sunrise is asked for in UTC and converted by the page into the port's civil time, so
    the clock and the sun on the card agree with each other.
    """
    data = get_json(
        "https://api.open-meteo.com/v1/forecast?"
        + urllib.parse.urlencode(
            {
                "latitude": f"{lat:.4f}",
                "longitude": f"{lon:.4f}",
                "daily": "sunrise,sunset,daylight_duration",
                "timezone": "UTC",
                "forecast_days": "2",
            }
        )
    )
    out: dict = {"moon": moon_phase()}

    place = civil_place((read_json(DATA / "ports.json", {}).get("ports") or []))
    if place:
        tz = port_timezone(float(place["lat"]), float(place["lon"]))
        if tz:
            out.update({"civil_timezone": tz, "civil_place": place["name"],
                        "civil_country": place.get("country")})

    if data:
        daily = data.get("daily") or {}
        sunrise = (daily.get("sunrise") or [None])[0]
        sunset = (daily.get("sunset") or [None])[0]
        seconds = (daily.get("daylight_duration") or [None])[0]
        out.update({
            "sunrise_utc": sunrise,
            "sunset_utc": sunset,
            "daylight_hours": None if seconds is None else round(seconds / 3600, 1),
        })
        print(f"  -> clock ashore: {out.get('civil_place')} ({out.get('civil_timezone')}), "
              f"sunrise {sunrise} UTC, sunset {sunset} UTC")
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


# ------------------------------------------------- routing and weather ahead
# A 107 KB bitmap of the Atlantic marks open sea at 0.1 degrees with about 6 km
# clearance from land, built from Natural Earth 1:10m coastlines. With it the script
# can work out the sea route from wherever she actually is to the next port, and ask
# for the forecast at the places she will be, at the times she will be there.

SEAGRID_BIN = DATA / "seagrid.bin"
SEAGRID_META = DATA / "seagrid.json"
AHEAD = DATA / "ahead.json"
WAKE = DATA / "wake.json"
EVENTS = DATA / "events.json"
COURSE = DATA / "course.json"
ORBIT = DATA / "orbit.json"
# Forecast points along the projected route. The ladder is trimmed to the leg, so a
# two-day hop shows only the near steps and a two-week ocean crossing reaches as far as
# the models do. Open-Meteo's marine model stops at 8 days, so 168 h is the ceiling.
AHEAD_LADDER = [int(h) for h in os.environ.get(
    "AHEAD_HOURS", "0,6,12,24,36,48,72,96,120,144,168").split(",")]
AHEAD_MAX_HOURS = int(os.environ.get("AHEAD_MAX_HOURS", "168"))
CRUISE_KN = float(os.environ.get("CRUISE_KN", "5.5"))     # used when she is lying still

_grid = None


def sea_grid():
    """Loads the bitmap once. Returns None if the files are missing."""
    global _grid
    if _grid is None:
        try:
            meta = json.loads(SEAGRID_META.read_text(encoding="utf-8"))
            _grid = (meta, SEAGRID_BIN.read_bytes())
        except Exception as exc:
            print(f"  ! no sea grid ({exc}) - routing ahead is skipped", file=sys.stderr)
            _grid = (None, None)
    return _grid if _grid[0] else None


def _is_sea(i: int, j: int) -> bool:
    meta, bits = _grid
    if not (0 <= i < meta["nlat"] and 0 <= j < meta["nlon"]):
        return False
    k = i * meta["nlon"] + j
    return bool(bits[k >> 3] & (1 << (k & 7)))


def _cell(lat: float, lon: float):
    meta = _grid[0]
    return (round((lat - meta["lat0"]) / meta["step"]),
            round((lon - meta["lon0"]) / meta["step"]))


def _coord(i: int, j: int):
    meta = _grid[0]
    return (meta["lat0"] + i * meta["step"], meta["lon0"] + j * meta["step"])


def _nearest_sea(lat: float, lon: float, radius: int = 25):
    ci, cj = _cell(lat, lon)
    if _is_sea(ci, cj):
        return (ci, cj)
    for r in range(1, radius + 1):
        best = None
        for di in range(-r, r + 1):
            for dj in range(-r, r + 1):
                if max(abs(di), abs(dj)) != r:
                    continue
                if _is_sea(ci + di, cj + dj):
                    d = di * di + dj * dj
                    if best is None or d < best[0]:
                        best = (d, ci + di, cj + dj)
        if best:
            return (best[1], best[2])
    return None


def sea_route(start: tuple, goal: tuple) -> list | None:
    """A* across the sea bitmap. Returns a list of (lat, lon) including both ends."""
    import heapq

    if not sea_grid():
        return None
    a, b = _nearest_sea(*start), _nearest_sea(*goal)
    if not a or not b:
        return None

    neigh = [(di, dj) for di in (-1, 0, 1) for dj in (-1, 0, 1) if (di, dj) != (0, 0)]
    g = {a: 0.0}
    came: dict = {}
    heap = [(nm_between(_coord(*a), _coord(*b)), a)]
    seen = set()
    guard = 0
    while heap:
        guard += 1
        if guard > 400000:                     # never let one run stall the job
            print("  ! routing gave up after 400k nodes", file=sys.stderr)
            return None
        _, cur = heapq.heappop(heap)
        if cur == b:
            path = [cur]
            while cur in came:
                cur = came[cur]
                path.append(cur)
            path.reverse()
            pts = [start] + [_coord(*c) for c in path] + [goal]
            return _thin_route(pts)
        if cur in seen:
            continue
        seen.add(cur)
        for di, dj in neigh:
            n = (cur[0] + di, cur[1] + dj)
            if not _is_sea(*n):
                continue
            ng = g[cur] + nm_between(_coord(*cur), _coord(*n))
            if ng < g.get(n, 1e18):
                g[n] = ng
                came[n] = cur
                heapq.heappush(heap, (ng + nm_between(_coord(*n), _coord(*b)), n))
    return None


def _clear(a: tuple, b: tuple) -> bool:
    steps = max(2, int(nm_between(a, b) / 3))
    for k in range(steps + 1):
        la = a[0] + (b[0] - a[0]) * k / steps
        lo = a[1] + (b[1] - a[1]) * k / steps
        if not _is_sea(*_cell(la, lo)):
            return False
    return True


def _thin_route(points: list) -> list:
    out = [points[0]]
    i = 0
    while i < len(points) - 1:
        j = len(points) - 1
        while j > i + 1 and not _clear(points[i], points[j]):
            j -= 1
        out.append(points[j])
        i = j
    return out


def positions_ahead(route: list, speed_kn: float, hours: list, wait_h: float = 0.0) -> list:
    """Walks along the route at the given speed and notes where she will be.

    wait_h holds her at the start until she is due to sail, so a leg that begins after a
    stay in port does not have her creeping out of the harbour on day one.
    """
    legs = [nm_between(route[i - 1], route[i]) for i in range(1, len(route))]
    total = sum(legs)
    out = []
    for h in hours:
        want = speed_kn * max(0.0, h - wait_h)
        if want > total:                       # she arrives before this hour
            break
        run = 0.0
        for i, leg in enumerate(legs):
            if run + leg >= want:
                f = (want - run) / leg if leg else 0
                a, b = route[i], route[i + 1]
                out.append({"hours": h,
                            "lat": round(a[0] + (b[0] - a[0]) * f, 4),
                            "lon": round(a[1] + (b[1] - a[1]) * f, 4)})
                break
            run += leg
    return out


def fetch_ahead(points: list) -> list:
    """Wind and sea state at each projected position, at the hour she gets there."""
    if not points:
        return []
    lats = ",".join(f"{p['lat']:.4f}" for p in points)
    lons = ",".join(f"{p['lon']:.4f}" for p in points)
    base_hour = now_utc().replace(minute=0, second=0, microsecond=0)
    furthest = max(p["hours"] for p in points)
    days_needed = max(2, min(16, furthest // 24 + 2))

    air = get_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": lats, "longitude": lons,
        "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,temperature_2m,weather_code",
        "wind_speed_unit": "ms", "forecast_days": str(days_needed), "timezone": "UTC"}), timeout=45)
    sea = get_json("https://marine-api.open-meteo.com/v1/marine?" + urllib.parse.urlencode({
        "latitude": lats, "longitude": lons,
        "hourly": "wave_height,wave_direction,wave_period",
        "forecast_days": str(min(8, days_needed)), "timezone": "UTC"}), timeout=45)

    def block(data, k):
        if not data:
            return {}
        blocks = data if isinstance(data, list) else [data]
        return (blocks[k].get("hourly") or {}) if k < len(blocks) else {}

    out = []
    for k, p in enumerate(points):
        when = base_hour + timedelta(hours=p["hours"])
        stamp = when.strftime("%Y-%m-%dT%H:00")
        a, s = block(air, k), block(sea, k)
        ai = a.get("time", []).index(stamp) if stamp in (a.get("time") or []) else None
        si = s.get("time", []).index(stamp) if stamp in (s.get("time") or []) else None
        pick = lambda d, key, i: (d.get(key) or [None])[i] if i is not None and d.get(key) else None
        out.append({
            "hours": p["hours"], "time_utc": iso(when), "lat": p["lat"], "lon": p["lon"],
            "wind_ms": pick(a, "wind_speed_10m", ai),
            "wind_dir": pick(a, "wind_direction_10m", ai),
            "gust_ms": pick(a, "wind_gusts_10m", ai),
            "temp_c": pick(a, "temperature_2m", ai),
            "weather_code": pick(a, "weather_code", ai),
            "wave_m": pick(s, "wave_height", si),
            "wave_dir": pick(s, "wave_direction", si),
            "wave_period_s": pick(s, "wave_period", si),
        })
    return out


# ------------------------------------------------------- where she has been, and in what

# How far back the rewind slider can go. Kept modest on purpose: this is "what was it
# like last night", not an archive. Open-Meteo serves past hours from the same endpoints
# through past_days, so no separate historical API and no key.
def position_at(points: list, when: datetime) -> dict | None:
    """Where she was at a given moment, interpolated between the two nearest fixes.

    Returns None outside the track, and None across a gap longer than three hours -
    drawing a straight line through a two-day AIS silence would be an invention, not
    a position.
    """
    if len(points) < 2:
        return None
    lo, hi = 0, len(points) - 1
    try:
        if when < parse_iso(points[0]["t"]) or when > parse_iso(points[-1]["t"]):
            return None
    except Exception:
        return None
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if parse_iso(points[mid]["t"]) <= when:
            lo = mid
        else:
            hi = mid
    a, b = points[lo], points[hi]
    ta, tb = parse_iso(a["t"]), parse_iso(b["t"])
    span = (tb - ta).total_seconds()
    if span > 3 * 3600:
        return None
    f = 0.0 if span <= 0 else (when - ta).total_seconds() / span
    return {
        "lat": a["lat"] + (b["lat"] - a["lat"]) * f,
        "lon": a["lon"] + (b["lon"] - a["lon"]) * f,
        "sog": a.get("sog") if f < 0.5 else b.get("sog"),
        "cog": a.get("cog") if f < 0.5 else b.get("cog"),
    }


def fetch_past(slots: list) -> list:
    """Wind and sea state at each past position, at the hour she was there.

    One call to each API for the whole set: the coordinates go in comma-separated and
    Open-Meteo answers with one block per location. past_days carries the history.
    """
    if not slots:
        return []
    lats = ",".join(f"{s['lat']:.4f}" for s in slots)
    lons = ",".join(f"{s['lon']:.4f}" for s in slots)
    span_h = (now_utc() - parse_iso(slots[0]["t"])).total_seconds() / 3600
    past_days = max(1, min(7, int(span_h // 24) + 1))

    air = get_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": lats, "longitude": lons,
        "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,temperature_2m,weather_code",
        "wind_speed_unit": "ms", "past_days": str(past_days), "forecast_days": "1",
        "timezone": "UTC"}), timeout=45)
    sea = get_json("https://marine-api.open-meteo.com/v1/marine?" + urllib.parse.urlencode({
        "latitude": lats, "longitude": lons,
        "hourly": "wave_height,wave_direction,wave_period",
        "past_days": str(past_days), "forecast_days": "1", "timezone": "UTC"}), timeout=45)

    def block(data, k):
        if not data:
            return {}
        blocks = data if isinstance(data, list) else [data]
        return (blocks[k].get("hourly") or {}) if k < len(blocks) else {}

    out = []
    for k, slot in enumerate(slots):
        stamp = parse_iso(slot["t"]).strftime("%Y-%m-%dT%H:00")
        a, sea_b = block(air, k), block(sea, k)
        ai = a.get("time", []).index(stamp) if stamp in (a.get("time") or []) else None
        si = sea_b.get("time", []).index(stamp) if stamp in (sea_b.get("time") or []) else None
        pick = lambda d, key, i: (d.get(key) or [None])[i] if i is not None and d.get(key) else None
        out.append({**slot,
                    "wind_ms": pick(a, "wind_speed_10m", ai),
                    "wind_dir": pick(a, "wind_direction_10m", ai),
                    "gust_ms": pick(a, "wind_gusts_10m", ai),
                    "temp_c": pick(a, "temperature_2m", ai),
                    "weather_code": pick(a, "weather_code", ai),
                    "wave_m": pick(sea_b, "wave_height", si),
                    "wave_dir": pick(sea_b, "wave_direction", si),
                    "wave_period_s": pick(sea_b, "wave_period", si)})
    return out


def build_wake(points: list) -> None:
    """One entry per whole hour for the last WAKE_HOURS: where she was, and the weather
    that was actually there.

    Entries already fetched are kept, so a run only asks about the hours that are new -
    normally one or two. That keeps the file honest as history rather than re-deriving it
    from today's model run every time.
    """
    if len(points) < 2:
        return
    kept = {e["t"]: e for e in (read_json(WAKE, {}) or {}).get("hours", [])
            if isinstance(e, dict) and e.get("t")}

    top = now_utc().replace(minute=0, second=0, microsecond=0)
    window = [top - timedelta(hours=h) for h in range(WAKE_HOURS, -1, -1)]
    wanted, missing = [], []
    for when in window:
        stamp = iso(when)
        where = position_at(points, when)
        if not where:
            continue                              # outside the track, or across a silence
        old = kept.get(stamp)
        if old and old.get("wind_ms") is not None:
            wanted.append({**old, **where, "t": stamp})   # refresh the position, keep the weather
        else:
            slot = {"t": stamp, **where}
            wanted.append(slot)
            missing.append(slot)

    if missing:
        # Open-Meteo takes a bounded number of locations per call, so ask in chunks of 24
        # - the same order of magnitude as the 5 x 5 grid that has always worked. Two
        # chunks per run fills a 48-hour window on the first run; anything older than that
        # (a long backfill) catches up over the next few runs, newest first.
        CHUNK, MAX_CHUNKS = 24, 2
        todo = missing[-CHUNK * MAX_CHUNKS:]
        fresh = {}
        for i in range(0, len(todo), CHUNK):
            for e in fetch_past(todo[i:i + CHUNK]):
                fresh[e["t"]] = e
        wanted = [fresh.get(w["t"], w) for w in wanted]
        left = len(missing) - len(todo)
        if left:
            print(f"  -> wake: {left} older hours left for the next run")

    payload = {"generated_utc": iso(now_utc()), "hours_back": WAKE_HOURS, "hours": wanted}
    write_json(WAKE, payload)
    withw = sum(1 for w in wanted if w.get("wind_ms") is not None)
    print(f"  -> wake: {len(wanted)} hours on the track, {withw} with weather")


def next_port(ports: list) -> dict | None:
    today = now_utc().strftime("%Y-%m-%d")
    return next((q for q in ports if q.get("arrive", "") > today), None)


def build_ahead(lat: float, lon: float, speed_kn: float | None) -> None:
    """The whole chain: route from here to the next port, then the weather on the way."""
    try:
        plan = read_json(DATA / "ports.json", {})
        port = next_port(plan.get("ports") or [])
        if not port:
            return
        route = sea_route((lat, lon), (port["lat"], port["lon"]))
        if not route or len(route) < 2:
            print("  ! could not route to the next port", file=sys.stderr)
            return
        legs = sum(nm_between(route[i - 1], route[i]) for i in range(1, len(route)))

        # How fast to assume she covers the route. Her speed through the water is the
        # wrong basis: a square rigger beats to windward and runs sail drills, so at
        # 5.5 knots she would "arrive" at Lerwick in two days when the plan says eleven.
        # The plan is the better predictor of when she is due, so pace her to it - and
        # never faster than she can actually sail.
        cruise = max(3.0, speed_kn or CRUISE_KN)
        speed, basis, due_utc, sails_at = cruise, "speed", None, now_utc()

        # If she is alongside somewhere, the passage has not begun: the clock starts when
        # she sails, not now. Otherwise pacing a leg that starts in three weeks would
        # spread it over the weeks in port too and creep along at half a knot.
        in_port = None
        for q in (plan.get("ports") or []):
            if q.get("arrive") and q.get("depart") and q["arrive"] <= today_iso() <= q["depart"]:
                in_port = q
                break
        if in_port:
            try:
                sails_at = max(now_utc(), parse_iso(in_port["depart"] + "T12:00:00Z"))
            except Exception:
                pass

        if port.get("arrive"):
            try:
                due = parse_iso(port["arrive"] + "T12:00:00Z")
                passage_h = (due - sails_at).total_seconds() / 3600
                if passage_h > 1:
                    plan_kn = legs / passage_h
                    if plan_kn < cruise:
                        speed, basis, due_utc = max(0.2, plan_kn), "plan", iso(due)
            except Exception:
                pass
        wait_h = max(0.0, (sails_at - now_utc()).total_seconds() / 3600)
        # Trim the ladder to this leg: to her own arrival at cruising speed, and never
        # past the day the plan says she is due, nor past what the models will answer.
        eta_h = legs / speed
        limit = min(AHEAD_MAX_HOURS, eta_h + 12)
        hours = [h for h in AHEAD_LADDER if h <= limit]
        if not hours:
            hours = [AHEAD_LADDER[0]]
        points = positions_ahead(route, speed, hours, wait_h)
        ahead = fetch_ahead(points)
        write_json(AHEAD, {
            "generated_utc": iso(now_utc()),
            "to": port["name"], "country": port.get("country"),
            "distance_nm": round(legs, 1),
            "speed_kn": round(speed, 1),
            "pace_basis": basis,          # "plan" = spread across the days until she is due
            "due_utc": due_utc,
            "eta_utc": iso(now_utc() + timedelta(hours=legs / speed)),
            "route": [[round(a, 4), round(b, 4)] for a, b in route],
            "points": ahead,
        })
        print(f"  -> route to {port['name']}: {legs:.0f} nm at {speed:.2f} kn "
              f"({'paced to the plan' if basis == 'plan' else 'at cruising speed'}), "
              f"{len(ahead)} forecast points on the way")
    except Exception as exc:
        print(f"  ! weather ahead failed: {exc}", file=sys.stderr)


# --------------------------------------------------------- the course she would sail
# The dashed route to the next port assumes she closes on it steadily. She does not: she
# cannot point within about 58 degrees of the wind, so on a headwind leg she has to beat,
# and where the tacks fall depends on the forecast. This works that out.

COURSE_MAX_HOURS = float(os.environ.get("COURSE_MAX_HOURS", "168"))
COURSE_STEP_H = float(os.environ.get("COURSE_STEP_H", "3"))


def build_course(lat: float, lon: float) -> None:
    try:
        import sailrouter as SR
    except ImportError as exc:
        print(f"  ! sailrouter not importable: {exc}", file=sys.stderr)
        return
    try:
        plan = read_json(DATA / "ports.json", {})
        ports = plan.get("ports") or []
        port = next_port(ports)
        if not port:
            return

        start, goal = (lat, lon), (float(port["lat"]), float(port["lon"]))
        direct = nm_between(start, goal)
        if direct < 15:
            write_json(COURSE, {"generated_utc": iso(now_utc()), "to": port["name"],
                                "note": "already within 15 nm of the port", "points": []})
            return

        # She sails when she sails: if alongside, the passage starts at her departure.
        depart = now_utc()
        for q in ports:
            if q.get("arrive") and q.get("depart") and q["arrive"] <= today_iso() <= q["depart"]:
                try:
                    depart = max(depart, parse_iso(q["depart"] + "T12:00:00Z"))
                except Exception:
                    pass
                break

        # How long the plan allows, for the comparison that is the interesting part.
        plan_hours = None
        if port.get("arrive"):
            try:
                plan_hours = round(
                    (parse_iso(port["arrive"] + "T12:00:00Z") - depart).total_seconds() / 3600, 1)
            except Exception:
                pass

        pad = max(1.5, direct / 120)
        box = (min(start[0], goal[0]) - pad, max(start[0], goal[0]) + pad,
               min(start[1], goal[1]) - pad, max(start[1], goal[1]) + pad)
        horizon = int(min(COURSE_MAX_HOURS, (plan_hours or COURSE_MAX_HOURS) + 24))
        field = SR.WindField.fetch(box, depart, horizon, get_json)
        if not field:
            print("  ! no wind field for the course estimate", file=sys.stderr)
            return

        sea_grid()                                # make sure the bitmap is loaded

        # The land mask keeps a 6 km clearance, so her actual position in or near a
        # harbour often falls inside it. That is a property of the mask, not a mistake
        # about where she is: the router only ever tests positions it is proposing to
        # sail to, and the start is exempt because she is demonstrably afloat there.
        def is_sea(la, lo):
            cell = _cell(la, lo)
            return True if cell is None else _is_sea(*cell)

        route = SR.isochrone_route(start, goal, depart, field, is_sea=is_sea,
                                  step_h=COURSE_STEP_H, max_hours=horizon)
        if not route or len(route.get("points") or []) < 2:
            print("  ! could not work out a sailing course", file=sys.stderr)
            return

        pts = route["points"]
        winds = [p["tws"] for p in pts if p.get("tws") is not None]
        payload = {
            "generated_utc": iso(now_utc()),
            "to": port["name"],
            "depart_utc": iso(depart),
            "reached": bool(route.get("reached")),
            "hours": route["hours"],
            "plan_hours": plan_hours,
            "direct_nm": round(direct, 1),
            "sailed_nm": round(sum(nm_between((pts[i-1]["lat"], pts[i-1]["lon"]),
                                              (pts[i]["lat"], pts[i]["lon"]))
                                   for i in range(1, len(pts))), 1),
            "tacks": route["tacks"],
            "wind_kn": {"min": round(min(winds), 1), "max": round(max(winds), 1)} if winds else None,
            "points": pts,
        }
        write_json(COURSE, payload)
        verdict = "arrives" if route.get("reached") else "runs out of forecast"
        extra = ""
        if plan_hours and route.get("reached"):
            extra = f", plan allows {plan_hours:.0f} h"
        print(f"  -> sailing course to {port['name']}: {verdict} in {route['hours']:.0f} h"
              f"{extra}, {len(route['tacks'])} tacks over {payload['sailed_nm']:.0f} nm "
              f"({payload['direct_nm']:.0f} nm direct)")
    except Exception as exc:
        print(f"  ! course estimate failed: {exc}", file=sys.stderr)


# ------------------------------------------------------------------ event log
# A tracking site's "Manoeuvring" and "Destination changed" lines are not messages the
# ship sends - they are that site's reading of two AIS fields: the navigational status in
# every position report, and the voyage block the crew fill in by hand. We receive both,
# so we can derive the same log ourselves, and name things more usefully than a generic
# "manoeuvring": for a full-rigged ship, engine versus sail is the interesting change.

NAV_STATUS = {
    0: "Under way using engine",
    1: "At anchor",
    2: "Not under command",
    3: "Restricted manoeuvrability",
    4: "Constrained by draught",
    5: "Moored",
    6: "Aground",
    7: "Engaged in fishing",
    8: "Under way under sail",
    11: "Under tow astern",
    12: "Under tow alongside",
    14: "Distress signal (AIS-SART)",
    15: "Status not reported",
}
EVENT_CAP = int(os.environ.get("EVENT_CAP", "400"))
SILENCE_HOURS = float(os.environ.get("SILENCE_HOURS", "6"))


def build_events(collected: list, static: dict) -> None:
    """Append what changed to data/events.json.

    Only changes are recorded, and a status has to hold for two fixes before it counts -
    a single stray report while she tacks would otherwise fill the log with noise.
    """
    stored = read_json(EVENTS, {}) or {}
    events = [e for e in (stored.get("events") or []) if isinstance(e, dict) and e.get("t")]
    state = dict(stored.get("state") or {})
    added = 0

    def note(when: str, kind: str, text: str, detail: str | None = None):
        nonlocal added
        if any(e["t"] == when and e.get("kind") == kind and e.get("text") == text for e in events):
            return
        row = {"t": when, "kind": kind, "text": text}
        if detail:
            row["detail"] = detail
        events.append(row)
        added += 1

    fixes = sorted([c for c in collected if c.get("seen_utc")], key=lambda c: c["seen_utc"])

    # A long silence, now ended: worth a line, because on this voyage it will mean an ocean.
    if fixes and state.get("last_fix_utc"):
        try:
            gap = (parse_iso(fixes[0]["seen_utc"]) - parse_iso(state["last_fix_utc"])).total_seconds() / 3600
            if gap >= SILENCE_HOURS:
                note(fixes[0]["seen_utc"], "coverage",
                     f"Back in AIS coverage after {round(gap)} hours",
                     f"last heard {state['last_fix_utc']}")
        except Exception:
            pass

    # Navigational status, with a two-fix confirmation.
    pending, pending_since, count = state.get("nav_pending"), state.get("nav_pending_since"), state.get("nav_pending_n", 0)
    for f in fixes:
        ns = f.get("nav_status")
        if ns is None or ns == 15:
            continue
        if ns == state.get("nav_status"):
            pending, pending_since, count = None, None, 0
            continue
        if ns == pending:
            count += 1
        else:
            pending, pending_since, count = ns, f["seen_utc"], 1
        if count >= 2:
            name = NAV_STATUS.get(ns, f"Status {ns}")
            was = NAV_STATUS.get(state.get("nav_status"))
            note(pending_since, "nav", name, f"was {was}" if was else None)
            state["nav_status"] = ns
            pending, pending_since, count = None, None, 0
    state["nav_pending"], state["nav_pending_since"], state["nav_pending_n"] = pending, pending_since, count

    # The voyage block the crew type in.
    newest = fixes[-1]["seen_utc"] if fixes else iso(now_utc())
    merged_static = dict(static or {})
    for f in fixes:
        for key in ("destination", "draught_m"):
            if f.get(key):
                merged_static.setdefault(key, f[key])
    for key, label in (("destination", "Destination reported"),
                       ("eta_text", "ETA reported"),
                       ("draught_m", "Draught reported")):
        value = merged_static.get(key)
        if value in (None, "", 0):
            continue
        if state.get(key) != value:
            shown = f"{value} m" if key == "draught_m" else str(value)
            was = state.get(key)
            note(newest, key, f"{label}: {shown}",
                 f"was {was}{' m' if key == 'draught_m' and was else ''}" if was else None)
            state[key] = value

    if fixes:
        state["last_fix_utc"] = fixes[-1]["seen_utc"]

    events.sort(key=lambda e: e["t"])
    events = events[-EVENT_CAP:]
    write_json(EVENTS, {"generated_utc": iso(now_utc()), "state": state, "events": events})
    if added:
        print(f"  -> events: {added} new, {len(events)} kept")
    else:
        print(f"  -> events: nothing new, {len(events)} kept")


# --------------------------------------------------- satellite passes that saw her
# Searched at most once an hour: the catalogues only gain a scene every few hours, and
# each search is two HTTP calls we have no reason to repeat every round.

ORBIT_EVERY_MIN = float(os.environ.get("ORBIT_EVERY_MIN", "55"))


def build_orbit(points: list) -> None:
    try:
        import orbithunter
    except ImportError as exc:
        print(f"  ! orbithunter not importable: {exc}", file=sys.stderr)
        return
    try:
        stored = read_json(ORBIT, {}) or {}
        last = stored.get("generated_utc")
        if last:
            try:
                if (now_utc() - parse_iso(last)).total_seconds() / 60 < ORBIT_EVERY_MIN:
                    return
            except Exception:
                pass
        # There used to be a land_span() walk here, widening a panel until the land mask
        # found a coastline. It served the wide NASA weather panel, which is gone - the
        # Satellite button on the map showed the same data - so the walk went with it.
        hits = orbithunter.hunt(points, position_at, iso, parse_iso,
                                known=stored.get("hits") or [])
        write_json(ORBIT, {"generated_utc": iso(now_utc()), "hits": hits})
        shot = [h for h in hits if h.get("cog")]
        print(f"  -> orbit: {len(hits)} passes on record, {len(shot)} with a picture we can show")
    except Exception as exc:
        print(f"  ! orbit search failed: {exc}", file=sys.stderr)


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
        # Two sources for the same window: BarentsWatch in a background thread,
        # aisstream on the main one. Whatever each hears goes into the same pot.
        import threading

        pot: dict[str, dict] = {}
        lock = threading.Lock()
        bw_thread = threading.Thread(
            target=collect_from_barentswatch, args=(LISTEN_SECONDS, pot, lock), daemon=True)
        bw_thread.start()

        position, ais_points = fetch_position_from_aisstream(api_key)
        bw_thread.join(timeout=30)

        with lock:
            for pt in ais_points:
                pot.setdefault(pt["seen_utc"], pt)
            merged = [pot[t] for t in sorted(pot)]

        # Third source: the foundation's own page. It is a rolling window of the last
        # few days rather than a live stream, so it also repairs anything the two
        # streams missed while nobody was listening - including whole hours between runs.
        for pt in fetch_from_foundation():
            pot.setdefault(pt["seen_utc"], pt)
        merged = [pot[t] for t in sorted(pot)]

        if not merged:
            fallback = bw_latest_position()
            if fallback:
                merged = [fallback]
        collected = merged
        if collected:
            position = collected[-1]
            by_source: dict[str, int] = {}
            for pt in collected:
                by_source[pt["source"]] = by_source.get(pt["source"], 0) + 1
            print("* combined sources: " + ", ".join(f"{k} {v}" for k, v in by_source.items())
                  + f" -> {len(collected)} positions total")

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
        # The foundation's feed reaches days back, so a fix can belong anywhere in the
        # track, not only at the end. Merge by time instead of appending, and drop a fix
        # that lands within MIN_GAP_MIN of one we already have unless it has moved.
        import bisect

        merged_pts = []
        for pt in points:
            try:
                merged_pts.append((parse_iso(pt["t"]), pt))
            except Exception:
                continue
        merged_pts.sort(key=lambda x: x[0])
        stamps = [t for t, _ in merged_pts]

        added = 0
        for fix in collected:
            try:
                when = parse_iso(fix["seen_utc"])
            except Exception:
                continue
            lat_i, lon_i = float(fix["lat"]), float(fix["lon"])

            k = bisect.bisect_left(stamps, when)
            crowded = False
            for j in (k - 1, k):
                if 0 <= j < len(merged_pts):
                    t_j, pt_j = merged_pts[j]
                    gap = abs((when - t_j).total_seconds()) / 60
                    moved = nm_between((pt_j["lat"], pt_j["lon"]), (lat_i, lon_i))
                    if gap < 0.5 or (moved < MIN_MOVE_NM and gap < MIN_GAP_MIN):
                        crowded = True
                        break
            if crowded:
                continue

            sog_v, cog_v = fix.get("sog_kn"), fix.get("cog_deg")
            entry = {"t": iso(when), "lat": round(lat_i, 5), "lon": round(lon_i, 5),
                     "sog": None if sog_v is None else round(float(sog_v), 1),
                     "cog": None if cog_v is None else round(float(cog_v))}
            merged_pts.insert(k, (when, entry))
            stamps.insert(k, when)
            added += 1

        points = [pt for _, pt in merged_pts]
        print(f"  -> added {added} of {len(collected)} collected positions to the track")
        points = thin_track(points)

    # A backfill can leave the track newer than the position we just derived; keep the
    # two in step so the marker never sits behind the end of its own line.
    if points:
        try:
            newest_t = parse_iso(points[-1]["t"])
            if not position.get("seen_utc") or newest_t > parse_iso(position["seen_utc"]):
                position = dict(position, lat=points[-1]["lat"], lon=points[-1]["lon"],
                                sog_kn=points[-1].get("sog"), cog_deg=points[-1].get("cog"),
                                heading_deg=None, seen_utc=points[-1]["t"])
                print("  -> position taken from the newest track point")
        except Exception:
            pass

    # Older points were stored with full float precision - up to sixteen digits of noise
    # on a position an AIS receiver knows to a few metres. Round the lot on the way out.
    for q in points:
        q["lat"], q["lon"] = round(float(q["lat"]), 5), round(float(q["lon"]), 5)
        if q.get("sog") is not None:
            q["sog"] = round(float(q["sog"]), 1)
        if q.get("cog") is not None:
            q["cog"] = round(float(q["cog"]))

    # Steps under 0.02 nm are receiver jitter while she lies still, not distance sailed.
    distance_nm = sum(
        step for step in (
            nm_between((points[i - 1]["lat"], points[i - 1]["lon"]),
                       (points[i]["lat"], points[i]["lon"]))
            for i in range(1, len(points))
        ) if step >= 0.02
    )

    sun = fetch_sun_moon(lat, lon)

    for kind, path in (("wind", WIND), ("waves", WAVES)):
        grid = fetch_grid(lat, lon, kind)
        if grid:
            write_json(path, grid)
        else:
            print(f"  -> no {kind} grid this run, keeping the previous one")

    write_json(TRACK, {"mmsi": MMSI, "ship": SHIP_NAME, "points": points}, compact=True)
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
    build_ahead(lat, lon, position.get("sog_kn"))
    build_wake(points)
    # The voyage fields ride on the newest position, having been merged there from the
    # static messages, so pass them along rather than re-deriving them.
    build_events(collected, {k: position.get(k) for k in ("destination", "eta_text", "draught_m")})
    build_course(lat, lon)
    build_orbit(points)
    update_history(weather, points)
    print(f"* wrote {LATEST.name} and {TRACK.name} ({len(points)} points, {distance_nm:.0f} nm)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
