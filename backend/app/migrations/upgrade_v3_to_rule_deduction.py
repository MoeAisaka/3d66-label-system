"""Idempotent data upgrade from v3 grade contracts to rule deductions.

Existing EvaluationResult rows are deliberately untouched.  Only the four v3
config rows are upgraded in place: every existing dimension receives generic
placeholder rules when absent; inspiration keeps media penalty enabled and the
three legacy draft categories default it off.  Re-running after convergence is
a no-op and does not bump revisions.
"""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..category_evaluation_contract import (
    canonical_contract_hash,
    validate_category_evaluation_contract,
)
from ..dimension_composition import validate_subcategory_dimensions
from ..dimension_deduction_bridge import extract_dimension_deduction_rules
from ..dimension_schema_registry import canonical_json
from ..inspiration_category_seed import placeholder_deduction_rules
from ..models import CategoryEvaluationV3Config


TARGET_MEDIA_ENABLED = {
    "inspiration_image": True,
    "space_image": False,
    "material_image": False,
    "pdf_text": False,
}


def _upgrade_dimensions(
    subcategory_dimensions: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    upgraded = deepcopy(subcategory_dimensions)
    changed = False
    for config in upgraded.values():
        if not isinstance(config, dict):
            continue
        for group_name in ("common_group", "specific_group"):
            group = config.get(group_name)
            schema = group.get("schema_definition") if isinstance(group, dict) else None
            dimensions = schema.get("dimensions") if isinstance(schema, dict) else None
            if not isinstance(dimensions, list):
                continue
            for dimension in dimensions:
                if not isinstance(dimension, dict):
                    continue
                if not dimension.get("deduction_rules"):
                    dimension["deduction_rules"] = placeholder_deduction_rules(
                        str(dimension.get("label") or dimension.get("key") or "该维度")
                    )
                    changed = True
    return upgraded, changed


def upgrade_v3_to_rule_deduction(db: Session) -> dict[str, Any]:
    """Upgrade all existing target rows, committing is owned by the caller."""
    rows = db.scalars(
        select(CategoryEvaluationV3Config).where(
            CategoryEvaluationV3Config.category_key.in_(TARGET_MEDIA_ENABLED)
        )
    ).all()
    changed_keys: list[str] = []
    for row in rows:
        contract = json.loads(row.contract_json or "{}")
        dimensions = json.loads(row.subcategory_dimensions_json or "{}")
        upgraded_dimensions, dimension_changed = _upgrade_dimensions(dimensions)

        media = contract.setdefault("common_modifiers", {}).setdefault(
            "media_type_penalty", {}
        )
        desired_enabled = TARGET_MEDIA_ENABLED[row.category_key]
        media_changed = media.get("enabled", True) != desired_enabled
        if "enabled" not in media or media_changed:
            media["enabled"] = desired_enabled

        validate_category_evaluation_contract(contract)
        for config in upgraded_dimensions.values():
            validate_subcategory_dimensions(config)

        rules_mirror = extract_dimension_deduction_rules(upgraded_dimensions)
        serialized_contract = canonical_json(contract)
        serialized_dimensions = canonical_json(upgraded_dimensions)
        serialized_rules = canonical_json(rules_mirror)
        changed = any(
            (
                dimension_changed,
                media_changed,
                row.contract_json != serialized_contract,
                row.subcategory_dimensions_json != serialized_dimensions,
                row.dimension_deduction_rules_json != serialized_rules,
                row.media_penalty_enabled != desired_enabled,
            )
        )
        if not changed:
            continue
        row.contract_json = serialized_contract
        row.subcategory_dimensions_json = serialized_dimensions
        row.dimension_deduction_rules_json = serialized_rules
        row.media_penalty_enabled = desired_enabled
        row.contract_hash = canonical_contract_hash(contract)
        row.revision += 1
        changed_keys.append(row.category_key)

    db.flush()
    return {
        "target_count": len(rows),
        "changed_count": len(changed_keys),
        "changed_keys": sorted(changed_keys),
    }


def main() -> int:
    """Run the idempotent data upgrade against the configured database."""
    from ..database import init_database, session_scope

    init_database()
    with session_scope() as db:
        result = upgrade_v3_to_rule_deduction(db)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
