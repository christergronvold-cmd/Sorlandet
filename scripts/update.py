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
LISTEN_SECONDS = int(os.environ.get("LISTEN_SECONDS", "110"))

# A new track point is stored only if the ship has moved at least this many nautical
# miles, or at least this many minutes have passed since the previous point.
MIN_MOVE_NM = float(os.environ.get("MIN_MOVE_NM", "0.5"))
MIN_GAP_MIN = float(os.environ.get("MIN_GAP_MIN", "30"))

USER_AGENT = os.environ.get(
    "CONTACT_UA", "sorlandet-tracker/1.0 (family project; contact: change-me@example.com)"
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LATEST = DATA / "latest.json"
TRACK = DATA / "track.json"

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


def fetch_position_from_aisstream(api_key: str) -> dict | None:
    """Listens on aisstream.io until we see a position report for our MMSI."""
    try:
        from websockets.sync.client import connect
    except ImportError:
        print("! missing the 'websockets' package (pip install websockets)", file=sys.stderr)
        return None

    subscribe = {
        "APIKey": api_key,
        # aisstream requires a bounding box - we ask for the whole globe and let the
        # MMSI filter do the work.
        "BoundingBoxes": [[[-90, -180], [90, 180]]],
        "FiltersShipMMSI": [MMSI],
        "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
    }

    position: dict | None = None
    static: dict = {}
    deadline = now_utc() + timedelta(seconds=LISTEN_SECONDS)

    print(f"* listening on aisstream for up to {LISTEN_SECONDS}s for MMSI {MMSI} ...")
    try:
        with connect("wss://stream.aisstream.io/v0/stream", open_timeout=20) as ws:
            ws.send(json.dumps(subscribe))
            while now_utc() < deadline:
                remaining = (deadline - now_utc()).total_seconds()
                if remaining <= 0:
                    break
                try:
                    raw = ws.recv(timeout=remaining)
                except TimeoutError:
                    break
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue

                if "error" in msg:
                    print(f"! aisstream returned an error: {msg['error']}", file=sys.stderr)
                    return None

                meta = msg.get("MetaData") or {}
                if str(meta.get("MMSI", "")) != MMSI:
                    continue

                kind = msg.get("MessageType")
                body = (msg.get("Message") or {}).get(kind) or {}

                if kind == "ShipStaticData":
                    static["destination"] = (body.get("Destination") or "").strip() or None

                elif kind == "PositionReport":
                    lat = body.get("Latitude", meta.get("latitude"))
                    lon = body.get("Longitude", meta.get("longitude"))
                    if lat is None or lon is None:
                        continue
                    try:
                        seen = iso(parse_iso(str(meta.get("time_utc") or "")))
                    except Exception:
                        seen = iso(now_utc())
                    position = {
                        "lat": round(float(lat), 5),
                        "lon": round(float(lon), 5),
                        "sog_kn": body.get("Sog"),
                        "cog_deg": body.get("Cog"),
                        "heading_deg": None if body.get("TrueHeading") in (511, None) else body.get("TrueHeading"),
                        "nav_status": body.get("NavigationalStatus"),
                        "seen_utc": seen,
                        "source": "aisstream.io",
                    }
                    print(f"  -> position {position['lat']}, {position['lon']} at {seen}")
                    break
    except Exception as exc:  # network, TLS, dropped connection ...
        print(f"! error talking to aisstream: {exc}", file=sys.stderr)
        return None

    if position is None:
        print("  -> no AIS message in this window (normal outside coastal coverage)")
        return None

    position.update({k: v for k, v in static.items() if v})
    return position


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


# ----------------------------------------------------------------------- main


def main() -> int:
    api_key = os.environ.get("AISSTREAM_API_KEY", "").strip()
    demo = not api_key

    track = read_json(TRACK, {"mmsi": MMSI, "ship": SHIP_NAME, "points": []})
    points = track.get("points") or []
    previous = read_json(LATEST, {})

    if demo:
        print("* no AISSTREAM_API_KEY - running in sample mode")
        position = demo_position()
    else:
        position = fetch_position_from_aisstream(api_key)

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

    if fresh_fix:
        keep = True
        if points:
            last = points[-1]
            moved = nm_between((last["lat"], last["lon"]), (lat, lon))
            try:
                gap = (parse_iso(position["seen_utc"]) - parse_iso(last["t"])).total_seconds() / 60
            except Exception:
                gap = MIN_GAP_MIN + 1
            keep = moved >= MIN_MOVE_NM or gap >= MIN_GAP_MIN
            if gap <= 0:
                keep = False
        if keep:
            points.append(
                {
                    "t": position["seen_utc"],
                    "lat": lat,
                    "lon": lon,
                    "sog": position.get("sog_kn"),
                    "cog": position.get("cog_deg"),
                }
            )
            print(f"  -> new track point (#{len(points)})")
        else:
            print("  -> the ship is nearly stationary, skipping track point")

    distance_nm = sum(
        nm_between((points[i - 1]["lat"], points[i - 1]["lon"]), (points[i]["lat"], points[i]["lon"]))
        for i in range(1, len(points))
    )

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
            "stats": {
                "points": len(points),
                "distance_nm": round(distance_nm, 1),
                "first_point_utc": points[0]["t"] if points else None,
            },
        },
    )
    print(f"* wrote {LATEST.name} and {TRACK.name} ({len(points)} points, {distance_nm:.0f} nm)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
