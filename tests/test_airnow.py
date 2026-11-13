"""The strip at the top of the page: wind, air and speed must not go blank so easily.

13 November 2026. After the forecast fall-over to met.no (6c1ab136) the strip still showed
dashes for Wind and Air: the weather "where she is now" in latest.json came from
api.open-meteo.com alone, and that host had stopped answering the job. The wake - the
weather along the track she has sailed - had the same single source.

And SPEED showed a dash too, most of the day at sea. The foundation's feed sends no
speed, so p.sog_kn was null, and there was no fallback.

Only the network is stubbed (update._http). get_json, the met.no client, fetch_weather,
settle_weather, fetch_past and the page itself all run for real.
"""
import contextlib
import copy
import http.server
import io
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
TMP = Path(tempfile.mkdtemp())
U.METNO_CACHE = TMP / "metno-cache"
NOW = U.now_utc()
BASE = NOW.replace(minute=0, second=0, microsecond=0)
HTTPDATE = lambda t: t.strftime("%a, %d %b %Y %H:%M:%S GMT")


def check(label, got, want):
    if got != want:
        fails.append(f"{label}: got {got!r}, wanted {want!r}")


def metno_doc():
    series = []
    for h in list(range(0, 60)) + list(range(60, 216, 6)):
        t = BASE + timedelta(hours=h)
        step = "next_1_hours" if h < 60 else "next_6_hours"
        series.append({"time": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "data": {
            "instant": {"details": {"air_pressure_at_sea_level": 1012.3 + h / 10,
                                    "air_temperature": 21.5, "cloud_area_fraction": 64.1,
                                    "wind_from_direction": 247.6, "wind_speed": 7.6}},
            step: {"summary": {"symbol_code": "lightrainshowers_day"},
                   "details": {"precipitation_amount": 0.3}}}})
    return {"properties": {"timeseries": series}}


class Net:
    def __init__(self, forecast="ok", marine="ok", metno="ok"):
        self.forecast, self.marine, self.metno = forecast, marine, metno
        self.calls = []

    def __call__(self, url, headers=None, timeout=30):
        self.calls.append((url, dict(headers or {})))
        if "api.met.no" in url:
            if self.metno != "ok":
                return 503, {}, b"Service Unavailable"
            return 200, {"expires": HTTPDATE(NOW + timedelta(hours=1)),
                         "last-modified": HTTPDATE(NOW)}, json.dumps(metno_doc()).encode()
        if "marine-api.open-meteo.com" in url:
            if self.marine != "ok":
                return 500, {}, b'{"error":true,"reason":"marine down"}'
            if "current=" in url:
                return 200, {}, json.dumps({"current": {
                    "time": BASE.strftime("%Y-%m-%dT%H:00"), "wave_height": 2.2,
                    "wave_direction": 261, "wave_period": 9.0, "swell_wave_height": 1.9,
                    "swell_wave_period": 7.5, "wind_wave_height": 0.8,
                    "sea_surface_temperature": 21.4}}).encode()
            n = url.split("latitude=")[1].split("&")[0].count("%2C") + 1
            stamps = [(BASE - timedelta(hours=72) + timedelta(hours=h)).strftime(
                "%Y-%m-%dT%H:00") for h in range(0, 100)]
            return 200, {}, json.dumps([{"hourly": {
                "time": stamps, "wave_height": [1.5] * 100, "wave_direction": [300] * 100,
                "wave_period": [8.0] * 100}}] * n).encode()
        if "api.open-meteo.com" in url:
            if self.forecast != "ok":
                return 429, {}, b'{"error":true,"reason":"Daily API request limit exceeded."}'
            if "current=" in url:
                return 200, {}, json.dumps({"current": {
                    "time": BASE.strftime("%Y-%m-%dT%H:00"), "temperature_2m": 19.9,
                    "apparent_temperature": 19.0, "wind_speed_10m": 5.1,
                    "wind_direction_10m": 280, "wind_gusts_10m": 8.2, "pressure_msl": 1015,
                    "cloud_cover": 20, "precipitation": 0, "weather_code": 1}}).encode()
            raise AssertionError("past/forecast series not expected with Open-Meteo up here")
        raise AssertionError(f"unexpected request {url}")

    def metno_calls(self):
        return [c for c in self.calls if "api.met.no" in c[0]]


def run(net, fn, *a):
    keep, buf = U._http, io.StringIO()
    U._http = net
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            got = fn(*a)
    finally:
        U._http = keep
    return got, buf.getvalue()


# ---- 1. the weather where she is: Open-Meteo, then met.no ------------------------------
w, log = run(Net(), U.fetch_weather, 36.44, -6.96)
check("Open-Meteo up: air from it", (w.get("air") or {}).get("source"), "open-meteo")
check("...wind as it said", (w.get("air") or {}).get("wind_ms"), 5.1)

