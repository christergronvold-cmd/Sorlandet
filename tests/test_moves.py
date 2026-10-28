"""The message when she goes: what counts as going, and what must stay quiet.

Asked for on 22 October - "can we have a message when she leaves the anchorage and goes
in?" - and the honest answer that morning was no. Not because sending a message is hard,
but because the job would not have known for six hours. That is fixed, so this is now a
question about judgement rather than plumbing: which changes are worth a phone going off.

Two events, and only two. She was lying still at a port and is now moving; she was moving
at a port and has now stopped. Between them they are every arrival and every departure of
the voyage, and neither can be wrong about where she is - unlike "she has sailed for
Sevilla", which is a claim about what the master intends.

Everything below is about NOT sending. A notification nobody needed is worse than none at
all, because the next one gets ignored: she sheers on her cable at four tenths of a knot,
the AIS speed wobbles, the feed goes quiet for nine hours and comes back. None of that is
news. The one in the middle - 0.8 nm from the anchorage at four knots on the morning of
the 28th - is.
"""
import json
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import update  # noqa: E402

fails = []
PLAN = json.loads((ROOT / "data" / "ports.json").read_text(encoding="utf-8"))
PORTS = PLAN["ports"]
FUNCHAL = next(p for p in PORTS if p["name"] == "Funchal")
SEVILLA = next(p for p in PORTS if p["name"] == "Sevilla")
RIVER = next(q for q in PLAN["passages"] if "Guadalquivir" in q["name"])

ANCHORAGE = (32.64420, -16.89664)      # where she lay, 0.68 nm off
QUAY = (32.64288, -16.91460)           # where she moored on 26 October
SEA = (34.50, -14.00)                  # a day and a half out


def fix(where, sog=None, ns=None, mins_old=5, cog=None):
    return {"lat": where[0], "lon": where[1], "sog_kn": sog, "nav_status": ns,
            "cog_deg": cog,
            "seen_utc": update.iso(update.now_utc() - timedelta(minutes=mins_old))}


def track_at(where, hours=30):
    """A track that puts voyage_state at the right port - it is what picks the ports."""
    return [{"t": update.iso(update.now_utc() - timedelta(hours=hours - k)),
             "lat": where[0], "lon": where[1], "sog": 0.2, "cog": 0}
            for k in range(hours)]


class Run:
    """The state file, carried from one run of the job to the next."""

    def __init__(self, track):
        self.state, self.track = {}, track
        self.sent = []

    def __call__(self, where, sog=None, ns=None, plan=None, cog=None):
        self.state, ev = update.move_event(self.state, fix(where, sog, ns, cog=cog),
                                           plan or PLAN, self.track)
        if ev:
            self.sent.append(update.move_words(ev)[0])
        return ev


# --------------------------------------------------------- the morning she weighs anchor
r = Run(track_at(ANCHORAGE))
r(ANCHORAGE, 0.2, 1)                                   # first sighting: learn, say nothing
if r.sent:
    fails.append(f"shouted on the very first run it ever did: {r.sent}")

for sog in (0.0, 0.4, 0.3, 0.6):
    r(ANCHORAGE, sog, 1)
if r.sent:
    fails.append(f"a ship sheering on her cable set off {len(r.sent)} message(s): {r.sent}")

# The needle moves before the ship does: a knot and a half, but still on her anchor.
r((ANCHORAGE[0] + 0.0015, ANCHORAGE[1]), 1.5, None)
if r.sent:
    fails.append("sent on a speed alone - she had not moved a tenth of a mile, which is"
                 " what an AIS speed does on a ship swinging to the tide")

# ...and then she really goes: 0.9 nm from the anchorage, four knots.
ev = r((ANCHORAGE[0] + 0.015, ANCHORAGE[1]), 4.0, 0)
if not ev or ev["kind"] != "underway":
    fails.append("no message on the morning she weighs anchor and stands in - four knots"
                 " and most of a mile from where she lay. This is the one he asked for")
elif "under way at Funchal" not in r.sent[-1]:
    fails.append(f"the message does not say what happened or where: {r.sent[-1]!r}")

before = len(r.sent)
for step in (0.02, 0.025, 0.03):
    r((ANCHORAGE[0] + step, ANCHORAGE[1]), 4.0, 0)
if len(r.sent) != before:
    fails.append("kept sending while she carried on moving - the event is the change,"
                 " not the state")

