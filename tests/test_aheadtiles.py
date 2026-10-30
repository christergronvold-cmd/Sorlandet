"""What a forecast tile says: the temperature, and the place.

Two faults in the same strip of tiles, found on the same afternoon.

ONE. "Har ikke vi hatt med temperatur i denne seksjonen for?" No - ninety-three versions
of index.html and the forecast tiles never carried it. The temperature on the page is
always the temperature NOW: the figure over the map, and the weather card. The job has
been fetching temp_c for every projected position all along and nothing ever drew it. A
week of it, for a boy in the middle of the Atlantic, is a reasonable thing to want.

TWO. The tile for the day she arrives should say "in Sevilla" under it, and did not.
positions_ahead marks each hour "sea"; positions_after_arrival marks the hours past her
arrival "port", with the port's name, or "onward" once she has sailed again. Both were
careful about it. Then fetch_ahead built a fresh row out of the weather and dropped both
fields on the floor.

The holding branch had noticed years-of-the-voyage ago and put "where" back on afterwards
- which fixed the card in port and hid the fault everywhere else. All the way across the
Atlantic every tile came out unlabelled, and the day she arrives looked like any other
day at sea. A field a caller has to remember to restore is a field the next caller will
forget, so it is carried through now, and this is what notices if it stops being.
"""
import http.server
import json
import shutil
import socketserver
import sys
import tempfile
import threading
from datetime import timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import update as U  # noqa: E402

fails = []
HOURS = [0, 6, 12, 24, 36, 48, 72, 96, 120, 144, 168]


def check(label, got, want):
    if got != want:
        fails.append(f"{label}: got {got!r}, wanted {want!r}")


# ---- 1. fetch_ahead must carry the caller's own labels, and the temperature ------------
BASE = U.now_utc().replace(minute=0, second=0, microsecond=0)


def fake_get_json(url, timeout=0):
    """One block per coordinate, each with a week of hours - the shape Open-Meteo sends
    back when it is asked for several positions at once."""
    marine = "marine-api" in url
    stamps = [(BASE + timedelta(hours=h)).strftime("%Y-%m-%dT%H:00") for h in range(0, 200)]
    one = {"hourly": dict({"time": stamps},
                          **({"wave_height": [1.4] * len(stamps),
                              "wave_direction": [310] * len(stamps),
                              "wave_period": [9.0] * len(stamps)} if marine else
                             {"wind_speed_10m": [5.5] * len(stamps),
                              "wind_direction_10m": [280] * len(stamps),
                              "wind_gusts_10m": [8.0] * len(stamps),
                              "temperature_2m": [17.4] * len(stamps),
                              "weather_code": [2] * len(stamps)}))}
    return [dict(one) for _ in range(len(HOURS))]


asked = [{"hours": h, "lat": 36.0, "lon": -8.0,
          "where": "port" if h >= 144 else "sea",
          **({"port": "Sevilla"} if h >= 144 else {})} for h in HOURS]

keep = U.get_json
try:
    U.get_json = fake_get_json
    got = U.fetch_ahead(asked)
finally:
    U.get_json = keep

check("fetch_ahead returned a row per hour", [q["hours"] for q in got], HOURS)
check("every row has a temperature",
      all(q.get("temp_c") is not None for q in got), True)
check("the hours at sea are still marked at sea",
      [q.get("where") for q in got if q["hours"] < 144],
      ["sea"] * len([h for h in HOURS if h < 144]))
check("the hours after she arrives are still marked in port",
      [q.get("where") for q in got if q["hours"] >= 144], ["port", "port"])
check("...and still name the port",
      sorted({q.get("port") for q in got if q["hours"] >= 144}), ["Sevilla"])

# A caller that says nothing about the place must not have anything invented for it.
keep = U.get_json
try:
    U.get_json = fake_get_json
    bare = U.fetch_ahead([{"hours": h, "lat": 36.0, "lon": -8.0} for h in HOURS])
finally:
    U.get_json = keep
if any("where" in q or "port" in q for q in bare):
    fails.append("fetch_ahead invented a place for points that carried none")

# ---- 2. ...and the page draws both --------------------------------------------------
tmp = Path(tempfile.mkdtemp()) / "site"
shutil.copytree(ROOT, tmp,
                ignore=shutil.ignore_patterns(".git", "tests", "scripts", "__pycache__"))
(tmp / "data" / "ahead.json").write_text(json.dumps({
    "generated_utc": U.iso(BASE), "to": "Sevilla", "country": "Spain",
    "distance_nm": 446.0, "direct_nm": 437.0, "route_basis": "course", "speed_kn": 4.4,
    "pace_basis": "made good easing to the plan",
    "eta_utc": U.iso(BASE + timedelta(hours=150)),
    "route": [[33.9, -14.0], [36.2, -8.6], [36.8, -6.5], [37.385, -6.0]],
    "points": [{"hours": h, "time_utc": U.iso(BASE + timedelta(hours=h)),
                "lat": 36.0, "lon": -8.0, "wind_ms": 4.0, "wind_dir": 280,
                "gust_ms": 7.0, "temp_c": 17.4, "weather_code": 2,
                "wave_m": 1.4, "wave_dir": 310, "wave_period_s": 9,
                **({"where": "port", "port": "Sevilla"} if h >= 144
                   else {"where": "sea"})} for h in HOURS],
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

tiles, errors = [], []
with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1280, "height": 1400})
    pg.on("pageerror", lambda e: errors.append(str(e)))
    for pattern in ("**://*.tile.openstreetmap.org/**", "**://tiles.openseamap.org/**",
                    "**://gibs.earthdata.nasa.gov/**", "**://gc.zgo.at/**"):
        pg.route(pattern, lambda r: r.abort())
    pg.goto(f"http://127.0.0.1:{PORT}/index.html", wait_until="networkidle")
    pg.wait_for_timeout(2500)
    pg.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")
    pg.wait_for_timeout(400)
    tiles = pg.evaluate("""() => {
        const card = document.querySelector('[data-fold="ahead"]');
        return card ? [...card.querySelectorAll('.fc > div')].map(d => d.innerText) : null;
    }""")
    b.close()
httpd.shutdown()
shutil.rmtree(tmp.parent, ignore_errors=True)

if errors:
    fails.append(f"the page threw while rendering: {errors[0][:160]}")
if not tiles:
    fails.append("no forecast card on the page at all")
else:
    # Hour 0 is "now" and is not drawn; the other ten are.
    cold = [t.replace("\n", " ") for t in tiles if "°C" not in t]
    if cold:
        fails.append(f"{len(cold)} tiles with no temperature on them, e.g. {cold[0]!r}")
    if "17 °C" not in tiles[0]:
        fails.append(f"the temperature is not rounded to a whole degree: {tiles[0]!r}")
    named = [t for t in tiles if "in Sevilla" in t]
    if len(named) != 2:
        fails.append(f"{len(named)} tiles say \"in Sevilla\", wanted the 2 past her"
                     f" arrival - tiles were {[t.replace(chr(10), ' ') for t in tiles]}")
    at_sea = [t for t in tiles[:5] if "in Sevilla" in t]
    if at_sea:
        fails.append(f"a tile out in the Atlantic claims she is in Sevilla: {at_sea[0]!r}")

print(f"{len(tiles or [])} tiles; first reads {(tiles or ['-'])[0]!r}; "
      f"last reads {(tiles or ['-'])[-1]!r}")
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