shutil.rmtree(U.METNO_CACHE, ignore_errors=True)
net = Net(forecast="429")
w, log = run(net, U.fetch_weather, 36.44, -6.96)
air = w.get("air") or {}
check("Open-Meteo refuses: air from met.no", air.get("source"), "met.no")
check("met.no's wind", air.get("wind_ms"), 7.6)
check("met.no's direction, whole degrees", air.get("wind_direction_deg"), 248)
check("met.no's temperature", air.get("temp_c"), 21.5)
check("met.no's sky, as a WMO code", air.get("weather_code"), 80)
check("met.no's cloud", air.get("cloud_pct"), 64.1)
check("met.no's pressure", air.get("pressure_hpa"), 1012.3)
check("met.no's rain this hour", air.get("precip_mm"), 0.3)
check("the sea is still Open-Meteo marine's", (w.get("sea") or {}).get("wave_height_m"), 2.2)
check("met.no asked once", len(net.metno_calls()), 1)
if not all(U.REPO_URL in (c[1].get("User-Agent") or "") for c in net.metno_calls()):
    fails.append("met.no was asked without the repo in the User-Agent")
for words in ("HTTP 429", "air from met.no"):
    if words not in log:
        fails.append(f"the log does not say {words!r}:\n{log}")

# ---- 2. both refuse: hold the last good air for twelve hours, no longer ----------------
shutil.rmtree(U.METNO_CACHE, ignore_errors=True)
w, log = run(Net(forecast="429", metno="503"), U.fetch_weather, 36.44, -6.96)
check("both refuse: fetch_weather has no air", w.get("air"), None)
good_air = {"temp_c": 20.0, "wind_ms": 6.5, "wind_direction_deg": 250, "weather_code": 2,
            "source": "open-meteo", "fetched_utc": U.iso(NOW - timedelta(hours=3))}
prev = {"weather": {"fetched_utc": U.iso(NOW - timedelta(minutes=30)),
                    "sea": {"wave_height_m": 2.0}, "air": good_air}}
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    settled = U.settle_weather(copy.deepcopy(w), prev)
air = settled.get("air") or {}
check("the previous air is held", air.get("wind_ms"), 6.5)
check("...marked held", air.get("held"), True)
check("...and dated by when it was fetched", air.get("fetched_utc"), good_air["fetched_utc"])
check("the new sea reading is kept", (settled.get("sea") or {}).get("wave_height_m"), 2.2)
if "holding the reading" not in buf.getvalue():
    fails.append(f"the log does not say the air was held: {buf.getvalue()!r}")

# Held again next round: still dated by the first fetch.
again = U.settle_weather(copy.deepcopy(w), {"weather": settled})
check("a second hold keeps the original time",
      (again.get("air") or {}).get("fetched_utc"), good_air["fetched_utc"])

# Thirteen hours: not held.
old = copy.deepcopy(prev)
old["weather"]["air"]["fetched_utc"] = U.iso(NOW - timedelta(hours=13))
with contextlib.redirect_stdout(io.StringIO()):
    check("a reading thirteen hours old is not held",
          U.settle_weather(copy.deepcopy(w), old).get("air"), None)

# No sea and no air: the old reading, stale - and its air still within the limit.
with contextlib.redirect_stdout(io.StringIO()):
    both = U.settle_weather({"fetched_utc": U.iso(NOW)}, prev)
check("nothing at all: the previous sea, marked stale",
      (both.get("stale"), (both.get("sea") or {}).get("wave_height_m")), (True, 2.0))
check("...with its air held", (both.get("air") or {}).get("held"), True)
with contextlib.redirect_stdout(io.StringIO()):
    both_old = U.settle_weather({"fetched_utc": U.iso(NOW)}, old)
check("...but not an air reading past twelve hours", both_old.get("air"), None)

# ---- 3. the wake: met.no fills the hour she is in, and only that -----------------------
shutil.rmtree(U.METNO_CACHE, ignore_errors=True)
slots = [{"t": U.iso(BASE - timedelta(hours=h)), "lat": 36.4 + h * 0.01, "lon": -6.9}
         for h in (3, 2, 1, 0)]
net = Net(forecast="429")
rows, log = run(net, U.fetch_past, slots)
check("a row per hour", len(rows), 4)
check("the current hour has met.no's wind", rows[-1].get("wind_ms"), 7.6)
check("...marked as met.no's", rows[-1].get("source"), "met.no")
check("past hours are not invented", [r.get("wind_ms") for r in rows[:-1]], [None] * 3)
check("waves for every hour from marine", [r.get("wave_m") for r in rows], [1.5] * 4)
check("met.no is asked only for the current hour", len(net.metno_calls()), 1)
net = Net(forecast="429")
run(net, U.fetch_past, slots[:-1])
check("with no current hour, met.no is not asked at all", len(net.metno_calls()), 0)

# ---- 4. the page: credit, age, and a speed that is not a dash --------------------------
site = TMP / "site"
shutil.copytree(ROOT, site, ignore=shutil.ignore_patterns(".git", "tests", "scripts",
                                                          "__pycache__", ".cache"))
latest0 = json.loads((ROOT / "data" / "latest.json").read_text())
HERE = (36.44, -6.96)


