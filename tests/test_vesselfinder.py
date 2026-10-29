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

The sample below is a REAL reply from their server, fetched on 20 October while she lay
at anchor off Funchal, not a shape copied out of the documentation. That distinction is
the whole point: the still-image fetcher in September was written from a document, ran
for three weeks and brought back nothing, because the real server answered differently.
A recorded reply cannot drift, and when they change the shape this test is where it shows.

It also carries SRC: "TER". A terrestrial record costs one credit and a satellite one
costs ten, and this code never asks for satellite - so that field is the receipt, and the
fetcher shouts if SAT ever appears in it.
"""
import contextlib
import io
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
# Recorded from api.vesselfinder.com on 2026-10-20, she being at anchor off Funchal.
SAMPLE = [{"AIS": {"MMSI": 257165000, "TIMESTAMP": "2026-10-20 11:27:17 UTC",
                   "LATITUDE": 32.64436, "LONGITUDE": -16.89606, "COURSE": 247.2,
                   "SPEED": 0.3, "HEADING": 190, "NAVSTAT": 1, "IMO": 5334561,
                   "NAME": "SORLANDET", "CALLSIGN": "LDTY", "TYPE": 36, "A": 55, "B": 9,
                   "C": 5, "D": 4, "DRAUGHT": 4.1, "DESTINATION": "PT FNC",
                   "LOCODE": "PTFNC", "ETA_AIS": "10-22 10:00",
                   "ETA": "2026-10-22 10:00:00", "SRC": "TER", "ZONE": "West Africa",
                   "ECA": False, "DISTANCE_REMAINING": None, "ETA_PREDICTED": None}}]

pt = update._vf_point(SAMPLE)
if not pt:
    fails.append("a real reply from their server did not parse at all")
else:
    check("lat", pt["lat"], 32.64436)
    check("lon", pt["lon"], -16.89606)
    check("timestamp", pt["seen_utc"], "2026-10-20T11:27:17Z")
    check("source", pt["source"], "vesselfinder")
    check("destination", pt["destination"], "PT FNC")
    check("nav status kept", pt["nav_status"], 1)
    check("speed", pt["sog_kn"], 0.3)
    check("course", pt["cog_deg"], 247.2)
    check("heading kept", pt["heading_deg"], 190)
    # The receipt: one credit, not ten.
    check("terrestrial", pt["ais_src"], "TER")

# ...and 511, the AIS value for "no heading", must never reach the map as a bearing.
noHead = update._vf_point([{"AIS": dict(SAMPLE[0]["AIS"], HEADING=511)}])
check("heading 511 dropped", noHead and noHead["heading_deg"], None)

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

# Out at sea she is asked for too, just slowly. This was a hard stop once - more than
# fifty miles from a port and the source was not asked at all, because no camera could
# see her out there. True, and the wrong rule: it let the camera question decide a
# position question, and the two were built for different things. The dot was built to be
# fresh. On 28 October she sailed from Funchal, crossed fifty miles by evening, and the
# page would have shown the same position for a week.
import tempfile as _tf3
update.VF_STATE = Path(_tf3.mkdtemp()) / "vf.json"
cases = [
    ("free sources are current", [dict(NEAR, seen_utc=ago(0.2))], [FUNCHAL]),
    ("mid-ocean and heard an hour ago", [dict(FAR, seen_utc=ago(1))], [FUNCHAL]),
    ("nothing to judge by", [], []),
]
for label, have, ports in cases:
    update.VF_STATE = Path(_tf3.mkdtemp()) / "vf.json"
    if update.fetch_from_vesselfinder(have, ports, []) != []:
        fails.append(f"spent a credit when {label}")

# ...and the case that used to be silence and must not be any more.
update.VF_STATE = Path(_tf3.mkdtemp()) / "vf.json"
update.get_json = lambda url, timeout=30: SAMPLE
_hush3 = io.StringIO()
with contextlib.redirect_stdout(_hush3), contextlib.redirect_stderr(_hush3):
    at_sea = update.fetch_from_vesselfinder([dict(FAR, seen_utc=ago(12))], [FUNCHAL], [])
if not at_sea:
    fails.append("mid-ocean with nothing heard for twelve hours and still no request -"
                 " that is the dot going a week old on an Atlantic crossing")

update.VF_KEY = saved

# ------------------------------------------------------ the six days she lies at anchor
# This is the defect Alex's price list made visible, and it belongs in the suite counted
# in credits rather than argued in prose.
#
# The job runs every half hour. The free feeds went thirteen hours quiet while she lay at
# anchor off Funchal, and she lies there six days. The first version of this gate asked
# whenever the free feeds were quiet and she was near a port - both true, continuously,
# for the whole stay - so it would have bought the same answer 288 times. The answer never
# changed. The page spends a position on one question, which lens is nearest her, and a
# ship that has not moved cannot have changed the answer.
#
# Two things stop it now. A fix we have already BOUGHT counts as knowing where she is
# (the old gate looked only at what this run's listeners heard, so it did not), and while
# she is not moving the question is only worth asking every few hours - not never, because
# the free feeds may well miss the hour she slips her anchor, and that hour is one the
# cameras care about.
ANCHOR = (32.64436, -16.89606)            # where she actually lay, a mile off Funchal
START = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
# The real plan, nineteen ports of it. These tests used to pass a one-port list, and
# that was the hole the next bug walked through: with nothing AFTER Funchal in the list,
# "the next port" could never become the wrong one, so the suite was blind to the day
# the track registered her as ARRIVED and the next port became Sevilla, 606 nm away.
# She lay a mile off the Funchal waterfront with eleven lenses on her and the gate said
# no camera could be looking at her. Real data, or the test proves nothing.
PLAN = json.loads((ROOT / "data" / "ports.json").read_text())["ports"]


def anchored_track(upto, hours=range(1, 49)):
    """Two days of fixes in one circle off Funchal - a ship at anchor, from the track's
    point of view, which is how voyage_state comes to call her arrived."""
    return [{"t": update.iso(upto - timedelta(hours=h)), "lat": ANCHOR[0],
             "lon": ANCHOR[1], "sog": 0.2, "cog": None} for h in sorted(hours, reverse=True)]


def run_the_stay(hours, step_min=30, speed=0.3, navstat=1, moves=False, track=None,
                 ports=None, start=None):
    """Every run of the job across a stretch of hours, the free sources silent throughout.

    Returns how many credits it spent. Everything is faked but the gate: the clock, the
    server, and the file the receipt is written to.
    """
    import tempfile
    was = (update.now_utc, update.get_json, update.VF_STATE, update.VF_KEY)
    begin = start or START
    clock = {"now": begin, "n": 0}
    update.now_utc = lambda: clock["now"]
    update.VF_STATE = Path(tempfile.mkdtemp()) / "vesselfinder.json"
    update.VF_KEY = "test-key"

    def server(url, timeout=30):
        clock["n"] += 1
        drift = 0.02 * clock["n"] if moves else 0.0
        return [{"AIS": dict(SAMPLE[0]["AIS"],
                             TIMESTAMP=clock["now"].strftime("%Y-%m-%d %H:%M:%S UTC"),
                             LATITUDE=ANCHOR[0] + drift, LONGITUDE=ANCHOR[1],
                             SPEED=speed, NAVSTAT=navstat)}]

    update.get_json = server
    # The track as it stood when the feeds went quiet.
    track = list(track if track is not None else anchored_track(begin - timedelta(hours=13)))
    ports = ports if ports is not None else PLAN
    stale = {"lat": track[-1]["lat"], "lon": track[-1]["lon"], "source": "foundation",
             "seen_utc": track[-1]["t"]}

    bought = 0
    hush = io.StringIO()
    with contextlib.redirect_stdout(hush), contextlib.redirect_stderr(hush):
        for _ in range(int(hours * 60 / step_min)):
            got = update.fetch_from_vesselfinder([stale], ports, track)
            if got:
                bought += 1
                g = got[0]
                track.append({"t": g["seen_utc"], "lat": g["lat"], "lon": g["lon"],
                              "sog": g["sog_kn"], "cog": g["cog_deg"]})
            clock["now"] += timedelta(minutes=step_min)
    update.now_utc, update.get_json, update.VF_STATE, update.VF_KEY = was
    return bought


# Six days at anchor. One request every six hours is 24; the old gate would spend 288.
# Run against the real plan, so that "she has arrived at Funchal, therefore the next
# port is Sevilla, therefore nobody can see her" has a chance to happen - because it did.
at_anchor = run_the_stay(6 * 24)
if at_anchor == 0:
    fails.append("spent nothing at all across six days at anchor a mile off Funchal -"
                 " the distance is being measured to the wrong port")
# Six days from the 20th runs through the 22nd, the day the plan has her coming in, and
# that day is deliberately watched hourly rather than six-hourly - so the honest figure
# is a little over the bare 24, not the 288 of asking every half hour regardless.
if at_anchor > 60:
    fails.append(f"spent {at_anchor} credits on six days at anchor - the ship never moved,"
                 f" and anything near 288 is the old gate asking every half hour")
if at_anchor < 12:
    fails.append(f"only {at_anchor} credits across six days - that is not often enough to"
                 f" catch the hour she slips her anchor")

# ...and the run in, which is the thing the credits were actually bought for: eight hours
# of approach at half-hourly fixes is sixteen, and must not be throttled to the anchored
# cadence just because the previous answer said she was lying still.
inbound = [{"t": update.iso(START - timedelta(hours=h)), "lat": ANCHOR[0] + h * 0.05,
            "lon": ANCHOR[1], "sog": 6.4, "cog": 180} for h in (4, 3, 2, 1)]
approach = run_the_stay(8, speed=6.4, navstat=8, moves=True, track=inbound)
if approach < 12:
    fails.append(f"only {approach} credits across an eight-hour approach - she is moving"
                 f" four miles between fixes and the page is choosing a camera from them")

# The same eight hours, but the bridge forgot to change NAVSTAT off "at anchor". The
# speed over the ground has to win that argument, or a clerical slip blinds the cameras.
sloppy = run_the_stay(8, speed=6.4, navstat=1, moves=True, track=list(inbound))
if sloppy < 12:
    fails.append(f"only {sloppy} credits when she was making 6.4 knots with NAVSTAT still"
                 f" reading 'at anchor' - the speed must win over the hand-set status")

# The two cadences, each on its own day, so the difference is a number and not a claim.
# An ordinary day alongside buys four positions. The day the plan has her sailing buys
# a position an hour, because the free feeds went six hours silent overnight on exactly
# such a morning and she was due away at ten.
quiet_day = run_the_stay(24, start=datetime(2026, 10, 24, 0, 0, tzinfo=timezone.utc))
move_day = run_the_stay(24, start=datetime(2026, 10, 28, 0, 0, tzinfo=timezone.utc))
if not 3 <= quiet_day <= 6:
    fails.append(f"an ordinary day at anchor cost {quiet_day} credits, wanted about four")
if move_day < 16:
    fails.append(f"the day she is due to sail cost only {move_day} credits - she can weigh"
                 f" anchor and be clear before the next look")
if move_day <= quiet_day:
    fails.append("the day she is due to move is watched no closer than any other day")

# And the morning she sails. She has left Funchal, so the next port is Sevilla, 600 nm
# off - but the cameras that matter are the ones she is sailing away from, and the hour
# after she drops her lines is the hour they have something worth showing.
outbound = anchored_track(START - timedelta(hours=10), hours=range(10, 72))
outbound += [{"t": update.iso(START - timedelta(hours=h)), "lat": ANCHOR[0] - (10 - h) * 0.03,
              "lon": ANCHOR[1], "sog": 5.5, "cog": 200} for h in (8, 6, 4, 2)]
leaving = run_the_stay(6, speed=5.5, navstat=8, moves=True, track=outbound)
if leaving < 8:
    fails.append(f"only {leaving} credits in the six hours after she sailed from Funchal -"
                 f" the port she has just left is the one with cameras on her")

# A request that brings back nothing usable - dead key, spent balance - still cost a
# credit, so the next run must not simply try again.
import tempfile as _tf
_was = (update.now_utc, update.get_json, update.VF_STATE, update.VF_KEY)
update.VF_STATE = Path(_tf.mkdtemp()) / "vesselfinder.json"
update.VF_KEY = "test-key"
update.get_json = lambda url, timeout=30: {"error": "Invalid user key"}
_track = [{"t": update.iso(update.now_utc() - timedelta(hours=h)), "lat": ANCHOR[0] + h * 0.1,
           "lon": ANCHOR[1], "sog": 6.0, "cog": 180} for h in (3, 2, 1)][::-1]
_near = {"lat": ANCHOR[0], "lon": ANCHOR[1], "source": "foundation",
         "seen_utc": update.iso(update.now_utc() - timedelta(hours=9))}
_hush = io.StringIO()
with contextlib.redirect_stdout(_hush), contextlib.redirect_stderr(_hush):
    first = update.fetch_from_vesselfinder([_near], PLAN, _track)
    second = update.fetch_from_vesselfinder([_near], PLAN, _track)
check("a dead key returns nothing", first, [])
if second != [] or "asked_utc" not in (update.read_json(update.VF_STATE, {}) or {}):
    fails.append("asked again straight after a request that brought back nothing - a"
                 " request costs a credit whether or not the answer is usable")
update.now_utc, update.get_json, update.VF_STATE, update.VF_KEY = _was

# ----------------------------------------------- the shore first, the satellites after
# Alex at VesselFinder, 29 October: "I would suggest using sat=1 only when no usable data
# is provided with sat=0. This way you can minimise the 10 credit charges."
#
# sat=1 never over-charges outright - terrestrial still comes back at one credit when it
# is freshest. But it hands over a satellite position whenever satellite is merely NEWER,
# and a three-hour-old shore fix is usually all this page needs. So: shore first, and the
# satellites only when the shore has nothing worth having.
_asked = []
_was = (update.get_json, update.VF_KEY, update.VF_STATE)
import tempfile as _tf2
update.VF_KEY = "test-key"
# The recorded reply is nine days old by now, which is exactly the "not worth having"
# case. A freshly stamped copy of it is the "shore has her" case.
def _stamped(mins_ago):
    when = datetime.now(timezone.utc) - timedelta(minutes=mins_ago)
    return [{"AIS": dict(SAMPLE[0]["AIS"],
                         TIMESTAMP=when.strftime("%Y-%m-%d %H:%M:%S UTC"))}]

_fresh_sample = _stamped(4)
_old_sample = _stamped(60 * 24 * 9)

def _runs(replies):
    """Drive one fetch and hand back the URLs it asked, key stripped."""
    _asked.clear()
    update.VF_STATE = Path(_tf2.mkdtemp()) / "vf.json"
    seq = list(replies)

    def fake(url, timeout=30):
        _asked.append(url.split("userkey=")[1].split("&", 1)[-1])
        return seq.pop(0) if seq else None

    update.get_json = fake
    hush = io.StringIO()
    with contextlib.redirect_stdout(hush), contextlib.redirect_stderr(hush):
        out = update.fetch_from_vesselfinder(
            [dict(NEAR, seen_utc=ago(12))], PLAN,
            [{"t": ago(13), "lat": NEAR["lat"], "lon": NEAR["lon"], "sog": 5.0, "cog": 180}])
    return out, list(_asked)

# A fresh shore fix: one request, no sat, one credit.
got, urls = _runs([_fresh_sample])
if not urls:
    fails.append("no request was made at all in the case that is meant to make one")
elif "sat=1" in urls[0]:
    fails.append(f"the FIRST request already asks for satellite: {urls[0]!r} - that is the"
                 f" ten-credit charge taken before the shore stations have been asked")
elif len(urls) > 1:
    fails.append(f"asked the satellites anyway after a good shore fix: {urls}")

# Nothing usable from the shore - an expired key, a spent balance - then the satellites.
got, urls = _runs([{"error": "expired credits"}, _fresh_sample])
if len(urls) < 2 or "sat=1" not in urls[-1]:
    fails.append(f"the shore gave nothing and the satellites were never asked: {urls}")
if not got:
    fails.append("the satellite answer was thrown away")

# A shore fix so old it was not worth the asking: try the satellites too.
got, urls = _runs([_old_sample, _fresh_sample])
if len(urls) < 2:
    fails.append(f"a nine-day-old shore fix was accepted without asking the satellites:"
                 f" {urls}")

# Both dead: no crash, nothing returned, and the receipt SAYS so - which is the whole
# lesson of 28 October, when fifty-four errors in a row left a file that looked like a
# source quietly returning old data.
got, urls = _runs([{"error": "expired credits"}, {"error": "expired credits"}])
if got:
    fails.append("invented a position out of two error replies")
receipt = update.read_json(update.VF_STATE, {}) or {}
if not receipt.get("failing"):
    fails.append("the receipt does not record that the source is failing; for forty-one"
                 " hours that silence read as stale data instead of a dead account")
if not receipt.get("last_error"):
    fails.append("the receipt does not keep what the error actually said")

update.get_json, update.VF_KEY, update.VF_STATE = _was

print(f"parsed a real recorded reply, refused 6 bad ones, and held the credit in "
      f"{len(cases) + 1} cases where it would have bought nothing")
print(f"six days at anchor cost {at_anchor} credits, not 288; an eight-hour run in cost"
      f" {approach}, {sloppy} with the status left reading 'at anchor', and the six hours"
      f" after she sailed cost {leaving}; an ordinary day alongside costs {quiet_day} and"
      f" the day she is due to sail costs {move_day}")
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
