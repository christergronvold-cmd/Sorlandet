"""When Open-Meteo will not answer the job, the wind must come from somewhere else.

13 November 2026. From the round at 15:34 UTC every wind figure in ahead.json was null and
wind.json stopped being renewed, while the waves kept coming. A new runner had taken over
the chain; Open-Meteo's forecast host stopped answering it, its marine host did not. The
log said only "could not fetch", so not even the HTTP status was on record.

Now: Open-Meteo first, MET Norway's locationforecast when it fails, and when both fail the
last good forecast is held for up to twelve hours instead of being replaced by nulls.

Only the network is stubbed - update._http, the one function every request goes through.
Everything above it runs for real: get_json, the met.no client and its cache, the
translation, fetch_ahead, fetch_grid, refresh_grids, and the page that draws the result.
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
from datetime import timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import update as U  # noqa: E402

fails = []
TMP = Path(tempfile.mkdtemp())
U.METNO_CACHE = TMP / "metno-cache"
U.AHEAD = TMP / "ahead.json"
U.WIND = TMP / "wind.json"
U.WAVES = TMP / "waves.json"

NOW = U.now_utc()
BASE = NOW.replace(minute=0, second=0, microsecond=0)
STAMP = lambda t: t.strftime("%Y-%m-%dT%H:00")
HTTPDATE = lambda t: t.strftime("%a, %d %b %Y %H:%M:%S GMT")


def check(label, got, want):
    if got != want:
        fails.append(f"{label}: got {got!r}, wanted {want!r}")


# ---------------------------------------------------------------- fake answers
SYMBOLS = ["clearsky_day", "fair_night", "partlycloudy_day", "cloudy", "fog",
           "lightrain", "heavyrainshowers_day", "rainandthunder", "snow"]


def metno_doc():
    """met.no compact, shaped as it really comes: hourly for 60 h, then six-hourly."""
    series = []
    hours = list(range(0, 60)) + list(range(60, 216, 6))
    for h in hours:
        t = BASE + timedelta(hours=h)
        step = "next_1_hours" if h < 60 else "next_6_hours"
        series.append({"time": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "data": {
            "instant": {"details": {"air_pressure_at_sea_level": 1015.0,
                                    "air_temperature": 18.0 + (h % 5),
                                    "wind_from_direction": 350.0 if h % 12 == 0 else 10.0,
                                    "wind_speed": round(4.0 + (h % 7), 1),
                                    "relative_humidity": 70.0}},
            step: {"summary": {"symbol_code": SYMBOLS[h % len(SYMBOLS)]},
                   "details": {"precipitation_amount": 0.0}}}})
    return {"type": "Feature", "properties": {"timeseries": series}}


def om_blocks(n, marine):
    stamps = [STAMP(BASE - timedelta(hours=48) + timedelta(hours=h)) for h in range(0, 300)]
    one = {"latitude": 36.0, "longitude": -8.0, "hourly": dict({"time": stamps}, **(
        {"wave_height": [1.4] * len(stamps), "wave_direction": [310] * len(stamps),
         "wave_period": [9.0] * len(stamps)} if marine else
        {"wind_speed_10m": [5.5] * len(stamps), "wind_direction_10m": [280] * len(stamps),
         "wind_gusts_10m": [8.0] * len(stamps), "temperature_2m": [17.4] * len(stamps),
         "weather_code": [2] * len(stamps)}))}
    return [json.loads(json.dumps(one)) for _ in range(n)]


class Net:
    """Stands in for U._http. `forecast`, `marine`, `metno` say how each host behaves."""

    def __init__(self, forecast="ok", marine="ok", metno="ok"):
        self.forecast, self.marine, self.metno = forecast, marine, metno
        self.calls = []

    def __call__(self, url, headers=None, timeout=30):
        self.calls.append((url, dict(headers or {})))
        n = len(url.split("latitude=")[1].split("&")[0].split("%2C")) if "latitude=" in url else 1
        if "api.met.no" in url:
            if self.metno == "ok":
                if (headers or {}).get("If-Modified-Since"):
                    return 304, {"expires": HTTPDATE(NOW + timedelta(hours=1))}, b""
                return 200, {"expires": HTTPDATE(NOW + timedelta(hours=1)),
                             "last-modified": HTTPDATE(NOW - timedelta(minutes=20))}, \
                    json.dumps(metno_doc()).encode()
            return 503, {}, b"Service Unavailable"
        if "marine-api.open-meteo.com" in url:
            if self.marine == "ok":
                return 200, {}, json.dumps(om_blocks(n, True)).encode()
            return 500, {}, b'{"error":true,"reason":"marine down"}'
        if "api.open-meteo.com" in url:
            if self.forecast == "ok":
                return 200, {}, json.dumps(om_blocks(n, False)).encode()
            return 429, {}, (b'{"error":true,"reason":"Daily API request limit exceeded. '
                             b'Please try again tomorrow."}')
        raise AssertionError(f"unexpected request {url}")

    def metno_calls(self):
        return [c for c in self.calls if "api.met.no" in c[0]]


def run(net, fn, *a):
    """Call fn with the network replaced, returning (result, everything it logged)."""
    keep = U._http
    buf = io.StringIO()
    U._http = net
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            got = fn(*a)
    finally:
        U._http = keep
    return got, buf.getvalue()


def fresh_cache():
    shutil.rmtree(U.METNO_CACHE, ignore_errors=True)


HOURS = [0, 6, 12, 24, 36, 48, 72, 96, 120, 144, 168]
LADDER = [{"hours": h, "lat": 36.0 - h * 0.02, "lon": -8.0 - h * 0.03, "where": "sea"}
          for h in HOURS]

# ---- 1. met.no's answer, translated into our fields ----------------------------------
check("clearsky_day", U.metno_wmo("clearsky_day"), 0)
check("fair_night", U.metno_wmo("fair_night"), 1)
check("partlycloudy_polartwilight", U.metno_wmo("partlycloudy_polartwilight"), 2)
check("cloudy", U.metno_wmo("cloudy"), 3)
check("fog", U.metno_wmo("fog"), 45)
check("lightrain", U.metno_wmo("lightrain"), 61)
check("heavyrainshowers_day", U.metno_wmo("heavyrainshowers_day"), 82)
check("lightssnowshowersandthunder_day (met.no's own spelling)",
      U.metno_wmo("lightssnowshowersandthunder_day"), 95)
check("snow", U.metno_wmo("snow"), 73)
check("an unknown symbol draws no sky rather than a wrong one", U.metno_wmo("dragons"), None)
check("no symbol", U.metno_wmo(None), None)

hrs = U.metno_hours(metno_doc())
h5 = hrs[STAMP(BASE + timedelta(hours=5))]
check("hour 5, as met.no gave it", h5,
      {"wind_ms": 9.0, "wind_dir": 10.0, "gust_ms": None, "temp_c": 18.0, "weather_code": 61})
h6 = hrs[STAMP(BASE + timedelta(hours=60))]
check("from 60 h on the sky comes from next_6_hours",
      h6["weather_code"], U.metno_wmo(SYMBOLS[60 % len(SYMBOLS)]))
# 63 h falls half way between 60 h and 66 h.
mid = U.metno_at(hrs, STAMP(BASE + timedelta(hours=63)))
a, b = hrs[STAMP(BASE + timedelta(hours=60))], hrs[STAMP(BASE + timedelta(hours=66))]
check("between six-hourly steps the wind speed is drawn on a straight line",
      mid["wind_ms"], round((a["wind_ms"] + b["wind_ms"]) / 2, 1))
# 72 h is a multiple of 12: 350 degrees; 66 h is 10 degrees. Half way is 0, not 180.
dirs = U.metno_at(hrs, STAMP(BASE + timedelta(hours=69)))["wind_dir"]
if dirs not in (0, 360):
    fails.append(f"from 10 to 350 degrees, half way is north, the short way round: got {dirs}")
if U.metno_at(hrs, STAMP(BASE + timedelta(hours=400))) is not None:
    fails.append("an hour beyond met.no's horizon was invented")

# ---- 2. Open-Meteo refuses: the ladder ahead comes from met.no -----------------------
fresh_cache()
net = Net(forecast="429")
rows, log = run(net, U.fetch_ahead, LADDER)
check("a row per hour", [r["hours"] for r in rows], HOURS)
check("every hour has a wind from met.no",
      [r["wind_ms"] is not None for r in rows], [True] * len(HOURS))
want = U.metno_at(hrs, STAMP(BASE + timedelta(hours=6)))
check("hour 6 is met.no's hour 6", {k: rows[1][k] for k in U.WIND_KEYS}, want)
check("the waves still come from Open-Meteo's marine host",
      {r["wave_m"] for r in rows}, {1.4})
check("the file says where the wind came from",
      rows.meta.get("wind_source"), "met.no")
check("...and that it is not held", rows.meta.get("wind_held"), False)
for words in ("HTTP 429", "Daily API request limit exceeded", "api.open-meteo.com", "met.no"):
    if words not in log:
        fails.append(f"the log does not say {words!r}; it said:\n{log}")
ua = {c[1].get("User-Agent") for c in net.metno_calls()}
if not ua or not all(u and U.REPO_URL in u for u in ua):
    fails.append(f"met.no was asked without the repo in the User-Agent: {ua}")
asked = net.metno_calls()
if any("lat=" not in c[0] or len(c[0].split("lat=")[1].split("&")[0].split(".")[-1]) > 4
       for c in asked):
    fails.append("met.no was asked with more than four decimals")

# Within Expires, nothing is asked again.
net2 = Net(forecast="429")
rows2, _ = run(net2, U.fetch_ahead, LADDER)
check("within Expires the cache answers and met.no is not asked", len(net2.metno_calls()), 0)
check("...and the rows are the same", [r["wind_ms"] for r in rows2], [r["wind_ms"] for r in rows])

# After Expires: If-Modified-Since, and a 304 is a good answer.
for f in U.METNO_CACHE.glob("*.json"):
    c = json.loads(f.read_text())
    c["expires"] = HTTPDATE(NOW - timedelta(minutes=1))
    f.write_text(json.dumps(c))
net3 = Net(forecast="429")
rows3, _ = run(net3, U.fetch_ahead, LADDER)
ims = [c[1].get("If-Modified-Since") for c in net3.metno_calls()]
if not ims or not all(ims):
    fails.append(f"an expired copy was fetched again without If-Modified-Since: {ims}")
check("a 304 still gives the wind", [r["wind_ms"] for r in rows3], [r["wind_ms"] for r in rows])

# A week at a quay is one place, so one request.
fresh_cache()
net4 = Net(forecast="429")
run(net4, U.fetch_ahead, [{"hours": h, "lat": 37.38, "lon": -6.0, "where": "port"} for h in HOURS])
check("eleven hours at one quay ask met.no once", len(net4.metno_calls()), 1)

# Open-Meteo answering means met.no is never asked.
net5 = Net()
rows5, _ = run(net5, U.fetch_ahead, LADDER)
check("with Open-Meteo up, met.no is left alone", len(net5.metno_calls()), 0)
check("...and the file says open-meteo", rows5.meta.get("wind_source"), "open-meteo")

# ---- 3. the wind grid, from met.no, on the axis the waves use ------------------------
fresh_cache()
axis = U._grid_axis()
past = [t for t in axis if t < STAMP(NOW)]
U.write_json(U.WIND, {
    "generated_utc": U.iso(NOW - timedelta(hours=3)), "times": axis, "step_hours": 1,
    "centre": [36.0, -8.0],
    "cells": [{"lat": 36.0 + i * 0.6, "lon": -8.0 + j * 0.7, "ws": [3.3] * len(axis),
               "wd": [200] * len(axis), "wc": [3] * len(axis), "t2": [16.0] * len(axis)}
              for i in range(-4, 5) for j in range(-4, 5)]})
net6 = Net(forecast="429", marine="500")
grid, log = run(net6, U.fetch_grid, 36.0, -8.0, "wind")
if not grid:
    fails.append(f"no wind grid from met.no; the log said:\n{log}")
else:
    check("the grid says it is met.no's", grid.get("source"), "met.no")
    check("the grid runs over the same hours as the wave grid would", grid["times"], axis)
    if len(net6.metno_calls()) > 25:
        fails.append(f"{len(net6.metno_calls())} requests to met.no for one grid; the point"
                     f" was to keep that down")
    c = grid["cells"][len(grid["cells"]) // 2]
    k_now = axis.index(STAMP(BASE))
    if c["ws"][k_now] is None or c["wd"][k_now] is None or c["wc"][k_now] is None:
        fails.append(f"the grid has no wind, direction or sky now: {c['ws'][k_now]}, "
                     f"{c['wd'][k_now]}, {c['wc'][k_now]}")
    if past and c["ws"][0] != 3.3:
        fails.append(f"met.no has no past, and the hours behind her were not taken from the"
                     f" last grid - the rewind slider would be empty (got {c['ws'][0]})")
    if "wind grid from met.no" not in log:
        fails.append(f"the log does not say the grid came from met.no:\n{log}")

# ---- 4. both refuse: the last good forecast, not nulls -------------------------------
fresh_cache()
OLD = {"generated_utc": U.iso(NOW - timedelta(minutes=30)),
       "wind_source": "open-meteo", "wind_fetched_utc": U.iso(NOW - timedelta(hours=3)),
       "wind_held": False,
       "points": [{"hours": h, "time_utc": U.iso(BASE - timedelta(hours=3) + timedelta(hours=h)),
                   "wind_ms": 6.0 + h / 100, "wind_dir": 240, "gust_ms": 9.0, "temp_c": 19.0,
                   "weather_code": 1, "wave_m": 1.0} for h in [0, 3, 6, 9, 12, 24, 36, 48, 72,
                                                              96, 120, 144, 168, 192]]}
U.write_json(U.AHEAD, OLD)
net7 = Net(forecast="429", metno="503")
rows7, log = run(net7, U.fetch_ahead, LADDER)
empty = [r["hours"] for r in rows7 if r["wind_ms"] is None]
if empty:
    fails.append(f"both sources failed and these hours were written as null: {empty}")
check("the hold is marked", rows7.meta.get("wind_held"), True)
check("...and dated by the forecast it holds, not by this round",
      rows7.meta.get("wind_fetched_utc"), OLD["wind_fetched_utc"])
check("...from the source it came from", rows7.meta.get("wind_source"), "open-meteo")
check("the sky is held with the wind", {r["weather_code"] for r in rows7}, {1})
for words in ("HTTP 429", "HTTP 503", "held"):
    if words not in log:
        fails.append(f"with both down the log does not say {words!r}; it said:\n{log}")
check("met.no answered 503 and was not asked again for the other places",
      len(net7.metno_calls()), 1)

# Holding it again a round later still dates it by the original fetch.
U.write_json(U.AHEAD, {"generated_utc": U.iso(NOW), "points": rows7, **rows7.meta})
rows8, _ = run(Net(forecast="429", metno="503"), U.fetch_ahead, LADDER)
check("a second round of holding keeps the first fetch time",
      rows8.meta.get("wind_fetched_utc"), OLD["wind_fetched_utc"])

# Past twelve hours it is no longer a forecast worth showing.
U.write_json(U.AHEAD, dict(OLD, wind_fetched_utc=U.iso(NOW - timedelta(hours=13))))
rows9, _ = run(Net(forecast="429", metno="503"), U.fetch_ahead, LADDER)
if any(r["wind_ms"] is not None for r in rows9):
    fails.append("a forecast thirteen hours old was held; the limit is twelve")
check("...and then nothing is claimed to be held", rows9.meta.get("wind_held"), False)

# The wind grid: kept while young, taken off when it is not.
young = json.loads(U.WIND.read_text())
young["generated_utc"] = U.iso(NOW - timedelta(hours=3))
U.write_json(U.WIND, young)
_, log = run(Net(forecast="429", marine="500", metno="503"), U.refresh_grids, 36.0, -8.0)
after = json.loads(U.WIND.read_text())
check("a three-hour-old wind grid is kept as it was when nothing answers", after, young)
if "keeping the one from" not in log:
    fails.append(f"the log does not say the grid was kept:\n{log}")
young["generated_utc"] = U.iso(NOW - timedelta(hours=13))
U.write_json(U.WIND, young)
run(Net(forecast="429", marine="500", metno="503"), U.refresh_grids, 36.0, -8.0)
after = json.loads(U.WIND.read_text())
check("a thirteen-hour-old grid is taken off the map", after.get("cells"), [])
check("...but keeps a time axis, so the wave grid's slider still works",
      after.get("times"), U._grid_axis())

# ---- 5. the page says the forecast is old, and credits met.no ------------------------
site = TMP / "site"
shutil.copytree(ROOT, site, ignore=shutil.ignore_patterns(".git", "tests", "scripts",
                                                          "__pycache__", ".cache"))


def ahead_file(**meta):
    return json.dumps(dict({
        "generated_utc": U.iso(NOW), "to": "Las Palmas", "country": "Gran Canaria",
        "distance_nm": 600.0, "direct_nm": 590.0, "route_basis": "direct", "speed_kn": 5.0,
        "pace_basis": "made good easing to the plan",
        "route": [[36.6, -6.7], [28.1, -15.4]],
        "points": [{"hours": h, "time_utc": U.iso(BASE + timedelta(hours=h)), "lat": 36.0,
                    "lon": -8.0, "wind_ms": 6.0, "wind_dir": 240, "gust_ms": None,
                    "temp_c": 19.0, "weather_code": 1, "wave_m": 1.4, "wave_dir": 300,
                    "wave_period_s": 9, "where": "sea"} for h in HOURS]}, **meta))


class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(site), **k)

    def log_message(self, *a):
        pass


socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{httpd.server_address[1]}/index.html"

cases = {
    "held": dict(wind_source="open-meteo", wind_held=True,
                 wind_fetched_utc=U.iso(NOW - timedelta(hours=5))),
    "metno": dict(wind_source="met.no", wind_held=False, wind_fetched_utc=U.iso(NOW)),
    "fresh": dict(wind_source="open-meteo", wind_held=False, wind_fetched_utc=U.iso(NOW)),
}
seen, errors = {}, []
with sync_playwright() as pw:
    br = pw.chromium.launch(args=["--no-sandbox"])
    for name, meta in cases.items():
        (site / "data" / "ahead.json").write_text(ahead_file(**meta), encoding="utf-8")
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
            const card = document.querySelector('[data-fold="ahead"]');
            const n = card && card.querySelector('.wind-note');
            return {card: !!card, note: n ? n.innerText : null};
        }""")
        pg.close()
    br.close()
httpd.shutdown()

if errors:
    fails.append(f"the page threw: {errors[0][:160]}")
if not seen["held"]["card"]:
    fails.append("no forecast card on the page")
note = seen["held"]["note"] or ""
if "5 h old" not in note:
    fails.append(f"a held forecast is not shown as old: note was {note!r}")
if "MET Norway" not in (seen["metno"]["note"] or ""):
    fails.append(f"met.no's wind is not credited: note was {seen['metno']['note']!r}")
if seen["fresh"]["note"]:
    fails.append(f"a fresh Open-Meteo forecast carries a note: {seen['fresh']['note']!r}")

shutil.rmtree(TMP, ignore_errors=True)
print(f"page notes: held={seen.get('held')}, metno={seen.get('metno')}")
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
