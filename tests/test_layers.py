"""Which map layers are on when the page opens, and the two places that must agree.

Wind and weather on, waves off. Three layers of arrows and numbers over the same water is
more than anyone reads, and the sea state is on the card under the map as a figure anyway.

The trap this guards is not the choice but the bookkeeping: the state lives in `show` and
the tick lives in the markup, in two different places, and nothing but a person's eye has
been keeping them in step. A button that says Waves ✓ over a map with no waves on it is a
lie that costs nothing to introduce.
"""
import http.server
import socketserver
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
WANT = {"wind": True, "waves": False, "weather": True, "sat": False}
BUTTONS = {"wind": "windtoggle", "waves": "wavetoggle",
           "weather": "weathertoggle", "sat": "sattoggle"}
fails = []


def check(label, got, want):
    if got != want:
        fails.append(f"{label}: got {got!r}, wanted {want!r}")


class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(ROOT), **k)
    def log_message(self, *a):
        pass


socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()

with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1280, "height": 1000})
    # Tiles and the weather API are unreachable from here and irrelevant to this.
    for pattern in ("**://*.tile.openstreetmap.org/**", "**://tiles.openseamap.org/**",
                    "**://gibs.earthdata.nasa.gov/**"):
        pg.route(pattern, lambda r: r.abort())
    pg.goto(f"http://127.0.0.1:{PORT}/index.html", wait_until="load")
    pg.wait_for_timeout(3000)

    state = pg.evaluate("() => JSON.parse(JSON.stringify(show))")
    for key, want in WANT.items():
        check(f"show.{key} at load", state.get(key), want)

    # ...and the buttons must say the same thing. Both the highlight and the tick.
    for key, el in BUTTONS.items():
        got = pg.evaluate(f"""() => {{
            const b = document.getElementById({el!r});
            return {{on: b.classList.contains('on'), tick: b.textContent.includes('✓')}};
        }}""")
        check(f"{el} highlighted", got["on"], WANT[key])
        check(f"{el} ticked", got["tick"], WANT[key])

    # Clicking still works both ways and keeps the two in step. Waves is the one that
    # changed, so click it on and off and watch the button follow.
    def wavestate():
        return pg.evaluate("""() => {
            const b = document.getElementById('wavetoggle');
            return {show: show.waves, on: b.classList.contains('on'),
                    tick: b.textContent.includes('✓')};
        }""")

    pg.click("#wavetoggle")
    pg.wait_for_timeout(200)
    after = wavestate()
    check("waves on after a click", after["show"], True)
    check("...button highlighted too", after["on"], True)
    check("...and ticked", after["tick"], True)

    pg.click("#wavetoggle")
    pg.wait_for_timeout(200)
    back = wavestate()
    check("waves off again", back["show"], False)
    check("...button dim again", back["on"], False)
    check("...and unticked", back["tick"], False)

    # Turning waves off must not take the wind arrows with it - they are drawn by the same
    # function off the same grid.
    check("wind is still on", pg.evaluate("() => show.wind"), True)
    check("weather is still on", pg.evaluate("() => show.weather"), True)

    b.close()

if fails:
    print("FAILURES:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