# ------------------------------------------------------------------- she ties up again
ev = r(QUAY, 0.0, 5)
if not ev or ev["kind"] != "still":
    fails.append("no message when she stopped at the quay - the arrival is half the story")
else:
    title, body, line = update.move_words(ev)
    if "has arrived at Funchal" not in title:
        fails.append(f"arrival message reads {title!r}")
    if "moored in Funchal" not in body:
        fails.append("she broadcast NAVSTAT 5, moored, and the message does not pass that"
                     f" on: {body!r}")
    # The one line a lock screen shows is the only part most people will ever read, so it
    # has to carry the news on its own - no @mention, no bullet list, no link.
    if len(line) > 160 or "Funchal" not in line:
        fails.append(f"the lock-screen line does not stand alone or is too long: {line!r}")

# At anchor is said as at anchor, not as moored.
r2 = Run(track_at(ANCHORAGE))
r2(ANCHORAGE, 4.0, 0)
ev = r2(ANCHORAGE, 0.1, 1)
if not ev or "at anchor off Funchal" not in update.move_words(ev)[1]:
    fails.append("a ship that says she is at anchor is not reported as at anchor")

# ------------------------------------------------------------------------- out at sea
r3 = Run(track_at(ANCHORAGE))
r3(ANCHORAGE, 0.2, 1)
r3(SEA, 6.0, 8)
r3(SEA, 7.0, 8)
if r3.sent:
    fails.append(f"messages from the open ocean, where nothing happened: {r3.sent}")

# A feed that goes quiet and comes back IN THE SAME PLACE is not an event: no speed,
# nothing said, and nothing moved.
r4 = Run(track_at(ANCHORAGE))
r4(ANCHORAGE, 0.2, 1)
if r4(ANCHORAGE, None, None) is not None:
    fails.append("treated a bare position in the same spot as a change of state")

# ------------------------------------------------- 28 October, and why the phone was quiet
# She sailed from Funchal. Nothing happened.
#
# The track for the sixteen hours either side of it, in full:
#
#   27 Oct 16:45  32.64291,-16.91461  sog 0     ns 5   moored at the quay
#   28 Oct 09:26  32.64223,-16.90425  sog null  ns -   half a mile away, saying nothing
#
# One position in sixteen hours and forty minutes, from the foundation's feed, which
# sends bare positions. aisstream heard nothing all night; VesselFinder was asked
# thirty-five times and handed back the same fix from the previous afternoon each time.
#
# So the only evidence that existed was that she was no longer where she had been welded
# for six days - and the rule said a message with no speed and no status is not an answer
# and must be ignored. It was right about the message. A ship is not a message.
BERTH = (32.64291, -16.91461)
GONE = (32.64223, -16.90425)

r10 = Run([{"t": update.iso(update.now_utc() - timedelta(hours=h)), "lat": BERTH[0],
            "lon": BERTH[1], "sog": 0.1, "cog": 180, "ns": 5} for h in range(40, 16, -6)])
r10(BERTH, 0.0, 5)
ev = r10(GONE, None, None)
if not ev or ev["kind"] != "underway":
    fails.append(f"she is {update.nm_between(GONE, BERTH):.2f} nm from a berth she had not"
                 f" left in six days, and the only fix anyone has carries no speed - and"
                 f" still nothing is sent. This is the morning of 28 October exactly")

# ...but a bare fix that has drifted a boat's length is a ship swinging, not a ship going.
r11 = Run([{"t": update.iso(update.now_utc() - timedelta(hours=h)), "lat": BERTH[0],
            "lon": BERTH[1], "sog": 0.1, "cog": 180, "ns": 5} for h in range(40, 16, -6)])
r11(BERTH, 0.0, 5)
if r11((BERTH[0] + 0.001, BERTH[1] + 0.001), None, None) is not None:
    fails.append("a bare fix a hundred metres off the berth set off a departure notice;"
                 " the foundation's feed rounds positions and does that all day")

# ...and the dead zone between the two thresholds is silence - WHERE SHE IS, that is.
#
# This case used to put her a mile from the anchorage at 0.7 knots and demand silence,
# and it was wrong in the same way the rule it was guarding was wrong. Seven tenths of a
# knot says nothing either way. A mile of water says she went. The speed at the instant
# of one fix is not the question; where she is, is.
r5 = Run(track_at(ANCHORAGE))
r5(ANCHORAGE, 0.2, 1)
if r5(ANCHORAGE, 0.7, None) is not None:
    fails.append("0.7 knots on the same spot is neither under way nor stopped, and must"
                 " not be reported as either")
