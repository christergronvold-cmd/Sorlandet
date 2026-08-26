#!/usr/bin/env python3
"""
Backfills the track from BarentsWatch historic AIS - the Norwegian Coastal
Administration's archive. Dense, free, up to 14 days back, Norwegian waters only.

This version discovers the endpoints instead of assuming them: it reads the API's
own OpenAPI description, finds the paths that return a vessel track, and tries them
in turn, printing exactly what each one answered. If nothing works, the log tells us
what the API actually offers, which is all that is needed to fix it.

Environment:
    BW_CLIENT_ID       e.g. you@example.com:ClientName   (plain, not urlencoded)
    BW_CLIENT_SECRET   the secret you chose
    MMSI               optional, defaults to Sørlandet

Usage:
    python3 scripts/backfill_barentswatch.py [days]     # 1-14, default 1
"""

from __future__ import annotations

import json
import os
import re
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
HOSTS = ["https://historic.ais.barentswatch.no", "https://live.ais.barentswatch.no"]
SPECS = ["/swagger/v1/swagger.json", "/openapi.json", "/swagger/v1/openapi.json"]
CTX = ssl.create_default_context()
UA = {"User-Agent": "sorlandet-tracker/1.0 (family project)"}


def fetch(url: str, tok: str | None = None, timeout: int = 45):
    headers = dict(UA, Accept="application/json")
    if tok:
        headers["Authorization"] = f"bearer {tok}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        return exc.code, detail
    except Exception as exc:
        return None, str(exc)


def get_token() -> str:
    cid = os.environ.get("BW_CLIENT_ID", "").strip()
    secret = os.environ.get("BW_CLIENT_SECRET", "").strip()
    print(f"* credentials: client id {'set (' + str(len(cid)) + ' chars)' if cid else 'MISSING'}, "
          f"secret {'set (' + str(len(secret)) + ' chars)' if secret else 'MISSING'}")
    if not cid or not secret:
        sys.exit("! Set BW_CLIENT_ID and BW_CLIENT_SECRET as repository secrets first.")
    if "%40" in cid or "%3A" in cid:
        print("! the client id looks urlencoded - use the plain one, with @ and :", file=sys.stderr)

    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": cid,
        "client_secret": secret,
        "scope": "ais",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, headers=dict(
        UA, **{"Content-Type": "application/x-www-form-urlencoded"}))
    try:
        with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
            tok = json.loads(r.read())["access_token"]
        print("* got an access token")
        return tok
    except urllib.error.HTTPError as exc:
        sys.exit(f"! token request failed: HTTP {exc.code} {exc.reason} - "
                 f"{exc.read().decode('utf-8', 'replace')[:300]}")
    except Exception as exc:
        sys.exit(f"! token request failed: {exc}")


def discover(tok: str) -> list[str]:
    """Reads the API's own description and returns candidate track paths."""
    found: list[str] = []
    for host in HOSTS:
        for spec in SPECS:
            status, data = fetch(host + spec, tok, timeout=30)
            if status != 200 or not isinstance(data, dict):
                continue
            paths = list((data.get("paths") or {}).keys())
            print(f"* {host}{spec}: {len(paths)} paths")
            for p in paths:
                if re.search(r"track|position|historic", p, re.I):
                    print(f"    {p}")
                    found.append(host + p)
            if paths:
                return found
    print("* could not read any OpenAPI description - falling back to known paths")
    return found


def candidates(discovered: list[str], start: datetime, end: datetime) -> list[str]:
    """Fills {mmsi}/{from}/{to} style placeholders and adds the documented paths."""
    f_iso, t_iso = start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")
    urls: list[str] = []
    for u in discovered:
        filled = re.sub(r"\{[^}]*mmsi[^}]*\}", MMSI, u, flags=re.I)
        filled = re.sub(r"\{[^}]*from[^}]*\}", f_iso, filled, flags=re.I)
        filled = re.sub(r"\{[^}]*to[^}]*\}", t_iso, filled, flags=re.I)
        if "{" in filled:                     # unresolved placeholders - skip
            continue
        if "from" not in filled and "last24" not in filled.lower():
            filled += ("&" if "?" in filled else "?") + urllib.parse.urlencode(
                {"from": f_iso, "to": t_iso})
        urls.append(filled)
    base = HOSTS[0] + "/v1/historic"
    urls += [
        f"{base}/trackslast24hours/{MMSI}",
        f"{base}/tracks/{MMSI}?" + urllib.parse.urlencode({"from": f_iso, "to": t_iso}),
        f"{base}/track/{MMSI}?" + urllib.parse.urlencode({"from": f_iso, "to": t_iso}),
    ]
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def rows_to_points(payload) -> list[dict]:
    rows = payload
    if isinstance(payload, dict):
        for key in ("features", "positions", "tracks", "items", "data", "value"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                break
    if not isinstance(rows, list):
        return []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        props = r.get("properties") if isinstance(r.get("properties"), dict) else r
        lat = props.get("latitude", props.get("lat", props.get("Latitude")))
        lon = props.get("longitude", props.get("lon", props.get("Longitude")))
        geom = r.get("geometry") if isinstance(r.get("geometry"), dict) else None
        if (lat is None or lon is None) and geom and isinstance(geom.get("coordinates"), list):
            coords = geom["coordinates"]
            if len(coords) >= 2:
                lon, lat = coords[0], coords[1]
        t = (props.get("msgtime") or props.get("msgTime") or props.get("timestamp")
             or props.get("dateTimeUtc") or props.get("time"))
        if lat is None or lon is None or not t:
            continue
        try:
            stamp = datetime.fromisoformat(str(t).replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            continue
        out.append({
            "t": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "lat": round(float(lat), 5),
            "lon": round(float(lon), 5),
            "sog": props.get("speedOverGround", props.get("sog")),
            "cog": props.get("courseOverGround", props.get("cog")),
        })
    return out


def main() -> int:
    days = max(1, min(14, int(sys.argv[1]) if len(sys.argv) > 1 else 1))
    tok = get_token()
    discovered = discover(tok)

    now = datetime.now(timezone.utc)
    found: list[dict] = []
    for k in range(days):
        end = now - timedelta(days=k)
        start = end - timedelta(days=1)
        got = []
        for url in candidates(discovered, start, end):
            status, data = fetch(url, tok)
            shown = url.replace(HOSTS[0], "").replace(HOSTS[1], "")[:110]
            if status == 200:
                got = rows_to_points(data)
                print(f"  {start:%d %b}: {shown} -> {len(got)} positions")
                if got:
                    break
            else:
                print(f"  {start:%d %b}: {shown} -> HTTP {status} {str(data)[:90]}")
        found += got
        if k == 0 and not got:
            print("* the first day returned nothing - stopping so the log stays readable")
            break

    if not found:
        print("* nothing came back. The paths listed above are what the API offers - "
              "send that list along and the script can be pointed at the right one.")
        return 0                              # not a failure, just nothing to add

    track = json.loads(TRACK.read_text(encoding="utf-8")) if TRACK.exists() else {}
    points = track.get("points") or []
    have = {p["t"] for p in points}
    fresh = [p for p in found if p["t"] not in have]
    merged = sorted(points + fresh, key=lambda p: p["t"])
    TRACK.write_text(json.dumps({"mmsi": MMSI, "ship": track.get("ship", "Sørlandet"),
                                 "points": merged}, ensure_ascii=False, indent=1) + "\n",
                     encoding="utf-8")
    print(f"* added {len(fresh)} new positions, track now has {len(merged)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
