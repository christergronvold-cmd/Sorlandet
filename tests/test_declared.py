"""The one warning that arrives before she goes, and why it was thrown away.

28 October. She sailed from Funchal and the phone stayed quiet. Part of that was a bug,
now fixed. The rest was not fixable at all: between 27 October 16:45 and 28 October 09:26
there was no position from anywhere. Sixteen hours and forty minutes. aisstream heard
nothing all night, and the paid source was asked thirty-five times and handed back the
same stale fix every time.

But something DID get through at 16:45 on the 27th, and it was the last thing that did:

    "destination": "ES SVQ",  "heard_utc": "2026-10-27T16:45:21Z"

The day before it had read PT FNC. That is AIS message 5 - the voyage record a mate types
on the bridge - and changing it is the first thing anyone does once the next passage is
settled. Seventeen hours of warning, sitting in latest.json, unread.

It is not a departure and must never be reported as one. It says a decision has been
made, which is earlier and different, and at a port where nobody can hear her it may be
the only thing anybody gets.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import update  # noqa: E402

fails = []
PLAN = json.loads((ROOT / "data" / "ports.json").read_text(encoding="utf-8"))
sent = []

update.MOVES = Path(tempfile.mkdtemp()) / "moves.json"
update.open_issue = lambda title, body: (sent.append((title, body)) or True)
update.send_push = lambda title, line: 0


def declare(dest, heard="2026-10-27T16:45:21Z"):
    before = len(sent)
    update.tell_him_she_declared({"destination": dest, "heard_utc": heard}, PLAN, [])
    return sent[-1] if len(sent) > before else None


# The first look learns and says nothing: otherwise the first run after a deploy
# announces a decision taken a week ago.
if declare("PT FNC") is not None:
    fails.append("announced a destination the very first time it ever looked")

# ...and then the evening of 27 October.
got = declare("ES SVQ")
if got is None:
    fails.append("the bridge changed her destination from PT FNC to ES SVQ and nothing"
                 " was sent - this is the seventeen hours of warning we had and binned")
else:
    title, body = got
    if "ES SVQ" not in title:
        fails.append(f"the message does not say where she has declared for: {title!r}")
    if "PT FNC" not in body:
        fails.append("it does not say what it changed FROM, which is the whole evidence")
    for wrong in ("under way", "has left", "has sailed", "departed"):
        if wrong in (title + body).lower():
            fails.append(f"the message claims she has sailed ({wrong!r}). She has not."
                         f" It is a decision, not a departure")
    if "not a departure" not in body.lower():
        fails.append("nothing in the message says what it is NOT, and the difference"
                     " between 'she has decided' and 'she has gone' is the whole point")

# The same thing again, and the same thing spelled differently, are not news.
if declare("ES SVQ") is not None:
    fails.append("repeated the same destination")
if declare("ESSVQ") is not None:
    fails.append("announced ES SVQ again because a receiver dropped the space")

# Rubbish in the field is not news either. Crews leave it blank, or lean on a key.
for junk in ("", "   ", "-", "??"):
    if declare(junk) is not None:
        fails.append(f"announced {junk!r} as a destination")

# A real change still gets through afterwards.
if declare("ES LPA") is None:
    fails.append("stopped noticing changes after a bad value went past")

print(f"learned one destination quietly, announced {len(sent)} real changes, and ignored"
      f" the repeats, the respacing and four kinds of rubbish")
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
