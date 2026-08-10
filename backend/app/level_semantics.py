"""Single production meaning for all L-levels.

``L1`` is always best and larger numeric suffixes always mean lower quality.
Categories may disable buckets through their frozen level scale, but cannot
reverse this direction.
"""

from __future__ import annotations

from typing import Any

from .category_evaluation_aggregator import (
    LEVEL_SEMANTICS_VERSION as UNIFIED_LEVEL_SEMANTICS_VERSION,
)


def describe_level_semantics(version: str) -> dict[str, Any]:
    """Describe the sole known direction; unknown versions fail closed."""
    if version == UNIFIED_LEVEL_SEMANTICS_VERSION:
        return {
            "version": version,
            "known": True,
            "best_level": "L1",
            "worst_level": "L5",
            "direction": "L1=最优，L序号越大质量越差",
            "levels": {
                "L1": "best",
                "L2": "mid",
                "L3": "mid",
                "L4": "mid",
                "L5": "worst",
            },
        }
    return {
        "version": version,
        "known": False,
        "best_level": None,
        "worst_level": None,
        "direction": "unknown",
        "levels": {},
    }
