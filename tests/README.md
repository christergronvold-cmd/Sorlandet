# Tests

Run them all:

    bash tests/run.sh

Each file prints `ALL CHECKS PASSED` or a list of what failed; the runner collects them.
They need `playwright` and a Chromium, and nothing else — no network: every test that
would reach out stubs the server it talks to.

## Why these live in the repo

They did not, until 7 October 2026. The suite sat in Claude's working environment, which
was reset while Christer was away, and about sixty tests written over five weeks went with
it. None of it was recoverable. What is here now is what could be rebuilt from memory of
the bugs it was written for.

The rule from here: a test that matters goes in the repo, next to the thing it checks.

## What each one is for

| file | the bug it was written for |
|---|---|
| `test_stillpath.py` | Frame capture fetched with HEAD, untested against the real server; eight fixes of her coming into St. Malo produced no pictures. Also guards the `CONTACT_UA` NameError that made every capture fail silently for three weeks. |
| `test_engine.py` | The map drew a tacking sailing course while she was motoring out of Dublin under engine on all 124 messages. |
| `test_heading.py` | With no course in the fix, the ship's arrow rotated to zero — due north — while she steered 180 all night. |
| `test_layers.py` | The layer defaults live in two places, `show` and the button markup, and nothing but an eye kept them in step. |

## Writing another one

Two habits earned the hard way, both from bugs these tests now cover:

Run the real function, not a stub of it. The capture bug survived three weeks because the
old tests replaced `grab_frame` and `grab_still` with stubs, so the real bodies — which
raised `NameError` on every call — were never executed.

Then check that the test fails against the broken version before trusting it. Put the bug
back, watch it go red, put the fix back. A test that has never failed has never been shown
to test anything.
