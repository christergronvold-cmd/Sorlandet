#!/usr/bin/env python3
"""Hent arkivbilder fra et Skaping-kamera for et tidsvindu som alt er passert.

Port Vauban legger ut ett bilde hvert kvarter på en datert sti og beholder arkivet.
Så lenge du vet omtrent NÅR hun gikk, ligger bildene der fortsatt - uansett at
capture_port aldri ba om dem.

Klokka i stien er IKKE UTC og ikke Paris (se kommentaren i scripts/update.py rundt
linje 2535: 22. september sto arkivet på 11-30 mens UTC var 10:31 og Paris 12:31).
Derfor prøver dette skriptet seg fram til forskyvningen med en HEAD mot et tidspunkt
du vet det fins bilde på, og bruker så den samme forskyvningen for hele vinduet.

    python3 recover_stills.py --from 2026-10-02T06:30 --to 2026-10-02T09:30

Tidene oppgis i UTC. Bildene havner i images/shore/pending/ med repoets eget
navneformat, så de kan flyttes rett inn i images/shore/St. Malo/ etterpå.
"""
import argparse, pathlib, sys, urllib.request
from datetime import datetime, timedelta, timezone

TEMPLATE = ("https://skaping.s3.gra.io.cloud.ovh.net/saint-malo/port-vauban"
            "/video/%Y/%m/%d/%H-%M.jpg")
EVERY_MIN = 15
UA = "sorlandet-tracker (recovery)"


def bucket(when, skew_min):
    t = (when + timedelta(minutes=skew_min)).replace(second=0, microsecond=0)
    return t.replace(minute=(t.minute // EVERY_MIN) * EVERY_MIN)


def exists(url):
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return getattr(r, "status", 200) == 200
    except Exception:
        return False


def find_skew(probe):
    """Samme søkemønster som resolve_still: vår egen klokke først, så time for time ut."""
    offsets = [0]
    for h in range(1, 14):
        offsets += [h * 60, -h * 60]
    for skew in offsets:
        if exists(bucket(probe, skew).strftime(TEMPLATE)):
            print(f"  forskyvning funnet: {skew:+d} min")
            return skew
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", required=True, help="UTC, f.eks. 2026-10-02T06:30")
    ap.add_argument("--to", dest="end", required=True, help="UTC")
    ap.add_argument("--out", default="images/shore/pending")
    a = ap.parse_args()

    start = datetime.fromisoformat(a.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(a.end).replace(tzinfo=timezone.utc)
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"Leter etter arkivets klokke rundt {start:%Y-%m-%d %H:%M} UTC ...")
    skew = find_skew(start)
    if skew is None:
        print("  ! ingenting svarte - enten er vinduet utenfor arkivet, eller kameraet er nede.")
        print("    Dette sier ingenting om hvorvidt kameraet virket DA; bare at det ikke svarer nå.")
        return 1

    got = 0
    t = start
    while t <= end:
        at = bucket(t, skew)
        url = at.strftime(TEMPLATE)
        # Filnavnet får kameraets egen klokke, slik repoet gjør det.
        dest = out / f"{at:%Y-%m-%d-%H%M}-port-vauban.jpg"
        if not dest.exists():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = r.read()
                if data[:2] == b"\xff\xd8":
                    dest.write_bytes(data)
                    print(f"  {dest.name}  ({len(data) // 1024} kB)")
                    got += 1
            except Exception as e:
                print(f"  - {url.rsplit('/', 1)[-1]}: {e}")
        t += timedelta(minutes=EVERY_MIN)
    print(f"\n{got} bilde(r) i {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
