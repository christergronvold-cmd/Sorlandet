#!/usr/bin/env python3
"""
Makes share-qr.svg, the QR code shown in the Share card on the page.

    python3 scripts/make_qr.py https://your-name.github.io/your-repo/

Run it again if the address ever changes. Needs: pip install qrcode
"""
import sys
from pathlib import Path

import qrcode
import qrcode.image.svg

url = sys.argv[1] if len(sys.argv) > 1 else "https://christergronvold-cmd.github.io/Sorlandet/"
qr = qrcode.QRCode(box_size=10, border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
qr.add_data(url)
qr.make(fit=True)
img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
out = Path(__file__).resolve().parent.parent / "share-qr.svg"
img.save(str(out))

# Make it theme-friendly: transparent background, currentColor modules.
svg = out.read_text(encoding="utf-8")
svg = svg.replace('fill="#000000"', 'fill="currentColor"').replace('fill="black"', 'fill="currentColor"')
out.write_text(svg, encoding="utf-8")
print(f"Wrote {out.name} for {url}")
