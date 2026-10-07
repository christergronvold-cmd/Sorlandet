"""Dated-path still cameras: find the newest frame, and do not save it three times.

Skaping carry Port Vauban in St. Malo and gave permission on 22 September 2026. Their
pictures live at a dated path - .../video/2026/09/26/08-15.jpg - with no "current" alias,
and their clock is not ours: on 22 September the newest frame was stamped 11-30 while UTC
read 10:31 and Paris read 12:31. So the job scans for the newest frame rather than
computing the path from an assumed offset.

Two bugs this guards, both of which cost real pictures:

  - grab_frame and grab_still asked for a user agent called CONTACT_UA, which is the name
    of the ENVIRONMENT VARIABLE, not of the constant. Every call raised NameError, the
    surrounding except swallowed it and printed a line about the camera, and the job
    carried on. Not one frame could be taken from 31 August to 22 September. The tests of
    the day replaced both functions with stubs, so nothing ever ran the real body.

  - The scan then probed with HEAD, because asking for a hundred headers is politer than
    asking for a hundred pictures. That was an assumption about somebody else's server,
    untested, and when she came into St. Malo on 25 September the capture rule fired on
    eight fixes and not one frame was saved. It fetches with GET now.

Both were invisible from outside. So: run the real bodies against a fake server, and
assert on what comes out.
"""
import os
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("AISSTREAM_API_KEY", "fake")
import update as U                                                   # noqa: E402

NOW = datetime(2026, 9, 26, 10, 31, tzinfo=timezone.utc)
U.now_utc = lambda: NOW
TPL = "https://example.test/port-vauban/video/%Y/%m/%d/%H-%M.jpg"
JPEG = b"\xff\xd8\xff" + b"x" * 40000
fails = []


def check(label, got, want):
    if got != want:
        fails.append(f"{label}: got {got!r}, wanted {want!r}")


# ---------------------------------------------------------------- a fake camera server
class Resp:
    def __init__(self, body=b""):
        self.status, self._b = 200, body
    def read(self):
        return self._b
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class Server:
    """Holds the frames that exist. Counts every request, and by method."""

    def __init__(self, exists, body=JPEG, head_403=False):
        self.exists, self.body, self.head_403 = set(exists), body, head_403
        self.calls = []

    def __call__(self, req, timeout=None):
        url, method = req.full_url.split("?")[0], req.get_method()
        self.calls.append((method, url))
        if method == "HEAD" and self.head_403:
            raise U.urllib.error.HTTPError(url, 403, "Forbidden", None, None)
        if url not in self.exists:
            raise U.urllib.error.HTTPError(url, 404, "Not Found", None, None)
        return Resp(b"" if method == "HEAD" else self.body)

    def gets(self):
        return [u for m, u in self.calls if m == "GET"]


def serve(exists, **kw):
    U._STILL_SKEW.clear()
    U._STILL_DEAD.clear()
    s = Server(exists, **kw)
    U.urllib.request.urlopen = s
    return s


# ---------------------------------------------------------------- flooring the bucket
check("floors to the quarter hour",
      U.still_at(TPL, datetime(2026, 9, 26, 8, 22, tzinfo=timezone.utc), 900),
      "https://example.test/port-vauban/video/2026/09/26/08-15.jpg")
check("a bucket boundary stays put",
      U.still_at(TPL, datetime(2026, 9, 26, 8, 15, tzinfo=timezone.utc), 900),
      "https://example.test/port-vauban/video/2026/09/26/08-15.jpg")
check("a one-minute camera keeps its minute",
      U.still_at(TPL, datetime(2026, 9, 26, 8, 22, tzinfo=timezone.utc), 60),
      "https://example.test/port-vauban/video/2026/09/26/08-22.jpg")

# ---------------------------------------------------------------- it must not use HEAD
# The St. Malo failure in one assertion: a server that refuses HEAD and serves GET. The
# old scan found nothing here; the only way to see that from outside was eight fixes of
# a ship coming in and an empty folder.
AHEAD = "https://example.test/port-vauban/video/2026/09/26/11-30.jpg"
s = serve({AHEAD}, head_403=True)
got = U.resolve_still(TPL, 900)
check("finds the frame on a server that refuses HEAD", got and got[0], AHEAD)
check("...and never asked with HEAD at all",
      sum(1 for m, _ in s.calls if m == "HEAD"), 0)

