#!/bin/bash
# Run every test. From anywhere: bash tests/run.sh
#
# These live in the repo on purpose. The suite used to sit only in Claude's working
# environment; that environment was reset on 25 September 2026 and about sixty tests went
# with it, written over five weeks and not recoverable. Whatever is worth checking is
# worth keeping next to the thing it checks.
set -u
cd "$(dirname "$0")/.." || exit 2

# The tests need Python 3.10+, playwright, cryptography and Pillow. The Mac's own python3
# is 3.9 and has none of them, so they live in a venv of their own; use it when it is there.
PYBIN=python3
[ -x "$HOME/.venvs/sorlandet/bin/python3" ] && PYBIN="$HOME/.venvs/sorlandet/bin/python3"

PY=$(ls tests/test_*.py | sort)
FAIL=0
for t in $PY; do
  # The five-minute cap is kept in python, not in `timeout`: that is GNU coreutils, and a
  # Mac does not have it. On 5 November every test on the Mac "failed" with
  # "timeout: command not found" before a single check had run.
  out=$("$PYBIN" -c '
import subprocess, sys
try:
    r = subprocess.run([sys.executable, sys.argv[1]], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True, timeout=300)
    print(r.stdout, end="")
except subprocess.TimeoutExpired:
    print("TIMEOUT - still running after 300 s")
' "$t" 2>&1)
  if echo "$out" | grep -q "ALL CHECKS PASSED"; then
    printf '.'
  else
    echo
    echo "FAIL $t"
    echo "$out" | grep -A8 FAILURES | head -12
    echo "$out" | tail -4
    FAIL=1
  fi
done
echo " $(echo "$PY" | wc -w | tr -d ' ') tests"
[ $FAIL -eq 0 ] && echo "alt grønt" || echo "noe feilet"
exit $FAIL