r5b = Run(track_at(ANCHORAGE))
r5b(ANCHORAGE, 0.2, 1)
if r5b((ANCHORAGE[0] + 0.02, ANCHORAGE[1]), 0.7, None) is None:
    fails.append("a mile from where she lay, and silence because the one fix happened to"
                 " read 0.7 knots - the distance is the evidence, not the needle")

# ----------------------------------------------- the hour before, not the minute after
# "...naar baaten gaar inn ved Sanlucar." The first version of this fired as she entered
# the frame. She crosses the mouth of the Guadalquivir in about a quarter of an hour, so
# that message arrives while the phone is still being unlocked - and making the job
# faster does not fix it, because the problem is not lag, it is that the event is the
# wrong one. The hour BEFORE is the thing worth sending.
RIVER_AT = (RIVER["lat"], RIVER["lon"])


def out_at(nm_off, brg_from_site):
    """A position nm_off miles from the river mouth, on the given bearing from it."""
    import math
    d = nm_off / 60.0
    lat = RIVER_AT[0] + d * math.cos(math.radians(brg_from_site))
    lon = RIVER_AT[1] + d * math.sin(math.radians(brg_from_site)) / math.cos(
        math.radians(RIVER_AT[0]))
    return (lat, lon)


APPROACH = out_at(4.5, 215)                 # four and a half miles south-west, at sea
COURSE_IN = 35                              # ...heading for the bar

r6 = Run(track_at(ANCHORAGE))
r6(ANCHORAGE, 0.2, 1)
ev = r6(APPROACH, 5.0, 0, cog=COURSE_IN)
if not ev or ev["kind"] != "coming":
    fails.append("no warning on the run in to Sanlúcar - four and a half miles out at"
                 " five knots is the better part of an hour, which is the whole point")
else:
    title, body, line = update.move_words(ev)
    if not 30 <= ev["mins_out"] <= 75:
        fails.append(f"the warning says {ev['mins_out']} minutes for 4.5 nm at 5 knots")
    if "minutes from" not in title or "Sanlúcar" not in title:
        fails.append(f"the title does not say how long or where: {title!r}")
    if "Bajo de Guía" not in body:
        fails.append(f"it does not name the camera that will see her: {body!r}")
    if len(line) > 160:
        fails.append(f"the lock-screen line is too long to read: {len(line)} characters")

# Closer in, and then right on top of it: already told, so nothing more.
before = len(r6.sent)
r6(out_at(3.0, 215), 5.0, 0, cog=COURSE_IN)
r6(out_at(0.6, 215), 5.0, 0, cog=COURSE_IN)
if len(r6.sent) != before:
    fails.append("announced the same camera again as she closed on it - one message per"
                 " approach, or the hour of warning becomes four messages")

# Well up the river, out of the band: the name is forgotten so a return trip counts again.
r6((37.05, -6.17), 5.0, 0, cog=45)
if len(r6.sent) != before:
    fails.append("something fired halfway up the river, out of reach of both cameras")

# A ship stopped off the bar waiting for the tide is not approaching anything.
r7 = Run(track_at(ANCHORAGE))
r7(ANCHORAGE, 0.2, 1)
if r7(APPROACH, 0.1, 1, cog=COURSE_IN) is not None:
    fails.append("announced a ship lying stopped off the bar as an hour from the camera")

# ...and neither is one crossing the bay on her way somewhere else entirely.
r8 = Run(track_at(ANCHORAGE))
r8(ANCHORAGE, 0.2, 1)
if r8(APPROACH, 5.0, 0, cog=215) is not None:
    fails.append("announced a ship steering away from the river as approaching it")

# Too far out to promise anything: no arrival time, no message.
r9 = Run(track_at(ANCHORAGE))
r9(ANCHORAGE, 0.2, 1)
if r9(out_at(15.0, 215), 5.0, 0, cog=COURSE_IN) is not None:
    fails.append("announced her three hours out - an hour is warning, three is noise")

print(f"ran the morning she sails and six ways of staying quiet; {len(r.sent)} messages"
      f" from the first sequence: {r.sent}; and {r6.sent} coming in")
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
