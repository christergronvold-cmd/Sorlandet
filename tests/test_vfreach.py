"""The paid source must be aimed at the lens, and judged from the newest fix we have.

Two faults in the gate, found on 2 November while she was thirteen hours from the mouth of
the Guadalquivir - the one river on the voyage with a camera on it, and the one permission
Christer went and asked for.

ONE: the gate judged her distance from whatever the free sources last handed over. The
foundation's feed is a rolling window, not a live stream, and its newest entry had not
moved since 31 October. On any round where the two live streams heard nothing, that frozen
window WAS the answer, and the receipt said so: "near": "Funchal", while she was 109 nm
from Sevilla and five hundred from Madeira. Same shape as the weather grid on 1 November -
the last thing in a list taken for the newest thing known.

TWO: VF_NEAR_NM is measured to a PORT, and Sevilla's port is forty-two miles up the river.
The camera is at the mouth. Measured to the quay she came inside fifty miles with thirteen
miles still to run to the lens - about two hours at her speed - so the four-hour sea
cadence covered the whole approach she was being watched for, and the position the notice
is reckoned from could have been four hours and twenty-three miles old as she passed it.
A passage with a camera on it is somewhere she can be approaching, exactly as a port is.

Both are about the same question - where is she, and what can see her - and the cost of
getting it right is about seventeen credits on an eight-hour run in.
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import update as U  # noqa: E402

fails = []
PLAN = json.loads((ROOT / "data" / "ports.json").read_text(encoding="utf-8"))
MOUTH = next(q for q in PLAN["passages"] if "Guadalquivir" in q["name"])
NOW = datetime(2026, 11, 2, 7, 15, tzinfo=timezone.utc)

# The frozen window, exactly as it stood: her position at 02:09 on 31 October, which is
# 168 nm from Funchal and 440 from Sevilla.
FROZEN = [{"lat": 33.76221, "lon": -13.84114, "seen_utc": "2026-10-31T02:09:20Z",
           "sog_kn": None, "source": "foundation"}]


def track_to(lat, lon, stamp, sog=5.8):
    """A short track that ends where she really is, with way on her."""
    out = []
    for k in (3, 2, 1, 0):
        out.append({"t": U.iso(U.parse_iso(stamp) - timedelta(minutes=12 * k)),
                    "lat": lat - 0.02 * k, "lon": lon - 0.04 * k, "sog": sog, "ns": 0})
    return out


def run(have, points, asked_min_ago):
    """Drive the real gate with the network stubbed, and hand back what it did."""
    calls, wrote = [], {}
    keep = (U.now_utc, U.get_json, U.write_json, U.VF_STATE, U.VF_KEY)
    box = Path(tempfile.mkdtemp()) / "vf.json"
    try:
        U.now_utc = lambda: NOW
        U.VF_KEY = "test-key"
        U.VF_STATE = box
        keep[2](box, {"requests": 10, "near": "Funchal",
                      "asked_utc": U.iso(NOW - timedelta(minutes=asked_min_ago)),
                      "status_utc": U.iso(NOW)})          # balance already checked today

        def fake(url, timeout=0):
            calls.append(url)
            if "status" in url:
                return {"CREDITS": 900, "EXPIRATION_DATE": "2026-11-20 00:00:00"}
            return [{"AIS": {"LATITUDE": points[-1]["lat"], "LONGITUDE": points[-1]["lon"],
                             "TIMESTAMP": "2026-11-02 07:15:19", "SOG": 5.8,
                             "NAVSTAT": 0, "AIS_SRC": "TER"}}]
        U.get_json = fake
        U.write_json = lambda p, d: (wrote.update(d), keep[2](p, d))
        got = U.fetch_from_vesselfinder(have, PLAN["ports"], points)
    finally:
        (U.now_utc, U.get_json, U.write_json, U.VF_STATE, U.VF_KEY) = keep
    return {"asked": any("vessels" in c or "status" not in c for c in calls),
            "fixes": got, "receipt": wrote}


# 1. Far out, with the frozen window as the only "have": the gate must still judge from
#    the newest thing known, which is the track, and name the lens rather than Madeira.
far = track_to(36.14, -8.26, "2026-11-02T03:09:59Z")
r1 = run(FROZEN, far, asked_min_ago=600)
near = r1["receipt"].get("near")
if near == "Funchal":
    fails.append("the gate still measured from the frozen feed and called Funchal the"
                 " nearest place - she is 500 nm from it and 82 from the camera")
elif near != MOUTH["name"]:
    fails.append(f"nearest watched place came out as {near!r}, wanted {MOUTH['name']!r}")
if not r1["fixes"]:
    fails.append("ten hours since the last request and it did not ask at all")

# 2. Thirty miles off the lens and moving: that is the run in, and the run in is worth a
#    credit every half hour. Measured to the quay she is still 72 nm out and "at sea",
#    and the old rule would have waited four hours.
# Her last fix 45 minutes old: inside the half-hour cadence this is due, outside the
# four-hour one it is not. That difference is the whole point.
close = track_to(36.55, -6.70, "2026-11-02T06:30:00Z")
off = U.nm_between((36.55, -6.70), (MOUTH["lat"], MOUTH["lon"]))
quay = U.nm_between((36.55, -6.70), (37.385, -6.0))
r2 = run([], close, asked_min_ago=45)
if not r2["fixes"]:
    fails.append(f"{off:.0f} nm off the camera, under way, and 45 minutes since the last"
                 f" request - it did not ask. Measured to the quay she is {quay:.0f} nm"
                 f" out, which is how this was missed")

# 3. ...but it must not turn into asking every half hour for the whole Atlantic. Out in
#    mid-ocean, 45 minutes is not a reason.
ocean = track_to(34.0, -14.0, "2026-11-02T06:30:00Z")
r3 = run([], ocean, asked_min_ago=45)
if r3["fixes"]:
    fails.append("asked again after 45 minutes in mid-Atlantic - the sea cadence is gone"
                 " and the credits with it")

# 4. The balance is read, and read sparingly.
if not hasattr(U, "_vf_balance"):
    fails.append("update.py never asks what is left in the account: no credits, no expiry"
                 " date, nothing to warn on. On 27 October the credits ran out seven"
                 " seconds after the last good fix and the page stood still for 41 hours")
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)

asked = []
keep = (U.now_utc, U.get_json)
try:
    U.now_utc = lambda: NOW
    U.get_json = lambda url, timeout=0: (asked.append(url),
                                         {"CREDITS": 90, "EXPIRATION_DATE": "2026-11-05"})[1]
    fresh = U._vf_balance({"status_utc": U.iso(NOW - timedelta(hours=1))})
    stale = U._vf_balance({"status_utc": U.iso(NOW - timedelta(hours=30)),
                           "warned_on": U.today_iso()})
finally:
    (U.now_utc, U.get_json) = keep
if fresh:
    fails.append("asked for the balance again an hour after the last time")
if stale.get("credits") != 90 or not stale.get("expires"):
    fails.append(f"the balance was not recorded in the receipt: {stale}")
if len(asked) != 1:
    fails.append(f"the status endpoint was called {len(asked)} times for two checks")

# 5. A status call that goes wrong must never stop the job asking for a position.
keep = (U.now_utc, U.get_json)
try:
    U.now_utc = lambda: NOW

    def boom(url, timeout=0):
        raise RuntimeError("no answer")
    U.get_json = boom
    broke = U._vf_balance({"status_utc": U.iso(NOW - timedelta(hours=30))})
except Exception as exc:
    fails.append(f"a failed status call raised instead of being recorded: {exc}")
    broke = {}
finally:
    (U.now_utc, U.get_json) = keep
if not broke.get("status_error"):
    fails.append("a failed status call left nothing in the receipt to say so")

print(f"far out: nearest watched place {near!r}, asked: {bool(r1['fixes'])}."
      f" {off:.0f} nm off the lens ({quay:.0f} nm to the quay): asked {bool(r2['fixes'])}."
      f" Mid-Atlantic after 45 min: asked {bool(r3['fixes'])}.")
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
