"""Spend the VesselFinder credits by 29 May - all of them, and not before.

23 November 2026. The account was topped up to 9,572 credits for the rest of the voyage,
and "det er ikke noe poeng å sitte igjen med mange kreditter når de er i havn 29. mai".
So the spacing at sea is no longer a fixed four hours: it is the balance, less a reserve
and what the port days will cost, spread over the sea days left in the plan - clamped to
between two and six hours. And the interval follows it, because asking every three hours
with interval=240 would buy back the position bought three hours before.

Nothing here goes on the network; the clock and the server are both stand-ins.
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
NOW = datetime(2026, 12, 10, 12, 0, tzinfo=timezone.utc)       # Las Palmas -> Mindelo
U.now_utc = lambda: NOW
U.today_iso = lambda: NOW.strftime("%Y-%m-%d")

PLAN = [
    {"name": "Las Palmas", "lat": 28.145, "lon": -15.428, "arrive": "2026-11-18", "depart": "2026-12-04"},
    {"name": "Mindelo", "lat": 16.886, "lon": -24.990, "arrive": "2026-12-13", "depart": "2026-12-17"},
    {"name": "Noronha", "lat": -3.840, "lon": -32.410, "arrive": "2026-12-29", "depart": "2027-01-02"},
    {"name": "Home", "lat": 58.14, "lon": 7.99, "arrive": "2027-01-20", "depart": "2027-01-20"},
]


def check(label, got, want):
    if got != want:
        fails.append(f"{label}: got {got!r}, wanted {want!r}")


# ---- 1. the days left ------------------------------------------------------------------
sea, port = U._voyage_days_left(PLAN, datetime(2027, 1, 21, tzinfo=timezone.utc))
# 10 Dec 12:00 -> 13 Dec: 2.5 at sea; Mindelo 13-18: 5 in port; 18-29: 11 at sea;
# Noronha 29 Dec - 3 Jan: 5 in port; 3-20 Jan: 17 at sea; Home 20 Jan: 1 in port.
check("sea days left", round(sea, 1), 30.5)
check("port days left", round(port, 1), 11.0)

# ---- 2. the spacing follows the balance ------------------------------------------------
receipt = {"credits": 3000, "expires": "2027-12-06 21:59:59"}
h, line = U.vf_sea_spacing_h(receipt, PLAN)
# (3000 - 300 - 11*10) / 11 / 30.5 = 7.72 asks a day -> 3.1 h
check("3000 credits over 30.5 sea days", round(h, 1), 3.1)
if "3000 credits" not in line or "30 sea days" not in line and "31 sea days" not in line:
    fails.append(f"the log line does not show the sum: {line!r}")
check("a large balance: as often as allowed, no more", U.vf_sea_spacing_h({"credits": 50000}, PLAN)[0],
      U.VF_SEA_MIN_H)
check("a small balance: as seldom as allowed", U.vf_sea_spacing_h({"credits": 600}, PLAN)[0],
      U.VF_SEA_MAX_H)
check("an empty balance: as seldom as allowed", U.vf_sea_spacing_h({"credits": 0}, PLAN)[0],
      U.VF_SEA_MAX_H)
check("no balance on record: the old four hours", U.vf_sea_spacing_h({}, PLAN)[0], U.VF_SEA_H)
check("no plan: the old four hours", U.vf_sea_spacing_h({"credits": 3000}, [])[0], U.VF_SEA_H)
more = U.vf_sea_spacing_h({"credits": 4000}, PLAN)[0]
less = U.vf_sea_spacing_h({"credits": 2000}, PLAN)[0]
if not (more < h < less):
    fails.append(f"more credits must mean asking more often: 4000->{more:.1f} h,"
                 f" 3000->{h:.1f} h, 2000->{less:.1f} h")
# Credits that lapse before she is home are spent before they lapse.
early = U.vf_sea_spacing_h({"credits": 3000, "expires": "2026-12-31 21:59:59"}, PLAN)[0]
if not early < h:
    fails.append(f"credits lapsing on 31 Dec should be spent faster: {early:.1f} h vs {h:.1f} h")

# ---- 3. in the fetcher: the wait and the interval both follow it -----------------------
FAR = {"lat": 22.0, "lon": -20.0, "source": "foundation"}          # between the islands
MINDELO_NEAR = {"lat": 16.90, "lon": -25.00, "source": "foundation"}


def run(credits, heard_h_ago, where=FAR):
    keep = (U.get_json, U.VF_STATE, U.VF_KEY, U.VF_SAT, U.VF_INTERVAL)
    urls = []
    U.get_json = lambda url, timeout=30: (urls.append(url), [])[1]
    U.VF_KEY, U.VF_SAT, U.VF_INTERVAL = "k", "1", "240"
    U.VF_STATE = Path(tempfile.mkdtemp()) / "vf.json"
    U.write_json(U.VF_STATE, {"credits": credits, "expires": "2027-12-06 21:59:59"})
    have = [dict(where, seen_utc=U.iso(NOW - timedelta(hours=heard_h_ago)))]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            U.fetch_from_vesselfinder(have, PLAN, [])
    finally:
        U.get_json, U.VF_STATE, U.VF_KEY, U.VF_SAT, U.VF_INTERVAL = keep
    return urls, buf.getvalue()


# 3000 credits: every 3.1 h. Heard 3.5 h ago -> ask; 2.5 h ago -> not yet.
urls, log = run(3000, 3.5)
if not urls:
    fails.append(f"3.5 h of silence with a 3.1 h budget, and no request: {log!r}")
else:
    iv = parse_qs(urlparse(urls[0]).query).get("interval")
    if iv != [str(int(h * 60))]:
        fails.append(f"at sea the interval should follow the spacing ({int(h * 60)} min),"
                     f" not stay at 240 - it would buy back the last position: {iv}")
if "every 3.1 h at sea" not in log:
    fails.append(f"the log does not say what the budget allows: {log!r}")
urls, _ = run(3000, 2.5)
check("2.5 h of silence with a 3.1 h budget: no request", len(urls), 0)

# 600 credits: every 6 h. Heard 5 h ago -> wait; the interval stays at its 240 ceiling.
urls, _ = run(600, 5)
check("5 h of silence with a 6 h budget: no request", len(urls), 0)
urls, _ = run(600, 7)
iv = parse_qs(urlparse(urls[0]).query).get("interval") if urls else None
check("with a 6 h spacing the interval stays at the 240 ceiling", iv, ["240"])

if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
