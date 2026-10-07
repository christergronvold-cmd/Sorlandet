#!/bin/bash
# Run every test. From anywhere: bash tests/run.sh
#
# These live in the repo on purpose. The suite used to sit only in Claude's working
# environment; that environment was reset on 25 September 2026 and about sixty tests went
# with it, written over five weeks and not recoverable. Whatever is worth checking is
# worth keeping next to the thing it checks.
set -u
cd "$(dirname "$0")/.." || exit 2

PY=$(ls tests/test_*.py | sort)
FAIL=0
for t in $PY; do
  out=$(timeout 300 python3 "$t" 2>&1)
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
