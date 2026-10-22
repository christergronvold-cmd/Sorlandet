"""A ship at her anchor is not a ship at the quay - and the page should not have to guess.

22 October 2026, mid-morning. Sørlandet lay to her anchor off the Funchal waterfront,
swinging at two tenths of a knot, and the page said "alongside now" under the marina
camera - to seventy-two families, with the lens pointed at a quay she was not at.
VesselFinder's own page said "At anchor" in the same minute. So did the ship: NAVSTAT 1,
which we had bought that morning for one credit.

The cause was BERTH_NM = 0.8. The page calls her berthed when a fix lands within eight
tenths of a mile of the harbour. The anchorage is six tenths out. She was inside it.

The temptation is to shrink the circle. That is the same mistake as the camera radius the
week before - nine, then fourteen, then twenty-two - and it ends the same way, because no
radius tells a quay from an anchorage a few hundred metres off it. The bridge does, by
hand, every time the cable goes out or comes up, and it is broadcast. So where the ship
has said, the ship decides; where she has not, the circle still does.

And the word changed too. "Alongside" is a claim about a rope on a bollard. "She has
arrived" is true at the quay, true at the anchorage, and true in the hour between - so on
the days nobody has said which, the page no longer has to pick one and be wrong.

Both halves are driven here against the real page and the real port file: a ship at
anchor must not read as arrived at the quay, and a ship at the quay must still read as
having got there.
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
ANCHORAGE = (32.64420, -16.89664)
fails = []
stamp = "%Y-%m-%dT%H:%M:%SZ"
now = datetime.now(timezone.utc)


def nm(a, b):
    R = 3440.065
    la1, la2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin(dl / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(h))


BERTH_NM = float(next(line.split("=")[1].strip().rstrip(";")
                      for line in (ROOT / "index.html").read_text(encoding="utf-8").splitlines()
                      if line.startswith("const BERTH_NM")))


def drive(where, sog, navstat):
    """Serve a copy of the real site with her put where we say, and read what it says back."""
    tmp = Path(tempfile.mkdtemp()) / "site"
    shutil.copytree(ROOT, tmp,
                    ignore=shutil.ignore_patterns(".git", "tests", "scripts", "__pycache__"))
    for junk in ("ahead.json", "wake.json", "course.json", "orbit.json", "shore.json",
                 "wind.json", "waves.json", "history.json"):
        (tmp / "data" / junk).unlink(missing_ok=True)

    # Pin the calendar: two days into a week at Funchal, so no date rule is what decides.
    tp = json.loads((tmp / "data" / "ports.json").read_text(encoding="utf-8"))
    here = [q["name"] for q in tp["ports"]].index("Funchal")
    for i, q in enumerate(tp["ports"]):
        day = now + timedelta(days=(i - here) * 20 - 2)
        q["arrive"] = day.strftime("%Y-%m-%d")
        q["depart"] = (day + timedelta(days=6)).strftime("%Y-%m-%d")
    (tmp / "data" / "ports.json").write_text(json.dumps(tp), encoding="utf-8")

    latest = json.loads((tmp / "data" / "latest.json").read_text(encoding="utf-8"))
    latest["position"] = {"lat": where[0], "lon": where[1], "sog_kn": sog, "cog_deg": 88,
                          "heading_deg": None, "nav_status": navstat,
                          "seen_utc": (now - timedelta(minutes=20)).strftime(stamp),
                          "source": "test"}
    latest["updated_utc"] = now.strftime(stamp)
    latest["fresh_fix"] = True
    (tmp / "data" / "latest.json").write_text(json.dumps(latest), encoding="utf-8")

    # Two days in the same spot: enough to register as a stay, slow enough not to read as
    # passing through. She states her status every few hours, as the paid source gives it.
    pts = []
    for k in range(96):
        t = now - timedelta(minutes=30 * (95 - k))
        pt = {"t": t.strftime(stamp),
              "lat": where[0] + (k % 5 - 2) * 0.0002, "lon": where[1] + (k % 3 - 1) * 0.0002,
              "sog": sog, "cog": (k * 37) % 360}
        if k > 40 and k % 12 == 0:
            pt["ns"] = navstat
        pts.append(pt)
    (tmp / "data" / "track.json").write_text(json.dumps({"points": pts}), encoding="utf-8")

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(tmp), **k)

        def log_message(self, *a):
            pass

    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(args=["--no-sandbox"])
            pg = b.new_page(viewport={"width": 1280, "height": 1400})
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            for pattern in ("**://*.tile.openstreetmap.org/**", "**://tiles.openseamap.org/**",
                            "**://gibs.earthdata.nasa.gov/**", "**://gc.zgo.at/**"):
                pg.route(pattern, lambda r: r.abort())
            pg.goto(f"http://127.0.0.1:{port}/index.html", wait_until="networkidle")
            pg.wait_for_timeout(2500)
            body = pg.inner_text("body").lower()
            b.close()
    finally:
        httpd.shutdown()
        shutil.rmtree(tmp.parent, ignore_errors=True)
    if errors:
        fails.append(f"the page threw while rendering: {errors[0][:160]}")
    return body


# ----------------------------------------------------------------- at anchor, and saying so
QUAY = json.loads((ROOT / "data" / "ports.json").read_text(encoding="utf-8"))["ports"]
QUAY = next(q for q in QUAY if q["name"] == "Funchal")
OFF = nm(ANCHORAGE, (QUAY["lat"], QUAY["lon"]))
if OFF > BERTH_NM:
    fails.append(f"this test has stopped testing anything: the anchorage is {OFF:.2f} nm out"
                 f" and BERTH_NM is now {BERTH_NM}, so the circle no longer calls her"
                 f" berthed and the bug it was written for could not happen. Check why.")

anchored = drive(ANCHORAGE, 0.2, 1)
if "alongside now" in anchored:
    fails.append(f"\"alongside now\" about a ship at her cable {OFF:.2f} nm off the harbour,"
                 f" inside the {BERTH_NM} nm the page calls a berth - while she broadcasts"
                 f" NAVSTAT 1, at anchor")
if "arrived" in anchored:
    fails.append("the page says she has arrived while she lies at anchor outside the"
                 " harbour - at anchor is a thing worth saying when we actually know it")
if "at anchor" not in anchored and "lying off" not in anchored:
    fails.append("the page says neither that she is alongside nor that she is at anchor off"
                 " Funchal - it has stopped saying where she is at all")

# --------------------------------------------------------------- at the quay, and saying so
berthed = drive((QUAY["lat"], QUAY["lon"]), 0.0, 5)
if "arrived" not in berthed:
    fails.append("she is at the quay broadcasting NAVSTAT 5, moored, and the page does not"
                 " say she has arrived")
if "alongside now" in berthed:
    fails.append("\"alongside now\" is back - the word the page no longer claims, because"
                 " it cannot always see a rope on a bollard")

print(f"driven twice: at anchor {OFF:.2f} nm off against a {BERTH_NM} nm berth circle, and"
      f" moored at the quay")
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
