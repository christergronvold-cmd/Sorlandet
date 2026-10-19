"""The dashed route must not sail her backwards to pick up a waypoint.

A port's waypoint list ends with pilotage for the run in - a few points somebody chose by
hand to keep the line off the headlands. They were chosen for ONE direction of arrival.

On 19 October that broke in public. Funchal's approach waypoints run down the east side of
Madeira, because the plan had her coming that way. She came round the north-west corner
instead, under sail, three days early. The router dutifully aimed at the outermost
waypoint - twenty-two miles away on the far side of the island - and drew a dashed line
from her position at Porto Moniz back east around the whole island and in from the south.
Fifty-one miles of forecast to cover nineteen miles of water. Christer saw it on the map
and said it looked stupid, which it did.

So: a waypoint that makes the run in appreciably longer than the straight line is not an
approach, it is a detour, and approach_waypoints drops it. This checks that, from both
directions, because the fix must not throw away the waypoints when she DOES come the
planned way - that is what they are for.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import update  # noqa: E402

FUNCHAL = {
    "name": "Funchal", "lat": 32.645, "lon": -16.91,
    # The real list, east-about, as it stands in ports.json.
    "waypoints": [[42.2, -8.9], [32.9, -16.6], [32.7, -16.6], [32.5, -16.9]],
}
PORTO_MONIZ = (32.867, -17.173)      # north-west corner: where she actually was
NORTH_EAST = (33.20, -16.40)         # off the north-east, the way the plan had her
fails = []


def check(label, got, want):
    if got != want:
        fails.append(f"{label}: got {got!r}, wanted {want!r}")


def via_nm(frm, pts, goal):
    legs = [frm] + list(pts) + [goal]
    return sum(update.nm_between(legs[i - 1], legs[i]) for i in range(1, len(legs)))


goal = (FUNCHAL["lat"], FUNCHAL["lon"])

# 1. The ocean waypoint is never pilotage, from anywhere.
for name, frm in (("north-west", PORTO_MONIZ), ("north-east", NORTH_EAST)):
    kept = update.approach_waypoints(FUNCHAL, frm)
    if (42.2, -8.9) in kept:
        fails.append(f"from the {name}: kept the Atlantic crossing waypoint as an approach")

# 2. Coming from the north-west, nothing east of the island survives.
kept_nw = update.approach_waypoints(FUNCHAL, PORTO_MONIZ)
east = [p for p in kept_nw if p[1] > -16.75]
if east:
    fails.append(f"from Porto Moniz the route still goes via {east}, round the far side of"
                 f" Madeira - the loop this test exists to stop")

direct = update.nm_between(PORTO_MONIZ, goal)
through = via_nm(PORTO_MONIZ, kept_nw, goal)
if through > direct * update.DETOUR_MAX + 0.01:
    fails.append(f"from Porto Moniz the run in is {through:.0f} nm against {direct:.0f} nm"
                 f" direct ({through / direct:.1f}x) - still a detour")

# 3. ...but coming the planned way, the pilotage must survive. A fix that simply threw the
#    waypoints away would pass check 2 and be useless.
kept_ne = update.approach_waypoints(FUNCHAL, NORTH_EAST)
if not kept_ne:
    fails.append("from the north-east, every approach waypoint was dropped - the pilotage"
                 " has to survive the direction it was written for")

print(f"from Porto Moniz: kept {len(kept_nw)} waypoints, {through:.0f} nm against"
      f" {direct:.0f} nm direct. From the north-east: kept {len(kept_ne)}.")
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
