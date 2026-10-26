"""The page heals itself only if somebody remembers to change one line. Nobody does.

index.html checks its own source every five minutes, compares a build stamp, and reloads
through a fresh URL when the served copy is newer than the loaded one. It is careful work
- it even handles the case where reload() is answered from the same cache.

And it is worth nothing if the stamp does not move. On 26 October the page changed four
times in an afternoon with the stamp left at 7445a5bd73, so served and loaded looked
identical, every phone kept the copy it already had, and the one person who needed the
new version was told to delete the app and add it again. The mechanism was not broken.
It was never given the one fact it runs on.

So the stamp is not a thing to remember any more. It is the hash of the file, and this
test is what notices when the two have drifted apart.
"""
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
page = (ROOT / "index.html").read_text(encoding="utf-8")

found = re.search(r'const BUILD = "([^"]*)";', page)
if not found:
    print("FAILURES")
    print("  - index.html has no BUILD stamp at all, so it can never tell that a newer"
          " version of itself exists")
    sys.exit(1)

# Everything except the stamp line - a hash that included itself could never settle.
blank = re.sub(r'const BUILD = "[^"]*";', 'const BUILD = "";', page)
want = hashlib.sha256(blank.encode("utf-8")).hexdigest()[:10]
have = found.group(1)

if have != want:
    print("FAILURES")
    print(f"  - the build stamp says {have} but the page hashes to {want}. Every reader"
          f" with this page open, and every phone holding it on a home screen, will go on"
          f" showing the version they already have: the self-check compares these two and"
          f" concludes nothing has changed.")
    print(f"    Fix: set const BUILD = \"{want}\"; in index.html")
    sys.exit(1)

print(f"build stamp {have} matches the page")
print("ALL CHECKS PASSED")
