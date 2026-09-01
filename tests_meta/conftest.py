"""Top-level config for the harness/agent meta test suite.

These tests live OUTSIDE tests/ on purpose: tests/ is copied verbatim into every
scenario tree (scripts/ci.sh runs its pytest), and these meta tests import
`harness`, which is never present in a scenario tree.
"""

import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_ROOT = _HERE.parent
for _p in (str(_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
