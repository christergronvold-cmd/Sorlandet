"""Around her: a card with two switches, Ships and Planes, each with a map under it.

19 November 2026, asked for by Christer. The maps are other people's live pages in a frame
- VesselFinder for the ships, tar1090 on adsb.lol for the aircraft - so the rules are
about restraint:

  - the card is shut to begin with, and both switches are off;
  - nothing is fetched from either until its switch is on and the card is open;
  - the choice is remembered in the reader's browser;
  - each map is credited;
  - and a five-minute refresh must not reload a map under the reader. #cards is rebuilt
    from scratch on every refresh; a frame inside it would be too. That is why this card
    lives outside it, and this test checks the frame survives a refresh.

The two outside sites are not contacted: the browser's requests to them are answered here.
"""
import http.server
import shutil
import socketserver
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
fails = []

tmp = Path(tempfile.mkdtemp()) / "site"
shutil.copytree(ROOT, tmp, ignore=shutil.ignore_patterns(".git", "tests", "scripts",
                                                         "__pycache__", ".cache"))


class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(tmp), **k)

    def log_message(self, *a):
        pass


socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{httpd.server_address[1]}/index.html"

asked = []          # every request the page made to the two map sites


def stub(route):
    asked.append(route.request.url)
    route.fulfill(status=200, content_type="text/html",
                  body="<!doctype html><title>map</title><p>a map</p>")


def state(pg):
    return pg.evaluate("""() => {
        const c = document.getElementById('around');
        const f = id => document.querySelector(`#${id} iframe`);
        return {
          exists: !!c, inCards: !!document.querySelector('#cards #around'),
          open: c ? c.open : null,
          ships: document.getElementById('sw-ships')?.checked ?? null,
          planes: document.getElementById('sw-planes')?.checked ?? null,
          shipsSrc: f('around-ships')?.src || null,
          planesSrc: f('around-planes')?.src || null,
          text: c ? c.textContent : '',
        };
    }""")


