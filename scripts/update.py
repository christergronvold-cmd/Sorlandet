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
import re
import shutil
import ssl
import subprocess
import sys
import unicodedata
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

# AIS reports speed over ground in tenths of a knot, and 1023 (102.3 kn) means "not
# available". Some receivers pass that straight through, and a few emit other junk. None of
# it is a speed, and any of it becomes a permanent record on a page that keeps a maximum.
SOG_MAX_PLAUSIBLE = float(os.environ.get("SOG_MAX_PLAUSIBLE", "20"))
# The ship's own feed carries positions only, so speed there has to be worked out from the
# step between two fixes. Over a short step that number is mostly noise: the chord between
# two positions is the shortest path, so a curve reads low, while a metre or two of receiver
# error divided by thirty seconds reads high. Two minutes is the shortest step worth using.
MIN_DERIVE_SEC = float(os.environ.get("MIN_DERIVE_SEC", "120"))


def clean_sog(value) -> float | None:
    """A transmitted speed, or None if what arrived cannot be one."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v < 0 or v >= SOG_MAX_PLAUSIBLE:
        return None
    return v

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

    # Speed and course from the step between neighbours, so the page still has both. This is
    # geometry, not something the ship said, and it is marked as such: a straight line
    # between two fixes is the shortest path, so a ship sailing a curve always reads slower
    # than she was going, while receiver error over a short step reads faster. Downstream
    # must never let one of these set a speed record - see `derived` below.
    derived = 0
    for i in range(1, len(fixes)):
        a, b = fixes[i - 1], fixes[i]
        try:
            seconds = (parse_iso(b["seen_utc"]) - parse_iso(a["seen_utc"])).total_seconds()
        except Exception:
            continue
        if not MIN_DERIVE_SEC <= seconds <= 3 * 3600:
            continue
        dist = nm_between((a["lat"], a["lon"]), (b["lat"], b["lon"]))
        speed = dist / (seconds / 3600)
        if speed < SOG_MAX_PLAUSIBLE:
            b["sog_kn"] = round(speed, 1)
            b["cog_deg"] = round(bearing((a["lat"], a["lon"]), (b["lat"], b["lon"])))
            b["sog_derived"] = True
            derived += 1

    if fixes:
        print(f"  -> foundation feed: {len(fixes)} positions, newest {fixes[-1]['seen_utc']}"
              f"; speed worked out for {derived} of them (marked as computed, not reported)")
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
                    "sog_kn": clean_sog(body.get("Sog")),
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
        "sog_kn": clean_sog(msg.get("speedOverGround")),
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


# The page used to thicken this grid in the browser: every reader's map fetched its own
# lattice from Open-Meteo for whatever was on screen. Ninety locations, four variables and
# five days per fetch, on every pan and on every five-minute refresh, from every open tab -
# and on 30 August it spent the whole daily allowance and came back 429 for the rest of the
# day. The wind simply vanished off the map.
#
# One fetch here serves all hundred and forty families instead. So this grid is now dense
# enough to be the only one: 9 x 9 over 300 nm is a point every 37 miles, which is what the
# map shows when it is looking at her.
GRID_CELLS = int(os.environ.get("GRID_CELLS", "9"))        # 9 x 9 points
GRID_SPAN_NM = float(os.environ.get("GRID_SPAN_NM", "300"))  # box side in nautical miles

# ...and it is not refetched every round. A forecast is reissued every few hours, and she
# makes 40 nm in seven of them; asking every 29 minutes buys nothing and costs eighty-one
# locations a time. Refetch when the file is old, or when she has sailed out of the middle
# of it - whichever comes first.
GRID_MAX_AGE_MIN = float(os.environ.get("GRID_MAX_AGE_MIN", "75"))
GRID_MOVE_NM = float(os.environ.get("GRID_MOVE_NM", "40"))
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


def _grid_still_good(path, lat: float, lon: float) -> str | None:
    """Why the grid on disk does not need replacing, or None if it does.

    Age and distance, nothing else. If the file is unreadable or has no centre - which is
    every file written before this existed - it is refetched, once.
    """
    try:
        old = read_json(path, None)
        if not old or not old.get("cells"):
            return None
        centre = old.get("centre")
        if not centre:
            return None
        age_min = (now_utc() - parse_iso(old["generated_utc"])).total_seconds() / 60
        moved = nm_between((float(centre[0]), float(centre[1])), (lat, lon))
        if age_min >= GRID_MAX_AGE_MIN or moved >= GRID_MOVE_NM:
            return None
        return (f"is {age_min:.0f} min old and she has moved {moved:.0f} nm from its "
                f"middle - kept")
    except Exception:
        return None


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


def civil_place(ports: list, points: list | None = None) -> dict | None:
    """The port whose clock belongs on the page: the one she is in, else the next."""
    st = voyage_state(ports, points)
    return st["in_port"] or st["to"]


def port_timezone(lat: float, lon: float) -> str | None:
    """The IANA zone name ashore at a place, e.g. Europe/London.

    The name and not the offset, so the page can work out the time for any date - summer
    time included - rather than freezing today's offset into the file.
    """
    data = get_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
        "current": "temperature_2m", "timezone": "auto", "forecast_days": "1"}), timeout=25)
    return (data or {}).get("timezone")


def fetch_sun_moon(lat: float, lon: float, points: list | None = None) -> dict | None:
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

    place = civil_place((read_json(DATA / "ports.json", {}).get("ports") or []), points)
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


def update_history(weather: dict, points: list, fixes: list | None = None) -> None:
    """Keeps one row per day: strongest wind, highest wave, fastest she reported.

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

    # Fastest she REPORTED that day, and only that. A speed worked out from the step
    # between two positions is not a measurement - it reads low through a tack and high
    # over a short step - and letting those into a daily maximum would be the 11.1-knot
    # bug again, once per day. Every fix heard this run is considered, not just the last
    # one, so a peak in the middle of the window is not missed.
    for fix in (fixes or []):
        if fix.get("sog_derived"):
            continue
        v = clean_sog(fix.get("sog_kn"))
        if v is None or v <= 0:
            continue
        day = str(fix.get("seen_utc") or "")[:10] or today
        r = by_date.setdefault(day, {"date": day})
        r["max_sog_kn"] = round(max(v, r.get("max_sog_kn") or 0), 1)

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
# Inside this, no route line: the sea grid the route is searched on keeps six kilometres
# clear of land, which is wider than Bressay Sound, so the last mile cannot be drawn
# without putting it ashore. Matches AHEAD_MIN_NM on the page.
AHEAD_CLEAR_NM = 4.0
WAKE = DATA / "wake.json"
COURSE = DATA / "course.json"
ORBIT = DATA / "orbit.json"
POLAR = DATA / "polar.json"
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


def made_good_kn(points: list, target: tuple, window_h: float,
                 cap_kn: float = 12.0) -> float | None:
    """How fast she has actually been closing on the target, over the last window_h.

    Not her speed through the water: a square rigger tacking makes seven knots through the
    water and two towards where she is going. What the projection needs is the second
    number - the rate at which the distance still to run is shrinking - and the only honest
    place to get it is her own track.
    """
    if len(points) < 2:
        return None
    try:
        t_end = parse_iso(points[-1]["t"])
    except Exception:
        return None
    cutoff = t_end - timedelta(hours=window_h)
    start = None
    for q in points:
        try:
            if parse_iso(q["t"]) >= cutoff:
                start = q
                break
        except Exception:
            continue
    if start is None:
        return None

    # A least-squares slope through every fix in the window, not the difference between the
    # first and the last. Two-point arithmetic is exactly what produced the phantom 11-knot
    # speed record: one bad fix at either end moves the answer a long way, and here it would
    # move the whole projection with it. With fifty fixes in six hours, one outlier barely
    # shifts the line.
    rows = []
    for q in points:
        try:
            t = parse_iso(q["t"])
        except Exception:
            continue
        if t < cutoff:
            continue
        rows.append(((t - cutoff).total_seconds() / 3600,
                     nm_between((q["lat"], q["lon"]), target)))
    if len(rows) < 2:
        return None
    span = rows[-1][0] - rows[0][0]
    if span < 1:
        return None
    n = len(rows)
    mx = sum(x for x, _ in rows) / n
    my = sum(y for _, y in rows) / n
    var = sum((x - mx) ** 2 for x, _ in rows)
    if var <= 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in rows) / var   # nm per hour, negative = closing
    return min(cap_kn, -slope)


