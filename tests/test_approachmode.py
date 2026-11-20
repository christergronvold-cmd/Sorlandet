"""Approach mode: publish her every few minutes while she comes in or goes out.

18 November 2026, Las Palmas. Christer was on the quay as she came in and the map was
behind her the whole way in. Every position was fresh when published - but they were
published every half hour, so at 09:12 the map had her two miles south of the entrance
and kept her there until 09:42, by which time she was nearly alongside.

Near a port, while she moves or is due to sail, a round now listens six minutes instead
of twenty-nine, the workflow runs on hours rather than a count of rounds, and the page
asks every minute. Nothing here goes on the network.
"""
import contextlib
import http.server
import io
import json
import re
import shutil
import socketserver
import subprocess
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
NOW = U.now_utc()
TODAY = U.today_iso()
LP = {"name": "Las Palmas", "lat": 28.14529, "lon": -15.42772,
      "arrive": "2000-01-01", "depart": "2099-01-01"}


def check(label, got, want):
    if got != want:
        fails.append(f"{label}: got {got!r}, wanted {want!r}")


def pt(mins_ago, lat, lon, sog):
    return {"t": U.iso(NOW - timedelta(minutes=mins_ago)), "lat": lat, "lon": lon, "sog": sog}


def mode(points, ports=None, passages=None):
    real = U._ports_in_reach
    U._ports_in_reach = lambda plan, pts: list(plan)       # the port under test, always
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            return U.approach_mode({}, points, ports if ports is not None else [LP], passages)
    finally:
        U._ports_in_reach = real


# ---- 1. when it is on ----------------------------------------------------------------
# The real morning: 09:11 she was 1.9 nm south of the harbour at 6.5 kn.
coming_in = [pt(40, 28.1316, -15.251, 8.4), pt(10, 28.1167, -15.4017, 6.5)]
m = mode(coming_in)
check("coming in at 6.5 kn, two miles off: on", m.get("on"), True)
check("...and it says which port", m.get("port"), "Las Palmas")
if "moving" not in (m.get("why") or ""):
    fails.append(f"the reason does not say she is moving: {m}")

# Twenty-five miles out: a normal round. The open sea is not an approach.
far = [pt(40, 28.10, -14.95, 8.4), pt(10, 28.10, -14.97, 8.4)]
check("25 nm off: off", mode(far).get("on"), False)

# Alongside, not sailing today: off - a week at the quay must not be seven-minute rounds.
quay = [pt(40, 28.1460, -15.4277, 0.0), pt(10, 28.1460, -15.4277, 0.1)]
check("alongside on an ordinary day: off", mode(quay).get("on"), False)

# Alongside on the day she sails: on, before she moves - that is the point.
check("alongside on the day she sails: on",
      mode(quay, [dict(LP, depart=TODAY)]).get("on"), True)

# Moving, but heard two hours ago: we do not know she is moving NOW.
old = [pt(150, 28.12, -15.38, 7.0), pt(120, 28.13, -15.40, 7.0)]
check("a moving fix two hours old: off", mode(old).get("on"), False)

# No speed in the fixes (the foundation's feed sends none), but she has covered ground.
nospeed = [pt(40, 28.1167, -15.4017, None), pt(10, 28.1422, -15.4253, None)]
check("no speed reported, but 1.9 nm covered in half an hour: on",
      mode(nospeed).get("on"), True)

# A passage with a camera counts as somewhere to come in to.
river = {"name": "The mouth of the Guadalquivir", "lat": 36.78897, "lon": -6.35269}
up_river = [pt(30, 36.70, -6.45, 6.0), pt(5, 36.75, -6.40, 6.2)]
check("running in to a camera passage: on",
      mode(up_river, [], [river]).get("on"), True)

# Anything odd must give a normal round, never an exception.
check("nonsense in the track: off, no exception",
      mode([{"t": "not a time", "lat": "x", "lon": None}]).get("on"), False)

# ---- 2. what it does to the round ----------------------------------------------------
with contextlib.redirect_stdout(io.StringIO()):
    check("normal round listens 29 minutes", U.round_listen_seconds(1740, {"on": False}), 1740)
    check("approach round listens six", U.round_listen_seconds(1740, {"on": True, "why": "x"}),
          U.APPROACH_LISTEN)
    check("the quick pass stays quick", U.round_listen_seconds(20, {"on": True, "why": "x"}), 20)
    check("a round started late stops at the end of the run",
          U.round_listen_seconds(1740, {"on": False}, "900"), 660)
    check("...and never below thirty seconds",
          U.round_listen_seconds(1740, {"on": False}, "100"), 30)
