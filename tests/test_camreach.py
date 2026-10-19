"""How far out the camera section opens, and whether it opens in time to be any use.

The gate was a single radius: nine miles from the port's own coordinates. That was right
for as long as every camera on the page stood inside the harbour it belonged to, because
then the distance to the port WAS the distance to the lens.

Funchal broke it. On 9 October five Madeira cameras went in, two of them round the eastern
corner of the island at Caniçal and Machico, ten and eleven miles from the quay. She
passes closest to Caniçal twelve and a half miles out from Funchal - so the section,
measured from the quay, was still shut while she was in full view of it, and only opened
after she had rounded the corner and left it astern. The two cameras added to catch her
first were the two that could not.

Two checks, because one alone would not have caught it:

  1. The live page, driven to the position where she passes Caniçal, must actually show
     the section. This is the behaviour; everything else is a proxy for it.

  2. A port must place all of its cameras or none. Reach is per camera now: a camera with
     a position opens when she comes within CAM_SEE_NM of IT. But a port where only some
     carry one falls back to the old port radius for the gate and turns off the
     nearest-camera ordering, silently, and the half that are placed look like they work.
"""
import http.server
import json
import math
import shutil
import socketserver
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
fails = []


def nm(a, b):
    R = 3440.065
    la1, la2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin(dl / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(h))


def default_see_nm():
    """Read the default out of the page rather than repeating it here."""
    for line in (ROOT / "index.html").read_text(encoding="utf-8").splitlines():
        if "const CAM_SEE_NM" in line:
            return float(line.split("=")[1].split(";")[0])
    fails.append("index.html no longer defines CAM_SEE_NM")
    return 9.0


# ------------------------------------------------------------ 2. placed all, or none
DEFAULT_SEE = default_see_nm()
plan = json.loads((ROOT / "data" / "ports.json").read_text(encoding="utf-8"))
placed = 0
for port in plan["ports"]:
    cams = port.get("cameras") or []
    have = [c for c in cams if c.get("lat") is not None and c.get("lon") is not None]
    placed += len(have)
    if have and len(have) != len(cams):
        missing = [c["short"] for c in cams if c.get("lat") is None]
        fails.append(
            f"{port['name']}: {len(have)} of {len(cams)} cameras have a position. Mixed is "
            f"the worst case - the port falls back to its own radius and the nearest-camera "
            f"ordering switches off. Place or remove: {', '.join(missing)}")
    for c in have:
        away = nm((port["lat"], port["lon"]), (c["lat"], c["lon"]))
        if away > 60:
            fails.append(f"{port['name']} / {c['short']}: {away:.0f} nm from the port. That "
                         f"is further than a camera belonging to it should be - check the "
                         f"coordinates.")

# ---------------------------------------------------------- 1. the page, at the real spot
# Where she passes closest to Caniçal, worked out along the planned approach: six miles off
# the camera and twelve and a half from the quay, steering for Funchal.
HER = (32.6581, -16.6628)
COG, SOG = 266, 7.6
WANT = "Caniçal"

# The whole site, not just the page: leaflet is vendored in the repo, and a tree without
# vendor/ fails with "L is not defined" before anything is drawn.
tmp = Path(tempfile.mkdtemp()) / "site"
shutil.copytree(ROOT, tmp,
                ignore=shutil.ignore_patterns(".git", "tests", "scripts", "__pycache__"))
now = datetime.now(timezone.utc)

# Optional feeds the page shrugs off. Dropping them keeps the test about one thing.
for junk in ("ahead.json", "wake.json", "course.json", "orbit.json", "shore.json",
             "wind.json", "waves.json", "history.json"):
    (tmp / "data" / junk).unlink(missing_ok=True)

# Pin the calendar so the test means the same thing in March as it does today: every port
# before Funchal is behind her, Funchal is four days ahead, the rest are further out.
tp = json.loads((tmp / "data" / "ports.json").read_text(encoding="utf-8"))
names = [q["name"] for q in tp["ports"]]
if "Funchal" not in names:
    fails.append("ports.json no longer has Funchal; this test needs rewriting")
    here = 0
else:
    here = names.index("Funchal")
for i, q in enumerate(tp["ports"]):
    day = now + timedelta(days=(i - here) * 20 + 4)
    q["arrive"] = day.strftime("%Y-%m-%d")
    q["depart"] = (day + timedelta(days=3)).strftime("%Y-%m-%d")
(tmp / "data" / "ports.json").write_text(json.dumps(tp), encoding="utf-8")

latest = json.loads((tmp / "data" / "latest.json").read_text(encoding="utf-8"))
latest["position"] = {"lat": HER[0], "lon": HER[1], "sog_kn": SOG, "cog_deg": COG,
                      "heading_deg": COG,
                      "seen_utc": (now - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "source": "test"}
latest["updated_utc"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
latest["fresh_fix"] = True
(tmp / "data" / "latest.json").write_text(json.dumps(latest), encoding="utf-8")

# A short track ending where she is now, so the page reads her as at sea and closing.
pts = [{"t": (now - timedelta(hours=3 - k)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lat": HER[0] + (3 - k) * 0.12, "lon": HER[1] + (3 - k) * 0.18,
        "sog": SOG, "cog": COG} for k in range(4)]
(tmp / "data" / "track.json").write_text(json.dumps({"points": pts}), encoding="utf-8")


class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(tmp), **k)

    def log_message(self, *a):
        pass


socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()

with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1280, "height": 1400})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    # Tiles and the counter are unreachable from here and beside the point.
    for pattern in ("**://*.tile.openstreetmap.org/**", "**://tiles.openseamap.org/**",
                    "**://gibs.earthdata.nasa.gov/**", "**://gc.zgo.at/**"):
        pg.route(pattern, lambda r: r.abort())
    pg.goto(f"http://127.0.0.1:{PORT}/index.html", wait_until="networkidle")
    pg.wait_for_timeout(2500)
    body = pg.inner_text("body")
    b.close()
httpd.shutdown()
shutil.rmtree(tmp.parent, ignore_errors=True)

if errors:
    fails.append(f"the page threw while rendering: {errors[0][:160]}")
if WANT not in body:
    fails.append(
        f"she is 6 nm off {WANT} and 12.5 nm from Funchal, steering for it at {SOG} kn, "
        f"and the camera section is shut - '{WANT}' is nowhere on the page")

print(f"{placed} placed cameras checked, default lens reach {DEFAULT_SEE:.0f} nm,"
      f" and the page driven to 32.658N 16.663W")
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
