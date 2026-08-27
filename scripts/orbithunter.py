#!/usr/bin/env python3
"""Find the satellite passes that actually looked at her.

Searching for scenes "near the ship" is the wrong question - it finds pictures of the
water she happens to be in today, taken on a morning she was two hundred miles away.
The right question is a space-and-time intersection: for every scene in the window, where
was she at that scene's own timestamp, and does the scene's footprint contain that point?

Sources, both free and keyless, via Element84's Earth Search over AWS Open Data:

  Sentinel-2 L2A   10 m optical. She is 64 m, so about six pixels - a visible ship with a
                   wake. Ruined by cloud, and ESA largely does not acquire over open
                   ocean, so expect hits near coasts and none mid-Atlantic.

Sentinel-1 radar was tried and removed. It covers the ocean, it sees through cloud, and a
steel ship lights up in it - but its pixels are in a requester-pays bucket, so all the page
could offer was a link to a grey scene showing neither the ship nor any landscape. An entry
you cannot look at is not worth a row.

Nothing here says "she is in this picture" on its own. It says a satellite photographed
the patch of sea she was in, at the moment she was in it. The page shows the crop and
where AIS put her; the reader can see for themselves whether there is a ship there.
"""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
import ssl
from datetime import datetime, timedelta, timezone

STAC = "https://earth-search.aws.element84.com/v1/search"
# Only optical. Sentinel-1 radar does cover the open ocean and does show ships, but its
# pixels live in a requester-pays bucket, so all we could offer was a link to a grey
# scene the reader cannot interpret - neither ship nor landscape, which is no picture at
# all. If a free route to S1 pixels appears, add it back here.
COLLECTIONS = [
    ("sentinel-2-l2a", "sentinel-2"),
]
MAX_HITS = int(os.environ.get("ORBIT_MAX_HITS", "24"))
MAX_GAP_MIN = float(os.environ.get("ORBIT_MAX_GAP_MIN", "20"))
CLOUD_LIMIT = float(os.environ.get("ORBIT_CLOUD_LIMIT", "40"))


def _post(url: str, payload: dict, timeout: int = 45, ua: str = "sorlandet-tracker"):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json", "User-Agent": ua})
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=ssl.create_default_context()) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None


def _point_in_rings(rings, lat: float, lon: float) -> bool:
    inside = False
    for ring in rings:
        n = len(ring)
        j = n - 1
        for i in range(n):
            xi, yi = ring[i][0], ring[i][1]
            xj, yj = ring[j][0], ring[j][1]
            if (yi > lat) != (yj > lat):
                if yj != yi and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                    inside = not inside
            j = i
    return inside


def _covers(geometry: dict, lat: float, lon: float) -> bool:
    if not geometry:
        return False
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    polys = [coords] if kind == "Polygon" else coords if kind == "MultiPolygon" else []
    return any(_point_in_rings(p, lat, lon) for p in polys)


def _tci_href(item: dict) -> str | None:
    """The public 10 m true-colour COG, if this scene has one on plain HTTPS."""
    for key in ("visual", "visual-jp2"):
        href = ((item.get("assets") or {}).get(key) or {}).get("href") or ""
        if href.startswith("https://") and href.endswith((".tif", ".TIF")):
            return href
    return None


def hunt(points: list, position_at, iso, parse_iso, days: int = 10,
         known: list | None = None, land_span=None) -> list:
    """Return the passes that covered her, newest first.

    position_at(points, when) must give her interpolated position or None; passing it in
    keeps the one implementation of that rule in update.py rather than copying it here.
    """
    if len(points) < 2:
        return []
    try:
        t_first = parse_iso(points[0]["t"])
        t_last = parse_iso(points[-1]["t"])
    except Exception:
        return []
    start = max(t_first, t_last - timedelta(days=days))

    lats = [p["lat"] for p in points]
    lons = [p["lon"] for p in points]
    bbox = [min(lons) - 0.4, min(lats) - 0.4, max(lons) + 0.4, max(lats) + 0.4]
    # A track that has crossed the date line would give a bbox spanning the globe; skip
    # rather than ask for every scene on earth.
    if bbox[2] - bbox[0] > 60:
        print("  -> orbit: track too wide for one search, skipping this run")
        return []

    seen = {h.get("id") for h in (known or []) if isinstance(h, dict)}
    hits = list(known or [])
    added = 0

    for collection, label in COLLECTIONS:
        data = _post(STAC, {
            "collections": [collection], "limit": 100, "bbox": bbox,
            "datetime": f"{iso(start)}/{iso(t_last)}",
        })
        if not data:
            print(f"  ! orbit: no answer from the {label} catalogue")
            continue
        features = data.get("features") or []
        covered = 0
        for f in features:
            fid = f.get("id")
            if not fid or fid in seen:
                continue
            props = f.get("properties") or {}
            try:
                when = parse_iso(props["datetime"].replace("Z", "+00:00")
                                 if props["datetime"].endswith("Z") else props["datetime"])
            except Exception:
                continue
            where = position_at(points, when)
            if not where:
                continue
            if not _covers(f.get("geometry") or {}, where["lat"], where["lon"]):
                continue
            covered += 1

            cloud = props.get("eo:cloud_cover")
            if label == "sentinel-2" and cloud is not None and cloud > CLOUD_LIMIT:
                continue

            # how close the nearest real fix is to the shutter, so the page can be honest
            gap = min(abs((parse_iso(p["t"]) - when).total_seconds()) for p in points) / 60

            hit = {
                "id": fid, "kind": label, "t": iso(when),
                "lat": round(where["lat"], 5), "lon": round(where["lon"], 5),
                "gap_min": round(gap, 1),
                "cloud_pct": None if cloud is None else round(float(cloud), 1),
            }
            href = _tci_href(f)
            if not href:
                continue                          # nothing we can show; not worth listing
            hit["cog"] = href

            # How far out the wide panel has to reach before it contains a coastline. A
            # picture of empty water tells the reader nothing about where she is; a
            # picture with Norway and Denmark in it tells them everything.
            if land_span:
                hit["wide_span_deg"] = land_span(where["lat"], where["lon"])
            hits.append(hit)
            seen.add(fid)
            added += 1
        print(f"  -> orbit: {len(features)} {label} scenes in the box, {covered} covered her")

    hits = [h for h in hits if h.get("gap_min", 0) <= MAX_GAP_MIN]
    hits.sort(key=lambda h: h["t"], reverse=True)
    hits = hits[:MAX_HITS]
    if added:
        print(f"  -> orbit: {added} new pass(es) that saw her, {len(hits)} kept")
    return hits
