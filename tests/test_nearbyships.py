"""Other ships near her, as a layer on the main map.

19 November 2026. "Around her" began as a card with two embedded maps - VesselFinder for
ships, adsb.lol for aircraft. Christer wanted one map, not three: "det samme kartet vi
alltid har kjort, men at i det kartet lagt inn skip i naerheten". Aircraft were dropped -
adsb.lol's API sends no CORS header, so a page cannot read it. Ships come from the job:
a minute on aisstream.io at the end of each round, for a box about 30 nm round her,
written to data/nearby_ships.json. The page draws them when the Ships button is on.

Nothing here goes on the network. The job's websocket is a fake that plays back fixed
aisstream messages; the page is served from a copy of the site with a fixed ship list.
"""
import contextlib
import http.server
import io
import json
import shutil
import socketserver
import sys
import tempfile
import threading
import time
from datetime import timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import update as U  # noqa: E402

fails = []
TMP = Path(tempfile.mkdtemp())
U.NEARBY = TMP / "nearby_ships.json"
U.NEARBY_SECONDS = 1
NOW = U.now_utc()
HERE = (28.1453, -15.4277)          # Las Palmas
T = lambda mins: (NOW - timedelta(minutes=mins)).strftime("%Y-%m-%d %H:%M:%S.000000000 +0000 UTC")


def pos(mmsi, name, lat, lon, cog=90.0, sog=11.2, hdg=88, mins=2, kind="PositionReport"):
    return {"MessageType": kind,
            "MetaData": {"MMSI": mmsi, "ShipName": name, "latitude": lat, "longitude": lon,
                         "time_utc": T(mins)},
            "Message": {kind: {"Latitude": lat, "Longitude": lon, "Cog": cog, "Sog": sog,
                               "TrueHeading": hdg}}}


MESSAGES = [
    pos(257165000, "SORLANDET", HERE[0], HERE[1]),                    # her - left out
    pos(224000001, "ARMAS        ", 28.20, -15.35, mins=9),            # an older fix ...
    pos(224000001, "ARMAS", 28.21, -15.34, cog=271.5, sog=14.0, hdg=270, mins=1),  # ... newer
    {"MessageType": "ShipStaticData",
     "MetaData": {"MMSI": 224000001, "ShipName": "ARMAS"},
     "Message": {"ShipStaticData": {"Name": "VOLCAN DE TAMASITE", "Type": 60}}},
    pos(538000002, "NORDIC TANKER", 28.05, -15.30, cog=10, sog=0.1, hdg=511, mins=4),
    {"MessageType": "ShipStaticData", "MetaData": {"MMSI": 538000002},
     "Message": {"ShipStaticData": {"Name": "NORDIC TANKER", "Type": 84}}},
    pos(244000003, "", 28.16, -15.41, cog=None, sog=5.0, hdg=511, mins=3,
        kind="StandardClassBPositionReport"),
    {"MessageType": "StaticDataReport", "MetaData": {"MMSI": 244000003},
     "Message": {"StaticDataReport": {"ReportA": {"Valid": True, "Name": "BLUE MOON"},
                                      "ReportB": {"Valid": True, "ShipType": 36}}}},
    # In the corner of the box but more than 30 nm away: out.
    pos(311000004, "FAR AWAY", HERE[0] + 0.45, HERE[1] + 0.5),
    "not json",
]


class FakeWS:
    def __init__(self, messages, log):
        self.q = [m if isinstance(m, str) else json.dumps(m) for m in messages]
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send(self, data):
        self.log.append(("send", json.loads(data)))

    def recv(self, timeout=None):
        if self.q:
            return self.q.pop(0)
        time.sleep(min(timeout or 0.05, 0.05))
        raise TimeoutError


def opener(messages, log):
    def connect(url, **kw):
        log.append(("connect", url))
        return FakeWS(messages, log)
    return connect


def check(label, got, want):
    if got != want:
        fails.append(f"{label}: got {got!r}, wanted {want!r}")


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        out = fn(*a, **k)
    return out, buf.getvalue()


# ---- 1. the job: listen, sort out, write ------------------------------------------
log = []
_, out = quiet(U.update_nearby_ships, "KEY", HERE[0], HERE[1], connect=opener(MESSAGES, log))
sub = next((x[1] for x in log if x[0] == "send"), None)
if not sub:
    fails.append("nothing was subscribed")
else:
    check("the key goes in the subscription", sub.get("APIKey"), "KEY")
    if "FiltersShipMMSI" in sub:
        fails.append("the subscription is filtered to one MMSI - it would only hear her")
    (la0, lo0), (la1, lo1) = sub["BoundingBoxes"][0]
    if not (la0 < HERE[0] < la1 and lo0 < HERE[1] < lo1):
        fails.append(f"the box {sub['BoundingBoxes']} is not round her")
    if abs((la1 - la0) * 60 / 2 - 30) > 0.5:
        fails.append(f"the box is not about 30 nm either side: {(la1 - la0) * 30:.1f}")
