"""The ship marker must not invent a heading it does not have.

The foundation's feed carries positions only, and the job refuses to work a course out of
two fixes more than three hours apart. On 21 September 2026 the gap was eleven and a half
hours: heading and course both came through empty, `rotate(heading ?? 0)` became rotate(0),
which is due north, and the arrow pointed back the way she came while she had been steering
180 all night. The first thing it made a parent ask was whether she had turned round.

Zero is a real heading and must still draw an arrow. It is only the confusion of "north"
with "we do not know" that was the bug.
"""
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "index.html").read_text()

m = re.search(r"^const shipIcon = .*?^};", SRC, re.S | re.M)
if not m:
    print("FAILURES:\n  - could not find shipIcon in index.html")
    sys.exit(1)
FN = m.group(0)

fails = []


def check(label, got, want):
    if got != want:
        fails.append(f"{label}: got {got!r}, wanted {want!r}")


with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--no-sandbox"])
    pg = b.new_page()
    pg.set_content("<!doctype html><title>t</title>")
    # Leaflet is not here and is not the point: divIcon hands back what it was given.
    pg.evaluate("() => { window.L = { divIcon: o => o }; }")
    pg.add_script_tag(content=FN + "\nwindow.shipIcon = shipIcon;")

    def icon(heading, ghost=False):
        arg = "null" if heading is None else repr(heading)
        return pg.evaluate(f"() => shipIcon({arg}, {str(ghost).lower()}).html")

    # --- a real heading draws the arrow, turned to it ------------------------------------
    for h in (0, 90, 180, 270, 359, 5.4):
        html = icon(h)
        check(f"heading {h}: has the arrow", "<path" in html, True)
        check(f"heading {h}: rotated to it", f"rotate({h}deg)" in html, True)

    check("north is a heading, not a blank", "<path" in icon(0), True)

    # --- no heading draws no arrow -------------------------------------------------------
    html = icon(None)
    check("null: no arrow", "<path" in html, False)
    check("null: no rotation at all", "rotate(" in html, False)
    check("null: still a circle", "<circle" in html, True)

    u = pg.evaluate("() => shipIcon(undefined, false).html")
    check("undefined: no arrow", "<path" in u, False)
    check("undefined: no rotation", "rotate(" in u, False)

    # NaN is what a bad parse leaves behind, and it is not a direction either.
    nan = pg.evaluate("() => shipIcon(NaN, false).html")
    check("NaN: no arrow", "<path" in nan, False)

    # --- the ghost marker follows the same rule ------------------------------------------
    # It is drawn from a remembered fix (was.cog), which can be missing for the same reason.
    check("ghost with a course keeps its arrow", "<path" in icon(120, True), True)
    check("ghost without one loses it", "<path" in icon(None, True), False)
    check("ghost is still the ghost colour", "#e0803a" in icon(None, True), True)
    check("ship is still the ship colour", "#0b6ea8" in icon(None, False), True)

    # Whatever else changes, the marker stays a marker: same box, same anchor, so a dot
    # without an arrow sits exactly where the arrow used to.
    size = pg.evaluate("() => JSON.stringify([shipIcon(null,false).iconSize,"
                       " shipIcon(null,false).iconAnchor,"
                       " shipIcon(90,false).iconSize, shipIcon(90,false).iconAnchor])")
    check("box and anchor unchanged", size, "[[34,34],[17,17],[34,34],[17,17]]")

    b.close()

if fails:
    print("FAILURES:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
