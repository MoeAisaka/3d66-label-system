from __future__ import annotations

from copy import deepcopy
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.category_evaluation_contract import DeductionRule
from app.dimension_composition import (
    DimensionCompositionError,
    validate_subcategory_dimensions,
)
from app.inspiration_category_seed import build_inspiration_subcategory_dimensions
from app.inspiration_category_seed import build_inspiration_v3_contract
from app.database import Base
from app.migrations.upgrade_v3_to_rule_deduction import upgrade_v3_to_rule_deduction
from app.models import CategoryEvaluationV3Config


def test_deduction_rule_requires_chinese_description_and_positive_deduction() -> None:
    rule = DeductionRule.model_validate(
        {
            "rule_id": "composition_minor",
            "description": "构图存在局部失衡",
            "deduction": 12.5,
            "tags": ["构图", "占位"],
        }
    )
    assert rule.deduction == 12.5
    with pytest.raises(ValueError):
        DeductionRule.model_validate(
            {"rule_id": "bad", "description": "english only", "deduction": 10}
        )
    with pytest.raises(ValueError):
        DeductionRule.model_validate(
            {"rule_id": "bad", "description": "扣分", "deduction": 0}
        )


def test_dimension_contract_rejects_duplicate_rule_ids() -> None:
    config = deepcopy(build_inspiration_subcategory_dimensions()["class_one"])
    dimension = config["common_group"]["schema_definition"]["dimensions"][0]
    dimension["deduction_rules"].append(deepcopy(dimension["deduction_rules"][0]))
    with pytest.raises(DimensionCompositionError) as excinfo:
        validate_subcategory_dimensions(config)
    assert "duplicate" in excinfo.value.code


def test_grade_points_remain_a_deprecated_compatible_fallback() -> None:
    config = deepcopy(build_inspiration_subcategory_dimensions()["class_one"])
    for dimension in config["common_group"]["schema_definition"]["dimensions"]:
        dimension.pop("deduction_rules")
    validate_subcategory_dimensions(config)


def test_data_upgrade_is_idempotent_and_preserves_lifecycle_state() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    contract = build_inspiration_v3_contract()
    contract["common_modifiers"]["media_type_penalty"].pop("enabled")
    dimensions = build_inspiration_subcategory_dimensions()
    for config in dimensions.values():
        for dimension in config["common_group"]["schema_definition"]["dimensions"]:
            dimension.pop("deduction_rules")
    with Session(engine) as db:
        db.add(
            CategoryEvaluationV3Config(
                category_key="inspiration_image",
                display_name="灵感图",
                status="active",
                contract_json=json.dumps(contract, ensure_ascii=False),
                classification_map_json='{"legacy":true}',
                subcategory_dimensions_json=json.dumps(dimensions, ensure_ascii=False),
                revision=7,
                contract_hash="old",
            )
        )
        db.commit()
        first = upgrade_v3_to_rule_deduction(db)
        db.commit()
        row = db.query(CategoryEvaluationV3Config).one()
        assert first["changed_keys"] == ["inspiration_image"]
        assert row.status == "active"
        assert row.revision == 8
        assert row.media_penalty_enabled is True
        assert json.loads(row.dimension_deduction_rules_json)
        second = upgrade_v3_to_rule_deduction(db)
        db.commit()
        assert second["changed_count"] == 0
        assert row.revision == 8
    engine.dispose()
