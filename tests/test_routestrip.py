"""The full-route strip gives the window she is in port, not only the day she arrives.

"Der det nå kun står 22 på Funchal, så vil jeg ha 22 - (avreise dato)."

The arrival answers "when is she there". It does not answer "how long have I got", and
that is the question a parent acts on - somebody deciding whether to fly to Madeira needs
to know she sails again on the 28th. Both dates were in the plan from the start; only one
of them was ever printed.

The tiles are 96 px wide, so a stay inside one month collapses to a single month name:
"22-28 Oct", not "22 Oct - 28 Oct". Across a month boundary it has to spell both out.

The month names come from the browser, not from Python. en-GB writes September as "Sept",
not "Sep", and the first version of this test failed St. Malo over that one letter while
the page was perfectly correct. Asserting a locale's abbreviations against strftime is
testing the wrong thing; the range logic is what this file is about, so that is what it
builds itself and the month name is asked of the page.

This drives the real page and reads the strip, rather than testing the formatter in
isolation, because the formatter being right and the strip still showing the old value is
exactly the sort of gap that has bitten this page before.
"""
import http.server
import json
import shutil
import socketserver
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
fails = []

plan = json.loads((ROOT / "data" / "ports.json").read_text(encoding="utf-8"))
ports = plan["ports"]


NAMED = {}        # iso date -> how the browser writes it; filled in from the page below


def want(q):
    """What that port's tile should read."""
    a, b = q.get("arrive"), q.get("depart")
    if not a:
        return None
    if not b or b == a:
        return NAMED[a]
    A = datetime.strptime(a, "%Y-%m-%d")
    B = datetime.strptime(b, "%Y-%m-%d")
    return (f"{A.day}\u2013{NAMED[b]}" if (A.year, A.month) == (B.year, B.month)
            else f"{NAMED[a]} \u2013 {NAMED[b]}")


tmp = Path(tempfile.mkdtemp()) / "site"
shutil.copytree(ROOT, tmp,
                ignore=shutil.ignore_patterns(".git", "tests", "scripts", "__pycache__"))


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
    pg = b.new_page(viewport={"width": 1280, "height": 1600})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    for pattern in ("**://*.tile.openstreetmap.org/**", "**://tiles.openseamap.org/**",
                    "**://gibs.earthdata.nasa.gov/**", "**://gc.zgo.at/**"):
        pg.route(pattern, lambda r: r.abort())
    pg.goto(f"http://127.0.0.1:{PORT}/index.html", wait_until="networkidle")
    pg.wait_for_timeout(2500)
    # The strip folds. Open every <details> so the text is there to read.
    pg.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")
    pg.wait_for_timeout(400)
    body = pg.inner_text("body")
    dates = sorted({d for q in ports for d in (q.get("arrive"), q.get("depart")) if d})
    NAMED.update(pg.evaluate(
        """(ds) => Object.fromEntries(ds.map(d => [d, new Date(d + "T12:00:00Z")
             .toLocaleDateString("en-GB", { day: "numeric", month: "short" })]))""",
        dates))
    b.close()
httpd.shutdown()
shutil.rmtree(tmp.parent, ignore_errors=True)

if errors:
    fails.append(f"the page threw while rendering: {errors[0][:160]}")

staying = [q for q in ports if q.get("depart") and q["depart"] != q.get("arrive")]
if not staying:
    fails.append("no port in the plan has a departure date; this test has nothing to check")

for q in staying:
    wanted = want(q)
    if wanted not in body:
        fails.append(f"{q['name']}: the strip should read {wanted!r} "
                     f"(arrives {q['arrive']}, sails {q['depart']}) and does not")

print(f"checked {len(staying)} ports that have a departure date, "
      f"e.g. {staying[0]['name']}: {want(staying[0])!r}")
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
