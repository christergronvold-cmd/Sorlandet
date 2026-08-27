#!/usr/bin/env python3
"""Stamp index.html with a build id derived from its own content.

The page compares this against the copy the server hands back, so a browser holding a
stale version can notice and heal itself. Run this after every edit to index.html.
"""
import hashlib
import re
import sys
from pathlib import Path

page = Path(__file__).resolve().parent.parent / "index.html"
text = page.read_text(encoding="utf-8")

neutral = re.sub(r'const BUILD = "[^"]*"', 'const BUILD = ""', text)
digest = hashlib.sha1(neutral.encode("utf-8")).hexdigest()[:10]

new, n = re.subn(r'const BUILD = "[^"]*"', f'const BUILD = "{digest}"', text, count=1)
if not n:
    sys.exit("! no BUILD constant found in index.html")
if new != text:
    page.write_text(new, encoding="utf-8")
print(f"build {digest}")
