from copy import deepcopy

from sqlalchemy.orm import Session

from app.dimension_schema_registry import canonical_json
from app.inspiration_category_seed import (
    build_inspiration_classification_map,
    build_inspiration_subcategory_dimensions,
    build_inspiration_v3_contract,
)
from app.models import CategoryEvaluationV3Config


def add_active_v3_contract(
    db: Session,
    category_key: str = "space_image",
) -> dict:
    contract = deepcopy(build_inspiration_v3_contract())
    contract["category_key"] = category_key
    contract["spec_version"] = f"{category_key}-test-v3"
    classification_map = deepcopy(build_inspiration_classification_map())
    dimensions = deepcopy(build_inspiration_subcategory_dimensions())
    row = CategoryEvaluationV3Config(
        category_key=category_key,
        display_name=f"{category_key} 测试 v3",
        status="active",
        contract_json=canonical_json(contract),
        classification_map_json=canonical_json(classification_map),
        subcategory_dimensions_json=canonical_json(dimensions),
        revision=1,
        created_by="test:v3-only",
    )
    db.add(row)
    db.flush()
    return {
        "contract": contract,
        "classification_map": classification_map,
        "subcategory_dimensions": dimensions,
    }