# How quickly the pace eases from what she is doing now towards what the plan implies.
PACE_TAU_H = float(os.environ.get("PACE_TAU_H", "18"))
# The most this ship credibly makes good on a destination, day in day out. A ceiling on the
# measured rate, so one strange stretch of track cannot launch the projection into orbit.
PACE_MAX_KN = float(os.environ.get("PACE_MAX_KN", "10"))


def pace_along(legs_nm: float, vmg_kn: float | None, plan_h: float | None,
               cruise_kn: float, hold_h: float = 24.0, tau_h: float = PACE_TAU_H):
    """Distance along the route at hour h, and a word for what it is based on.

    Two questions were being answered with one number, and each answer ruined the other.
    "Where is she in six hours" wants the rate she is actually making good. "When is she
    due" wants the plan, which on the Lerwick leg allows about eleven days for two hundred
    and fifty miles - a shade under one knot. Spreading the whole distance across the whole
    plan, which is what this did first, made her creep five miles in six hours while she was
    in fact making forty. Using her observed rate for the whole passage had her alongside a
    week early, which she certainly will not be.

    So: she is assumed to keep up her measured rate for `hold_h` - the same span it was
    measured over - and then to ease towards a residual chosen so that the route still lands
    on the day the plan says she is due.

    The flat first stretch matters. An exponential that starts decaying immediately covers
    noticeably less in its first day than the rate it started from, which is a strange thing
    to tell a reader: "she made 78 miles yesterday, so tomorrow, 51". Holding it means the
    first day of the projection really is her last day's progress.
    """
    if vmg_kn is None or vmg_kn <= 0.1:
        if plan_h and plan_h > 1:
            rate = max(0.05, legs_nm / plan_h)
            return (lambda h: rate * h), "plan", rate
        rate = max(0.5, min(cruise_kn, vmg_kn or cruise_kn))
        return (lambda h: rate * h), "cruise", rate

    v0 = min(vmg_kn, cruise_kn)
    steady = (lambda h: v0 * h), "made good", v0
    if not plan_h or plan_h <= 1:
        return steady

    hold = max(0.0, min(hold_h, plan_h))
    held = v0 * hold
    if held >= legs_nm:
        return steady            # she gets there inside the held stretch; nothing to ease to

    rest_h = plan_h - hold
    k = tau_h * (1 - math.exp(-rest_h / tau_h))
    if rest_h - k <= 1:                        # the plan ends inside the easing window
        return steady
    resid = (legs_nm - held - v0 * k) / (rest_h - k)
    resid = max(0.05, min(cruise_kn, resid))

    def distance_at(h: float) -> float:
        if h <= hold:
            return v0 * h
        g = h - hold
        return held + resid * g + (v0 - resid) * tau_h * (1 - math.exp(-g / tau_h))

    return distance_at, "made good easing to the plan", v0


def positions_ahead(route: list, distance_at, hours: list, wait_h: float = 0.0) -> list:
    """Walks along the route and notes where she will be at each hour on the ladder.

    distance_at(h) gives how far along the route she is after h hours of sailing - a
    function rather than a speed, because the pace is not constant (see pace_along).

    wait_h holds her at the start until she is due to sail, so a leg that begins after a
    stay in port does not have her creeping out of the harbour on day one.
    """
    legs = [nm_between(route[i - 1], route[i]) for i in range(1, len(route))]
    total = sum(legs)
    out = []
    for h in hours:
        want = distance_at(max(0.0, h - wait_h))
        if want > total:                       # she arrives before this hour
            break
        run = 0.0
        for i, leg in enumerate(legs):
            if run + leg >= want:
                f = (want - run) / leg if leg else 0
                a, b = route[i], route[i + 1]
                out.append({"hours": h, "where": "sea",
                            "lat": round(a[0] + (b[0] - a[0]) * f, 4),
                            "lon": round(a[1] + (b[1] - a[1]) * f, 4)})
                break
            run += leg
    return out


