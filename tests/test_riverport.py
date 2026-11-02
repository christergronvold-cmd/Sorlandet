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
#
#    Sevilla's list is now the whole river, so a port without a declared reach still keeps
#    the marks near the quay - as it should. What it must NOT keep is anything beyond the
#    default thirty miles, and the mark at the bar is forty-three out.
coastal = dict(SEVILLA)
coastal.pop("approach_nm", None)
too_far = [p for p in update.approach_waypoints(coastal, AT_SEA)
           if update.nm_between(p, QUAY) > update.APPROACH_NM]
if too_far:
    fails.append(f"a port that does NOT declare a reach kept {too_far} as pilotage, each"
                 f" more than {update.APPROACH_NM:.0f} nm from the quay - the fix was made"
                 f" by moving the constant, not by asking the port")

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

# 5. The pilotage must be the river, not a ruler laid over it. One straight leg from the
#    bar to the quay is 43 nm across the Doñana marshes; the river is about 54, and no
#    single reach of it runs much over ten miles. A chain that collapses back towards the
#    straight line has stopped describing the Guadalquivir.
run_in = [p for p in kept] + [QUAY]
legs = [update.nm_between(run_in[i - 1], run_in[i]) for i in range(1, len(run_in))]
winding = sum(legs) / update.nm_between(MOUTH, QUAY)
if winding < 1.15:
    fails.append(f"the run in is {sum(legs):.0f} nm against {update.nm_between(MOUTH, QUAY):.0f}"
                 f" nm straight ({winding:.2f}x) - that is a ruler, not a river")
if max(legs) > 12:
    fails.append(f"one leg of the river is {max(legs):.0f} nm long - at that length it is"
                 f" cutting across a bend rather than following it")

# 6. ...and it must be the way IN, not the way past.
#
#    At 37.315 the water forks. One branch is also called Rio Guadalquivir and carries on
#    north past the city on the west side; the other goes through the Nueva Esclusa at
#    37.319 and up the dock to the quays. The first tracing took the wrong one, and the
#    last leg went from the canal across Triana into the dock - "siste biten der, over
#    land er litt feil". The lock is the thing that tells the two apart.
LOCK = (37.31895, -6.00574)          # Nueva Esclusa, from OpenStreetMap
SAL = (37.37237, -5.99198)           # Muelle de las Delicias, where she actually lies
past_lock = min(update.nm_between(LOCK, p) for p in kept)
if past_lock > 0.6:
    fails.append(f"the run in never comes within {past_lock:.1f} nm of the lock at Sevilla"
                 f" - it is going up the branch that passes the city, not into the dock")
ends_at = update.nm_between(kept[-1], SAL)
if ends_at > 0.15:
    fails.append(f"the line stops {ends_at * 1852:.0f} m from Muelle de las Delicias - it"
                 f" should end in the channel abeam the quay she lies at, neither short of"
                 f" it nor a mile past it up at the city bridges")

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
