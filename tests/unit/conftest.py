"""Shared test bootstrapping for the unit suite.

``flow_runner`` imports ``psutil`` at module load time even though it is not
used in the exercised code paths. The historical test module stubbed it inline;
hoisting the stub into a conftest lets every unit test module import
``flow_runner`` without repeating the shim (and without requiring psutil to be
installed in the test environment).
"""

import os
import sys
import types

sys.modules.setdefault("psutil", types.ModuleType("psutil"))

# Ensure the repo root is importable regardless of pytest's rootdir/invocation.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
