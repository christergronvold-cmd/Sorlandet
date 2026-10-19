"""A camera turns on when SHE is near IT, not when she is near the quay.

19 October, late morning. She came round the north-west corner of Madeira under sail,
three days early, and stood past Porto Moniz five and a half miles off the camera there.
The section on the page was shut, and it was shut for two separate reasons, both of which
were about the harbour rather than the camera:

  - she was 23 nm from the quay in Funchal and the radius was 22; and
  - she was not closing on Funchal at all - she was crossing west, away from it.

Both true. Both beside the point. A lens nineteen miles up the coast had a full-rigged
ship in it. The morning had already been spent widening that radius from nine to fourteen
to twenty-two, each time to catch where she had got to, which is the shape of a rule
measuring the wrong thing.

So the gate asks each placed camera how far away she is. This drives the real page from
the position she was actually in and insists the section is open with Porto Moniz at the
top - because the nearest camera is the one looking at her, and it is no use being open
on a tab showing the quay she will not reach for days.
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
# Where she was: AIS fix of 19 Oct 11:32 UTC, west of Porto Moniz, making 6.5 kn on 245.
HER = (32.873, -17.280)
COG, SOG = 245, 6.5
NEAR, FAR = "Porto Moniz", "Pontinha"
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

# Pin the calendar so the test means the same thing in March: Funchal four days ahead.
tp = json.loads((tmp / "data" / "ports.json").read_text(encoding="utf-8"))
names = [q["name"] for q in tp["ports"]]
here = names.index("Funchal") if "Funchal" in names else 0
for i, q in enumerate(tp["ports"]):
    day = now + timedelta(days=(i - here) * 20 + 4)
    q["arrive"] = day.strftime("%Y-%m-%d")
    q["depart"] = (day + timedelta(days=3)).strftime("%Y-%m-%d")
funchal = tp["ports"][here]
(tmp / "data" / "ports.json").write_text(json.dumps(tp), encoding="utf-8")

# The distances this test turns on, stated out loud so a failure explains itself.
quay = nm(HER, (funchal["lat"], funchal["lon"]))
lens = min(nm(HER, (c["lat"], c["lon"])) for c in funchal["cameras"]
           if c.get("lat") is not None)

latest = json.loads((tmp / "data" / "latest.json").read_text(encoding="utf-8"))
latest["position"] = {"lat": HER[0], "lon": HER[1], "sog_kn": SOG, "cog_deg": COG,
                      "heading_deg": COG,
                      "seen_utc": (now - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "source": "test"}
latest["updated_utc"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
latest["fresh_fix"] = True
(tmp / "data" / "latest.json").write_text(json.dumps(latest), encoding="utf-8")

pts = [{"t": (now - timedelta(hours=3 - k)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lat": HER[0] + (3 - k) * 0.10, "lon": HER[1] + (3 - k) * 0.22,
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

if NEAR not in body:
    fails.append(f"she is {lens:.1f} nm from the {NEAR} camera and the section is shut."
                 f" The quay is {quay:.0f} nm away and she is sailing away from it, which"
                 f" is exactly the case this gate must not be fooled by")
elif FAR in body and body.index(FAR) < body.index(NEAR):
    fails.append(f"{FAR} is listed above {NEAR}, but she is {lens:.1f} nm from {NEAR} and"
                 f" {quay:.0f} nm from {FAR}. The tab that opens first looks at water she"
                 f" is nowhere near")

print(f"driven at 32.873N 17.280W: {lens:.1f} nm from the nearest lens, {quay:.0f} nm from"
      f" the quay, not closing")
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
