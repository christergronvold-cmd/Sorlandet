"""The last evening alongside must not be one tile of wind over an empty quay.

The forecast card has two jobs, and which one it is doing depends on where she is. In
port it stands still at the berth and describes the week she is having there. At sea it
walks along her course and describes the passage. The switch between them is the line
`holding`, and it was drawn in the wrong place.

While she is alongside the ladder runs only to the end of her departure day. On the night
of 28 October the file was written at 23:47 - twelve minutes of span - so the rungs came
out as [0, 6] and seventy-two families got a card with ONE tile on it: two metres a second
over the quay in Funchal, for a ship that sailed the next morning. "Denne er jo helt
borte ??? Værmelding for de neste dagene på turen."

The branch is still right and still needed: it exists so that a ship tied up in Lerwick
for another three weeks is not drawn 542 miles of dashed line to Dublin. But six hours
before she sails, that line is not a forecast of something that has not begun. It is
where she is going tonight.

So: hold while there is a stay left to describe, and once there is not, forecast the
passage. This checks both halves, because a fix that simply stopped holding would pass
the first and throw away the second.
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import update as U  # noqa: E402

PLAN = json.loads((ROOT / "data" / "ports.json").read_text())
SEVILLA = next(p for p in PLAN["ports"] if p["name"] == "Sevilla")
BERTH = (32.6422, -16.9043)                       # the quay in Funchal, where she lay
fails = []


def check(label, got, want):
    if got != want:
        fails.append(f"{label}: got {got!r}, wanted {want!r}")


def card(when: datetime, depart: str):
    """The forecast card as it would be written at `when`, with her alongside until
    `depart`."""
    here = {"name": "Funchal", "country": "Madeira", "lat": BERTH[0], "lon": BERTH[1],
            "depart": depart}
    wrote = {}
    keep = (U.now_utc, U.today_iso, U.voyage_state, U.next_port, U.fetch_ahead,
            U.write_json, U.COURSE)
    try:
        U.now_utc = lambda: when
        U.today_iso = lambda: when.strftime("%Y-%m-%d")
        U.voyage_state = lambda ports, points=None: {"in_port": here, "to": SEVILLA,
                                                     "done": 1}
        U.next_port = lambda ports, points=None: SEVILLA
        U.fetch_ahead = lambda pts: [dict(q, wind_ms=2.0) for q in pts]
        U.write_json = lambda p, d: wrote.update({"doc": d})
        U.COURSE = Path(tempfile.mkdtemp()) / "course.json"   # no sailing course: A* only
        U._build_ahead(BERTH[0], BERTH[1], 0.0, [
            {"t": U.iso(when - timedelta(minutes=m)), "lat": BERTH[0], "lon": BERTH[1],
             "sog": 0.0} for m in (120, 60, 20)])
    finally:
        (U.now_utc, U.today_iso, U.voyage_state, U.next_port, U.fetch_ahead,
         U.write_json, U.COURSE) = keep
    return wrote.get("doc")


# 1. The night she sailed: 23:47, twelve minutes of her departure day left.
late = card(datetime(2026, 10, 28, 23, 47, tzinfo=timezone.utc), "2026-10-28")
if not late:
    fails.append("nothing written at all on her last evening")
else:
    rungs = [q["hours"] for q in late.get("points") or []]
    if late.get("to") != "Sevilla":
        fails.append(f"card still headed for {late.get('to')!r} - on the night she sails,"
                     f" the forecast that matters is the passage")
    if len(rungs) < 6:
        fails.append(f"only {len(rungs)} rungs on the ladder ({rungs}) - this is the one"
                     f" tile in the screenshot")
    if late.get("holding_until"):
        fails.append("still marked as holding alongside with twelve minutes of the stay"
                     " left")
    if not late.get("route"):
        fails.append("no route drawn, so the ladder has nowhere to stand along")

# 2. ...but three days earlier she IS alongside, and the card must say so. A fix that
#    simply stopped holding would pass check 1 and put 542 nm of dashed line on the map
#    of a ship that is not going anywhere for days.
early = card(datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc), "2026-10-28")
if not early:
    fails.append("nothing written three days before she sails")
else:
    check("three days out: holding", bool(early.get("holding_until")), True)
    check("three days out: no route drawn", bool(early.get("route")), False)
    check("three days out: the ladder stands in port",
          {q.get("where") for q in early.get("points") or []}, {"port"})
    at_berth = all(abs(q["lat"] - BERTH[0]) < 1e-6 and abs(q["lon"] - BERTH[1]) < 1e-6
                   for q in early.get("points") or [])
    check("three days out: every rung at the berth", at_berth, True)

# 3. ...and the stay's own ladder never collapses below a rung a reader can use, however
#    the dates are written.
odd = card(datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc), "2026-10-29")
if odd and len([q["hours"] for q in odd.get("points") or []]) < 2:
    fails.append("a stay with hours left still produced a ladder with nothing on it")

print(f"last evening: to {late.get('to') if late else None},"
      f" {len(late.get('points') or []) if late else 0} rungs."
      f" Three days out: holding until {early.get('holding_until') if early else None},"
      f" {len(early.get('points') or []) if early else 0} rungs at the berth.")
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
