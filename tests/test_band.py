"""A frame of a garden with a ship in the corner is not a picture of a ship.

The camera at Bajo de Guia is mounted at the sailing club, and most of what it sees is the
club: lawn, hedge, paving, boats on trailers. The water is a band across the top, about a
quarter of the height. She crosses that band turning in from the sea, and at a mile and a
half she is around sixty pixels long in a frame two and a half thousand wide. Whole, on a
phone, she is a speck in a photograph of a garden.

So a camera can declare the strip worth keeping. This checks the three things that matter:
the strip is actually cut, the clock the club prints into the top corner survives the cut,
and every way of getting the band wrong leaves the frame exactly as it arrived - because a
smaller picture is a convenience and a missing one is a gap in the record.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import update as U  # noqa: E402

try:
    from PIL import Image
except Exception:
    print("FAILURES")
    print("  - Pillow is not installed, so the crop cannot be checked at all")
    sys.exit(1)

fails = []
BOX = Path(tempfile.mkdtemp())
W, H = 2592, 1944                      # the size the club's camera actually sends
BAND = [0.0, 0.0, 1.0, 0.42]           # what ports.json gives Bajo de Guia


def a_frame(name="f.jpg"):
    """A frame shaped like theirs: a bright mark in the top corner for the clock, a dark
    one far down in the garden, so the test can see which survived."""
    im = Image.new("RGB", (W, H), (120, 150, 180))
    for x in range(W - 400, W - 40):           # the clock, top right
        for y in range(30, 90):
            im.putpixel((x, y), (255, 255, 255))
    for x in range(200, 600):                  # the lawn, well below the water
        for y in range(1500, 1700):
            im.putpixel((x, y), (0, 0, 0))
    p = BOX / name
    im.save(p, "JPEG", quality=90)
    return p


def corner(path, box):
    with Image.open(path) as im:
        return im.crop(box).convert("L").getextrema()


# 1. The strip is cut, and it is the strip the band asked for.
f = a_frame()
if not U.crop_to_band(f, BAND):
    fails.append("the crop refused a frame and a band that are both fine")
with Image.open(f) as im:
    got = (im.width, im.height)
want_h = round(0.42 * H)
if got[0] != W or abs(got[1] - want_h) > 2:
    fails.append(f"cropped to {got}, wanted {W}x{want_h}")

# 2. The clock survives. It lives in the top corner, which is why the band starts at zero.
if corner(f, (W - 400, 30, W - 40, 90))[1] < 200:
    fails.append("the clock in the top corner did not survive the crop - a frame that"
                 " cannot say when it was taken is worth much less than one that can")

# 3. ...and the garden is gone, which is the whole point.
with Image.open(f) as im:
    dark = im.convert("L").getextrema()[0]
if dark < 40:
    fails.append("something as dark as the lawn is still in the frame - the crop did not"
                 " take the garden out")

# 4. Every way of being wrong leaves the frame alone.
for label, band in (("not four numbers", [0.0, 0.0, 1.0]),
                    ("strings", ["a", "b", "c", "d"]),
                    ("out of order", [0.0, 0.8, 1.0, 0.2]),
                    ("beyond the frame", [0.0, 0.0, 1.4, 0.4]),
                    ("too thin to see a ship in", [0.0, 0.0, 1.0, 0.01]),
                    ("nothing at all", None)):
    g = a_frame(f"bad-{abs(hash(label))}.jpg")
    before = g.read_bytes()
    try:
        took = U.crop_to_band(g, band)
    except Exception as exc:
        fails.append(f"{label}: raised {type(exc).__name__}: {exc}")
        continue
    if took:
        fails.append(f"{label}: the crop went ahead on a band it should have refused")
    if g.read_bytes() != before:
        fails.append(f"{label}: refused the band but changed the file anyway")

# 5. A file that is not a picture must not be mangled either.
junk = BOX / "junk.jpg"
junk.write_bytes(b"this is not a jpeg")
if U.crop_to_band(junk, BAND) or junk.read_bytes() != b"this is not a jpeg":
    fails.append("a file that is not a picture was not left alone")

print(f"2592x1944 cropped to {got[0]}x{got[1]}; clock kept, garden gone;"
      f" 6 bad bands refused without touching the file.")
if fails:
    print("FAILURES")
    for x in fails:
        print("  -", x)
    sys.exit(1)
print("ALL CHECKS PASSED")