if not (300 <= U.APPROACH_LISTEN <= 420):
    fails.append(f"APPROACH_LISTEN is {U.APPROACH_LISTEN}s - wanted about six minutes, so a"
                 f" round is about seven and Pages is not asked more than ~10 times an hour")

# The nearby-ship listen shrinks to a minute in approach mode.
seen = {}


class WS:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def send(self, d): pass
    def recv(self, timeout=None): raise TimeoutError


def conn(url, **kw):
    return WS()


real_now, base = U.now_utc, NOW
ticks = iter(range(0, 100000, 30))
U.now_utc = lambda: base + timedelta(seconds=next(ticks))   # 30 s passes per look at the clock
U.APPROACH_ON, U.LISTEN_SECONDS = True, 360
try:
    with contextlib.redirect_stdout(io.StringIO()) as out:
        U.fetch_nearby_ships("K", 28.1, -15.4, connect=conn)
    m_ = re.search(r"in (\d+) s\)", out.getvalue())
    check("nearby ships listen a minute in approach mode", m_ and int(m_.group(1)), 60)
finally:
    U.now_utc, U.APPROACH_ON, U.LISTEN_SECONDS = real_now, False, 240

# main() decides it before anything listens, and writes it for the page.
src = (ROOT / "scripts" / "update.py").read_text()
main_src = src[src.index("def main() -> int:"):]
if main_src.index("approach_mode(") > main_src.index("collect_from_barentswatch"):
    fails.append("approach mode is decided after the listening has started")
if '"approach":' not in main_src:
    fails.append("latest.json does not say whether approach mode is on")

# ---- 3. the workflow runs on hours, and tells each round what is left ----------------
wf = (ROOT / ".github" / "workflows" / "update.yml").read_text()
if "ROUND_BUDGET_S=" not in wf:
    fails.append("the workflow does not tell a round how long is left (ROUND_BUDGET_S)")
if re.search(r"for round in \$\(seq", wf):
    fails.append("the workflow still counts eleven rounds - seven-minute rounds would end"
                 " the run after an hour and a quarter")
run = wf[wf.index("- name: Listen, save, repeat"):wf.index("- name: Start the next run")]
body = run[run.index("run: |") + len("run: |"):]
body = "\n".join(l[10:] if l.startswith(" " * 10) else l for l in body.splitlines())
body = re.sub(r"\$\{\{[^}]*\}\}", "5.5", body)
r = subprocess.run(["bash", "-n"], input=body, text=True, capture_output=True)
if r.returncode:
    fails.append(f"the workflow's run script does not parse: {r.stderr.strip()[:200]}")

# ---- 4. the page asks every minute while it is on -------------------------------------
site = Path(tempfile.mkdtemp()) / "site"
shutil.copytree(ROOT, site, ignore=shutil.ignore_patterns(".git", "tests", "scripts",
                                                          "__pycache__", ".cache"))
latest0 = json.loads((site / "data" / "latest.json").read_text())


class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(site), **k)

    def log_message(self, *a):
        pass


socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{httpd.server_address[1]}/index.html"
got = {}
errors = []
with sync_playwright() as pw:
    br = pw.chromium.launch(args=["--no-sandbox"])
    for name, appr in (("on", {"on": True, "port": "Las Palmas", "nm": 1.9,
                               "why": "moving, 1.9 nm from Las Palmas"}),
                       ("off", {"on": False})):
        (site / "data" / "latest.json").write_text(json.dumps(dict(latest0, approach=appr)))
        pg = br.new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))
        for p in ("**://*.tile.openstreetmap.org/**", "**://tiles.openseamap.org/**",
                  "**://gibs.earthdata.nasa.gov/**", "**://gc.zgo.at/**"):
            pg.route(p, lambda r: r.abort())
        pg.goto(URL, wait_until="load")
        pg.wait_for_timeout(2000)
        got[name] = pg.evaluate("() => ({ every: refreshEvery(),"
                                " stamp: document.getElementById('stamp').textContent })")
        pg.close()
    br.close()
httpd.shutdown()
shutil.rmtree(site.parent, ignore_errors=True)

check("approach on: the page asks every minute", got.get("on", {}).get("every"), 60000)
check("approach off: every five", got.get("off", {}).get("every"), 300000)
if "every minute" not in got.get("on", {}).get("stamp", ""):
    fails.append(f"the page does not say it refreshes every minute: {got.get('on')}")
if errors:
    fails.append(f"the page threw: {errors[0][:200]}")

if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
