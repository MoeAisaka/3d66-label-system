from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.field_demand_contracts import create_field_demand_contract
from app.inspiration_category_seed import (
    build_inspiration_classification_map,
    build_inspiration_subcategory_dimensions,
    build_inspiration_v3_contract,
)
from app.mechanism_profiles import (
    MechanismProfileError,
    mechanism_profile_catalog,
    validate_mechanism_artifacts,
)


def _field_contract(db: Session, *, status: str = "active", category: str = "model_3d_su"):
    row = create_field_demand_contract(
        db,
        contract_key="3d-search",
        category_key=category,
        consumer_key="search",
        owner="tpeng-3d",
        fields=[
            {
                "field_key": "geometry_integrity",
                "source_path": "quality.geometry_integrity",
                "required": True,
                "data_type": "number",
            },
            {
                "field_key": "design_aesthetics",
                "source_path": "semantic.design_aesthetics",
                "required": True,
                "data_type": "number",
            },
        ],
        thresholds={"accuracy": 0.9, "recall": 0.9},
        status=status,
        created_by="admin",
    )
    db.flush()
    return row


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _artifacts(db: Session, **contract_overrides: object):
    field_contract = _field_contract(db)
    contract = build_inspiration_v3_contract()
    contract.update(
        {
            "profile_type": "3d-asset-quality-v1",
            "category_key": "model_3d_su",
            "field_demand_contract_id": field_contract.id,
            "source_schema_fingerprint": "f" * 64,
            "stage_fields": {
                "a_quality_fields": ["quality.geometry_integrity"],
                "b_aesthetic_fields": ["semantic.design_aesthetics"],
            },
        }
    )
    contract.update(contract_overrides)
    return (
        contract,
        build_inspiration_classification_map(),
        build_inspiration_subcategory_dimensions(),
    )


def test_three_d_profile_catalog_is_executable() -> None:
    item = next(
        item
        for item in mechanism_profile_catalog()
        if item["profile_type"] == "3d-asset-quality-v1"
    )
    assert item["can_execute"] is True
    assert item["editor_route"] == "three-d"
    assert item["read_only_fallback"] is False


def test_three_d_profile_requires_active_matching_field_contract(db: Session) -> None:
    contract, classification, dimensions = _artifacts(db)
    contract["field_demand_contract_id"] = 9999
    with pytest.raises(MechanismProfileError) as missing:
        validate_mechanism_artifacts(contract, classification, dimensions, db=db)
    assert missing.value.code == "three_d_field_contract_inactive"

    wrong = _field_contract(db, category="space_image")
    contract["field_demand_contract_id"] = wrong.id
    with pytest.raises(MechanismProfileError) as mismatch:
        validate_mechanism_artifacts(contract, classification, dimensions, db=db)
    assert mismatch.value.code == "three_d_field_contract_mismatch"


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"source_schema_fingerprint": ""}, "three_d_source_fingerprint_missing"),
        (
            {"stage_fields": {"a_quality_fields": [], "b_aesthetic_fields": ["semantic.design_aesthetics"]}},
            "three_d_stage_fields_missing",
        ),
        (
            {"stage_fields": {"a_quality_fields": ["quality.geometry_integrity"], "b_aesthetic_fields": []}},
            "three_d_stage_fields_missing",
        ),
    ],
)
def test_three_d_profile_rejects_missing_source_or_stage_fields(
    db: Session,
    override: dict[str, object],
    code: str,
) -> None:
    contract, classification, dimensions = _artifacts(db, **override)
    with pytest.raises(MechanismProfileError) as excinfo:
        validate_mechanism_artifacts(contract, classification, dimensions, db=db)
    assert excinfo.value.code == code


def test_three_d_profile_accepts_valid_contract(db: Session) -> None:
    contract, classification, dimensions = _artifacts(db)
    assert (
        validate_mechanism_artifacts(contract, classification, dimensions, db=db)
        == "3d-asset-quality-v1"
    )
    assert json.dumps(contract, ensure_ascii=False)


def test_three_d_profile_offline_validation_defers_only_database_reference(
    db: Session,
) -> None:
    contract, classification, dimensions = _artifacts(db)

    assert (
        validate_mechanism_artifacts(
            contract,
            classification,
            dimensions,
            require_database=False,
        )
        == "3d-asset-quality-v1"
    )

    contract["source_schema_fingerprint"] = ""
    with pytest.raises(MechanismProfileError) as invalid_structure:
        validate_mechanism_artifacts(
            contract,
            classification,
            dimensions,
            require_database=False,
        )
    assert invalid_structure.value.code == "three_d_source_fingerprint_missing"
