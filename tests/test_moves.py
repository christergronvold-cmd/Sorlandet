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


def fix(where, sog=None, ns=None, mins_old=5):
    return {"lat": where[0], "lon": where[1], "sog_kn": sog, "nav_status": ns,
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

    def __call__(self, where, sog=None, ns=None, plan=None):
        self.state, ev = update.move_event(self.state, fix(where, sog, ns),
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

# A feed that goes quiet and comes back is not an event either: no speed, nothing said.
r4 = Run(track_at(ANCHORAGE))
r4(ANCHORAGE, 0.2, 1)
if r4(ANCHORAGE, None, None) is not None:
    fails.append("treated a bare position with no speed and no status as a change of"
                 " state - it is not an answer, and the page says so everywhere else")

# ...and the dead zone between the two thresholds is silence, not a guess.
r5 = Run(track_at(ANCHORAGE))
r5(ANCHORAGE, 0.2, 1)
if r5((ANCHORAGE[0] + 0.02, ANCHORAGE[1]), 0.7, None) is not None:
    fails.append("0.7 knots with nothing said is neither under way nor stopped, and must"
                 " not be reported as either")

# ------------------------------------------------------- the one he asked for by name
# "...naar baaten gaar inn ved Sanlucar." It would not have fired. The club's camera sits
# at the mouth of the Guadalquivir, fifty miles below the quay in Sevilla, so by the port
# test a ship turning in over the bar is at sea and nothing happens - on the one day, and
# at the one place, where the only camera on the river can see her.
BAR = (36.80, -6.37)                        # standing in over the bar, 1.3 nm off the club
UPRIVER = (37.05, -6.17)                    # well up the river, out of its reach

r6 = Run(track_at(ANCHORAGE))
r6(ANCHORAGE, 0.2, 1)
ev = r6(BAR, 5.0, 0)
if not ev or ev["kind"] != "passing":
    fails.append("no message when she turns in at Sanlúcar, which is the one he asked for"
                 " by name - and the only camera on fifty miles of river")
else:
    title, body, line = update.move_words(ev)
    if "passing" not in title or "Guadalquivir" not in title:
        fails.append(f"the message does not say what she is passing: {title!r}")
    if "Bajo de Guía" not in body:
        fails.append(f"it does not say which camera is looking at her: {body!r}")

before = len(r6.sent)
r6((36.795, -6.365), 5.0, 0)
r6((36.81, -6.36), 5.0, 0)
if len(r6.sent) != before:
    fails.append("announced the same camera again while she was still in front of it -"
                 " a ship standing up a river must not buzz every half hour")

r6(UPRIVER, 5.0, 0)
if len(r6.sent) != before:
    fails.append("something fired on the way up the river, out of reach of both cameras")

# ...and a ship stopped at the bar, waiting for the tide, is not passing anything.
r7 = Run(track_at(ANCHORAGE))
r7(ANCHORAGE, 0.2, 1)
if r7(BAR, 0.1, 1) is not None:
    fails.append("announced a ship lying stopped at the bar as passing the camera")

print(f"ran the morning she sails and six ways of staying quiet; {len(r.sent)} messages"
      f" from the first sequence: {r.sent}; and {r6.sent} at the river mouth")
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
