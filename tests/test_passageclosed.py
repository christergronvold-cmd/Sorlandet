"""A passage she is through is closed, for the page and the job alike.

On 13 November she came down the Guadalquivir and out over the bar at Sanlúcar. The club
gave its camera for the days of her passage, and once she was out of the frame Christer
said: turn it off. closed_utc on the passage does that. Before it, everything works as
before; after it, the job takes no frames, sends no "approaching the lens" notice, and
the page stops offering the camera.
"""
import copy
import json
import re
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import update  # noqa: E402

fails = []
PLAN = json.loads((ROOT / "data" / "ports.json").read_text(encoding="utf-8"))
RIVER = next(q for q in PLAN["passages"] if "Guadalquivir" in q["name"])
BAR = (36.80, -6.40)          # 2.4 nm off the lens, under way


def fix():
    return {"lat": BAR[0], "lon": BAR[1], "sog_kn": 10.0, "cog_deg": 240,
            "seen_utc": update.iso(update.now_utc() - timedelta(minutes=4))}


def plan_closing(at):
    plan = copy.deepcopy(PLAN)
    river = next(q for q in plan["passages"] if "Guadalquivir" in q["name"])
    river["closed_utc"] = update.iso(at)
    return plan


now = update.now_utc()
open_plan = plan_closing(now + timedelta(hours=1))
shut_plan = plan_closing(now - timedelta(minutes=1))

got = update.capture_port(fix(), open_plan, [])
if not (got and "Guadalquivir" in got["name"]):
    fails.append("before closed_utc the river mouth must still capture as it always did")
got = update.capture_port(fix(), shut_plan, [])
if got and "Guadalquivir" in got["name"]:
    fails.append("after closed_utc the job still photographs the river mouth")
if any("Guadalquivir" in q.get("name", "") for q in update._watched_sites(shut_plan)):
    fails.append("after closed_utc the river mouth is still watched for an approach notice")
if not any("Guadalquivir" in q.get("name", "") for q in update._watched_sites(open_plan)):
    fails.append("before closed_utc the river mouth must still be watched")

page = (ROOT / "index.html").read_text(encoding="utf-8")
if not re.search(r"q\.closed_utc\s*&&\s*Date\.now\(\)\s*>=\s*new Date\(q\.closed_utc\)", page):
    fails.append("the page does not read closed_utc, so the camera stays on the page after"
                 " the passage is closed")

if RIVER.get("closed_utc") is None:
    fails.append("the Guadalquivir passage has no closed_utc - she is through it")

if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED - a closed passage is closed for the job, the notices and the page")
