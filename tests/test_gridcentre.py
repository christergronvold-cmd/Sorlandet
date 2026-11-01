"""The weather must be fetched where the page says she is.

On 1 November Christer sent a screenshot of the map and asked whether she was outside her
own square of weather. She was, near enough: the marker sat at 35.16 N 10.93 W, where she
had been heard at 05:35 that morning, and the 81-point weather grid was a box centred on
33.76 N 13.84 W - her position at 02:09 the morning BEFORE. A hundred and sixty-six miles
behind her, and thirty-two hours stale. Every weather icon on the map, the wind arrows,
the sea state and the sun and moon times were all taken there.

The two facts were four lines apart in the same function:

    lat, lon = float(position["lat"]), float(position["lon"])     # line 4401
    ...
    position = dict(position, lat=points[-1]["lat"], ...)         # line 4487

A backfill can leave the track newer than the fix this round happened to hear - a source
replaying an old position does it every round - so `position` is corrected to the newest
track point, which is what the page draws. `lat` and `lon` were taken before that
correction and never taken again, so every fetch after it used the stale place while the
marker used the corrected one.

So they are one answer now. _where_she_is returns the corrected position AND the
coordinates, and a caller cannot take one without the other. This checks the invariant
that the fault broke: whatever comes back, the coordinates are the position's own.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import update as U  # noqa: E402

fails = []

# Against the code that was published on 1 November this is where the test stops, because
# there is nothing to call: the correction and the coordinates are still two separate
# facts, four lines apart, and the only thing that could be checked is the map a parent
# is already looking at.
if not hasattr(U, "_where_she_is"):
    print("FAILURES")
    print("  - update.py has no _where_she_is. The published position and the coordinates"
          " every fetch uses are still taken separately, so a round that hears a stale"
          " replay centres the weather grid, the sun and moon and the weather card on a"
          " place she has left - 166 nm behind her on 1 November, and nothing in the job"
          " says so.")
    sys.exit(1)

# Her real numbers from that morning.
HEARD_STALE = {"lat": 33.76221, "lon": -13.84114, "seen_utc": "2026-10-31T02:09:20Z",
               "sog_kn": 6.2, "cog_deg": 40, "source": "vesselfinder"}
TRACK = [{"t": "2026-10-31T22:40:49Z", "lat": 34.77264, "lon": -11.91331, "sog": 7.3, "cog": 70},
         {"t": "2026-11-01T00:48:29Z", "lat": 34.87175, "lon": -11.61248, "sog": 8.0, "cog": 60},
         {"t": "2026-11-01T05:35:40Z", "lat": 35.1625, "lon": -10.93498, "sog": 7.0, "cog": 54}]
NEWEST = TRACK[-1]


def agree(label, pos, lat, lon):
    """The one rule: the coordinates are the published position's own."""
    if (round(lat, 5), round(lon, 5)) != (round(float(pos["lat"]), 5),
                                          round(float(pos["lon"]), 5)):
        off = U.nm_between((lat, lon), (float(pos["lat"]), float(pos["lon"])))
        fails.append(f"{label}: the page would draw her at {pos['lat']}, {pos['lon']} and"
                     f" fetch the weather at {lat}, {lon} - {off:.0f} nm apart")


# 1. The fault itself: this round heard a stale replay, the track knows better.
pos, lat, lon = U._where_she_is(dict(HEARD_STALE), TRACK)
agree("stale fix behind a newer track", pos, lat, lon)
if pos.get("seen_utc") != NEWEST["t"]:
    fails.append(f"the published position is still the stale one ({pos.get('seen_utc')})"
                 f" rather than the newest track point")
if round(lat, 4) != round(NEWEST["lat"], 4):
    fails.append(f"the weather would be fetched at {lat}, which is not where the newest"
                 f" track point is ({NEWEST['lat']}) - this is the screenshot")

# 2. ...and the correction must not fire when the heard fix IS the newest. A fix that
#    simply always took the track's end would throw away a fresher position.
AHEAD_OF_TRACK = {"lat": 35.9, "lon": -10.1, "seen_utc": "2026-11-01T09:00:00Z",
                  "sog_kn": 7.4, "cog_deg": 52, "source": "aisstream"}
pos2, lat2, lon2 = U._where_she_is(dict(AHEAD_OF_TRACK), TRACK)
agree("fresh fix ahead of the track", pos2, lat2, lon2)
if pos2.get("seen_utc") != AHEAD_OF_TRACK["seen_utc"]:
    fails.append("a fix newer than the whole track was overwritten by the track's end")

# 3. No track at all, and rubbish in the timestamps: still one answer, still no crash.
for label, pos_in, track_in in (
        ("no track", dict(HEARD_STALE), []),
        ("track with an unreadable stamp", dict(HEARD_STALE),
         [{"t": "not a date", "lat": 35.0, "lon": -11.0}]),
        ("position with no seen_utc",
         {"lat": 33.76221, "lon": -13.84114}, TRACK)):
    try:
        p3, la, lo = U._where_she_is(pos_in, track_in)
    except Exception as exc:
        fails.append(f"{label}: raised {type(exc).__name__}: {exc}")
        continue
    agree(label, p3, la, lo)

# 4. The grid built from those coordinates must actually contain her. 300 nm box, so the
#    centre is 150 nm from the edge - a centre 166 nm behind her put her outside it.
lats, lons = U.grid_around(lat, lon)
box = (min(float(a) for a in lats), max(float(a) for a in lats),
       min(float(b) for b in lons), max(float(b) for b in lons))
if not (box[0] <= lat <= box[1] and box[2] <= lon <= box[3]):
    fails.append(f"she is outside her own weather grid: {lat}, {lon} against {box}")
margin_nm = U.nm_between((lat, lon), (box[1], lon))
if margin_nm < 100:
    fails.append(f"only {margin_nm:.0f} nm of grid north of her - the box is not centred"
                 f" on her any more")

print(f"published {pos['seen_utc']} at {pos['lat']}, {pos['lon']};"
      f" weather fetched at {lat}, {lon}; {margin_nm:.0f} nm of grid ahead of her.")
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
