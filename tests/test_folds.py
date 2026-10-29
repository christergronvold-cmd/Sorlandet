"""Every card on the page must be one a reader can shut.

"Jeg vil ogsaa at alle disse seksjonene skal kunne lukkes. man kan aapne de man er
interessert i." The page answers a lot of different questions now - where she is, what time
it is on board, what the sea is doing, how far she has come, what the forecast along her
course looks like - and no two readers want the same four of them. Seven cards folded
already; the five at the top of the page, the ones most people see most often, did not.

Two halves to get right, and the second is the one worth a test:

  - every card folds, and the choice is kept per card in the reader's own browser;
  - nothing is shut to begin with. A reader who has never touched the page sees exactly
    what they saw before. Seventy-two families did not ask for a page that hides things
    from them by default, and a fix that folded everything away would have passed the
    first half and quietly broken the page for everyone who never opens a card.

The forecast card only exists when there is a forecast, so this writes one into the copy
it serves rather than depending on where the ship happens to be today.
"""
import http.server
import json
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

# The five that did not fold, and the keys they fold under.
MUST = {"plan": "Voyage plan", "clock": "the clock on board",
        "weather": "Weather where she is", "progress": "Voyage progress",
        "ahead": "the forecast along her course"}

tmp = Path(tempfile.mkdtemp()) / "site"
shutil.copytree(ROOT, tmp,
                ignore=shutil.ignore_patterns(".git", "tests", "scripts", "__pycache__"))

# A forecast to put the "On the way to" card on the page whatever the ship is doing.
NOW = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
(tmp / "data" / "ahead.json").write_text(json.dumps({
    "generated_utc": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "to": "Sevilla", "country": "Spain", "distance_nm": 546.3, "direct_nm": 444.3,
    "route_basis": "course", "winding": 1.23, "speed_kn": 5.5,
    "pace_basis": "made good easing to the plan",
    "eta_utc": (NOW + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "route": [[33.97, -14.09], [36.2, -8.6], [36.8, -6.5], [37.385, -6.0]],
    "points": [{"hours": h, "time_utc": (NOW + timedelta(hours=h)).strftime("%Y-%m-%dT%H:00:00Z"),
                "lat": 34.0 + h * 0.015, "lon": -14.0 + h * 0.045,
                "wind_ms": 5.0, "wind_dir": 300, "gust_ms": 8.0, "temp_c": 19.0,
                "weather_code": 2, "wave_m": 1.8, "wave_dir": 320, "wave_period_s": 9,
                "where": "sea"}
               for h in (0, 6, 12, 24, 36, 48, 72, 96, 120, 144, 168)],
}), encoding="utf-8")


class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(tmp), **k)

    def log_message(self, *a):
        pass


socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()

cards, after, errors = [], {}, []
with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--no-sandbox"])
    # A phone, because that is where folding earns its keep.
    pg = b.new_page(viewport={"width": 414, "height": 896})
    pg.on("pageerror", lambda e: errors.append(str(e)))
    for pattern in ("**://*.tile.openstreetmap.org/**", "**://tiles.openseamap.org/**",
                    "**://gibs.earthdata.nasa.gov/**", "**://gc.zgo.at/**"):
        pg.route(pattern, lambda r: r.abort())
    URL = f"http://127.0.0.1:{PORT}/index.html"
    pg.goto(URL, wait_until="networkidle")
    pg.wait_for_timeout(2500)

    cards = pg.evaluate("""() => [...document.querySelectorAll('#cards > *')]
        .filter(el => (el.querySelector('h2') || {}).textContent)
        .map(el => ({ tag: el.tagName, key: el.dataset.fold || null,
                      open: el.tagName === 'DETAILS' ? el.open : null,
                      title: el.querySelector('h2').textContent.trim() }))""")

    # Shut two of them, by the summary a reader would actually click, then come back.
    # Only the ones that are there: when a card does not fold at all the check below says
    # so in words, which is more use than a click that times out.
    for key in ("weather", "progress"):
        where = f'details[data-fold="{key}"] > summary'
        if pg.locator(where).count():
            pg.click(where)
    pg.wait_for_timeout(400)
    pg.goto(URL, wait_until="networkidle")
    pg.wait_for_timeout(2500)
    after = pg.evaluate("""() => Object.fromEntries(
        [...document.querySelectorAll('details[data-fold]')].map(e => [e.dataset.fold, e.open]))""")
    b.close()
httpd.shutdown()
shutil.rmtree(tmp.parent, ignore_errors=True)

if errors:
    fails.append(f"the page threw while rendering: {errors[0][:160]}")

keys = {c["key"] for c in cards if c["key"]}
for key, what in MUST.items():
    if key not in keys:
        fails.append(f"{what} is not foldable - no card with data-fold=\"{key}\"")

# Nothing a reader can read is nailed open any more.
stuck = [c["title"][:40] for c in cards if c["tag"] != "DETAILS"]
if stuck:
    fails.append(f"these cards still cannot be shut: {stuck}")

# ...and nothing they have not touched is shut. The two that ship folded are the reference
# cards at the bottom, and they are allowed to be.
SHIPPED_SHUT = {"route", "share"}
early = [c["title"][:40] for c in cards
         if c["key"] and c["key"] not in SHIPPED_SHUT and c["open"] is False]
if early:
    fails.append(f"shut on a first visit, with nobody having asked: {early}")

# The choice is per card, kept, and does not spill onto the others.
for key in ("weather", "progress"):
    if after.get(key) is not False:
        fails.append(f"{key} did not stay shut when the reader came back")
for key in ("plan", "clock", "ahead"):
    if after.get(key) is not True:
        fails.append(f"{key} was shut too - one card's choice is closing another's")

print(f"{len(cards)} cards on the page, {len(keys)} of them foldable;"
      f" shut weather and progress, came back to"
      f" {sorted(k for k, v in after.items() if not v)}.")
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
