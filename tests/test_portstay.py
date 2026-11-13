"""A quiet night at the quay is not a departure.

Sevilla, 4 to 12 November 2026. She came up the river on the 4th and lay at the
Muelle de las Delicias for the rest of the week, never more than 0.15 nm from the port's
position. But the feeds hear a ship alongside only now and then, and several nights the
gap between two fixes was nine, ten, twelve hours. portStays cut the stay in two at every
gap longer than PORT_REJOIN_H, six hours, so the event log said "Left Sevilla" - twice -
while she lay tied up at the quay.

The six-hour rule is for something else: she is seen OUTSIDE the port, then back inside.
Then the length of the absence decides whether she went somewhere or only shifted berth.
A gap with nothing seen in between is not an absence. Every fix she gave was in Sevilla.

Both halves are driven against the real page: the recorded fixes must make one stay that
has not ended, and the same fixes with one sighting down the river put in, followed by a
long silence, must still make two.
"""
import http.server
import json
import shutil
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
fails = []

# Recorded fixes, 4-12 November 2026, from data/track.json as it stood on the 13th. The
# first two are coming up the river, outside the five-mile radius; every one after that
# lies within 0.15 nm of the quay.
SEVILLA = [
    {"t": "2026-11-04T09:46:42Z", "lat": 37.13907, "lon": -6.09688, "sog": 11.4, "ns": 0},
    {"t": "2026-11-04T10:18:33Z", "lat": 37.22937, "lon": -6.06444, "sog": 11.3, "ns": 0},
    {"t": "2026-11-04T12:15:26Z", "lat": 37.365, "lon": -5.99167, "sog": 3.3},
    {"t": "2026-11-04T13:23:26Z", "lat": 37.36592, "lon": -5.99181, "sog": 0.0, "ns": 5},
    {"t": "2026-11-04T15:27:24Z", "lat": 37.36833, "lon": -5.99167},
    {"t": "2026-11-04T23:12:25Z", "lat": 37.37, "lon": -5.99167},
    {"t": "2026-11-05T00:35:19Z", "lat": 37.37132, "lon": -5.99191, "sog": 0.0, "ns": 5},
    {"t": "2026-11-05T09:18:26Z", "lat": 37.37, "lon": -5.99167},
    {"t": "2026-11-05T10:51:26Z", "lat": 37.37, "lon": -5.99167, "sog": 0.0},
    {"t": "2026-11-05T13:45:15Z", "lat": 37.37131, "lon": -5.99187, "sog": 0.0, "ns": 5},
    {"t": "2026-11-05T22:42:27Z", "lat": 37.37, "lon": -5.99167},
    {"t": "2026-11-06T00:58:40Z", "lat": 37.3713, "lon": -5.9919, "sog": 0.0, "ns": 0},
    {"t": "2026-11-06T09:09:18Z", "lat": 37.37132, "lon": -5.99189, "sog": 0.0, "ns": 0},
    {"t": "2026-11-06T11:03:29Z", "lat": 37.37, "lon": -5.99167},
    {"t": "2026-11-06T12:21:29Z", "lat": 37.37, "lon": -5.99167, "sog": 0.0},
    {"t": "2026-11-06T22:36:26Z", "lat": 37.37, "lon": -5.99167},
    {"t": "2026-11-07T01:07:38Z", "lat": 37.3713, "lon": -5.99188, "sog": 0.0, "ns": 0},
    {"t": "2026-11-07T09:21:14Z", "lat": 37.37132, "lon": -5.99189, "sog": 0.0, "ns": 0},
    {"t": "2026-11-07T14:27:30Z", "lat": 37.37, "lon": -5.99167},
    {"t": "2026-11-07T22:57:32Z", "lat": 37.37, "lon": -5.99167},
    {"t": "2026-11-08T01:40:41Z", "lat": 37.37132, "lon": -5.9919, "sog": 0.0, "ns": 0},
    {"t": "2026-11-08T10:44:25Z", "lat": 37.37131, "lon": -5.99191, "sog": 0.0, "ns": 0},
    {"t": "2026-11-08T11:30:33Z", "lat": 37.37, "lon": -5.99167},
    {"t": "2026-11-08T23:21:34Z", "lat": 37.37, "lon": -5.99167},
    {"t": "2026-11-09T01:48:04Z", "lat": 37.3713, "lon": -5.99189, "sog": 0.0, "ns": 0},
    {"t": "2026-11-09T12:38:20Z", "lat": 37.37134, "lon": -5.99191, "sog": 0.1, "ns": 5},
    {"t": "2026-11-10T01:03:14Z", "lat": 37.37133, "lon": -5.9919, "sog": 0.0, "ns": 5},
    {"t": "2026-11-10T02:13:03Z", "lat": 37.37131, "lon": -5.99188, "sog": 0.0, "ns": 5},
    {"t": "2026-11-10T09:29:24Z", "lat": 37.37133, "lon": -5.9919, "sog": 0.0, "ns": 5},
    {"t": "2026-11-10T10:29:36Z", "lat": 37.37131, "lon": -5.99191, "sog": 0.0, "ns": 5},
    {"t": "2026-11-10T12:39:37Z", "lat": 37.37, "lon": -5.99167},
    {"t": "2026-11-10T22:33:38Z", "lat": 37.37, "lon": -5.99167},
    {"t": "2026-11-11T00:15:56Z", "lat": 37.37132, "lon": -5.99187, "sog": 0.0, "ns": 0},
    {"t": "2026-11-11T11:48:38Z", "lat": 37.37, "lon": -5.99167},
    {"t": "2026-11-11T13:00:53Z", "lat": 37.37132, "lon": -5.9919, "sog": 0.0, "ns": 0},
    {"t": "2026-11-11T21:30:42Z", "lat": 37.37, "lon": -5.99167},
    {"t": "2026-11-12T00:40:06Z", "lat": 37.37133, "lon": -5.9919, "sog": 0.0, "ns": 0},
    {"t": "2026-11-12T08:43:35Z", "lat": 37.37132, "lon": -5.9919, "sog": 0.0, "ns": 0},
    {"t": "2026-11-12T15:33:40Z", "lat": 37.37, "lon": -5.99167},
    {"t": "2026-11-12T21:18:43Z", "lat": 37.37, "lon": -5.99167},
    {"t": "2026-11-12T22:18:42Z", "lat": 37.37, "lon": -5.99167, "sog": 0.0},
]

