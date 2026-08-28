#!/usr/bin/env python3
"""A polar diagram learned from Sørlandet's own sailing.

`sailrouter.py` ships with a polar I wrote from general square-rigger figures. It is a
guess about a class of ship, not a measurement of this one. But the job already stores,
hour by hour, where she was, which way she was heading, how fast she was going and what the
wind was doing there - which is exactly the raw material for measuring her real one.

For every hour we can pair her course and speed with the wind at her position:

    TWA  = the angle between her heading and where the wind is coming from
    TWS  = how hard it was blowing
    boat = how fast she was actually going

Bin those, and after a few thousand hours you have her polar rather than a textbook's.

Three things this is careful about, because each of them would quietly produce a polar for
a slower, duller ship than she is:

* **Only when she is under sail.** AIS navigational status 8 means under sail, 0 means
  under engine. Hours with no status at all are dropped, not assumed - the ship's own
  position feed carries no status, so "unknown" is common and guessing would poison
  everything. Right now the crew are training, heaving to and motoring; none of that is
  sailing performance.
* **A high percentile, not an average.** She spends hours drifting under reduced canvas
  with the same wind and angle as when she is pressing on. The mean of that is a number
  no boat ever sailed. The 85th percentile of what was actually observed at a given angle
  and wind strength is much closer to "what she can do".
* **Counts are kept and published.** A bin backed by three hours is not knowledge. The
  router only prefers a learned figure over the textbook one where there is enough of it,
  and the file says how much there is, so the claim can always be checked.

Nothing here is used until it has earned its place: `blended_table()` falls back to the
built-in polar bin by bin.
"""

from __future__ import annotations

import math

# The bins. Ten degrees of wind angle is fine enough to see the shape of a polar and coarse
# enough to fill up in a season; port and starboard are folded together, since a square
# rigger is symmetric and halving the data to prove otherwise would not be worth it.
TWA_STEP = 10                      # degrees, 0..180 -> 18 bins
TWS_EDGES = [0, 4, 8, 12, 16, 20, 25, 30, 99]      # knots of true wind
SPEED_BUCKET = 0.5                 # knots
SPEED_BUCKETS = 28                 # 0 .. 14 knots
PERCENTILE = 0.85                  # "what she can do", not "what she averaged"
MIN_SAMPLES = 8                    # below this a bin is a rumour, not a measurement


def tws_bin(kn: float) -> int:
    for i in range(len(TWS_EDGES) - 1):
        if TWS_EDGES[i] <= kn < TWS_EDGES[i + 1]:
            return i
    return len(TWS_EDGES) - 2


def twa_bin(deg: float) -> int:
    return min(int(abs(deg) // TWA_STEP), 180 // TWA_STEP - 1)


def key(a: int, w: int) -> str:
    return f"{a}|{w}"


def true_wind_angle(cog_deg: float, wind_from_deg: float) -> float:
    """Signed angle between her heading and the wind's origin, -180..180."""
    return ((wind_from_deg - cog_deg + 540) % 360) - 180


def empty() -> dict:
    return {"version": 1, "samples": 0, "hours_used": [], "bins": {}}


def _add(bins: dict, a: int, w: int, boat_kn: float) -> None:
    k = key(a, w)
    slot = bins.setdefault(k, {"n": 0, "h": [0] * SPEED_BUCKETS})
    b = min(SPEED_BUCKETS - 1, max(0, int(boat_kn / SPEED_BUCKET)))
    slot["h"][b] += 1
    slot["n"] += 1


def learn(hours: list, polar: dict | None = None, under_sail_only: bool = True,
          max_boat_kn: float = 14.0) -> dict:
    """Fold new wake hours into the polar. Hours already used are skipped.

    `hours` are wake.json entries: t, lat, lon, sog, cog, ns, wind_ms, wind_dir.
    """
    polar = polar or empty()
    used = set(polar.get("hours_used") or [])
    bins = polar.setdefault("bins", {})
    added = skipped = 0

    for e in hours or []:
        t = e.get("t")
        if not t or t in used:
            continue
        ns, cog, sog = e.get("ns"), e.get("cog"), e.get("sog")
        wind_ms, wind_dir = e.get("wind_ms"), e.get("wind_dir")
        if under_sail_only and ns != 8:
            skipped += 1
            used.add(t)                 # settled: an hour under engine is never revisited
            continue
        if cog is None or sog is None or wind_ms is None or wind_dir is None:
            skipped += 1
            continue                    # not settled: the weather may arrive on a later run
        try:
            boat = float(sog)
            tws = float(wind_ms) * 1.94384
            twa = true_wind_angle(float(cog), float(wind_dir))
        except (TypeError, ValueError):
            skipped += 1
            used.add(t)
            continue
        if not (0 <= boat <= max_boat_kn) or tws < 0 or tws > 90:
            skipped += 1
            used.add(t)
            continue
        _add(bins, twa_bin(twa), tws_bin(tws), boat)
        used.add(t)
        added += 1

    polar["samples"] = sum(b["n"] for b in bins.values())
    # Only the window the wake covers is ever offered again, so the list cannot grow without
    # bound over a nine-month voyage.
    polar["hours_used"] = sorted(used)[-2000:]
    polar["added_last_run"] = added
    polar["skipped_last_run"] = skipped
    return polar


def bin_speed(slot: dict, pct: float = PERCENTILE) -> float | None:
    """The percentile speed in a bin, from its histogram."""
    n = slot.get("n") or 0
    if n <= 0:
        return None
    target = pct * n
    run = 0
    for i, c in enumerate(slot.get("h") or []):
        run += c
        if run >= target:
            return round((i + 0.5) * SPEED_BUCKET, 2)
    return round((SPEED_BUCKETS - 0.5) * SPEED_BUCKET, 2)


def summary(polar: dict, min_samples: int = MIN_SAMPLES) -> dict:
    """What has actually been learned, in a shape a person can read."""
    out = {}
    for k, slot in (polar.get("bins") or {}).items():
        if slot.get("n", 0) < min_samples:
            continue
        a, w = (int(x) for x in k.split("|"))
        out[k] = {"twa": a * TWA_STEP + TWA_STEP // 2,
                  "tws_from": TWS_EDGES[w], "tws_to": TWS_EDGES[w + 1],
                  "n": slot["n"], "kn": bin_speed(slot)}
    return out


def blended_table(polar: dict, fallback, min_samples: int = MIN_SAMPLES):
    """A speed(twa, tws) function that prefers what she has shown, where there is enough.

    `fallback(twa, tws)` is the built-in polar. Bins with fewer than `min_samples` hours
    behind them fall through to it, so an empty or thin polar behaves exactly as before -
    which is what makes it safe to switch on from the first day.
    """
    bins = polar.get("bins") or {}
    learned = {}
    for k, slot in bins.items():
        if slot.get("n", 0) >= min_samples:
            v = bin_speed(slot)
            if v is not None:
                learned[k] = v

    def speed(twa: float, tws: float) -> float:
        v = learned.get(key(twa_bin(twa), tws_bin(tws)))
        return v if v is not None else fallback(twa, tws)

    speed.learned_bins = len(learned)
    return speed
