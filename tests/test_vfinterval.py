"""VesselFinder's interval: do not pay twice for the same position.

23 November 2026. VesselFinder recommended the "interval" parameter - in their
documentation, "the maximum age of the returned positions (in minutes)". At sea the job
asks every four hours when nothing free has heard her, and without it the second question
can hand back - and charge for - the satellite position bought by the first. With
interval=240 a position older than four hours is not returned at all.

An empty answer is then the expected answer, not a broken account. These tests check
that the parameter is sent on every request (shore and satellite), that it can be set
with VF_INTERVAL, and that "nothing newer than the interval" is neither counted as a
failure nor shouted about - while a real error still is.

Nothing here goes on the network: get_json is replaced with recorded-shape replies.
"""
import contextlib
import io
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import update as U  # noqa: E402

fails = []


def check(label, got, want):
    if got != want:
        fails.append(f"{label}: got {got!r}, wanted {want!r}")


def ago(hours):
    return U.iso(U.now_utc() - timedelta(hours=hours))


FUNCHAL = {"name": "Funchal", "lat": 32.645, "lon": -16.91,
           "arrive": (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d"),
           "depart": (datetime.now(timezone.utc) + timedelta(days=9)).strftime("%Y-%m-%d")}
FAR = {"lat": 38.00, "lon": -20.00, "source": "foundation"}           # mid-ocean
HAVE = [dict(FAR, seen_utc=ago(12))]                                  # nothing for 12 h

stamp = (U.now_utc() - timedelta(minutes=50)).strftime("%Y-%m-%d %H:%M:%S UTC")
FIX = [{"AIS": {"MMSI": 257165000, "TIMESTAMP": stamp, "LATITUDE": 37.9,
                "LONGITUDE": -20.1, "COURSE": 247.2, "SPEED": 7.3, "HEADING": 250,
                "NAVSTAT": 8, "IMO": 5334561, "NAME": "SORLANDET", "SRC": "SAT"}}]


def ask(replies, interval="240"):
    """One call of the real fetcher. replies: what the server says to (shore, satellite)."""
    keep = (U.get_json, U.VF_STATE, U.VF_KEY, U.VF_SAT, U.VF_INTERVAL)
    urls = []

    def server(url, timeout=30):
        urls.append(url)
        sat = "sat=1" in url
        return replies[1] if sat else replies[0]

    U.get_json, U.VF_KEY, U.VF_SAT, U.VF_INTERVAL = server, "test-key", "1", interval
    U.VF_STATE = Path(tempfile.mkdtemp()) / "vesselfinder.json"
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            got = U.fetch_from_vesselfinder(HAVE, [FUNCHAL], [])
        state = U.read_json(U.VF_STATE, {}) or {}
    finally:
        U.get_json, U.VF_STATE, U.VF_KEY, U.VF_SAT, U.VF_INTERVAL = keep
    return got, urls, state, buf.getvalue()


def q(url):
    return parse_qs(urlparse(url).query)


# ---- 1. the parameter is on every request ----------------------------------------------
got, urls, state, log = ask(([], FIX))
check("two requests: shore, then satellite", len(urls), 2)
for u in urls:
    if q(u).get("interval") != ["240"]:
        fails.append(f"a request went without interval=240: {u.split('userkey=')[0]}…"
                     f"{u.split('&', 1)[-1]}")
if "sat" in q(urls[0]):
    fails.append("the shore was not asked first")
check("the satellite position is still taken", bool(got) and got[0]["ais_src"], "SAT")

# VF_INTERVAL steers it, in minutes; 0 or empty leaves it off.
_, urls, _, _ = ask((FIX, FIX), interval="120")
check("VF_INTERVAL=120 sends interval=120", q(urls[0]).get("interval"), ["120"])
for off in ("0", ""):
    _, urls, _, _ = ask((FIX, FIX), interval=off)
    if "interval" in q(urls[0]):
        fails.append(f"VF_INTERVAL={off!r} still sent interval")
if U.VF_INTERVAL != "240" and "VF_INTERVAL" not in __import__("os").environ:
    fails.append(f"the default is {U.VF_INTERVAL!r} minutes, not 240 (four hours)")

# ---- 2. "nothing newer than the interval" is not a failure -----------------------------
for shape, label in (([], "an empty list"), ({}, "an empty object"),
                     ([{"AIS": {}}], "a row with an empty AIS record")):
    got, urls, state, log = ask((shape, shape))
    check(f"{label}: no position returned", got, [])
    check(f"{label}: both shore and satellite were asked", len(urls), 2)
    check(f"{label}: not counted as a failure", state.get("failing"), 0)
    check(f"{label}: no error recorded", state.get("last_error"), None)
    if "no position newer than 240 min" not in log:
        fails.append(f"{label}: the log does not say why there was nothing: {log!r}")
    if "nothing usable" in log or "DEAD" in log:
        fails.append(f"{label}: an empty answer was reported as a fault: {log!r}")

# Three rounds of nothing new in a row: still no alarm.
keep = U.VF_STATE
for _ in range(3):
    got, urls, state, log = ask(([], []))
check("three empty answers in a row: still not failing", state.get("failing"), 0)

# ---- 3. ...but a real error still is ----------------------------------------------------
got, urls, state, log = ask(({"error": "Invalid API key"}, {"error": "Invalid API key"}))
check("an error: no position", got, [])
check("an error is counted", state.get("failing"), 1)
if "Invalid API key" not in (state.get("last_error") or ""):
    fails.append(f"the error is not recorded: {state.get('last_error')!r}")
if "nothing usable" not in log:
    fails.append(f"an error is not reported: {log!r}")

# Shore says nothing new, satellite errors: that is an error.
got, urls, state, log = ask(([], {"error": "Out of credits"}))
check("nothing from shore, an error from satellite: counted", state.get("failing"), 1)

# Without interval an empty answer stays what it always was - nothing usable.
got, urls, state, log = ask(([], []), interval="0")
check("no interval: an empty answer is still a failure", state.get("failing"), 1)

if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
