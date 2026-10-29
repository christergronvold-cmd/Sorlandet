"""A port forty-two miles up a river must still be reachable.

On 29 October Christer sent a screenshot of the forecast card. It read ON THE WAY TO
FUNCHAL, with a weather ladder standing at the quay in Funchal and "NaN nm to run" - about
a ship that had sailed from there the day before and was by then a hundred and fifteen
miles out into the Atlantic, bound for Sevilla. "nå er denne tullete." It had been wrong
for fourteen hours.

The file had not been written since 23:47 the night before, and the reason was this:

    ! the sailing course to Sevilla runs over land on leg 57 of 57
    ! the route to the approach crosses land on leg 2 - not drawing a line
    ! could not route to the next port

Both branches of the router failed, on the same leg, for the same reason. Sevilla is not
on the coast. It is forty-two miles up the Guadalquivir, and the sea mask is a six
kilometre grid that cannot see a river at all. The one piece of pilotage anybody had
written for the run in - a mark at the river mouth - was being discarded before either
branch saw it, because approach_waypoints called anything more than thirty miles from the
quay "the ocean crossing, none of the router's business". Thirty miles is the right reach
for a harbour on the coast. It is the wrong reach for a river.

So the search was left aiming at a quay fifty miles inland. A* snapped the goal to the
nearest sea cell - the mouth - appended the quay to the end unchecked, and then every land
test from here to Spain threw the whole line away, correctly, as a line over land.

Three things had to change, and this checks all three:

  - how far the pilotage reaches is the PORT's business, not a constant's;
  - a sailing course whose ONLY bad leg is the goal pinned on the end has not lied, it has
    stopped short, and the pilotage can walk the rest of the way in;
  - the last gate judges the water, not the pilotage - but it must still bite everywhere
    else, which is what check 4 is for.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import update  # noqa: E402

fails = []
PLAN = json.loads((ROOT / "data" / "ports.json").read_text())
SEVILLA = next(p for p in PLAN["ports"] if p["name"] == "Sevilla")
QUAY = (float(SEVILLA["lat"]), float(SEVILLA["lon"]))
MOUTH = (36.8, -6.5)                     # the approach mark, 42 nm below the city
AT_SEA = (33.97, -14.09)                 # where she was when he sent the screenshot

# A track that is only at sea. The ports she has already called at are not the subject
# here, so next_port is answered directly below and this only has to keep her off a quay.
TRACK = [{"t": "2026-10-29T0%d:00:00Z" % h, "lat": 33.6 + h * 0.06, "lon": -15.4 + h * 0.2,
          "sog": 5.2, "cog": 60} for h in range(1, 9)]


def build(port: dict, course_points: list | None):
    """Run the router for one port and hand back what it wrote, if anything."""
    wrote = {}
    keep = (update.next_port, update.fetch_ahead, update.write_json, update.COURSE)
    with tempfile.TemporaryDirectory() as box:
        tmp = Path(box) / "course.json"
        if course_points is not None:
            tmp.write_text(json.dumps({
                "generated_utc": update.iso(update.now_utc()),
                "to": port["name"], "tacks": [1, 2],
                "points": [{"lat": a, "lon": b} for a, b in course_points]}))
        try:
            update.next_port = lambda ports, points=None: port
            update.fetch_ahead = lambda pts: [dict(q, wind_ms=0.0) for q in pts]
            update.write_json = lambda p, d: wrote.update({"path": p, "doc": d})
            update.COURSE = tmp
            update._build_ahead(AT_SEA[0], AT_SEA[1], 5.2, TRACK)
        finally:
            update.next_port, update.fetch_ahead, update.write_json, update.COURSE = keep
    return wrote.get("doc")


# The real sailing course, as the router wrote it that morning: honest sea all the way,
# stopping 72 nm short of the city because the wind forecast ran out, with the goal pinned
# on the end. That last leg is not a course. It is a label saying "and then Sevilla".
SEA_COURSE = [AT_SEA, (35.1, -11.0), (36.2, -8.6), (36.7876, -7.581), (36.8889, -7.3611),
              QUAY]

# 1. The pilotage survives, because the port says how far its approach reaches.
kept = update.approach_waypoints(SEVILLA, AT_SEA)
if MOUTH not in [(round(a, 4), round(b, 4)) for a, b in kept]:
    fails.append(f"Sevilla kept {kept} as pilotage - the mark at the river mouth was"
                 f" dropped, which is the whole fault: {update.nm_between(MOUTH, QUAY):.0f}"
                 f" nm from the quay against a {update.APPROACH_NM:.0f} nm default reach")

# 2. ...and the default is untouched. Raising the constant for everybody would have passed
#    check 1 and quietly turned ocean waypoints into approaches at every other port.
coastal = dict(SEVILLA)
coastal.pop("approach_nm", None)
if update.approach_waypoints(coastal, AT_SEA):
    fails.append("a port that does NOT declare a reach still kept a 42 nm waypoint as"
                 " pilotage - the fix was made by moving the constant, not by asking"
                 " the port")

# 3. The router gets her to Sevilla, along her own course rather than a ruled line.
doc = build(SEVILLA, SEA_COURSE)
if not doc:
    fails.append("nothing written for Sevilla at all - this is the screenshot")
else:
    route = [tuple(q) for q in doc.get("route") or []]
    if doc.get("to") != "Sevilla":
        fails.append(f"headed for {doc.get('to')!r}, not Sevilla")
    if doc.get("route_basis") != "course":
        fails.append(f"drew a {doc.get('route_basis')!r} line - fifty-seven good legs of"
                     f" tacking were thrown away over the one leg on the end")
    if not route or update.nm_between(route[-1], QUAY) > 0.5:
        fails.append(f"the line does not end at the quay (ends {route[-1] if route else None})")
    if route and min(update.nm_between(p, MOUTH) for p in route) > 1.0:
        fails.append("the line reaches Sevilla without passing the river mouth - it is not"
                     " walking in along the pilotage")
    if not doc.get("points"):
        fails.append("no weather ladder - the card would have nothing to show")
    if doc.get("distance_nm") is None:
        fails.append("no distance_nm - this is what printed NaN nm to run")

# 4. The exception is for the goal on the end, and nothing else. A course that crosses a
#    country in the MIDDLE is still a course that lies, and is still refused.
OVER_SPAIN = [AT_SEA, (36.0, -9.0), (38.6, -5.2), (37.9, -5.6), QUAY]   # leg 2 inland
doc_bad = build(SEVILLA, OVER_SPAIN)
if doc_bad and doc_bad.get("route_basis") == "course":
    fails.append("a course running across Andalusia was spliced and drawn - the exception"
                 " has become a blanket, and the land gate no longer means anything")

print(f"Sevilla: pilotage {[(round(a,3), round(b,3)) for a, b in kept]},"
      f" {update.nm_between(MOUTH, QUAY):.0f} nm of river;"
      f" route {doc.get('distance_nm') if doc else None} nm along"
      f" {doc.get('route_basis') if doc else None},"
      f" {len(doc.get('points') or []) if doc else 0} forecast points.")
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
