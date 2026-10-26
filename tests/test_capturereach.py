"""The camera at the mouth of a river is not fifty miles away from the ship.

On 26 October the Real Club Náutico de Sanlúcar wrote back: yes, show our camera while
the ship goes up the Guadalquivir, and yes, keep the pictures of her passing. It is the
only camera on the whole river, and the river is fifty miles long.

Their camera is filed under Sevilla, because Sevilla is the port she is bound for. The
capture gate measured her distance to SEVILLA against a nine-mile reach. Standing in over
the bar at Sanlúcar she is two miles from the lens and forty from the quay, so the gate
would have refused - and the one permission anybody has given us to KEEP frames would
have bought nothing at all. The Porto Moniz mistake of 19 October, in the capture path.

The second thing in the way was quieter: the port branch asked for `hls`, a live stream,
while the passages branch beside it and capture_frames below it both take `hls` or `img`.
Sanlúcar's is a Foscam serving one JPEG and no stream. It could have been photographed
once a round began, but it could never have begun one.

Both are measured here against the real port file.
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
SEVILLA = next(p for p in PLAN["ports"] if p["name"] == "Sevilla")
# It is not a camera AT Sevilla. It is a camera at a different place on the route, which
# she passes a day before she gets there - and the plan has had a word for that since the
# Sound of Mull. Filing it under the port was the shape of the bug as much as the
# arithmetic was.
RIVER = next(q for q in PLAN["passages"] if "Guadalquivir" in q["name"])
GRABBABLE = [c for c in RIVER["cameras"] if c.get("hls") or c.get("img")]

# Standing in over the bar, bound up the river: the hour the club said yes to.
BAR = (36.80, -6.40)
# Halfway up the river - past one camera, not yet at the other.
MIDRIVER = (37.00, -6.10)


def fix(where, sog, cog, mins_old=4):
    return {"lat": where[0], "lon": where[1], "sog_kn": sog, "cog_deg": cog,
            "seen_utc": update.iso(update.now_utc() - timedelta(minutes=mins_old))}


def got(where, sog, cog):
    port = update.capture_port(fix(where, sog, cog), PLAN, [])
    return port["name"] if port else None


# ------------------------------------------------- the camera is where the camera is
if not GRABBABLE:
    fails.append("no camera at the river mouth serves a still or a stream, so nothing on"
                 " the Guadalquivir can be photographed at all")
else:
    quay = update.nm_between(BAR, (SEVILLA["lat"], SEVILLA["lon"]))
    lens = update.cam_off(RIVER, BAR, GRABBABLE)
    if quay <= update.CAPTURE_NM:
        fails.append(f"this test has stopped testing anything: the bar is {quay:.0f} nm"
                     f" from the Sevilla quay and CAPTURE_NM is {update.CAPTURE_NM}, so"
                     f" measuring to the port would no longer refuse. Check why it moved.")
    if lens > float(RIVER.get("see_nm") or update.CAPTURE_NM):
        fails.append(f"she is {lens:.1f} nm from the nearest grabbable lens and the site"
                     f" says it can see {RIVER.get('see_nm')} nm - the camera is not"
                     f" placed, or it is placed in the wrong river")

# Standing in from the sea at five knots: this is the picture, and there is one chance.
if got(BAR, 5.0, 60) != RIVER["name"]:
    fails.append(f"no capture over the bar at Sanlúcar, where she is"
                 f" {update.cam_off(RIVER, BAR, GRABBABLE):.1f} nm from the club's camera"
                 f" - measured to the quay in Sevilla she is forty, which is the mistake")

# ...and a still with no stream behind it has to be enough to start the round.
if not any(c.get("img") and not c.get("hls") for c in GRABBABLE):
    fails.append("the Sanlúcar camera no longer offers a plain still - if it moved to a"
                 " stream, this test should be rewritten rather than deleted")

# ------------------------------------------------------------- and the quiet cases
if got(MIDRIVER, 5.0, 45) is not None:
    fails.append("capturing halfway up the river, out of reach of both cameras - the"
                 " nearest lens test has turned into no test at all")
if got(BAR, 0.2, 60) is not None:
    fails.append("capturing a ship that is not moving: the rule is an arrival or a"
                 " departure, not a ship sitting off the bar waiting for the tide")

# Going out again, down the river, is as worth a picture as coming in.
if got(BAR, 5.0, 240) != RIVER["name"]:
    fails.append("no capture on the way OUT past Sanlúcar - leaving is as photogenic as"
                 " arriving, and the rule is meant to be the same rule")

# --------------------------------------------------------------- the promise of a credit
# Two clubs have now lent us a camera on the condition that they are named, and the page
# builds that line from this field rather than from a name written into the code. A
# permission with nobody to credit, or nobody to link to, is a promise we would quietly
# stop keeping.
for port in PLAN["ports"] + (PLAN.get("passages") or []):
    for c in port.get("cameras") or []:
        if not c.get("permission"):
            continue
        if not c.get("by"):
            fails.append(f"{port['name']}/{c['name']} has permission recorded but nobody"
                         f" to credit for it")
        if not (c.get("site") or c.get("url")):
            fails.append(f"{port['name']}/{c['name']} has permission recorded but no link"
                         f" to send anyone to")
if not any(c.get("permission") for c in RIVER["cameras"]
           if "Sanlúcar" in c.get("by", "")):
    fails.append("the club's permission for the Guadalquivir is no longer recorded against"
                 " their camera, so the page will stop crediting them for it")

print(f"over the bar at Sanlúcar: {update.cam_off(RIVER, BAR, GRABBABLE):.1f} nm from the"
      f" lens, {update.nm_between(BAR, (SEVILLA['lat'], SEVILLA['lon'])):.0f} nm from the"
      f" quay in Sevilla, reach {RIVER.get('see_nm')} nm")
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