f = json.loads(U.NEARBY.read_text()) if U.NEARBY.exists() else {}
ships = {x["mmsi"]: x for x in f.get("ships", [])}
check("the source is named", f.get("source"), "aisstream.io")
if "257165000" in ships:
    fails.append("she is in her own list of nearby ships")
if "311000004" in ships:
    fails.append("a ship more than 30 nm away (in the corner of the box) was kept")
check("the three near ships, nearest first",
      [x["mmsi"] for x in f.get("ships", [])], ["244000003", "224000001", "538000002"])
a = ships.get("224000001", {})
check("the name comes from the static report", a.get("name"), "VOLCAN DE TAMASITE")
check("type 60 is a passenger ship", a.get("type"), "passenger")
check("the newest fix is kept", (a.get("lat"), a.get("cog"), a.get("sog")), (28.21, 271.5, 14.0))
check("its heading", a.get("heading"), 270)
for k in ("mmsi", "name", "type", "lat", "lon", "cog", "sog", "t"):
    if k not in a:
        fails.append(f"a ship in the file has no {k!r}")
t = ships.get("538000002", {})
check("type 84 is a tanker", t.get("type"), "tanker")
check("heading 511 means not available", t.get("heading"), None)
c = ships.get("244000003", {})
check("a class B name from report A", c.get("name"), "BLUE MOON")
check("a class B type from report B", c.get("type"), "sailing")
if "nearby ships: 3 within 30 nm" not in out:
    fails.append(f"the log does not say what was heard: {out!r}")

# Names and types come every six minutes, positions every few seconds. What was heard
# on an earlier round is remembered and filled in.
f1 = json.loads(U.NEARBY.read_text())
if (f1.get("known") or {}).get("224000001", {}).get("type_code") != 60:
    fails.append(f"what was heard is not remembered in the file: {f1.get('known')}")
only_positions = [pos(224000001, "", 28.22, -15.33, mins=0),
                  pos(538000002, "", 28.06, -15.31, mins=0)]
_, out = quiet(U.update_nearby_ships, "KEY", HERE[0], HERE[1],
               connect=opener(only_positions, []))
f2 = {x["mmsi"]: x for x in json.loads(U.NEARBY.read_text())["ships"]}
check("a later round with positions only still names the ship",
      f2.get("224000001", {}).get("name"), "VOLCAN DE TAMASITE")
check("...and still knows its type", f2.get("224000001", {}).get("type"), "passenger")
check("...and the tanker's", f2.get("538000002", {}).get("type"), "tanker")
check("...and the new position is the one drawn", f2.get("224000001", {}).get("lat"), 28.22)
if "filled in from earlier rounds" not in out:
    fails.append(f"the log does not say what was filled in: {out!r}")
# Old memories go.
f3 = json.loads(U.NEARBY.read_text())
f3["known"]["123456789"] = {"name": "FORGOTTEN", "t": U.iso(NOW - timedelta(days=40))}
U.write_json(U.NEARBY, f3)
quiet(U.update_nearby_ships, "KEY", HERE[0], HERE[1], connect=opener(only_positions, []))
if "123456789" in (json.loads(U.NEARBY.read_text()).get("known") or {}):
    fails.append("a ship last heard forty days ago is still remembered")

# ---- 2. it must never cost the round ------------------------------------------------
before = U.NEARBY.read_text()
log = []
_, out = quiet(U.update_nearby_ships, "", HERE[0], HERE[1], connect=opener(MESSAGES, log))
if log:
    fails.append("with no key it still connected")
if "no aisstream key" not in out:
    fails.append(f"with no key the log says nothing: {out!r}")


def broken(url, **kw):
    raise OSError("connection refused")


try:
    _, out = quiet(U.update_nearby_ships, "KEY", HERE[0], HERE[1], connect=broken)
except Exception as exc:
    fails.append(f"a refused connection raised out of the job: {exc}")
check("a refused connection leaves the previous list", U.NEARBY.read_text(), before)
_, out = quiet(U.update_nearby_ships, "BAD", HERE[0], HERE[1],
               connect=opener([{"error": "Api Key Is Not Valid"}], []))
check("a refused key leaves the previous list", U.NEARBY.read_text(), before)
if "Api Key Is Not Valid" not in out:
    fails.append(f"aisstream's refusal is not in the log: {out!r}")
_, out = quiet(U.update_nearby_ships, "KEY", HERE[0], HERE[1],
               connect=opener([{"MessageType": "PositionReport", "MetaData": {"MMSI": 1},
                                "Message": None}], []))
