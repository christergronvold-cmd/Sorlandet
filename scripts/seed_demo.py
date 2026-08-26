#!/usr/bin/env python3
"""
Writes sample data (data/latest.json + data/track.json) without network access, so you
can open the page and see how it looks before the AIS key is in place.

    python3 scripts/seed_demo.py

Then run scripts/update.py with AISSTREAM_API_KEY set for real data - these files are
overwritten by real positions.
"""

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
DATA.mkdir(parents=True, exist_ok=True)
iso = lambda dt: dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
now = datetime.now(timezone.utc)

# A made-up track following the current leg of the voyage plan:
# Kristiansand (departed 17 August 2026) towards Lerwick (scheduled 7 September).
A, B = (58.145, 8.000), (60.155, -1.145)
points = []
for i in range(80):
    f = i / 110                       # roughly 70 % of the way
    t = now - timedelta(hours=(80 - i) * 2.4)
    lat = A[0] + (B[0] - A[0]) * f + 0.18 * math.sin(i / 7)
    lon = A[1] + (B[1] - A[1]) * f + 0.30 * math.cos(i / 6)
    if 30 < i < 37:                   # simulated gap in AIS coverage
        continue
    points.append({
        "t": iso(t),
        "lat": round(lat, 5),
        "lon": round(lon, 5),
        "sog": round(5.1 + 1.9 * math.sin(i / 4), 1),
        "cog": round(318 + 14 * math.sin(i / 9), 1),
    })

last = points[-1]
hourly = [
    {"time_utc": iso(now + timedelta(hours=h)).replace("Z", ""), "wave_height_m": round(1.9 + 0.7 * math.sin(h / 9), 1)}
    for h in range(0, 36, 6)
]
daily = [
    {
        "date": (now + timedelta(days=d)).strftime("%Y-%m-%d"),
        "weather_code": [2, 3, 61, 1][d],
        "temp_max_c": [15.4, 14.8, 14.1, 15.2][d],
        "temp_min_c": [11.6, 11.2, 10.8, 11.4][d],
        "wind_max_ms": [9.4, 12.1, 14.6, 8.2][d],
    }
    for d in range(4)
]

(DATA / "track.json").write_text(json.dumps(
    {"mmsi": "257165000", "ship": "Sørlandet", "points": points}, ensure_ascii=False, indent=1) + "\n",
    encoding="utf-8")

(DATA / "latest.json").write_text(json.dumps({
    "ship": "Sørlandet",
    "mmsi": "257165000",
    "imo": "5334561",
    "updated_utc": iso(now),
    "fresh_fix": True,
    "demo": True,
    "position": {
        "lat": last["lat"], "lon": last["lon"],
        "sog_kn": 5.8, "cog_deg": 322.0, "heading_deg": 319,
        "nav_status": 8, "seen_utc": last["t"], "source": "demo",
        "destination": "LERWICK",
    },
    "weather": {
        "fetched_utc": iso(now),
        "sea": {
            "wave_height_m": 2.1, "wave_direction_deg": 292, "wave_period_s": 8.4,
            "swell_height_m": 1.8, "swell_period_s": 11.0, "wind_wave_height_m": 0.9,
            "sea_temp_c": 14.9, "time_utc": iso(now).replace("Z", ""),
        },
        "air": {
            "temp_c": 14.2, "feels_c": 12.8, "wind_ms": 9.7, "gust_ms": 14.2,
            "wind_direction_deg": 231, "pressure_hpa": 1018.4, "cloud_pct": 62,
            "precip_mm": 0.0, "weather_code": 2, "time_utc": iso(now).replace("Z", ""),
        },
        "sea_forecast": hourly,
        "air_forecast": daily,
    },
    "stats": {
        "points": len(points),
        "distance_nm": 412.7,
        "first_point_utc": points[0]["t"],
    },
}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

print(f"Wrote sample data: {len(points)} track points.")
