"""Every camera entry says truthfully what it is and where it points.

This file is the one place a parent is told what they are about to look at, so a wrong
line here is worse than no line. Three mistakes have actually been made in it, and this
test is the memory of them.

The first: a camera listed with a `stream` whose video id did not match its own `url`, so
the tab played one thing and the link opened another. The second: a Sevilla entry
described as "the bridge at the mouth of the Delicias quay" that turns out to be a road
camera pointing along the carriageway — a `view` nobody had looked at. The third: Las
Palmas carried the same physical camera twice, once as a YouTube embed and once as a
SkylineWebcams page that rebroadcasts it, counted as two.

Only the first and the shape of the rest can be checked by machine. The `view` text still
needs a person's eye; what this guards is that the fields cannot quietly disagree.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORTS = json.loads((ROOT / "data" / "ports.json").read_text(encoding="utf-8"))
YT_ID = re.compile(r"(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=)|youtu\.be/)([A-Za-z0-9_-]{11})")
fails = []


def bad(where, msg):
    fails.append(f"{where}: {msg}")


for port in PORTS["ports"]:
    name = port.get("name", "?")
    cams = port.get("cameras") or []
    seen_keys = {}
    leads = 0

    for cam in cams:
        who = f"{name} / {cam.get('short') or cam.get('name') or '?'}"

        for field in ("name", "short", "url", "view"):
            if not (cam.get(field) or "").strip():
                bad(who, f"no {field}")

        # A stream and its link must be the same camera.
        stream, url = cam.get("stream"), cam.get("url", "")
        if stream:
            s_id = YT_ID.search(stream)
            u_id = YT_ID.search(url)
            if not s_id:
                bad(who, f"stream is not a recognisable embed: {stream}")
            elif not u_id:
                bad(who, f"has a YouTube stream but url is not a YouTube link: {url}")
            elif s_id.group(1) != u_id.group(1):
                bad(who, f"stream plays {s_id.group(1)} but url opens {u_id.group(1)}")
            if "autoplay=1" not in stream or "mute=1" not in stream:
                bad(who, "embed must autoplay muted, or it shows a dead grey box")

        # The same camera must not be listed twice in one port.
        m = YT_ID.search(url)
        key = m.group(1) if m else url.rstrip("/").lower()
        if key in seen_keys:
            bad(who, f"same camera as '{seen_keys[key]}'")
        seen_keys[key] = cam.get("short") or cam.get("name")

        best = cam.get("best")
        if best is not None and best not in ("approach", "alongside"):
            bad(who, f"best is {best!r}, must be 'approach' or 'alongside'")

        if cam.get("lead"):
            leads += 1

    if leads > 1:
        bad(name, f"{leads} cameras marked lead; only one tab can open by default")
    if cams and leads == 0 and any(c.get("lead") is not None for c in cams):
        bad(name, "a lead key is present but set falsy; drop it instead")

print(f"checked {sum(len(p.get('cameras') or []) for p in PORTS['ports'])} cameras "
      f"in {len(PORTS['ports'])} ports")
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