def point(mins_ago, dlat=0.0):
    return {"t": U.iso(NOW - timedelta(minutes=mins_ago)), "lat": HERE[0] - dlat, "lon": HERE[1]}


# Fixes 150 and 80 minutes ago, five nm apart: nothing in the last hour or two, both in three.
FIVE_NM = 5 / 60
track_3h = [point(600, 0.3), point(150, FIVE_NM), point(80)]
want_3h = U.nm_between((track_3h[1]["lat"], track_3h[1]["lon"]),
                       (track_3h[2]["lat"], track_3h[2]["lon"])) / (70 / 60)


def page_case(air, sog, seen_mins, track):
    d = copy.deepcopy(latest0)
    d["updated_utc"] = U.iso(NOW)
    d["position"] = dict(d.get("position") or {}, lat=HERE[0], lon=HERE[1], sog_kn=sog,
                         seen_utc=U.iso(NOW - timedelta(minutes=seen_mins)),
                         source="foundation")
    d["weather"] = {"fetched_utc": U.iso(NOW), "sea": {"wave_height_m": 2.2}, "air": air}
    return d, {"points": track}


cases = {
    "metno_3h": page_case(dict(temp_c=21.5, wind_ms=7.6, wind_direction_deg=248,
                               weather_code=80, source="met.no",
                               fetched_utc=U.iso(NOW)), None, 80, track_3h),
    "held_fresh_sog": page_case(dict(good_air, held=True), 6.3, 5,
                                track_3h + [point(5)]),
    "nothing": page_case(dict(good_air, fetched_utc=U.iso(NOW)), None, 500,
                         [point(900, 0.2), point(500)]),
    "fresh": page_case(dict(good_air, fetched_utc=U.iso(NOW)), None, 10,
                       [point(40, FIVE_NM), point(10)]),
}


class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(site), **k)

    def log_message(self, *a):
        pass


socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{httpd.server_address[1]}/index.html"

seen, errors = {}, []
with sync_playwright() as pw:
    br = pw.chromium.launch(args=["--no-sandbox"])
    for name, (latest, track) in cases.items():
        (site / "data" / "latest.json").write_text(json.dumps(latest), encoding="utf-8")
        (site / "data" / "track.json").write_text(json.dumps(track), encoding="utf-8")
        pg = br.new_page(viewport={"width": 1280, "height": 1400})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        for pattern in ("**://*.tile.openstreetmap.org/**", "**://tiles.openseamap.org/**",
                        "**://gibs.earthdata.nasa.gov/**", "**://gc.zgo.at/**"):
            pg.route(pattern, lambda r: r.abort())
        pg.goto(url, wait_until="networkidle")
        pg.wait_for_timeout(2000)
        pg.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")
        pg.wait_for_timeout(300)
        seen[name] = pg.evaluate("""() => {
            const tiles = [...document.querySelectorAll('#kpi > div')].map(d => d.innerText);
            const n = document.querySelector('.air-note');
            return {tiles, note: n ? n.innerText : null};
        }""")
        pg.close()
    br.close()
httpd.shutdown()

if errors:
    fails.append(f"the page threw: {errors[0][:200]}")


def speed_tile(name):
    t = (seen.get(name) or {}).get("tiles") or []
    return t[0].replace("\n", " ") if t else ""


s = speed_tile("metno_3h")
if "SPEED 3H" not in s.upper() or f"{want_3h:.1f}" not in s:
    fails.append(f"no fixes in the last hour or two, two in three: wanted Speed 3h at"
                 f" {want_3h:.1f} kn, the tile reads {s!r}")
s = speed_tile("held_fresh_sog")
if "6.3" not in s or "H" in s.upper().replace("SPEED", "").replace("KN", ""):
    fails.append(f"a fresh reported speed should be shown as plain Speed 6.3: {s!r}")
s = speed_tile("fresh")
if "SPEED 1H" not in s.upper():
    fails.append(f"two fixes inside the hour should give Speed 1h: {s!r}")
s = speed_tile("nothing")
if "–" not in s and "-" not in s:
    fails.append(f"no fixes in six hours and no speed: the tile should be a dash, got {s!r}")

note = (seen.get("metno_3h") or {}).get("note") or ""
if "MET Norway" not in note:
    fails.append(f"met.no's air is not credited in the weather card: {note!r}")
note = (seen.get("held_fresh_sog") or {}).get("note") or ""
if "3 h old" not in note:
    fails.append(f"a held air reading is not shown as old: {note!r}")
if (seen.get("fresh") or {}).get("note"):
    fails.append(f"a fresh Open-Meteo reading carries a note: {seen['fresh']['note']!r}")
wind_tile = ((seen.get("metno_3h") or {}).get("tiles") or ["", ""])[1]
if "7.6" not in wind_tile:
    fails.append(f"the Wind tile does not show met.no's wind: {wind_tile!r}")

shutil.rmtree(TMP, ignore_errors=True)
print({k: v.get("tiles", [""])[0:2] for k, v in seen.items()})
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
