"""Under engine, the page must not draw a sailing course.

The router draws the boards a square rigger would steer against the forecast wind, with
the tacks marked. Leaving Dublin on 20 September 2026 she transmitted nav status 0 - under
way using engine - on every one of 124 messages, made five and a half knots in three knots
of wind, and the map drew her nine tacks she was never going to make.

So: read the status she transmits, and when it says engine, prefer the direct route and
say so. When she says sail, or says nothing, nothing changes.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("AISSTREAM_API_KEY", "fake")
import update as U                                                   # noqa: E402

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
U.now_utc = lambda: NOW
fails = []


def check(label, got, want):
    if got != want:
        fails.append(f"{label}: got {got!r}, wanted {want!r}")


def pt(minutes_ago, ns=None):
    """A track point that many minutes before NOW, with or without a nav status."""
    p = {"t": U.iso(NOW - timedelta(minutes=minutes_ago)), "lat": 53.0, "lon": -6.0}
    if ns is not None:
        p["ns"] = ns
    return p


# --- what she is transmitting, read straight ------------------------------------------
check("engine on a fresh 0", U.under_engine([pt(400, 8), pt(30, 0)]), True)
check("sail on a fresh 8", U.under_engine([pt(400, 0), pt(30, 8)]), False)

# The newest status wins, not the most common one. She motors out of the river and then
# makes sail; twenty messages of engine behind her must not outvote the one that says she
# is sailing now.
check("newest status wins",
      U.under_engine([pt(300 - i * 10, 0) for i in range(20)] + [pt(15, 8)]), False)

# --- silence is not evidence ----------------------------------------------------------
# Her own feed does not carry nav status at all, and aisstream only sometimes does. An
# hours-old status says nothing about now, and a page that guessed from it would claim
# knowledge it does not have. None means "she has not said", and the caller then leaves
# the sailing course alone, which is what the page did before this existed.
check("no status at all", U.under_engine([pt(60), pt(20)]), None)
check("status too old", U.under_engine([pt(9 * 60, 0), pt(20)]), None)
check("empty track", U.under_engine([]), None)
check("no track", U.under_engine(None), None)

# Points with no status in front of a recent one must be skipped, not stop the search:
# in the real track only about seven in ten fixes carry a status at all.
check("skips the gaps", U.under_engine([pt(40, 0), pt(30), pt(20), pt(10)]), True)

# The window is a window. Just inside counts, just outside does not.
check("just inside the window",
      U.under_engine([pt(int(U.ENGINE_WINDOW_H * 60) - 5, 0)]), True)
check("just outside the window",
      U.under_engine([pt(int(U.ENGINE_WINDOW_H * 60) + 5, 0)]), None)

# A status we do not recognise is not a claim either way.
check("unknown status is not engine", U.under_engine([pt(20, 5)]), False)

# The case this was written for, replayed: 124 messages of status 0 and not one of 8.
check("her Dublin departure reads as engine",
      U.under_engine([pt(300 - i * 2, 0) for i in range(124)]), True)


# --- and what the job actually writes -------------------------------------------------
def fake_weather(url, timeout=30):
    from urllib.parse import urlparse, parse_qs
    q = parse_qs(urlparse(url).query)
    if "latitude" not in q:
        return None
    lats = q["latitude"][0].split(",")
    fwd = int(q.get("forecast_days", ["1"])[0])
    past = int(q.get("past_days", ["0"])[0])
    start = (U.now_utc() - timedelta(days=past)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    times = [(start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:00")
             for h in range((past + fwd) * 24)]
    out = []
    for i in range(len(lats)):
        h = {"time": times}
        if "marine" in url:
            h["wave_height"] = [1.0] * len(times)
            h["wave_direction"] = [200] * len(times)
            h["wave_period"] = [6] * len(times)
        else:
            h["wind_speed_10m"] = [8.0] * len(times)
            h["wind_direction_10m"] = [240] * len(times)
            h["wind_gusts_10m"] = [11.0] * len(times)
            h["temperature_2m"] = [14.0] * len(times)
            h["weather_code"] = [2] * len(times)
        out.append({"latitude": float(lats[i]), "longitude": 0.0, "hourly": h})
    return out


U.get_json = fake_weather
ports = json.loads((U.DATA / "ports.json").read_text())["ports"]
target = next(p for p in ports if p["name"] == "St. Malo")
U.next_port = lambda *a, **k: target

# The real course the router wrote to St. Malo on 21 September 2026, kept verbatim. An
# invented zigzag did not clear the Cornish and Breton coasts, so the land check emptied
# it and the test passed for the wrong reason.
LEGS = [[52.191, -5.8833], [52.0618, -5.7072], [51.9105, -5.5656], [51.7948, -5.4975],
        [51.713, -5.4213], [51.6205, -5.3353], [51.5511, -5.2709], [51.4344, -5.1629],
        [51.4189, -5.2314], [51.3907, -5.2053], [51.3538, -5.3074], [51.3538, -5.1917],
        [51.3411, -5.1676], [51.2932, -5.2588], [51.2239, -5.2185], [51.1942, -5.1787],
        [51.1623, -5.1602], [51.0903, -5.1185], [50.8483, -5.3396], [50.5609, -5.7184],
        [50.2429, -5.8993], [49.9422, -5.8169], [49.773, -5.5973], [49.667, -5.5377],
        [49.6044, -5.5207], [49.6701, -5.3445], [49.7719, -5.0707], [49.8533, -4.7215],
        [49.8902, -4.3927], [49.9197, -4.13], [49.9479, -3.8799], [49.9079, -3.7101],
        [49.8741, -3.4156], [49.7774, -3.2374], [49.6328, -3.1086], [49.4958, -2.9318],
        [49.3858, -2.8703], [49.1908, -2.7617], [49.0967, -2.5137], [49.0265, -2.3286],
        [48.9296, -2.2049], [48.645, -2.02]]
TACKS = [8, 9, 10, 11, 13, 15, 21, 25, 38]


def put_course(from_index=0):
    legs = LEGS[from_index:]
    U.write_json(U.COURSE, {
        "generated_utc": U.iso(NOW - timedelta(minutes=20)),
        "to": "St. Malo", "depart_utc": U.iso(NOW - timedelta(minutes=20)),
        "reached": True, "hours": 126.0, "plan_hours": 126.0,
        "direct_nm": 259.1, "sailed_nm": 368.4,
        "tacks": [t - from_index for t in TACKS if t > from_index],
        "wind_kn": {"min": 3.7, "max": 16.1},
        "points": [{"lat": a, "lon": b, "t": U.iso(NOW + timedelta(hours=3 * i)),
                    "hours": 3.0 * i, "course": 150, "twa": -70, "tws": 10.0, "sog": 5.0}
                   for i, (a, b) in enumerate(legs)],
    })
    return legs[0]


def run(label, ns, from_index, want_basis, want_flag, want_tacks):
    at = put_course(from_index)
    track = []
    for m in (60, 40, 20):
        p = {"t": U.iso(NOW - timedelta(minutes=m)), "lat": at[0], "lon": at[1], "sog": 5.5}
        if ns is not None:
            p["ns"] = ns
        track.append(p)
    U.build_ahead(at[0], at[1], 5.5, track)
    a = json.loads((U.DATA / "ahead.json").read_text())
    check(f"{label}: route_basis", a.get("route_basis"), want_basis)
    check(f"{label}: under_engine flag", a.get("under_engine"), want_flag)
    check(f"{label}: tacks drawn", bool(a.get("tacks")), want_tacks)
    # Whichever line it is, there still has to BE one, with weather on it. An empty map is
    # worse than a line that needs a sentence of explanation.
    check(f"{label}: has a route", bool(a.get("route")), True)
    check(f"{label}: weather filled in",
          all(p.get("wind_ms") is not None for p in (a.get("points") or [])), True)


# From index 21 she is off the Scillies, where the A* sea route can find its own way in.
run("in the approaches, under sail", 8, 21, "course", False, True)
run("in the approaches, under engine", 0, 21, "direct", True, False)
run("in the approaches, no status", None, 21, "course", None, True)

# From index 0 she is in the Irish Sea, and A* cannot get round Cornwall through a mask
# that keeps six kilometres off the land. Motoring must not blank the map: keep the course
# and let the page say she is not steering it.
run("in the Irish Sea, under sail", 8, 0, "course", False, True)
run("in the Irish Sea, under engine falls back", 0, 0, "course", True, True)

if fails:
    print("FAILURES:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
