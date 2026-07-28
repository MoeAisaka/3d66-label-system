"""Ensure ``backend/`` is importable as the package root for tests.

The existing convention runs pytest from ``backend/`` (see AGENTS.md), which
puts ``app`` on ``sys.path`` implicitly.  When pytest is instead invoked from
the repository root (for example ``python3 -m pytest -q backend/tests``), the
default ``prepend`` import mode only adds ``backend/tests`` to ``sys.path`` and
``import app`` would fail.  Adding the tests' parent directory here makes the
suite import-stable under both working directories without changing any test
behaviour; when ``backend/`` is already present the insert is a no-op.
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_ROOT = str(Path(__file__).resolve().parents[1])
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)