if U.NEARBY.read_text() == before and "failed" in out:
    fails.append(f"a malformed message broke the listener: {out!r}")
if "update_nearby_ships(api_key, lat, lon)" not in (ROOT / "scripts" / "update.py").read_text():
    fails.append("main() does not call update_nearby_ships")

# ---- 3. the page: a button under the map, ships drawn on the map ---------------------
site = TMP / "site"
shutil.copytree(ROOT, site, ignore=shutil.ignore_patterns(".git", "tests", "scripts",
                                                          "__pycache__", ".cache"))
latest = json.loads((site / "data" / "latest.json").read_text())
plat, plon = latest["position"]["lat"], latest["position"]["lon"]
iso = lambda mins: U.iso(NOW - timedelta(minutes=mins))
(site / "data" / "nearby_ships.json").write_text(json.dumps({
    "generated_utc": iso(3), "source": "aisstream.io", "centre": [plat, plon],
    "radius_nm": 30, "ships": [
        {"mmsi": "224000001", "name": "VOLCAN DE TAMASITE", "type": "passenger",
         "lat": plat + 0.05, "lon": plon + 0.05, "cog": 271.5, "sog": 14.0, "heading": 270,
         "t": iso(12), "nm": 4.1},
        {"mmsi": "538000002", "name": "NORDIC TANKER", "type": "tanker",
         "lat": plat - 0.08, "lon": plon + 0.1, "cog": 10, "sog": 0.1, "heading": None,
         "t": iso(30), "nm": 7.0},
        {"mmsi": "999000009", "name": "LONG GONE", "type": "cargo",
         "lat": plat + 0.1, "lon": plon - 0.1, "cog": 90, "sog": 9, "heading": 90,
         "t": iso(180), "nm": 8.0}]}), encoding="utf-8")


class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(site), **k)

    def log_message(self, *a):
        pass


socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{httpd.server_address[1]}/index.html"
BLOCK = ("**://*.tile.openstreetmap.org/**", "**://tiles.openseamap.org/**",
         "**://gibs.earthdata.nasa.gov/**", "**://gc.zgo.at/**")

STATE = """() => {
    const b = document.getElementById('shipstoggle');
    const row = document.querySelector('#windbar .aroundrow');
    return {
      row: !!row, tag: row ? row.textContent.replace(/\\s+/g, ' ').trim() : '',
      on: b ? b.classList.contains('on') : null, text: b ? b.textContent.trim() : '',
      planes: !!document.getElementById('planestoggle'),
      oldCard: !!document.getElementById('around') || !!document.querySelector('iframe[src*="vesselfinder"],iframe[src*="adsb"]'),
      markers: [...document.querySelectorAll('.nearby-ship')].map(m =>
        (m.querySelector('svg') || {}).getAttribute ? m.querySelector('svg').getAttribute('style') || '' : ''),
      credit: (document.querySelector('.leaflet-control-attribution') || {}).textContent || '',
      note: document.getElementById('shipsnote')?.hidden ? '' : document.getElementById('shipsnote')?.textContent,
    };
}"""

