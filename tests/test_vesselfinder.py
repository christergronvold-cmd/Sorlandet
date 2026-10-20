"""The one source that costs money: when it is asked, and what it does with the answer.

Three free sources feed this page and they are good. They are also not enough in one
place. aisstream listens twenty-nine minutes in every thirty and heard nothing at all
through nine days at Madeira; the foundation's satellite feed went twelve and a half
hours between fixes while she lay at anchor off Funchal. VesselFinder's API had her from
twelve minutes earlier. The gap only matters near land, because that is where the page
decides which harbour camera to open, and on 19 October a half-day-old position opened
one on the wrong side of the island.

So this source is asked last, rarely, and only when both conditions hold: the free ones
have gone quiet, AND she is near enough to a port for the answer to change the page. Each
request is a credit. The tests below are about not spending them, and about surviving the
day the trial runs out.

The parser was written from VesselFinder's documentation, not from a reply this code had
seen. That is exactly how the still-image fetcher was written in September, and it ran
for three weeks bringing back nothing, because the real server answered differently from
the document. So the sample below is the shape they document, the parser reads fields
case-insensitively, and anything it does not recognise is refused rather than guessed at.
Until a reply from the real server has been through it, treat this as unproven.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import update  # noqa: E402

fails = []


def check(label, got, want):
    if got != want:
        fails.append(f"{label}: got {got!r}, wanted {want!r}")


def ago(hours):
    return update.iso(update.now_utc() - timedelta(hours=hours))


FUNCHAL = {"name": "Funchal", "lat": 32.645, "lon": -16.91,
           "arrive": (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d"),
           "depart": (datetime.now(timezone.utc) + timedelta(days=9)).strftime("%Y-%m-%d")}
NEAR = {"lat": 32.65, "lon": -16.90, "seen_utc": None, "source": "foundation"}   # at anchor
FAR = {"lat": 38.00, "lon": -20.00, "seen_utc": None, "source": "foundation"}    # mid-ocean

# ------------------------------------------------------------------ the reply, parsed
# The shape VesselFinder's Vessels method documents.
SAMPLE = [{"AIS": {"MMSI": 257165000, "TIMESTAMP": "2026-10-20 11:10:17 UTC",
                   "LATITUDE": 32.64334, "LONGITUDE": -16.89501, "COURSE": 116.0,
                   "SPEED": 0.1, "HEADING": 511, "NAVSTAT": 1, "IMO": 5334561,
                   "NAME": "SORLANDET", "CALLSIGN": "LDTY", "DESTINATION": "PT FNC",
                   "DRAUGHT": 4.1}}]

pt = update._vf_point(SAMPLE)
if not pt:
    fails.append("the documented reply shape did not parse at all")
else:
    check("lat", pt["lat"], 32.64334)
    check("lon", pt["lon"], -16.89501)
    check("timestamp", pt["seen_utc"], "2026-10-20T11:10:17Z")
    check("source", pt["source"], "vesselfinder")
    check("destination", pt["destination"], "PT FNC")
    check("nav status kept", pt["nav_status"], 1)
    # 511 is the AIS value for "no heading", and it must not reach the map as a bearing.
    check("heading 511 dropped", pt["heading_deg"], None)

# A bare object rather than a list, which some replies use.
if not update._vf_point(SAMPLE[0]):
    fails.append("a reply that is one object rather than a list did not parse")

# Rubbish must be refused, not guessed at. An expired key and a spent balance both
# arrive here, and both must leave the page running on the free sources.
for junk in ({}, [], [{"AIS": {}}], {"error": "Invalid user key"},
             [{"AIS": {"LATITUDE": 200, "LONGITUDE": 0, "TIMESTAMP": "2026-10-20 11:10:17 UTC"}}],
             [{"AIS": {"LATITUDE": 32.6, "LONGITUDE": -16.9, "TIMESTAMP": "not a date"}}]):
    if update._vf_point(junk) is not None:
        fails.append(f"accepted a reply it should have refused: {junk!r}")

# --------------------------------------------------------------- when it is NOT asked
# The gate runs before any request, so a missing key is not needed to test it - but with
# no key at all nothing may be attempted either.
saved = update.VF_KEY
update.VF_KEY = ""
if update.fetch_from_vesselfinder([dict(NEAR, seen_utc=ago(12))], [FUNCHAL], []) != []:
    fails.append("asked for a position with no API key set")
update.VF_KEY = "test-key"

cases = [
    ("free sources are current", [dict(NEAR, seen_utc=ago(0.2))], [FUNCHAL]),
    ("she is mid-ocean", [dict(FAR, seen_utc=ago(12))], [FUNCHAL]),
    ("nothing to judge by", [], []),
]
for label, have, ports in cases:
    if update.fetch_from_vesselfinder(have, ports, []) != []:
        fails.append(f"spent a credit when {label}")

update.VF_KEY = saved

print(f"parsed the documented reply, refused 6 bad ones, and held the credit in "
      f"{len(cases) + 1} cases where it would have bought nothing")
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
