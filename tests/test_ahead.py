"""A forecast file that cannot be rewritten must be emptied, not left lying there.

29 October. The page was headed ON THE WAY TO FUNCHAL, with a weather ladder taken at the
quay and "NaN nm to run", about a ship a hundred and fifteen miles past the island and
making for Spain. data/ahead.json was from 23:47 the night before and had not moved since.

The cause was a guard meant to prevent exactly this. It clears a stale ROUTE when a round
cannot produce a new one - written in September, after a dashed line from 31 August sat on
the map for two days. But it began:

    if not before.get("route"):
        return                       # nothing stale to take out

and once the file had no route in it, that was every round for ever. The file froze whole:
the port name, the ladder, the holding note. The route was never the only perishable thing
in there; all of it is a forecast about where she will be.

These tests drive the wrapper with the work beneath it stubbed out, because the question
is not whether a route can be computed - it is what happens to the file when one cannot.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import update  # noqa: E402

fails = []
STALE = {"generated_utc": "2026-10-28T23:47:03Z", "to": "Funchal", "country": "Madeira",
         "holding_until": "2026-10-28", "near_nm": 0.3,
         "points": [{"hours": 0, "lat": 32.6422, "lon": -16.9043, "where": "port"},
                    {"hours": 6, "wind_ms": 2.2, "where": "port"}]}


def run(before, writes=None):
    """One call of the wrapper, with the real work replaced. Returns the file after."""
    update.AHEAD = Path(tempfile.mkdtemp()) / "ahead.json"
    if before is not None:
        update.write_json(update.AHEAD, before)
    real = update._build_ahead
    update._build_ahead = (lambda *a, **k: update.write_json(update.AHEAD, writes)) \
        if writes is not None else (lambda *a, **k: None)
    try:
        update.build_ahead(33.44, -14.84, None, [])
    finally:
        update._build_ahead = real
    return update.read_json(update.AHEAD, None)


# ------------------------------------------------- the file that would not let go
after = run(STALE)
if after is None:
    fails.append("the file was deleted outright; the page reads it and must find JSON")
else:
    if after.get("to"):
        fails.append(f"the round wrote nothing and the file still names a port"
                     f" ({after['to']}) - that is ON THE WAY TO FUNCHAL, fourteen hours"
                     f" after she left it")
    if after.get("points"):
        fails.append("the weather ladder from the quay is still there, and it is a"
                     " forecast for where she will be")
    if after.get("generated_utc") == STALE["generated_utc"]:
        fails.append("the file was not even restamped, so nothing downstream can tell"
                     " that it is no longer current")

# ...and when the work below DOES write, the wrapper must keep its hands off.
good = {"generated_utc": "2026-10-29T14:00:00Z", "to": "Sevilla",
        "distance_nm": 462, "route": [[33.4, -14.8], [36.8, -6.4]],
        "points": [{"hours": 6, "wind_ms": 7}]}
after = run(STALE, writes=good)
if after != good:
    fails.append(f"the wrapper overwrote a perfectly good new route: {after}")

# A first run, with no file at all, must not invent one.
if run(None) is not None:
    fails.append("wrote a cleared file on a first run, when there was never anything here")

# Clearing twice is not a loop: the second round finds a cleared file and leaves it.
update.AHEAD = Path(tempfile.mkdtemp()) / "ahead.json"
update.write_json(update.AHEAD, STALE)
real = update._build_ahead
update._build_ahead = lambda *a, **k: None
try:
    update.build_ahead(33.44, -14.84, None, [])
    once = update.read_json(update.AHEAD, {})
    update.build_ahead(33.44, -14.84, None, [])
    twice = update.read_json(update.AHEAD, {})
finally:
    update._build_ahead = real
if twice.get("to") or twice.get("points"):
    fails.append("the second clearing put something back")

print("cleared a frozen forecast, left a good one alone, wrote nothing on a first run")
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