errors, asked = [], []
with sync_playwright() as pw:
    br = pw.chromium.launch(args=["--no-sandbox"])
    ctx = br.new_context(viewport={"width": 1280, "height": 1400})
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("request", lambda r: asked.append(r.url) if "nearby_ships" in r.url else None)
    for p in BLOCK:
        pg.route(p, lambda r: r.abort())
    pg.goto(URL, wait_until="load")
    pg.wait_for_timeout(2000)
    s = pg.evaluate(STATE)
    if not s["row"] or not s["tag"].lower().startswith("around her"):
        fails.append(f"no Around her row in the bar under the map: {s['tag']!r}")
    if s["planes"]:
        fails.append("there is a Planes button; aircraft were dropped")
    if s["oldCard"]:
        fails.append("the old Around her card or its embedded maps are still on the page")
    if s["on"] or s["markers"]:
        fails.append(f"ships are on before anyone asked: {s}")
    if asked:
        fails.append("the ship list was fetched with the button off")
    if "aisstream" in s["credit"]:
        fails.append("aisstream is credited with the layer off")

    pg.click("#shipstoggle")
    pg.wait_for_timeout(800)
    s = pg.evaluate(STATE)
    if not s["on"] or "✓" not in s["text"]:
        fails.append(f"the button does not show it is on: {s['text']!r}")
    check("two ships drawn (the one heard three hours ago is not)", len(s["markers"]), 2)
    if not any("rotate(270deg)" in m for m in s["markers"]):
        fails.append(f"the moving ship is not turned to its heading: {s['markers']}")
    size = pg.evaluate("""() => { const m = document.querySelector('.nearby-ship svg');
        return m ? m.getBoundingClientRect().width : 0; }""")
    if size < 26:
        fails.append(f"the ship icons are {size:.0f} px - too small to find among the sea"
                     f" marks (\"Skipene er veldig små\"), wanted at least 26")
    if "Ships: aisstream.io" not in s["credit"]:
        fails.append(f"the map does not credit aisstream.io: {s['credit']!r}")
    if "2 ships" not in (s["note"] or ""):
        fails.append(f"the note beside the button does not count them: {s['note']!r}")
    pg.evaluate("() => document.querySelectorAll('.nearby-ship')[0].dispatchEvent(new MouseEvent('click', {bubbles: true}))")
    pg.wait_for_timeout(300)
    popup = pg.evaluate("() => (document.querySelector('.leaflet-popup-content') || {}).textContent || ''")
    for words in ("VOLCAN DE TAMASITE", "passenger", "14.0 kn", "course 272°", "12 min ago",
                  "VesselFinder"):
        if words not in popup:
            fails.append(f"the popup does not say {words!r}: {popup!r}")
    link = pg.evaluate("() => (document.querySelector('.leaflet-popup-content a') || {}).href || ''")
    if link != "https://www.vesselfinder.com/vessels/details/224000001":
        fails.append(f"the VesselFinder link is not to this ship's page: {link!r}")
    # The tanker is at 0.1 kn: lying still, and no course.
    pg.evaluate("""() => { const m = [...document.querySelectorAll('.nearby-ship')];
        m[1].dispatchEvent(new MouseEvent('click', {bubbles: true})); }""")
    pg.wait_for_timeout(300)
    still = pg.evaluate("() => (document.querySelector('.leaflet-popup-content') || {}).textContent || ''")
    if "NORDIC TANKER" in still:
        if "lying still" not in still or "course" in still:
            fails.append(f"a ship at 0.1 kn should be lying still, with no course: {still!r}")
    else:
        fails.append(f"could not open the tanker's popup: {still!r}")

    # Remembered, and off again.
    pg.goto(URL, wait_until="load")
    pg.wait_for_timeout(2000)
    s = pg.evaluate(STATE)
    if not s["on"] or len(s["markers"]) != 2:
        fails.append(f"Ships was not remembered as on: {s['on']}, {len(s['markers'])} ships")
    pg.click("#shipstoggle")
    pg.wait_for_timeout(400)
    s = pg.evaluate(STATE)
    if s["markers"] or "aisstream" in s["credit"]:
        fails.append("Ships off, but ships or their credit are still on the map")

    # Storage blocked: the button still works.
    c2 = br.new_context(viewport={"width": 1280, "height": 1400})
    c2.add_init_script("Object.defineProperty(window, 'localStorage', { get() { throw new Error('blocked'); } });")
    p2 = c2.new_page()
    p2.on("pageerror", lambda e: errors.append("(no storage) " + str(e)))
    for p in BLOCK:
        p2.route(p, lambda r: r.abort())
    p2.goto(URL, wait_until="load")
    p2.wait_for_timeout(2000)
    p2.click("#shipstoggle")
    p2.wait_for_timeout(600)
    if len(p2.evaluate(STATE)["markers"]) != 2:
        fails.append("with storage blocked the Ships button does nothing")
    c2.close()

    # A phone: the bar wraps, and the row is still its own line, last, on screen.
    c3 = br.new_context(viewport={"width": 375, "height": 800})
    p3 = c3.new_page()
    for p in BLOCK:
        p3.route(p, lambda r: r.abort())
    p3.goto(URL, wait_until="load")
    p3.wait_for_timeout(2000)
    lay = p3.evaluate("""() => {
        const bar = document.getElementById('windbar');
        const row = bar.querySelector('.aroundrow');
        const rr = row.getBoundingClientRect();
        const others = [...bar.children].filter(e => e !== row && !e.hidden
            && getComputedStyle(e).display !== 'none').map(e => e.getBoundingClientRect());
        const btn = document.getElementById('shipstoggle').getBoundingClientRect();
        return { below: others.every(o => o.bottom <= rr.top + 1),
                 fits: btn.right <= window.innerWidth && btn.left >= 0,
                 scrollX: document.documentElement.scrollWidth > window.innerWidth + 1 };
    }""")
    if not lay["below"]:
        fails.append("on a phone the Around her row is not a line of its own below the bar")
    if not lay["fits"] or lay["scrollX"]:
        fails.append(f"on a phone the row runs off the screen: {lay}")
    c3.close()
    br.close()
httpd.shutdown()
shutil.rmtree(TMP, ignore_errors=True)

if errors:
    fails.append(f"the page threw: {errors[0][:200]}")
if fails:
    print("FAILURES")
    for x in fails:
        print("  -", x)
    sys.exit(1)
print("ALL CHECKS PASSED")
