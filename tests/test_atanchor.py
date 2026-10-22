"""A ship at her anchor is not a ship at the quay, whatever the radius says.

22 October 2026, mid-morning. Sørlandet lay to her anchor off the Funchal waterfront,
swinging at two tenths of a knot, and the page said "alongside now" under the marina
camera - to seventy-two families, with the lens pointed at a quay she was not at.
VesselFinder's own page said "At anchor" in the same minute. So did the ship: NAVSTAT 1,
which we had bought that morning for one credit.

The cause was BERTH_NM = 0.8. The page calls her berthed when a fix lands within eight
tenths of a mile of the harbour. The anchorage is six tenths out. She was inside the
circle.

The temptation is to shrink the circle. That is the same mistake as the camera radius in
the week before - nine, then fourteen, then twenty-two - and it ends the same way, because
no radius distinguishes a quay from an anchorage that is a few hundred metres from it. The
bridge does, by hand, every time she brings up or drops her cable, and it is broadcast.

So: where the ship has said, the ship decides. Where she has not, the circle still does -
that is every fix in the track from before this existed.

This test drives the real page from the real position, with the real port file, and will
not accept "alongside" for a ship that has just said she is at anchor.
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
# The AIS fix of 22 Oct 05:42 UTC: the Funchal anchorage, 0.2 kn, NAVSTAT 1.
HER = (32.64420, -16.89664)
SOG = 0.2
fails = []


def nm(a, b):
    R = 3440.065
    la1, la2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin(dl / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(h))


tmp = Path(tempfile.mkdtemp()) / "site"
shutil.copytree(ROOT, tmp,
                ignore=shutil.ignore_patterns(".git", "tests", "scripts", "__pycache__"))
now = datetime.now(timezone.utc)
for junk in ("ahead.json", "wake.json", "course.json", "orbit.json", "shore.json",
             "wind.json", "waves.json", "history.json"):
    (tmp / "data" / junk).unlink(missing_ok=True)

# Pin the calendar: she is two days into a week at Funchal, so no date rule can be the
# thing that saves or sinks this test.
tp = json.loads((tmp / "data" / "ports.json").read_text(encoding="utf-8"))
names = [q["name"] for q in tp["ports"]]
here = names.index("Funchal")
for i, q in enumerate(tp["ports"]):
    day = now + timedelta(days=(i - here) * 20 - 2)
    q["arrive"] = day.strftime("%Y-%m-%d")
    q["depart"] = (day + timedelta(days=6)).strftime("%Y-%m-%d")
funchal = tp["ports"][here]
(tmp / "data" / "ports.json").write_text(json.dumps(tp), encoding="utf-8")

QUAY = nm(HER, (funchal["lat"], funchal["lon"]))
BERTH_NM = float(next(line.split("=")[1].strip().rstrip(";")
                      for line in (ROOT / "index.html").read_text(encoding="utf-8").splitlines()
                      if line.startswith("const BERTH_NM")))
if QUAY > BERTH_NM:
    fails.append(f"this test has stopped testing anything: the anchorage is {QUAY:.2f} nm"
                 f" out and BERTH_NM is now {BERTH_NM}, so the circle no longer calls her"
                 f" berthed and the bug could not happen here. Check why it moved.")

stamp = "%Y-%m-%dT%H:%M:%SZ"
latest = json.loads((tmp / "data" / "latest.json").read_text(encoding="utf-8"))
latest["position"] = {"lat": HER[0], "lon": HER[1], "sog_kn": SOG, "cog_deg": 88,
                      "heading_deg": None, "nav_status": 1,
                      "seen_utc": (now - timedelta(minutes=20)).strftime(stamp),
                      "source": "test"}
latest["updated_utc"] = now.strftime(stamp)
latest["fresh_fix"] = True
(tmp / "data" / "latest.json").write_text(json.dumps(latest), encoding="utf-8")

# Two days swinging to the cable: enough to register as a stay, slow enough not to read
# as passing through. The ship states her status on the later fixes, as the paid source
# gives it to us - every few hours, not every fix.
pts = []
for k in range(96):
    t = now - timedelta(minutes=30 * (95 - k))
    pt = {"t": t.strftime(stamp),
          "lat": HER[0] + (k % 5 - 2) * 0.0002, "lon": HER[1] + (k % 3 - 1) * 0.0002,
          "sog": SOG, "cog": (k * 37) % 360}
    if k > 40 and k % 12 == 0:
        pt["ns"] = 1                              # at anchor, and she says so
    pts.append(pt)
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

low = body.lower()
if "alongside now" in low:
    fails.append(f"the page says \"alongside now\" about a ship lying to her anchor"
                 f" {QUAY:.2f} nm off the harbour, inside the {BERTH_NM} nm it calls a"
                 f" berth - and the ship herself is broadcasting NAVSTAT 1, at anchor")
if "at anchor" not in low and "lying off" not in low:
    fails.append("the page neither says she is alongside nor that she is at anchor off"
                 " Funchal - it has stopped saying where she is at all")

print(f"driven at the Funchal anchorage, {QUAY:.2f} nm off the harbour against a"
      f" {BERTH_NM} nm berth circle, with NAVSTAT 1 on the recent fixes")
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