# ---------------------------------------------------------------- finding the newest
s = serve({AHEAD, "https://example.test/port-vauban/video/2026/09/26/11-15.jpg"})
got = U.resolve_still(TPL, 900)
check("takes the newest of two", got and got[0], AHEAD)
check("brings the picture back with it", got and got[1], JPEG)

BEHIND = "https://example.test/port-vauban/video/2026/09/26/09-45.jpg"
serve({BEHIND})
check("finds a frame on a slow clock", (U.resolve_still(TPL, 900) or [None])[0], BEHIND)

SAME = "https://example.test/port-vauban/video/2026/09/26/10-30.jpg"
serve({SAME})
check("finds it when the clocks agree", (U.resolve_still(TPL, 900) or [None])[0], SAME)

# A truncated file is not a picture, and must not stop the scan either.
s = serve({SAME, BEHIND})
s.exists = {SAME}
s.body = b"too small"
check("a stub body is not accepted as a frame", U.resolve_still(TPL, 900), None)

# ---------------------------------------------------------------- remembering the skew
s = serve({AHEAD})
U.resolve_still(TPL, 900)
first = len(s.calls)
s.calls.clear()
check("second lookup goes straight to it", (U.resolve_still(TPL, 900) or [None])[0], AHEAD)
check("...in one request", len(s.calls), 1)
check("the first scan was a scan", first > 1, True)

# Their clock moves twice a year. It must re-find the frame, not give up on the old skew.
s.exists = {SAME}
check("re-finds it when the skew changes",
      (U.resolve_still(TPL, 900) or [None])[0], SAME)

# ---------------------------------------------------------------- nothing there
s = serve(set())
check("camera down gives None, not a crash", U.resolve_still(TPL, 900), None)
asked = len(s.calls)
s.calls.clear()
check("...and is not asked again this run", U.resolve_still(TPL, 900), None)
check("...not one more request", len(s.calls), 0)
check("the scan it did do was bounded", asked < 200, True)

# ---------------------------------------------------------------- grab_still end to end
tmp = Path(tempfile.mkdtemp())

s = serve({AHEAD})
check("a dated url is resolved and saved", U.grab_still(TPL, tmp / "a.jpg"), True)
check("...the picture is on disk",
      (tmp / "a.jpg").read_bytes() if (tmp / "a.jpg").exists() else "ingen fil",
      JPEG)
# The scan passes a few misses before the hit; what matters is that the hit itself is
# fetched exactly once - the old code found it, then asked for the same picture again.
check("...the found picture was fetched exactly once",
      s.gets().count(AHEAD), 1)

PLAIN = "https://example.test/salen/current.jpg"
s = serve({PLAIN})
check("a plain url is left alone", U.grab_still(PLAIN, tmp / "b.jpg"), True)
check("...with no scanning at all", len(s.calls), 1)

serve(set())
check("a camera with nothing there writes no file",
      U.grab_still(TPL, tmp / "c.jpg"), False)
check("...and leaves no stub behind", (tmp / "c.jpg").exists(), False)

s = serve({PLAIN}, body=b"nope")
check("a tiny body is refused", U.grab_still(PLAIN, tmp / "d.jpg"), False)

# ------------------------------------------------- the grabbers must actually be callable
# The except swallows a NameError too, so the assertion is on the OUTCOME: against a
# server that is answering, a working grab_still returns True. A broken one returns False.
serve({PLAIN})
check("grab_still gets the picture from a server that is answering",
      U.grab_still(PLAIN, tmp / "e.jpg"), True)

calls = {}
U.shutil.which = lambda *a: "/usr/bin/ffmpeg"


def fake_run(cmd, **kw):
    calls["cmd"] = cmd
    (tmp / "f.jpg").write_bytes(JPEG)
    return types.SimpleNamespace(returncode=0, stderr="")


U.subprocess.run = fake_run
check("grab_frame gets a frame when ffmpeg succeeds",
      U.grab_frame("https://example.test/x.m3u8", tmp / "f.jpg"), True)
check("...and passes a real user agent, not an undefined name",
      "sorlandet-tracker" in " ".join(calls.get("cmd", [])), True)

if fails:
    print("FAILURES:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