# The other half: seen nine miles down the river in the small hours of the 8th, and not
# heard again until mid-morning at the quay - nine hours after the last fix in port.
WENT_OUT = sorted(SEVILLA + [{"t": "2026-11-08T05:00:00Z", "lat": 37.22937,
                              "lon": -6.06444, "sog": 6.0, "ns": 0}],
                  key=lambda p: p["t"])

PORTS = json.loads((ROOT / "data" / "ports.json").read_text(encoding="utf-8"))["ports"]


class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(SITE), **k)

    def log_message(self, *a):
        pass


SITE = Path(tempfile.mkdtemp()) / "site"
shutil.copytree(ROOT, SITE, ignore=shutil.ignore_patterns(".git", "tests", "scripts",
                                                          "__pycache__"))
socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--no-sandbox"])
        pg = b.new_page()
        local = f"http://127.0.0.1:{httpd.server_address[1]}/"
        # Nothing leaves the machine - see test_lens for what a live camera stream does.
        pg.route("**/*", lambda r: r.continue_() if r.request.url.startswith(local)
                 else r.abort())
        pg.goto(local + "index.html", wait_until="domcontentloaded")
        run = """([pts, ports]) => ({
            stays: portStays(pts, ports).map(v => ({port: v.port, in: v.in, out: v.out,
                                                    ended: v.ended})),
            calls: portCalls(pts, ports).map(c => c.text)})"""
        stayed = pg.evaluate(run, [SEVILLA, PORTS])
        went = pg.evaluate(run, [WENT_OUT, PORTS])
        b.close()
finally:
    httpd.shutdown()
    shutil.rmtree(SITE.parent, ignore_errors=True)

# --------------------------------------------------------- a week alongside is one stay
sev = [v for v in stayed["stays"] if v["port"] == "Sevilla"]
if len(sev) != 1:
    fails.append(f"a week alongside in Sevilla came out as {len(sev)} stays, not one -"
                 f" a long silence between two fixes at the quay was read as leaving:"
                 f" {[(v['in'], v['out']) for v in sev]}")
elif sev[0]["ended"]:
    fails.append("the Sevilla stay is marked as ended while every fix, the last included,"
                 " is at the quay")
lefts = [c for c in stayed["calls"] if c.startswith("Left")]
if lefts:
    fails.append(f"the event log says {lefts} for a ship that never left the quay")
if not any(c.startswith("Arrived Sevilla") for c in stayed["calls"]):
    fails.append(f"the arrival in Sevilla is missing from the log: {stayed['calls']}")

# ------------------------------------- seen outside, then a long silence: that is leaving
sev2 = [v for v in went["stays"] if v["port"] == "Sevilla"]
if len(sev2) != 2:
    fails.append(f"seen nine miles down the river and back nine hours later made"
                 f" {len(sev2)} stays, not two - the six-hour rule must still apply when"
                 f" she was seen outside the port in between")

print(f"{len(SEVILLA)} recorded fixes in Sevilla, 4-12 November: {len(sev)} stay(s),"
      f" log {stayed['calls']}; with a sighting down the river: {len(sev2)} stay(s)")
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