errors = []
with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--no-sandbox"])
    ctx = b.new_context(viewport={"width": 1280, "height": 1400})
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    for pattern in ("**://*.tile.openstreetmap.org/**", "**://tiles.openseamap.org/**",
                    "**://gibs.earthdata.nasa.gov/**", "**://gc.zgo.at/**"):
        pg.route(pattern, lambda r: r.abort())
    pg.route("**://*.vesselfinder.com/**", stub)
    pg.route("**://*.adsb.lol/**", stub)
    pg.route("**://*.flightradar24.com/**", stub)

    pg.goto(URL, wait_until="networkidle")
    pg.wait_for_timeout(2000)
    first = state(pg)
    if not first["exists"]:
        fails.append("there is no Around her card")
    else:
        if first["inCards"]:
            fails.append("the card is inside #cards, which is rebuilt every refresh - the"
                         " maps would reload every five minutes")
        if first["open"]:
            fails.append("the card is open on a first visit; it should be shut")
        if first["ships"] or first["planes"]:
            fails.append(f"a switch is on before anyone touched it: {first}")
        if "Ships" not in first["text"] or "Planes" not in first["text"]:
            fails.append(f"the switches are not labelled Ships and Planes: {first['text']!r}")

    # Open the card: still nothing loaded, the switches are off.
    pg.click("#around > summary")
    pg.wait_for_timeout(300)
    s = state(pg)
    if s["shipsSrc"] or s["planesSrc"] or asked:
        fails.append(f"maps loaded with both switches off: {s}, {asked}")

    # Ships on.
    pg.click("label:has(#sw-ships)")
    pg.wait_for_timeout(500)
    s = state(pg)
    src = s["shipsSrc"] or ""
    q = parse_qs(urlparse(src).query)
    if "vesselfinder.com/aismap" not in src:
        fails.append(f"Ships switched on, but no VesselFinder map: {src!r}")
    else:
        if q.get("mmsi") != ["257165000"]:
            fails.append(f"the ship map is not for her MMSI: {q.get('mmsi')}")
        if q.get("track") != ["true"]:
            fails.append(f"her track is not switched on in the ship map: {q.get('track')}")
    if "VesselFinder" not in s["text"]:
        fails.append("the ship map is not credited to VesselFinder")
    if s["planesSrc"]:
        fails.append("switching Ships on also loaded the Planes map")
    if any("adsb.lol" in u for u in asked):
        fails.append("adsb.lol was contacted with the Planes switch off")

    # A refresh must not rebuild the frame.
    pg.evaluate("() => { document.querySelector('#around-ships iframe').dataset.mark = 'kept'; }")
    pg.evaluate("() => load()")
    pg.wait_for_timeout(1500)
    kept = pg.evaluate("() => document.querySelector('#around-ships iframe')?.dataset.mark")
    if kept != "kept":
        fails.append("a refresh rebuilt the ship map - it reloads under the reader every five"
                     " minutes")

    # Planes on.
    pg.click("label:has(#sw-planes)")
    pg.wait_for_timeout(500)
    s = state(pg)
    src = s["planesSrc"] or ""
    q = parse_qs(urlparse(src).query, keep_blank_values=True)
    pos = pg.evaluate("() => aroundPos")
    if "adsb.lol" not in src:
        fails.append(f"Planes switched on, but no adsb.lol map: {src!r}")
    else:
        if q.get("zoom") != ["9"]:
            fails.append(f"the plane map is not at zoom 9: {q.get('zoom')}")
        try:
            lat, lon = float(q["lat"][0]), float(q["lon"][0])
            if not pos or abs(lat - pos[0]) > 1e-3 or abs(lon - pos[1]) > 1e-3:
                fails.append(f"the plane map is centred at {lat},{lon}, she is at {pos}")
        except (KeyError, ValueError):
            fails.append(f"the plane map has no centre: {src!r}")
    if "adsb.lol" not in s["text"]:
        fails.append("the plane map is not credited to adsb.lol")
    if "new tab" not in s["text"]:
        fails.append("there is no link to open the plane map in a tab if the frame is blocked")
    if any("flightradar24" in u for u in asked):
        fails.append("Flightradar24 was used")

    # Ships off: its frame goes, Planes stays.
    pg.click("label:has(#sw-ships)")
    pg.wait_for_timeout(300)
    s = state(pg)
    if s["shipsSrc"]:
        fails.append("Ships switched off, but the ship map is still there")
    if not s["planesSrc"]:
        fails.append("switching Ships off took the Planes map with it")

    # Remembered: come back, open the card, Planes is on and loads, Ships is off.
    pg.goto(URL, wait_until="networkidle")
    pg.wait_for_timeout(2000)
    s = state(pg)
    if s["ships"] is not False or s["planes"] is not True:
        fails.append(f"the switches were not remembered: ships={s['ships']},"
                     f" planes={s['planes']}")
    if not s["open"]:
        pg.click("#around > summary")
        pg.wait_for_timeout(400)
        s = state(pg)
    if not s["planesSrc"]:
        fails.append("Planes was remembered as on, but its map did not load")

    # Shutting the card takes the maps down.
    if s["open"]:
        pg.click("#around > summary")
        pg.wait_for_timeout(300)
    s = state(pg)
    if s["planesSrc"] or s["shipsSrc"]:
        fails.append("the card was shut and a map stayed loaded behind it")

    # And with storage blocked the page still renders and the switches still work.
    ctx2 = b.new_context(viewport={"width": 1280, "height": 1400})
    ctx2.add_init_script("""Object.defineProperty(window, 'localStorage',
        { get() { throw new Error('blocked'); } });""")
    pg2 = ctx2.new_page()
    pg2.on("pageerror", lambda e: errors.append("(no storage) " + str(e)))
    pg2.route("**://*.vesselfinder.com/**", stub)
    pg2.route("**://*.adsb.lol/**", stub)
    for pattern in ("**://*.tile.openstreetmap.org/**", "**://tiles.openseamap.org/**",
                    "**://gibs.earthdata.nasa.gov/**", "**://gc.zgo.at/**"):
        pg2.route(pattern, lambda r: r.abort())
    pg2.goto(URL, wait_until="networkidle")
    pg2.wait_for_timeout(2000)
    if not state(pg2)["exists"]:
        fails.append("with storage blocked there is no Around her card")
    b.close()
httpd.shutdown()
shutil.rmtree(tmp.parent, ignore_errors=True)

if errors:
    fails.append(f"the page threw: {errors[0][:200]}")
print(f"{len(asked)} requests to the map sites; first {asked[:1]}")
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
