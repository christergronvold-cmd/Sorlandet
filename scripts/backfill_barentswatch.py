#!/usr/bin/env python3
"""
Backfills the track from BarentsWatch's historic AIS - the Norwegian Coastal
Administration's own archive. Dense coverage, up to 14 days back, free.

Only covers the Norwegian economic zone, so it is useful while the ship is in
home waters and useless once she is past the North Sea. Sørlandet is 64 m long,
well above the 45 m threshold for sail and leisure vessels in that dataset.

Setup, once:
  1. Register at https://www.barentswatch.no  and sign in
  2. My page -> API clients -> create a client. Note the client id and secret.
  3. Then run, either locally or as a repository secret in Actions:

     export BW_CLIENT_ID=your-client-id
     export BW_CLIENT_SECRET=your-secret
     python3 scripts/backfill_barentswatch.py            # last 24 hours
     python3 scripts/backfill_barentswatch.py 14         # last 14 days, day by day

Existing points are kept; only timestamps we do not already have are added, and
the merged track is written back to data/track.json in time order.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

MMSI = os.environ.get("MMSI", "257165000")
ROOT = Path(__file__).resolve().parent.parent
TRACK = ROOT / "data" / "track.json"
TOKEN_URL = "https://id.barentswatch.no/connect/token"
API = "https://historic.ais.barentswatch.no/v1/historic"
CTX = ssl.create_default_context()


def token() -> str:
    cid = os.environ.get("BW_CLIENT_ID", "").strip()
    secret = os.environ.get("BW_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        sys.exit("Set BW_CLIENT_ID and BW_CLIENT_SECRET first - see the notes at the top of this file.")
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": cid,
        "client_secret": secret,
        "scope": "ais",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body,
                                headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
        return json.loads(r.read())["access_token"]


def get(path: str, tok: str):
    req = urllib.request.Request(f"{API}/{path}", headers={
        "Authorization": f"bearer {tok}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        print(f"  ! {path}: HTTP {exc.code} {exc.reason}", file=sys.stderr)
    except Exception as exc:
        print(f"  ! {path}: {exc}", file=sys.stderr)
    return None


def rows_to_points(rows) -> list[dict]:
    """BarentsWatch field names vary a little between endpoints, so be forgiving."""
    out = []
    for r in rows or []:
        lat = r.get("latitude", r.get("Latitude"))
        lon = r.get("longitude", r.get("Longitude"))
        t = r.get("msgtime", r.get("msgTime", r.get("timestamp")))
        if lat is None or lon is None or not t:
            continue
        t = t.replace("Z", "+00:00")
        try:
            stamp = datetime.fromisoformat(t).astimezone(timezone.utc)
        except ValueError:
            continue
        out.append({
            "t": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "lat": round(float(lat), 5),
            "lon": round(float(lon), 5),
            "sog": r.get("speedOverGround", r.get("sog")),
            "cog": r.get("courseOverGround", r.get("cog")),
        })
    return out


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    days = max(1, min(14, days))
    tok = token()
    print(f"* signed in to BarentsWatch, asking for {days} day(s) of MMSI {MMSI}")

    found: list[dict] = []
    if days == 1:
        found += rows_to_points(get(f"trackslast24hours/{MMSI}", tok))
    else:
        now = datetime.now(timezone.utc)
        for k in range(days):
            end = now - timedelta(days=k)
            start = end - timedelta(days=1)
            q = urllib.parse.urlencode({"from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                        "to": end.strftime("%Y-%m-%dT%H:%M:%SZ")})
            batch = rows_to_points(get(f"tracks/{MMSI}?{q}", tok))
            print(f"  {start:%d %b} - {end:%d %b}: {len(batch)} positions")
            found += batch

    if not found:
        print("* nothing came back. Either she has been outside Norwegian waters, or the "
              "endpoint names have changed - check developer.barentswatch.no/docs/AIS/")
        return 1

    track = json.loads(TRACK.read_text(encoding="utf-8")) if TRACK.exists() else {}
    points = track.get("points") or []
    have = {p["t"] for p in points}
    fresh = [p for p in found if p["t"] not in have]
    merged = sorted(points + fresh, key=lambda p: p["t"])

    TRACK.write_text(json.dumps({"mmsi": MMSI, "ship": track.get("ship", "Sørlandet"),
                                 "points": merged}, ensure_ascii=False, indent=1) + "\n",
                     encoding="utf-8")
    print(f"* added {len(fresh)} new positions, track now has {len(merged)}")
    print("  commit data/track.json and the page picks it up straight away")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