def positions_after_arrival(port: dict, ports: list, hours: list) -> list:
    """Where she will be once this leg is over: alongside, then on her way again.

    The ladder used to stop dead at her own arrival, and the card with it. Thirty miles off
    Lerwick that left a single tile six hours out - the forecast for the week she is
    actually going to spend there simply vanished, at the moment it became most useful.
    A family looking at the coming days wants the weather where she will BE, and for four
    of those days that is a quay in Shetland.

    After she sails the plan is all we have: no course has been routed for the next leg
    yet, so the position is walked along the straight line to the port after this one at
    her cruising speed. Each point says which of the three it is, so the page can too.
    """
    if not hours:
        return []
    here = (float(port["lat"]), float(port["lon"]))
    nxt = None
    for i, q in enumerate(ports):
        if q is port and i + 1 < len(ports):
            nxt = ports[i + 1]
            break
    depart = None
    if port.get("depart"):
        try:
            depart = parse_iso(port["depart"] + "T12:00:00Z")
        except Exception:
            depart = None

    out = []
    for h in hours:
        when = now_utc() + timedelta(hours=h)
        if depart and nxt and when > depart:
            goal = (float(nxt["lat"]), float(nxt["lon"]))
            total = nm_between(here, goal)
            run = (when - depart).total_seconds() / 3600 * CRUISE_KN
            f = min(1.0, run / total) if total else 1.0
            out.append({"hours": h, "where": "onward", "port": nxt["name"],
                        "lat": round(here[0] + (goal[0] - here[0]) * f, 4),
                        "lon": round(here[1] + (goal[1] - here[1]) * f, 4)})
        else:
            out.append({"hours": h, "where": "port", "port": port["name"],
                        "lat": round(here[0], 4), "lon": round(here[1], 4)})
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
        "ns": a.get("ns") if f < 0.5 else b.get("ns"),
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

    Wrapped, like every other builder: this was the only one without a guard, so one odd
    answer from the weather API took down the polar, the course, the projection, the orbit
    and the history that all run after it.
    """
    try:
        _build_wake(points)
    except Exception as exc:
        print(f"  ! wake failed: {exc}", file=sys.stderr)


def _build_wake(points: list) -> None:
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


# ------------------------------------------------------------------ where she is on the plan
#
# The same rule as the page, deliberately: a stay within PORT_NM of a listed port lasting at
# least PORT_DWELL_H, during which she averages under PORT_CALM_KN. Five miles covers an
# anchorage or a berth slightly off the position held for the port, and is wide enough that
# she also sails through it on the way past - the approaches to Lerwick are on the road to
# Dublin - so the dwell and the speed are both needed. `test_leg.py` runs this and the
# page's `voyageState()` over identical fixtures and fails if they ever answer differently.
PORT_NM = 5.0
PORT_DWELL_H = 2.5
PORT_CALM_KN = 2.0
PORT_REJOIN_H = 6.0


def port_stays(points: list, ports: list) -> list:
    """Every call she has actually made, in order, from her own track.

    Ports are identified by their INDEX on the route, never by name: this voyage ends where
    it began, so "Kristiansand" is both the first port and the nineteenth. Keyed by name,
    the very first morning alongside in August reads as having reached the last port of the
    voyage. `floor` is how far along the route she has been seen and the search never looks
    behind it, so the same quay means port 0 in August and port 18 next May. No two ports on
    this route are within five miles of each other, so nothing else is affected by it.
    """
    if not points or not ports:
        return []
    floor = 0

    def at(pt):
        nonlocal floor
        best, bd = -1, PORT_NM
        for i in range(floor, len(ports)):
            try:
                d = nm_between((pt["lat"], pt["lon"]), (ports[i]["lat"], ports[i]["lon"]))
            except (KeyError, TypeError):
                continue
            if d < bd:
                best, bd = i, d
        if best > floor:
            floor = best
        return best

    stays, prev = [], None
    for pt in points:
        if pt.get("lat") is None or pt.get("lon") is None or not pt.get("t"):
            continue
        pi = at(pt)
        if pi < 0:
            prev = None
            continue
        last = stays[-1] if stays else None
        t = parse_iso(pt["t"])
        if last and last["pi"] == pi and \
                (t - parse_iso(last["out"])).total_seconds() <= PORT_REJOIN_H * 3600:
            if prev:
                last["dist"] += nm_between((prev["lat"], prev["lon"]), (pt["lat"], pt["lon"]))
                last["span"] += (t - parse_iso(prev["t"])).total_seconds() / 3600
            last["out"] = pt["t"]
        else:
            stays.append({"pi": pi, "port": ports[pi]["name"],
                          "country": ports[pi].get("country"), "in": pt["t"],
                          "out": pt["t"], "dist": 0.0, "span": 0.0})
        prev = pt

    still = at(points[-1])
    out = []
    for i, v in enumerate(stays):
        if v["span"] < PORT_DWELL_H:
            continue
        if v["span"] > 0 and v["dist"] / v["span"] >= PORT_CALM_KN:
            continue                                  # she was passing through
        v["ended"] = not (i == len(stays) - 1 and still == v["pi"])
        out.append(v)
    return out


def lying_off_open(port: dict, when=None) -> bool:
    """Is this port's declared anchorage window open right now?

    A stay inside it means she is waiting OFF the port, not that she has called at it.
    Geometry cannot tell those apart here: Lerwick Roads is half a mile from the quay
    coordinate and the berth radius is eight tenths of a mile, so a ship lying to her
    anchor reads as alongside. On 4 September that turned into the page announcing the
    Lerwick call complete and setting the course for Dublin, five hundred and forty miles
    off, while she sat at anchor waiting for her berth on the sixth - and while the same
    page correctly said "at anchor" in the camera box.

    So a person's observation wins over half a mile of arithmetic, and it wins for exactly
    as long as they said it would.
    """
    st = (port or {}).get("lying_off") or {}
    if not st.get("from") or not st.get("until"):
        return False
    now = when or now_utc()
    try:
        if now < parse_iso(st["from"]):
            return False
        return now <= parse_iso(st["until"] + "T23:59:59Z")
    except Exception:
        return False


def voyage_state(ports: list, points: list | None = None, today: str | None = None) -> dict:
    """Which leg she is on: the track first, the calendar only where it cannot see.

    The plan is tentative and the page says so. Letting the calendar decide meant that at
    midnight on Lerwick's scheduled arrival date the whole forward projection swung to
    Dublin, 460 nm further on, whether or not she had docked - while the Voyage plan card
    said she was in Lerwick. Two cards on one screen disagreeing.
    """
    today = today or today_iso()
    ports = ports or []
    stays = port_stays(points or [], ports)
    last = None
    for v in stays:
        if last is None or v["pi"] >= last["pi"]:
            last = v

    def settle(i, ended, basis):
        return {"to": ports[i + 1] if i + 1 < len(ports) else None,
                "in_port": None if ended else ports[i],
                "done": i + 1 if ended else i,
                "basis": basis}

    if last:
        i = last["pi"]
        q = ports[i]
        # A call is not over until the day she is due to leave. port_stays will call a stay
        # ended on thin evidence - a drift outside the five-mile radius, a berth shift, or a
        # twelve-hour gap in AIS while she lies still - and on 4 September that sent the
        # course 542 miles to Dublin with her tied up in Lerwick until the 10th. The plan
        # knows when she leaves; evidence of her actually leaving still wins.
        newest = (points or [])[-1] if (points or []) else None
        gone = False
        if newest and q:
            try:
                off = nm_between((float(newest["lat"]), float(newest["lon"])),
                                 (float(q["lat"]), float(q["lon"])))
                gone = off > PORT_NM * 2 and float(newest.get("sog") or 0) > 1.0
            except Exception:
                gone = False
        # Only `ended` is overridden: "to" stays the NEXT port, so nothing else in the job
        # changes shape. The first attempt pointed "to" back at the port she was sitting
        # in, and three tests objected, correctly.
        if q.get("depart") and today <= q["depart"] and not gone and last["ended"]:
            out = settle(i, False, "in-port-until-depart")
            return out
        # Declared anchorage: she is off this port, not through it.
        if lying_off_open(ports[i]):
            # She IS at this port, off it rather than in it - so in_port stays set and the
            # next port is this one, not the one after.
            return {"to": ports[i], "in_port": ports[i], "done": i, "basis": "declared"}
        return settle(i, last["ended"], "track")

    # Nothing observed. Fall back to the calendar - but read the same shape out of it, so
    # the fallback cannot say she is alongside in Lerwick and also on her way to Lerwick.
    # A port counts as reached only once its arrival day has fully passed: on the morning
    # of the seventh, the plan claiming she is in Lerwick is a hope, not an observation.
    def end(q):
        return q.get("depart") or q.get("arrive") or ""

    i = -1
    for k, q in enumerate(ports):
        if q.get("arrive", "") < today:
            i = k
    if i < 0:
        return {"to": ports[0] if ports else None, "in_port": None, "done": 0,
                "basis": "plan"}
    return settle(i, end(ports[i]) < today, "plan")


def next_port(ports: list, points: list | None = None) -> dict | None:
    return voyage_state(ports, points)["to"]


def build_ahead(lat: float, lon: float, speed_kn: float | None,
                track_points: list | None = None) -> None:
    """Write the route ahead - or, if that fails, make sure the old one stops being drawn.

    This wrapper exists because of one map, on 2 September 2026. She had lain at anchor off
    Lerwick since the 31st, and the page still showed the dashed route written on the 31st:
    a line running from beside her back out to seaward and in again, across Bressay.
    Nothing was wrong with the drawing code. The route had simply never been rewritten -
    the work below can fail for several honest reasons (no pace to walk at, no wind field,
    no way through the sea grid) and every one of them used to leave the last good route
    sitting on disk.

    A two-day-old forecast presented as a forecast is worse than no forecast, and the page
    cannot tell the difference unless the file says so. So: if a round produces no new
    route, the old one is taken out and the file restamped. The weather ladder is kept -
    that is the part that still matters while she is in port.
    """
    before = read_json(AHEAD, {}) or {}
    # "Did this round write the file" is a question about the file, not about the timestamp
    # inside it. The first version compared generated_utc, which is truncated to the second
    # - so two rounds in the same second looked identical and a perfectly good new route
    # was thrown away as stale. The test suite runs that fast; a busy job could too.
    stamp = AHEAD.stat().st_mtime_ns if AHEAD.exists() else 0
    _build_ahead(lat, lon, speed_kn, track_points)
    if not AHEAD.exists() or AHEAD.stat().st_mtime_ns != stamp:
        return                                    # a fresh round wrote a fresh file
    if not before.get("route"):
        return                                    # nothing stale to take out
    write_json(AHEAD, {k: v for k, v in before.items()
                       if k not in ("route", "tacks", "route_basis", "distance_nm",
                                    "direct_nm", "winding")}
               | {"generated_utc": iso(now_utc()), "route_cleared": True})
    print("* no new route this round - cleared the old one rather than leave it on the map")


def _build_ahead(lat: float, lon: float, speed_kn: float | None,
                 track_points: list | None = None) -> None:
    """The whole chain: route from here to the next port, then the weather on the way."""
    try:
        plan = read_json(DATA / "ports.json", {})
        port = next_port(plan.get("ports") or [], track_points)
        if not port:
            return
        # Inside the approaches there is no route worth drawing, and the file must SAY so
        # rather than be left as it was.
        #
        # On 2 September the map still carried the route written on 31 August: a dashed line
        # running from beside her back out to seaward and in again, over Bressay, describing
        # a passage she had already made. Nothing was wrong with the drawing. The file was
        # simply never rewritten, because the steps below need a pace and a ship at anchor
        # has none - so the last good route sat there for two days looking like a forecast.
        #
        # A stale file is worse than an empty one: the page cannot tell the difference
        # without being told. So when she is this close, the route is cleared and only the
        # weather ladder is kept - which is the part that still matters in port.
        # ...and no route onward while she is still alongside on this call.
        #
        # "Ruten som nå er satt til Dublin. De kan ikke skje før 10. sept." The route to
        # the next port is a forecast of a passage that has not begun. Drawn from a ship
        # tied up in Lerwick it is 542 miles of dashed line across Scotland's latitude,
        # and it zooms the whole map out to fit itself - which is how the camera section
        # and the harbour she is actually in disappeared off the top of the page.
        state = voyage_state(plan.get("ports") or [], track_points)
        here = state.get("in_port")
        holding = bool(here and here.get("depart") and today_iso() <= here["depart"])
        near_nm = nm_between((lat, lon), (float(port["lat"]), float(port["lon"])))
        if holding or near_nm <= AHEAD_CLEAR_NM:
            # No route - and no forecast ladder strung out along one either.
            #
            # Clearing only the route was half a job. The ladder points had been worked out
            # along the old route to Dublin, and keeping them left a trail of circles from
            # Shetland down past the Hebrides to Ireland: the route removed, its shadow
            # still drawn. "Kun den stiplede linjen skal være igjen frem til 10. sept."
            #
            # So while she is holding, the ladder describes standing still: every hour of
            # it at the place she is, out to the day she sails. That is the forecast a
            # parent actually wants from a ship in port - the week she is having in Lerwick,
            # not the weather over a passage that has not begun.
            stay = here or port
            span = AHEAD_MAX_HOURS
            if here and here.get("depart"):
                try:
                    left = (parse_iso(here["depart"] + "T23:59:59Z") - now_utc())
                    span = max(6, min(AHEAD_MAX_HOURS, left.total_seconds() / 3600))
                except Exception:
                    pass
            hrs = [h for h in AHEAD_LADDER if h <= span] or [AHEAD_LADDER[0]]
            still = [{"t": iso(now_utc() + timedelta(hours=h)), "hours": h,
                      "lat": round(lat, 4), "lon": round(lon, 4),
                      "where": "port" if holding else "sea"} for h in hrs]
            old = read_json(AHEAD, {}) or {}
            write_json(AHEAD, {
                "generated_utc": iso(now_utc()),
                "to": (stay or port)["name"],
                "country": (stay or port).get("country"),
                "holding_until": here.get("depart") if here else None,
                "near_nm": round(near_nm, 2),
                # fetch_ahead composes its own rows, so `where` is put back on afterwards:
                # the page labels each forecast tile with it ("in Lerwick" rather than a
                # bare hour), and without it the tiles lose the one word that says she is
                # not at sea.
                "points": [dict(q, where="port" if holding else "sea")
                           for q in fetch_ahead(still)],
            })
            print("* " + (f"alongside in {here['name']} until {here['depart']}"
                           if holding else
                           f"she is {near_nm:.1f} nm off {port['name']}")
                  + f" - no route, and the {len(still)}-point ladder stays where she is"
                  + (" (was strung out to " + str(old.get("to")) + ")"
                     if old.get("route") else ""))
            return
        # The route she is drawn along is the SAILING COURSE, not a line ruled to the port.
        # A square rigger cannot point within about 58 degrees of the wind, so the straight
        # line was never a route she could take, and having both on the map meant two
        # forward projections disagreeing with each other. build_course runs first and
        # writes the isochrone route; this walks along it. sea_route stays as the fallback
        # for when the router has no wind field or cannot find a way through.
        course = read_json(COURSE, {}) or {}
        cpts = course.get("points") or []
        route, route_basis = None, "direct"
        if (course.get("to") == port["name"] and len(cpts) >= 2
                and course.get("generated_utc")):
            try:
                age_h = (now_utc() - parse_iso(course["generated_utc"])).total_seconds() / 3600
            except Exception:
                age_h = 999
            if age_h < 6:
                route = [(float(q["lat"]), float(q["lon"])) for q in cpts]
                # It starts where she was when the router ran; pin it to where she is now.
                route[0] = (lat, lon)
                route_basis = "course"
        if route is None:
            # The fallback line needs the same pilotage the sailing course gets, and for the
            # same reason. A* snaps the goal to the nearest navigable cell - the sea mask
            # keeps six kilometres clear of land, so that cell is well offshore - and then
            # the hop from it to the quay is appended unchecked. Coming at Lerwick from the
            # north-east that hop goes straight over Bressay, and on 30 August it did:
            # ahead.json drew her position, the 60.10/-1.00 waypoint, then the quay, with
            # the island in between. This matters most exactly when it went wrong, because
            # inside fifteen miles build_course stops writing a course at all and this line
            # is the only one left on the map.
            #
            # So: aim the search at the first approach waypoint and walk the rest of the
            # way in along the waypoints somebody already chose, in the plan's own order.
            approach = []
            for w in reversed(port.get("waypoints") or []):
                if nm_between((float(w[0]), float(w[1])),
                              (float(port["lat"]), float(port["lon"]))) > APPROACH_NM:
                    break
                approach.append((float(w[0]), float(w[1])))
            approach.reverse()
            goal_pt = (float(port["lat"]), float(port["lon"]))
            head = sea_route((lat, lon), approach[0] if approach else goal_pt)

            # Check the SEARCHED part, and only that part. sea_route appends its goal to
            # the end of the A* path without checking the hop to it - the sea mask keeps
            # six kilometres clear of land, so the last navigable cell can be a long way
            # off and on the wrong side of an island. That hop is what drew a line over
            # Bressay.
            #
            # The approach waypoints below are NOT checked, deliberately. They are pilotage
            # somebody chose by hand for exactly the water this grid cannot represent:
            # Lerwick Sound is about a kilometre wide against a six-kilometre mask, and the
            # leg between the two approach marks passes through a cell the mask calls land
            # or sea depending on which side of a boundary a sample happens to land.
            # Checking them would throw away the only line that is actually right.
            if head and len(head) > 1 and sea_grid():
                import sailrouter as SR
                at_sea = lambda la, lo: (lambda c: bool(c) and _is_sea(*c))(_cell(la, lo))
                bad = next((k for k in range(1, len(head))
                            if not SR.leg_clear(head[k - 1], head[k], at_sea,
                                                free_from=(lat, lon))), None)
                if bad is not None:
                    print(f"  ! the route to the approach crosses land on leg {bad}"
                          f" - not drawing a line", file=sys.stderr)
                    head = None

            if approach:
                route = ((head[:-1] if len(head) > 1 else [(lat, lon)]) + approach + [goal_pt]
                         if head else None)
            else:
                route = head
        if not route or len(route) < 2:
            print("  ! could not route to the next port", file=sys.stderr)
            return
        legs = sum(nm_between(route[i - 1], route[i]) for i in range(1, len(route)))

        # How fast to assume she covers the route. Neither of the obvious answers works.
        # Her speed through the water is far too fast - she beats to windward and runs sail
        # drills, so at 7 knots she would "arrive" at Lerwick in a day and a half when the
        # plan says eleven. Spreading the distance across the days until she is due, which
        # is what this did before, is far too slow: 250 nm over 11 days is 0.9 knots, so
        # six hours ahead put her five miles on while she was in fact making forty.
        #
        # What the near term actually wants is the rate she is making good on the port,
        # measured from her own track. What the far term wants is the plan. pace_along
        # eases from the first to the second.
        # The ceiling is what this ship can do, not what she happens to be doing this
        # minute. Using her current speed over the ground as the cap threw away a measured
        # day of good progress whenever she was briefly slow.
        cruise = PACE_MAX_KN
        target = (port["lat"], port["lon"])

        # A day is the window, not six hours. Six was the shortest one that showed progress,
        # on the reasoning that the near-term marker is what a reader is asking about - but a
        # square rigger on a cross tack makes almost nothing good for hours at a time, so the
        # six-hour figure collapsed to nearly zero and the first waypoint stopped moving.
        # Twenty-four hours spans a whole tack cycle and holds still while she works to
        # windward. Longer windows only come in if a day of her track shows no net progress
        # at all; the short ones only if the leg is younger than that.
        vmg, vmg_window = None, None
        for window in (24, 48, 72, 12, 6):
            got = made_good_kn(track_points or [], target, window)
            if got is not None and got > 0.1:
                vmg, vmg_window = got, window
                break
        due_utc, sails_at = None, now_utc()

        # If she is alongside somewhere, the passage has not begun: the clock starts when
        # she sails, not now. Otherwise pacing a leg that starts in three weeks would
        # spread it over the weeks in port too and creep along at half a knot.
        # Alongside according to her track, not to the calendar - she can be a day late in
        # or a day late out, and the projection has to start when she actually sails.
        in_port = voyage_state(plan.get("ports") or [], track_points)["in_port"]
        if in_port:
            try:
                sails_at = max(now_utc(), parse_iso(in_port["depart"] + "T12:00:00Z"))
            except Exception:
                pass

        plan_h = None
        if port.get("arrive"):
            try:
                due = parse_iso(port["arrive"] + "T12:00:00Z")
                passage_h = (due - sails_at).total_seconds() / 3600
                if passage_h > 1:
                    plan_h, due_utc = passage_h, iso(due)
            except Exception:
                pass

        # The rate is held for exactly the span it was measured over, so "her last day"
        # really becomes "her next day".
        # Made good is progress towards the PORT. The route she is drawn along is longer
        # than that, because she tacks: closing 338 nm of gap can mean sailing 410 nm of
        # water. Pacing a 410 nm route at a 3.6 kn made-good rate would creep. Scale it by
        # how much longer the route is than the gap it closes, and the two agree again.
        direct_nm = nm_between((lat, lon), target)
        winding = max(1.0, min(3.0, legs / direct_nm)) if direct_nm > 1 else 1.0
        vmg_route = None if vmg is None else vmg * winding

        distance_at, basis, near_kn = pace_along(legs, vmg_route, plan_h, cruise * winding,
                                                hold_h=float(vmg_window or 24))
        wait_h = max(0.0, (sails_at - now_utc()).total_seconds() / 3600)

        # When she gets there, solved from the pace itself. This has to be searched well
        # past the forecast horizon: the Lerwick leg is eleven days and the models answer
        # for seven, and an eta capped at the forecast horizon would have claimed she
        # arrives four days early.
        horizon = int(max(AHEAD_MAX_HOURS, plan_h or 0) + 48)
        eta_h = horizon
        for h in range(1, horizon + 1):
            if distance_at(h) >= legs:
                eta_h = h
                break
        # The ladder runs as far as the weather models will answer for, and no longer stops
        # at her arrival: the hours after she gets there are spent at the quay, and the
        # forecast for those is the one a family reads on a Wednesday to know what kind of
        # week she is having. eta_h is still worked out - the card uses it - but it no
        # longer truncates the ladder.
        hours = [h for h in AHEAD_LADDER if h <= AHEAD_MAX_HOURS]
        if not hours:
            hours = [AHEAD_LADDER[0]]
        points = positions_ahead(route, distance_at, hours, wait_h)
        done = {q["hours"] for q in points}
        points += positions_after_arrival(port, plan.get("ports") or [],
                                          [h for h in hours if h not in done])
        points.sort(key=lambda q: q["hours"])
        ahead = fetch_ahead(points)
        write_json(AHEAD, {
            "generated_utc": iso(now_utc()),
            "to": port["name"], "country": port.get("country"),
            "distance_nm": round(legs, 1),
            "direct_nm": round(direct_nm, 1),
            "route_basis": route_basis,           # "course" = the sailing course, not a line
            "winding": round(winding, 2),         # route length / the gap it closes
            "tacks": course.get("tacks") if route_basis == "course" else None,
            "speed_kn": round(near_kn, 1),        # the rate the next few hours are drawn at
            "vmg_kn": None if vmg is None else round(vmg, 2),
            "vmg_window_h": vmg_window,           # measured over her last N hours
            "vmg_hold_h": None if vmg is None else float(vmg_window or 24),
            "pace_basis": basis,
            "due_utc": due_utc,
            "eta_utc": iso(now_utc() + timedelta(hours=wait_h + eta_h)),
            "route": [[round(a, 4), round(b, 4)] for a, b in route],
            "points": ahead,
        })
        vmg_note = (f"making good {vmg:.2f} kn on it over her last {vmg_window} h"
                    if vmg is not None else "no usable made-good rate in the track")
        print(f"  -> route to {port['name']} along the {route_basis}: {legs:.0f} nm "
              f"({direct_nm:.0f} nm direct, {winding:.2f}x), {vmg_note}; "
              f"pace = {basis}; 6 h ahead is {distance_at(6):.1f} nm along, "
              f"24 h is {distance_at(24):.1f} nm; {len(ahead)} forecast points")
    except Exception as exc:
        print(f"  ! weather ahead failed: {exc}", file=sys.stderr)


def build_polar() -> None:
    """Fold the last window of sailing into her measured polar.

    Cheap: it reads the wake the job has already fetched, skips every hour it has counted
    before, and writes a file of counts. No network, no extra API calls.
    """
    try:
        import polar as PL
    except ImportError as exc:
        print(f"  ! polar not importable: {exc}", file=sys.stderr)
        return
    try:
        hours = (read_json(WAKE, {}) or {}).get("hours") or []
        if not hours:
            return
        learned = PL.learn(hours, read_json(POLAR, None))
        learned["generated_utc"] = iso(now_utc())
        useful = PL.summary(learned)
        learned["bins_with_enough"] = len(useful)
        write_json(POLAR, learned)
        print(f"  -> polar: +{learned['added_last_run']} sailing hours "
              f"(skipped {learned['skipped_last_run']}), {learned['samples']} in total, "
              f"{len(useful)} bins now solid enough to steer by")
    except Exception as exc:
        print(f"  ! polar failed: {exc}", file=sys.stderr)


# --------------------------------------------------------- the course she would sail
# The dashed route to the next port assumes she closes on it steadily. She does not: she
# cannot point within about 58 degrees of the wind, so on a headwind leg she has to beat,
# and where the tacks fall depends on the forecast. This works that out.

# How near a port one of its waypoints has to be to count as part of the approach
# rather than part of the ocean crossing before it.
APPROACH_NM = float(os.environ.get("APPROACH_NM", "30"))
COURSE_MAX_HOURS = float(os.environ.get("COURSE_MAX_HOURS", "168"))
COURSE_STEP_H = float(os.environ.get("COURSE_STEP_H", "3"))


def build_course(lat: float, lon: float, points: list | None = None) -> None:
    try:
        import sailrouter as SR
    except ImportError as exc:
        print(f"  ! sailrouter not importable: {exc}", file=sys.stderr)
        return
    try:
        import polar as PL
        stored = read_json(POLAR, None)
        if stored:
            table = PL.blended_table(stored, SR.boat_speed)
            if table.learned_bins:
                SR.use_learned_polar(table)
                print(f"  -> course: steering by {table.learned_bins} measured wind angles, "
                      f"the rest from the built-in polar")
    except Exception as exc:
        print(f"  ! learned polar not used: {exc}", file=sys.stderr)
    try:
        plan = read_json(DATA / "ports.json", {})
        ports = plan.get("ports") or []
        port = next_port(ports, points)
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
        here = voyage_state(ports, points)["in_port"]
        if here and here.get("depart"):
            try:
                depart = max(depart, parse_iso(here["depart"] + "T12:00:00Z"))
            except Exception:
                pass

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

        # The port's own approach waypoints, for the run in. Only the trailing ones that
        # are genuinely near the port: the same list also carries the ocean waypoints for
        # the whole leg, which are hundreds of miles away and none of the router's business.
        approach = []
        for w in reversed(port.get("waypoints") or []):
            if nm_between((float(w[0]), float(w[1])), goal) > APPROACH_NM:
                break
            approach.append((float(w[0]), float(w[1])))
        approach.reverse()

        # Sail to the sea entrance, then walk the rest in. See _unwind in sailrouter.
        router_goal = approach[0] if approach else goal
        run_in = (approach[1:] + [goal]) if approach else None
        route = SR.isochrone_route(start, router_goal, depart, field, is_sea=is_sea,
                                  step_h=COURSE_STEP_H, max_hours=horizon,
                                  approach=run_in)
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
SILENCE_HOURS = float(os.environ.get("SILENCE_HOURS", "6"))


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


SHORE_DIR = ROOT / "images" / "shore"
SHORE = DATA / "shore.json"
SHORE_MAX_KB = 700          # most of these families read the page on mobile data
SHORE_MAX_PX = 1600         # wider than any phone, and plenty to see a ship in


def shrink_frame(path: Path) -> Path:
    """Bring an oversized frame down to something a phone can afford, in place.

    The snapshot button on the webcam player saves PNG, and a 1080p PNG is about two
    megabytes. A hundred and forty families, most of them on a phone, should not each
    download two megabytes to see one picture of a ship - and Christer should not have to
    re-export every frame by hand either, because that is the work the drop-in folder
    exists to remove.

    So: same picture, sensible size. PNG becomes JPEG, and the original is dropped once the
    smaller file is written, since keeping both would show the same frame twice. Quality
    steps down only as far as it must. If Pillow is not installed the file is left exactly
    as it is and the caller warns instead - a big picture is a nuisance, a broken one is a
    lie about the ship.
    """
    try:
        from PIL import Image
    except Exception:
        return path
    if path.stat().st_size // 1024 <= SHORE_MAX_KB:
        return path
    try:
        with Image.open(path) as raw:
            im = raw.convert("RGB")
    except Exception as exc:
        print(f"  ! could not read {path.name} to shrink it: {exc}")
        return path
    out = path.with_suffix(".jpg")
    if out.exists() and out != path:
        out = path.with_name(path.stem + "-web.jpg")
    was = path.stat().st_size // 1024
    for width, quality in ((SHORE_MAX_PX, 85), (SHORE_MAX_PX, 78), (1400, 75), (1200, 70)):
        pic = im
        if pic.width > width:
            pic = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
        pic.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
        if out.stat().st_size // 1024 <= SHORE_MAX_KB:
            break
    now = out.stat().st_size // 1024
    if out != path:
        if now < was:
            path.unlink()
        else:
            out.unlink()                      # shrinking made it worse; keep the original
            return path
    print(f"* shrank {path.name} from {was} kB to {now} kB ({out.name})")
    return out


# Accents are folded rather than treated as punctuation, and the result is plain ASCII.
#
# Two reasons, and both were bugs. Matching: Shetland Webcams call the camera "Fjara
# SeaLevel Cam" with a ring over the a, and a filename anybody types says "fjara" - without
# folding, the ring acted as a separator, the camera slugged to "fjar", and a correctly
# named frame was published with no link to the camera that took it. Naming: the job builds
# filenames from the same names, and it wrote "2026-08-31-2031-fjara.jpg" with the ring
# still in it - a non-ASCII path in an <img src>, which is a percent-encoding argument
# waiting to happen on somebody's phone. Norwegian names on a Norwegian voyage: this is the
# common case here, not the exotic one.
FOLD = {"\u00e6": "ae", "\u00f8": "o", "\u00e5": "a", "\u00fc": "u", "\u00df": "ss",
        "\u0153": "oe", "\u00f0": "d", "\u00fe": "th", "\u0142": "l"}


def ascii_slug(text: str) -> str:
    flat = unicodedata.normalize("NFKD", (text or "").lower())
    keep = []
    for ch in flat:
        if unicodedata.combining(ch):
            continue                           # the ring, the acute, the umlaut: dropped
        ch = FOLD.get(ch, ch)
        keep.append(ch if ch.isalnum() and ch.isascii() else "-")
    return "-".join(p for p in "".join(keep).split("-") if p)


PENDING_DIR = SHORE_DIR / "pending"
PENDING = DATA / "pending.json"
CAPTURE_NM = 9.0            # the same "inside the approaches" figure the page uses
CAPTURE_EVERY_S = 300       # a frame from each camera every five minutes
CAPTURE_MAX_ROUND = 24      # ...and never more than this many in one round of the job
PENDING_MAX = 240           # if nobody reviews them, stop growing rather than fill the repo


def capture_port(position: dict, plan: dict) -> dict | None:
    """The port she is closing on and can be photographed at, or None.

    Andy gave permission for still frames on condition of a link and a mention, and Christer
    has the rewind subscription - but neither of them can be watching at 06:40 on a Sunday,
    which is the sort of hour a ship actually arrives. So the job watches instead.

    The rule is deliberately narrow: she has to be CLOSING on the port at better than a
    knot, and be inside the approaches. That is "under innseiling" and nothing else. It
    matters that it cannot mean anything else: she lay at anchor off Lerwick from 31 August
    to 6 September, a mile off a camera, and a rule based on distance alone would have taken
    a picture every five minutes for six days - some thousand frames of a ship not moving,
    on somebody else's bandwidth, for a folder nobody could then review.
    """
    lat, lon = position.get("lat"), position.get("lon")
    if lat is None or lon is None:
        return None
    # A fix old enough that we no longer know where she is cannot start a capture.
    try:
        age_min = (now_utc() - parse_iso(position["seen_utc"])).total_seconds() / 60
    except Exception:
        age_min = 0.0
    if age_min > 180:
        return None
    sog = position.get("sog_kn")
    cog = position.get("cog_deg")
    if sog is None or cog is None:
        return None
    for port in (plan.get("ports") or []):
        cams = [c for c in (port.get("cameras") or []) if c.get("hls")]
        if not cams:
            continue
        off = nm_between((lat, lon), (port["lat"], port["lon"]))
        if off > CAPTURE_NM:
            continue
        # Closing rate: her speed projected onto the bearing to the port.
        brg = bearing((lat, lon), (port["lat"], port["lon"]))
        closing = sog * math.cos(math.radians((brg - cog + 540) % 360 - 180))
        if closing < 1.0:
            continue
        print(f"* she is {off:.1f} nm from {port['name']}, closing at {closing:.1f} kn "
              f"- capturing from {len(cams)} camera(s)")
        return port
    return None


def grab_frame(url: str, dest: Path) -> bool:
    """One frame off a live HLS stream, via ffmpeg. Isolated so a test can replace it."""
    if not shutil.which("ffmpeg"):
        print("  ! no ffmpeg on this runner - cannot capture frames")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        out = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-nostdin",
             "-user_agent", CONTACT_UA,
             "-i", url, "-frames:v", "1", "-q:v", "3", str(dest)],
            capture_output=True, text=True, timeout=40)
    except Exception as exc:
        print(f"  ! {dest.name}: {exc}")
        return False
    if out.returncode != 0 or not dest.exists() or dest.stat().st_size < 8000:
        if dest.exists():
            dest.unlink()
        print(f"  ! {dest.name}: ffmpeg gave nothing back ({(out.stderr or '').strip()[:120]})")
        return False
    return True


def capture_frames(seconds: int) -> None:
    """Grab a frame from every streamable camera, every few minutes, while she comes in.

    Runs in a thread beside the AIS listeners, so it costs the job no extra time. Frames
    land in images/shore/pending, which the album does NOT read: Christer looks at them
    first. Nothing reaches the page - a hundred and forty families - until a person has
    seen it. Publishing is a rename, and the file is already named for it.
    """
    plan = read_json(DATA / "ports.json", {}) or {}
    port = capture_port(read_json(LATEST, {}).get("position") or {}, plan)
    if not port:
        return
    cams = [c for c in (port.get("cameras") or []) if c.get("hls")]
    tz = port.get("tz")
    taken, deadline = 0, now_utc() + timedelta(seconds=max(0, seconds - 30))
    while now_utc() < deadline and taken < CAPTURE_MAX_ROUND:
        held = len([p for p in PENDING_DIR.glob("*.jpg")]) if PENDING_DIR.is_dir() else 0
        if held >= PENDING_MAX:
            print(f"  ! {held} frames already waiting to be looked at - stopping there")
            return
        stamp = now_utc()
        if tz:
            # The album's convention is the clock on the shore, because that is the clock
            # printed in the corner of the frame - so the file is named in it, and a frame
            # published later needs no editing at all.
            try:
                from zoneinfo import ZoneInfo
                stamp = stamp.astimezone(ZoneInfo(tz))
            except Exception:
                pass
        for cam in cams:
            slug_name = ascii_slug(cam.get("short") or cam["name"])
            dest = PENDING_DIR / f"{stamp.strftime('%Y-%m-%d-%H%M')}-{slug_name}.jpg"
            if dest.exists():
                continue
            if grab_frame(cam["hls"], dest):
                taken += 1
                print(f"  captured {dest.name} ({dest.stat().st_size // 1024} kB)")
    if taken:
        print(f"* captured {taken} frame(s) into {PENDING_DIR.relative_to(ROOT)}")


LATEST_DIR = SHORE_DIR / "latest"


def capture_latest() -> None:
    """One frame from every camera at the port she is at, overwriting the last set.

    Not an album and not a candidate for one: three files that always hold the newest look
    at each camera, so the question "which of these can actually see her right now" can be
    answered by looking at three pictures instead of opening three video players. Fog moves,
    Andy swings the cameras about, and which view is worth being the default changes through
    the day - on 31 August the long view across the sound went to fog while the sea-level
    camera still had her.

    Overwritten every round on purpose. She lies at anchor for days; a growing folder would
    be hundreds of frames of the same ship not moving. Three files, always current.
    """
    plan = read_json(DATA / "ports.json", {}) or {}
    pos = read_json(LATEST, {}).get("position") or {}
    lat, lon = pos.get("lat"), pos.get("lon")
    if lat is None or lon is None:
        return
    for port in (plan.get("ports") or []):
        cams = [c for c in (port.get("cameras") or []) if c.get("hls")]
        if not cams or nm_between((lat, lon), (port["lat"], port["lon"])) > CAPTURE_NM:
            continue
        for cam in cams:
            dest = LATEST_DIR / f"{ascii_slug(cam.get('short') or cam['name'])}.jpg"
            tmp = dest.with_suffix(".tmp.jpg")
            if grab_frame(cam["hls"], tmp):
                tmp.replace(dest)
            elif tmp.exists():
                tmp.unlink()
        print(f"* refreshed the standing look at {len(cams)} camera(s) in {port['name']}")
        return


def build_pending() -> None:
    """Index the frames waiting to be looked at, so ?review can show them on a phone."""
    frames = []
    if PENDING_DIR.is_dir():
        for path in sorted(PENDING_DIR.iterdir()):
            if path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue
            frames.append({"file": f"images/shore/pending/{path.name}",
                           "kb": path.stat().st_size // 1024})
    frames.sort(key=lambda f: f["file"], reverse=True)
    live = []
    if LATEST_DIR.is_dir():
        for path in sorted(LATEST_DIR.iterdir()):
            if path.suffix.lower() != ".jpg":
                continue
            live.append({"file": f"images/shore/latest/{path.name}",
                         "camera": path.stem,
                         "kb": path.stat().st_size // 1024,
                         "t": iso(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))})
    payload = {
        "live": live,
        "note": ("Frames the job took by itself while she was coming in. NOTHING here is on "
                 "the page. To publish one, rename it in GitHub from "
                 "images/shore/pending/NAME to images/shore/NAME - it is already named for "
                 "the album. Delete the rest."),
        "frames": frames,
    }
    if read_json(PENDING, None) != payload:
        write_json(PENDING, payload)
        print(f"* wrote {PENDING.name} ({len(frames)} awaiting review)")


def git_added(path: Path) -> str | None:
    """When this file first appeared in the repository, as an ISO instant in UTC.

    Used for pictures whose filename says nothing - the snapshot button on a webcam player
    calls everything "Snapshot (1).png". The commit date is not when the shutter went, but
    it is within an hour or two of it and it is a fact rather than a guess. Git is the only
    place to ask: a checkout does not preserve modification times, so the mtime in a CI
    runner is the moment the runner cloned the repo, which is the same for every file.
    """
    try:
        out = subprocess.run(
            ["git", "log", "--diff-filter=A", "--format=%cI", "-1", "--", str(path)],
            cwd=str(ROOT), capture_output=True, text=True, timeout=20)
        line = (out.stdout or "").strip().splitlines()
        if line:
            when = datetime.fromisoformat(line[0])
            return iso(when.astimezone(timezone.utc))
    except Exception as exc:
        print(f"  ! could not read the commit date for {path.name}: {exc}")
    return None


def images_are_committed() -> bool:
    """Does the workflow actually push what this job writes into images/?

    A real hazard, not a theoretical one. Shrinking a 2 MB PNG means writing a new .jpg and
    deleting the PNG, and shore.json then points at the .jpg. If the commit step still
    stages only data/, the album gets published pointing at ten files that were never
    pushed - ten broken pictures on a page a hundred and forty families read, and the
    originals still sitting there at 2 MB each.

    So the job reads its own workflow and checks. If images/ is not staged it leaves every
    file exactly as it found it and says so, loudly, in the log. A big picture is a
    nuisance; a broken one is the page telling people something that is not true.
    """
    wf = ROOT / ".github" / "workflows" / "update.yml"
    try:
        text = wf.read_text(encoding="utf-8")
    except OSError:
        return True                    # not our workflow to reason about; assume it is fine
    return bool(re.search(r"git add[^\n]*images/", text))


def port_for_date(plan: dict, stamp: str) -> str | None:
    """Which port she was at on the day this frame was taken.

    For a frame whose filename names no camera - "Snapshot (1).png", which is what the
    rewind player calls everything - this is the only way to file it. It asks the plan, and
    it has to ask about both kinds of stay: the berth (arrive to depart) and the anchorage
    (the lying_off window). Her Lerwick frames from 31 August fall in neither the North Sea
    crossing nor the 6-10 September berth; they fall in the week she spent swinging at
    anchor off the town, and only lying_off knows about that.
    """
    day = (stamp or "")[:10]
    if len(day) != 10:
        return None
    for port in (plan.get("ports") or []):
        if not (port.get("cameras") or []):
            continue
        windows = []
        if port.get("arrive") and port.get("depart"):
            windows.append((port["arrive"], port["depart"]))
        lo = port.get("lying_off") or {}
        if lo.get("from") and lo.get("until"):
            windows.append((lo["from"][:10], lo["until"]))
        for a, b in windows:
            if a <= day <= b:
                return port["name"]
    return None


def build_shore() -> None:
    """Turn whatever is in images/shore into data/shore.json.

    The point of this is that Christer can drop a frame into the folder and be done. He
    pays Shetland Webcams for the rewind, which means he can go back and catch her at the
    right moment - and the moments worth catching happen while he is watching a camera, not
    while he is editing a JSON file. A webcam holds a day at most; a picture nobody saves in
    the next few hours is simply gone.

    GitHub Pages will not list a directory, so the page cannot discover the files by itself
    and something has to write the index. That something is this job, which already runs
    every half hour.

    The filename carries the facts:

        2026-08-31-1626-kirkabister.jpg
        ^^^^^^^^^^ ^^^^ ^^^^^^^^^^^
        date       local time (the clock in the corner of the frame), camera

    The camera part is matched against the cameras in ports.json, which is where the link
    and the owner's name already live - so the credit cannot drift out of step with the
    plan, and a frame from a camera we have not listed still gets published, just without a
    link. Captions already in shore.json are kept, so a caption can be added by hand later
    and will survive every later round.
    """
    if not SHORE_DIR.is_dir():
        return
    plan = read_json(DATA / "ports.json", {}) or {}
    cams = []
    for port in (plan.get("ports") or []):
        for cam in (port.get("cameras") or []):
            cams.append((port, cam))

    slug = ascii_slug

    old = {f.get("file"): f for f in ((read_json(SHORE, {}) or {}).get("frames") or [])}
    frames, warned = [], []
    commit_images = images_are_committed()
    # A folder per port, because a voyage with nineteen of them in one flat directory is not
    # a picture library, it is a heap. images/shore/Lerwick, images/shore/Dublin, and so on.
    # Loose files at the top level still work - that is where the first one lived - and are
    # filed under whichever port the camera in their name belongs to.
    # Read the whole tree BEFORE moving anything, and return a list rather than a
    # generator. The loop below files loose frames into their port's folder as it goes, and
    # a generator reading the directory while that happens is the oldest bug in the book:
    # "Lerwick" sorts before "snapshot (1).png", so a file moved into the Lerwick folder
    # after the walk had already passed that folder was never seen, and the album came out
    # one frame short - for one round, which is the round somebody looks.
    def walk():
        out = []
        for entry in sorted(SHORE_DIR.iterdir()):
            if entry.is_dir():
                if entry.name in ("pending", "latest"):
                    continue           # the job's own workspace, never the album
                out.extend((f, entry.name) for f in sorted(entry.iterdir()))
            else:
                out.append((entry, None))
        return out
    if not commit_images:
        warned.append("the workflow does not stage images/, so nothing in there is being "
                      "shrunk or renamed - update .github/workflows/update.yml")
    for path, folder in walk():
        if path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            continue
        # Keep the name it arrived under. The shrunk file is brand new, so git has never
        # heard of it, and asking git when it appeared would come back empty - leaving a
            # frame with no date for one round, which is exactly the round somebody looks.
        dropped_as = path
        if commit_images:
            path = shrink_frame(path)
        rel = ("images/shore/" + (f"{folder}/" if folder else "") + path.name)
        kb = path.stat().st_size // 1024
        if kb > SHORE_MAX_KB:
            warned.append(f"{path.name} is {kb} kB and could not be shrunk here - "
                          "worth re-exporting it smaller")
        parts = path.stem.split("-")
        stamp, tail, added = None, path.stem, False
        # YYYY-MM-DD-HHMM-name, and YYYY-MM-DD-name for a frame with no time in it
        if len(parts) >= 4 and len(parts[0]) == 4 and parts[0].isdigit():
            date = "-".join(parts[:3])
            if len(parts[3]) == 4 and parts[3].isdigit():
                stamp, tail = f"{date}T{parts[3][:2]}:{parts[3][2:]}:00", "-".join(parts[4:])
            else:
                stamp, tail = f"{date}T12:00:00", "-".join(parts[3:])
        # The rewind player names its snapshots "Snapshot (1).png", and renaming every one
        # by hand defeats the whole point of a drop-in folder. So an unparseable name is not
        # an error: the time it was committed is close enough to when it was taken, and it
        # is marked as an "added" date so the page never presents it as a capture time. The
        # picture itself carries the real one - these frames have the camera name and the
        # clock burned into the corner, which is better evidence than anything we could put
        # in a caption.
        named = stamp is not None
        if stamp is None:
            when = git_added(path) or (git_added(dropped_as) if dropped_as != path else None)
            # Last resort. In a CI runner every mtime is the moment the repo was cloned, so
            # this is nearly worthless for a file that came with the checkout - but it is
            # right for one this job has just written, and a dated picture in roughly the
            # right place beats an undated one that sorts to the bottom of the album.
            if not when:
                try:
                    when = iso(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))
                except Exception:
                    when = None
            if when:
                stamp, added, tail = when, True, ""
        want = slug(tail)
        found = None
        if want:
            for port, cam in cams:
                names = {slug(cam.get("name")), slug(cam.get("short"))}
                if want in names or any(n and (want in n or n in want) for n in names):
                    found = (port, cam)
                    break
        # Look the old entry up under both names: a file the job has just filed into a
        # port folder had a different path five minutes ago, and a caption written by hand
        # must not be lost to the tidying.
        keep = old.get(rel) or old.get(f"images/shore/{path.name}") or {}
        entry = {"file": rel}
        # Which port this frame belongs to, settled before the clock is chosen: a frame
        # filed under Lerwick is on Shetland's clock even when its own name says nothing.
        where = (folder or (found[0]["name"] if found else None)
                 or port_for_date(plan, stamp or "") or keep.get("port"))
        if stamp:
            entry["t"] = stamp
            if added:
                # A git timestamp is a real instant, in UTC, and it is when the file
                # arrived rather than when the shutter went.
                entry["added"] = True
            else:
                # The time in the filename is the clock in the corner of the frame, which
                # is the shore's own time, so it is stored with the zone it was read in
                # rather than pretended to be UTC. The folder name settles it when the
                # filename names no camera: a frame in images/shore/Lerwick is on
                # Shetland's clock whatever it is called.
                by_folder = next((q.get("tz") for q in (plan.get("ports") or [])
                                  if where and q["name"].lower() == where.lower()), None)
                entry["tz"] = ((found[0].get("tz") if found else None) or by_folder
                               or keep.get("tz") or "UTC")
                entry["local"] = True
        # A loose frame at the top level gets filed into its port's folder, by the job,
        # once. Ten snapshots uploaded in one go would otherwise mean ten renames by hand
        # in the GitHub web editor - and this job is the only thing here with write access
        # to the repository, so it is the only thing that can tidy up.
        if commit_images and folder is None and where:
            home = SHORE_DIR / where
            home.mkdir(parents=True, exist_ok=True)
            target = home / path.name
            if not target.exists():
                try:
                    path.rename(target)
                    print(f"* filed {path.name} under {where}/")
                    path, folder = target, where
                    rel = f"images/shore/{where}/{path.name}"
                except OSError as exc:
                    print(f"  ! could not file {path.name}: {exc}")
        entry["file"] = rel
        entry["port"] = where
        if not entry["port"]:
            entry.pop("port")
        if found:
            port, cam = found
            entry["camera"] = cam.get("name")
            entry["url"] = cam.get("url")
            entry["by"] = cam.get("by")
        else:
            # Only worth saying when the name was TRYING to name a camera. "Snapshot (1)"
            # was never meant to, and warning about it every half hour for the rest of the
            # voyage would train whoever reads the log to ignore it.
            if want and named:
                warned.append(f"{path.name}: no camera in the plan matches {want!r} - "
                              "published without a link")
            for k in ("camera", "url", "by"):
                if keep.get(k):
                    entry[k] = keep[k]
        if keep.get("caption"):
            entry["caption"] = keep["caption"]
        frames.append(entry)

    frames.sort(key=lambda f: f.get("t") or "", reverse=True)
    payload = {
        "note": ("Built from the files in images/shore by scripts/update.py. One folder per "
                 "port - images/shore/Lerwick, images/shore/Dublin - and any name will do; "
                 "YYYY-MM-DD-HHMM-camera.jpg also gives the time and the camera. Every "
                 "frame is somebody else's picture, kept with their permission."),
        "frames": frames,
    }
    before = read_json(SHORE, None)
    if before == payload:
        print(f"  shore album unchanged ({len(frames)} frames)")
    else:
        write_json(SHORE, payload)
        print(f"* wrote {SHORE.name} ({len(frames)} frames)")
    for w in warned:
        print(f"  ! {w}")


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

        # ...and, if she is on the run in, a third thread taking pictures of her.
        cap_thread = threading.Thread(target=capture_frames, args=(LISTEN_SECONDS,),
                                      daemon=True)
        cap_thread.start()

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
            # "d" means this speed was worked out from two positions rather than reported by
            # the ship. Without it the page cannot tell the two apart, and a computed number
            # ends up presented as a measurement - which is how the log came to claim a
            # speed record the ship never transmitted. One letter, because every track point
            # carries it.
            if fix.get("sog_derived") and sog_v is not None:
                entry["d"] = 1
            # Navigational status, but only the two values that answer "was she sailing":
            # 8 under sail, 0 under engine. It is what separates a polar learned from real
            # sailing from one polluted by sail drills and motoring, and the ship's own feed
            # does not carry it, so it is worth keeping where we do hear it.
            ns = fix.get("nav_status")
            if ns in (0, 8):
                entry["ns"] = ns
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

    # ---- what the crew typed in: destination, ETA, draught
    #
    # This is AIS message 5, the voyage record a mate fills in on the bridge - and it is
    # exactly what MarineTraffic shows as "ETA". It arrives only when a receiver that
    # carries message 5 hears her, which out here is seldom, and it was being thrown away
    # in between: the position record it rides on is replaced by the next fix from any
    # other source, and the voyage data died with it.
    #
    # It does not change like a position does. She keeps the same destination for a week.
    # So it is kept beside the position rather than inside it, carried forward from the
    # last time anybody heard it, with the hour it was heard so the page can say how old
    # the claim is.
    voyage = dict((previous.get("voyage") or {}) if previous else {})
    fresh = {k: position.get(k) for k in ("destination", "eta_text", "draught_m")
             if position.get(k)}
    if fresh:
        voyage.update(fresh)
        voyage["heard_utc"] = position.get("seen_utc") or iso(now_utc())

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

    sun = fetch_sun_moon(lat, lon, points)

    for kind, path in (("wind", WIND), ("waves", WAVES)):
        keep = _grid_still_good(path, lat, lon)
        if keep:
            print(f"  -> {kind} grid {keep}")
            continue
        grid = fetch_grid(lat, lon, kind)
        if grid:
            grid["centre"] = [round(lat, 3), round(lon, 3)]
            write_json(path, grid)
        else:
            print(f"  -> no {kind} grid this run, keeping the previous one")

    # Every point written from now on says whether its speed was reported by the ship or
    # worked out here. Points written before that distinction existed cannot be classified
    # after the fact - the source they came from was never stored - so the page is told the
    # moment from which the answer is trustworthy, and refuses to call anything earlier a
    # record. No record for a few days is better than the wrong one for a year.
    old = read_json(TRACK, {}) or {}
    # Stamped once, on the first run that knows about the distinction, and never moved
    # afterwards. The obvious-looking refinement - "if some points already carry the flag,
    # date it from the start of the track" - is wrong, and wrong in the worst direction:
    # on that very first run the newly merged fixes DO carry the flag while every older
    # point does not, so it dated the stamp to the beginning of the voyage and quietly let
    # the unknown numbers back in as records.
    sog_from = old.get("sog_provenance_from") or iso(now_utc())
    write_json(TRACK, {"mmsi": MMSI, "ship": SHIP_NAME,
                       "sog_provenance_from": sog_from, "points": points}, compact=True)
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
            "voyage": voyage or None,
            "weather": weather,
            "sun": sun or {},
            "stats": {
                "points": len(points),
                "distance_nm": round(distance_nm, 1),
                "first_point_utc": points[0]["t"] if points else None,
            },
        },
    )
    # Order matters here, and each step feeds the next:
    #   wake   - where she was hour by hour, and the weather that was there
    #   polar  - folds those hours into what she can actually do at each wind angle
    #   course - the isochrone route, steering by that polar
    #   ahead  - walks along the course at her measured rate of closing on the port
    build_wake(points)
    build_polar()
    build_course(lat, lon, points)
    build_ahead(lat, lon, position.get("sog_kn"), points)
    build_orbit(points)
    build_shore()
    capture_latest()
    build_pending()
    update_history(weather, points, collected)
    print(f"* wrote {LATEST.name} and {TRACK.name} ({len(points)} points, {distance_nm:.0f} nm)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
