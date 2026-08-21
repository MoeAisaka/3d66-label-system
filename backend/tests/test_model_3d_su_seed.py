from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.category_evaluation_contract import validate_category_evaluation_contract
from app.category_evaluation_contract import canonical_contract_hash
from app.category_evaluation_v3_revisions import ensure_projected_revision
from app.category_pipeline import default_pipeline
from app.database import Base
from app.dimension_deduction_bridge import extract_dimension_deduction_rules
from app.dimension_deduction_bridge import rule_scoring_mode
from app.dimension_schema_registry import canonical_json
from app.dimension_composition import validate_subcategory_dimensions
from app.models import (
    CategoryEvaluationV3Config,
    CategoryEvaluationV3Revision,
    CATEGORY_PROFILE_DEFAULTS,
    EvaluationCategoryProfile,
    ModelConfig,
    PromptVersion,
    TagDemandContract,
)
from app.main import CATEGORY_KEYS
from app.model_3d_su_category_seed import (
    MODEL_3D_SU_CALL_A_VERSION,
    MODEL_3D_SU_CALL_B_VERSION,
    MODEL_3D_SU_CATEGORY_KEY,
    MODEL_3D_SU_CREATED_BY,
    MODEL_3D_SU_RUBRIC_VERSION,
    MODEL_3D_SU_SEMANTIC_CONTRACT_KEY,
    build_model_3d_su_classification_map,
    build_model_3d_su_contract,
    build_model_3d_su_subcategory_dimensions,
    build_model_3d_su_semantic_contract,
    model_3d_su_pipeline,
    seed_model_3d_su,
)
from app.subcategory_resolver import validate_classification_map


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def test_model_3d_su_contract_freezes_l1_to_l4_and_record_only_policy() -> None:
    contract = build_model_3d_su_contract()

    validate_category_evaluation_contract(contract)
    assert contract["category_key"] == MODEL_3D_SU_CATEGORY_KEY
    assert contract["level_scale"]["levels"] == [
        {"level": "L1", "enabled": True, "min_score": 80, "display_name": "好"},
        {"level": "L2", "enabled": True, "min_score": 61, "display_name": "中等"},
        {"level": "L3", "enabled": True, "min_score": 41, "display_name": "中差"},
        {"level": "L4", "enabled": True, "min_score": 0, "display_name": "极差"},
        {"level": "L5", "enabled": False, "display_name": "过滤"},
    ]
    assert contract["redline_policy"]["enabled"] is False
    assert contract["common_modifiers"]["media_type_penalty"]["enabled"] is False
    assert contract["common_modifiers"]["high_score_veto"]["enabled"] is False
    assert contract["output_contract"]["class_specific_fields"]["unrendered"]["required"] is True


def test_model_3d_su_has_three_tracks_and_document_weights() -> None:
    contract = build_model_3d_su_contract()
    classification_map = build_model_3d_su_classification_map()
    dimensions = build_model_3d_su_subcategory_dimensions()
    track_keys = {track["key"] for track in contract["track_classification"]["tracks"]}

    validate_classification_map(classification_map, valid_track_keys=track_keys)
    assert track_keys == {"space_building", "soft_furnishing", "functional_model"}
    assert set(dimensions) == track_keys
    assert classification_map["category_to_subcategory"]["电子电器"] == "functional_model"
    assert classification_map["category_to_subcategory"]["家具"] == "soft_furnishing"

    expected_weights = {
        "space_building": [0.20, 0.25, 0.20, 0.20, 0.15],
        "soft_furnishing": [0.25, 0.25, 0.20, 0.20, 0.10],
        # The source document's functional-model proportions are 35:25:20:15:10
        # (105 total).  The persisted contract keeps that relative priority but
        # normalizes it to a strict 1.0 weight sum.
        "functional_model": [35 / 105, 25 / 105, 20 / 105, 15 / 105, 10 / 105],
    }
    for track_key, config in dimensions.items():
        validate_subcategory_dimensions(config)
        rows = config["common_group"]["schema_definition"]["dimensions"]
        assert [row["weight"] for row in rows] == pytest.approx(
            expected_weights[track_key]
        )
        assert sum(row["weight"] for row in rows) == pytest.approx(1.0)
        assert all(
            row["grade_points"]
            == {"1": 0.0, "2": 25.0, "3": 50.0, "4": 75.0, "5": 100.0}
            for row in rows
        )
        assert {
            rule["deduction"]
            for row in rows
            for rule in row["deduction_rules"]
        } == {20, 50, 80}
        assert {
            rule["rule_id"]
            for row in rows
            for rule in row["deduction_rules"]
        } == {"minor_defect", "obvious_defect", "severe_defect"}
        assert all(
            len(rule["description"]) >= 12
            for row in rows
            for rule in row["deduction_rules"]
        )
        assert config["grade_output_contract"] == {
            "format_version": "dimension-grade-output-v1",
            "require_exact_keys": True,
            "evidence_required": True,
        }
        assert rule_scoring_mode(config) == "deduction_v1"


def test_model_3d_su_is_registered_as_an_image_category() -> None:
    assert "model_3d_su" in CATEGORY_KEYS
    assert CATEGORY_PROFILE_DEFAULTS["model_3d_su"] == {
        "display_name": "3D & SU 模型",
        "allowed_mime_types_json": '["image/jpeg","image/png","image/webp","image/gif"]',
        "preprocess_config_json": '{"preprocess":"image","su_unrendered_marker":true}',
    }
    pipeline = default_pipeline("model_3d_su")
    assert pipeline["input_kind"] == "image"
    assert ".gif" in pipeline["allowed_suffixes"]
    assert pipeline["prompt_mode"] == "ab"


def test_model_3d_su_seed_is_idempotent_and_preserves_operator_rows() -> None:
    engine = _engine()
    with Session(engine) as db:
        db.add(ModelConfig(active=True))
        db.commit()
        settings = SimpleNamespace(project_root=PROJECT_ROOT)

        seed_model_3d_su(db, settings)
        db.commit()
        first_profile = db.scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == MODEL_3D_SU_CATEGORY_KEY
            )
        )
        first_config = db.scalar(
            select(CategoryEvaluationV3Config).where(
                CategoryEvaluationV3Config.category_key == MODEL_3D_SU_CATEGORY_KEY
            )
        )
        assert first_profile is not None
        assert first_config is not None
        assert first_profile.status == "active"
        assert first_config.status == "active"
        assert first_profile.rubric_version == MODEL_3D_SU_RUBRIC_VERSION
        assert first_config.revision == 1
        assert first_config.projected_revision_id is not None

        first_profile.description = "运营已编辑描述"
        db.commit()
        seed_model_3d_su(db, settings)
        db.commit()

        prompts = db.scalars(
            select(PromptVersion).where(
                PromptVersion.category_key == MODEL_3D_SU_CATEGORY_KEY
            )
        ).all()
        assert {prompt.version for prompt in prompts} == {
            MODEL_3D_SU_CALL_A_VERSION,
            MODEL_3D_SU_CALL_B_VERSION,
        }
        assert first_config.revision == 1
        assert first_profile.description == "运营已编辑描述"
        assert db.scalar(
            select(PromptVersion).where(
                PromptVersion.version == MODEL_3D_SU_CALL_A_VERSION
            )
        ).status == "published"


def test_model_3d_su_seed_repairs_missing_same_spec_projection() -> None:
    engine = _engine()
    with Session(engine) as db:
        db.add(ModelConfig(active=True))
        db.commit()
        settings = SimpleNamespace(project_root=PROJECT_ROOT)

        seed_model_3d_su(db, settings)
        db.commit()
        config = db.scalar(
            select(CategoryEvaluationV3Config).where(
                CategoryEvaluationV3Config.category_key == MODEL_3D_SU_CATEGORY_KEY
            )
        )
        assert config is not None
        config.projected_revision_id = None
        db.commit()

        seed_model_3d_su(db, settings)
        db.commit()

        assert config.projected_revision_id is not None
        revision = db.get(CategoryEvaluationV3Revision, config.projected_revision_id)
        assert revision is not None
        assert revision.category_key == MODEL_3D_SU_CATEGORY_KEY


def test_model_3d_su_seed_isolates_operator_same_version_prompt_collision() -> None:
    engine = _engine()
    with Session(engine) as db:
        db.add(ModelConfig(active=True))
        prompt = PromptVersion(
            stage="A",
            category_key=MODEL_3D_SU_CATEGORY_KEY,
            pipeline_scope="shared",
            name="3D/SU 模型分类与字段预检",
            version=MODEL_3D_SU_CALL_A_VERSION,
            system_prompt=(
                PROJECT_ROOT / "backend/prompts/model_3d_su_call_a_v3.txt"
            ).read_text(encoding="utf-8").strip(),
            user_prompt="operator poison",
            rubric_version=MODEL_3D_SU_RUBRIC_VERSION,
            status="draft",
            source="manual",
            change_note="operator collision",
            created_by="operator:prompt-owner",
        )
        db.add(prompt)
        db.commit()

        seed_model_3d_su(db, SimpleNamespace(project_root=PROJECT_ROOT))
        db.commit()

        rows = db.scalars(
            select(PromptVersion).where(
                PromptVersion.category_key == MODEL_3D_SU_CATEGORY_KEY,
                PromptVersion.stage == "A",
            )
        ).all()
        assert len(rows) == 2
        operator_row = next(row for row in rows if row.created_by == "operator:prompt-owner")
        system_row = next(row for row in rows if row.created_by == MODEL_3D_SU_CREATED_BY)
        assert operator_row.version == MODEL_3D_SU_CALL_A_VERSION
        assert system_row.version.startswith(f"{MODEL_3D_SU_CALL_A_VERSION}-system-seed-")
        config = db.scalar(
            select(CategoryEvaluationV3Config).where(
                CategoryEvaluationV3Config.category_key == MODEL_3D_SU_CATEGORY_KEY
            )
        )
        assert config is not None
        assert json.loads(config.contract_json)["prompt_bindings"]["call_a_version"] == system_row.version


def test_model_3d_su_seed_rejects_same_spec_content_drift() -> None:
    engine = _engine()
    with Session(engine) as db:
        db.add(ModelConfig(active=True))
        db.commit()
        settings = SimpleNamespace(project_root=PROJECT_ROOT)
        seed_model_3d_su(db, settings)
        db.commit()
        config = db.scalar(
            select(CategoryEvaluationV3Config).where(
                CategoryEvaluationV3Config.category_key == MODEL_3D_SU_CATEGORY_KEY
            )
        )
        assert config is not None
        config.subcategory_dimensions_json = "{}"
        config.projected_revision_id = None
        db.commit()

        with pytest.raises(RuntimeError, match="冻结合同.*不匹配"):
            seed_model_3d_su(db, settings)


def test_model_3d_su_seed_rejects_operator_owned_same_spec_projection_repair() -> None:
    engine = _engine()
    with Session(engine) as db:
        db.add(ModelConfig(active=True))
        db.commit()
        settings = SimpleNamespace(project_root=PROJECT_ROOT)

        seed_model_3d_su(db, settings)
        db.commit()
        config = db.scalar(
            select(CategoryEvaluationV3Config).where(
                CategoryEvaluationV3Config.category_key == MODEL_3D_SU_CATEGORY_KEY
            )
        )
        assert config is not None
        original_fields = {
            "display_name": config.display_name,
            "status": config.status,
            "contract_json": config.contract_json,
            "classification_map_json": config.classification_map_json,
            "subcategory_dimensions_json": config.subcategory_dimensions_json,
            "dimension_deduction_rules_json": config.dimension_deduction_rules_json,
            "media_penalty_enabled": config.media_penalty_enabled,
            "revision": config.revision,
            "contract_hash": config.contract_hash,
        }
        config.created_by = "operator:category-owner"
        config.projected_revision_id = None
        db.commit()

        with pytest.raises(RuntimeError, match="拒绝覆盖"):
            seed_model_3d_su(db, settings)

        assert config.created_by == "operator:category-owner"
        assert config.projected_revision_id is None
        for field, expected in original_fields.items():
            assert getattr(config, field) == expected


def test_model_3d_su_seed_appends_revision_when_system_spec_is_outdated() -> None:
    engine = _engine()
    with Session(engine) as db:
        db.add(ModelConfig(active=True))
        db.commit()
        settings = SimpleNamespace(project_root=PROJECT_ROOT)

        seed_model_3d_su(db, settings)
        db.commit()
        config = db.scalar(
            select(CategoryEvaluationV3Config).where(
                CategoryEvaluationV3Config.category_key == MODEL_3D_SU_CATEGORY_KEY
            )
        )
        assert config is not None
        assert config.projected_revision_id is not None
        original_revision_id = config.projected_revision_id

        legacy_contract = json.loads(config.contract_json)
        legacy_contract["spec_version"] = "model-3d-su-v0-test"
        config.contract_json = json.dumps(legacy_contract, ensure_ascii=False, sort_keys=True)
        config.contract_hash = "legacy-test-hash"
        db.commit()

        seed_model_3d_su(db, settings)
        db.commit()

        assert config.revision == 2
        assert config.projected_revision_id != original_revision_id
        original_revision = db.get(CategoryEvaluationV3Revision, original_revision_id)
        active_revision = db.get(CategoryEvaluationV3Revision, config.projected_revision_id)
        assert original_revision is not None
        assert original_revision.status == "retired"
        assert active_revision is not None
        assert active_revision.status == "active"
        assert active_revision.parent_revision_id == original_revision_id


def test_model_3d_su_prompts_keep_common_fields_and_forbid_final_level() -> None:
    engine = _engine()
    with Session(engine) as db:
        db.add(ModelConfig(active=True))
        db.commit()
        seed_model_3d_su(db, SimpleNamespace(project_root=PROJECT_ROOT))
        db.commit()

        prompt_a = db.scalar(
            select(PromptVersion).where(PromptVersion.version == MODEL_3D_SU_CALL_A_VERSION)
        )
        prompt_b = db.scalar(
            select(PromptVersion).where(PromptVersion.version == MODEL_3D_SU_CALL_B_VERSION)
        )
        assert prompt_a is not None and prompt_b is not None
        assert prompt_a.rubric_version == MODEL_3D_SU_RUBRIC_VERSION
        assert '"production_fields"' in prompt_a.system_prompt
        assert '"model_3d_su_fields"' in prompt_a.system_prompt
        assert "predicted_level" not in prompt_a.system_prompt
        assert "不得输出 final_level" in prompt_b.system_prompt
        assert "任何最终等级" in prompt_b.system_prompt
        assert "全部为 5" in prompt_b.system_prompt
        assert "全部为 4" in prompt_b.system_prompt
        assert "全部为 3" in prompt_b.system_prompt
        assert "全部为 1 或 2" in prompt_b.system_prompt


def test_model_3d_su_seed_upgrades_known_v1_rows_once_without_rewriting_history() -> None:
    engine = _engine()
    with Session(engine) as db:
        model = ModelConfig(active=True)
        db.add(model)
        db.flush()
        prompt_a_v1 = PromptVersion(
            stage="A",
            category_key=MODEL_3D_SU_CATEGORY_KEY,
            pipeline_scope="shared",
            name="3D/SU v1 A",
            version="model-3d-su-a-v1-20260814",
            system_prompt=(PROJECT_ROOT / "backend/prompts/model_3d_su_call_a_v1.txt").read_text(encoding="utf-8").strip(),
            user_prompt="",
            rubric_version="model-3d-su-rubric-v1",
            status="published",
            source="imported",
            created_by="system:model-3d-su-v1",
        )
        prompt_b_v1 = PromptVersion(
            stage="B",
            category_key=MODEL_3D_SU_CATEGORY_KEY,
            pipeline_scope="shared",
            name="3D/SU v1 B",
            version="model-3d-su-b-v1-20260814",
            system_prompt=(PROJECT_ROOT / "backend/prompts/model_3d_su_call_b_v1.txt").read_text(encoding="utf-8").strip(),
            user_prompt="",
            rubric_version="model-3d-su-rubric-v1",
            status="published",
            source="imported",
            created_by="system:model-3d-su-v1",
        )
        db.add_all([prompt_a_v1, prompt_b_v1])
        db.flush()
        profile = EvaluationCategoryProfile(
            category_key=MODEL_3D_SU_CATEGORY_KEY,
            display_name="3D & SU 模型",
            description="运营已编辑描述",
            status="active",
            allowed_mime_types_json=canonical_json(
                ["image/jpeg", "image/png", "image/webp", "image/gif"]
            ),
            preprocess_config_json=canonical_json(
                {"preprocess": "image", "su_unrendered_marker": True}
            ),
            pipeline_config_json=canonical_json(model_3d_su_pipeline()),
            pipeline_revision=1,
            prompt_a_id=prompt_a_v1.id,
            prompt_b_id=prompt_b_v1.id,
            model_config_id=model.id,
            rubric_version="model-3d-su-rubric-v1",
            created_by="system:model-3d-su-v1",
        )
        legacy_contract = build_model_3d_su_contract()
        legacy_contract["spec_version"] = "model-3d-su-v1-dingtalk-20260814"
        legacy_contract["prompt_bindings"] = {
            "call_a_version": prompt_a_v1.version,
            "call_b_version": prompt_b_v1.version,
        }
        legacy_dimensions = build_model_3d_su_subcategory_dimensions()
        for config in legacy_dimensions.values():
            config.pop("grade_output_contract", None)
            for dimension in config["common_group"]["schema_definition"]["dimensions"]:
                dimension.pop("grade_points", None)
                dimension["deduction_rules"] = [
                    {
                        "rule_id": "minor_defect",
                        "description": "微瑕：可见轻微缺陷",
                        "deduction": 20,
                        "tags": ["模型美感"],
                    },
                    {
                        "rule_id": "obvious_defect",
                        "description": "明显缺陷：可见明显问题",
                        "deduction": 50,
                        "tags": ["模型美感"],
                    },
                    {
                        "rule_id": "severe_defect",
                        "description": "严重硬伤：可见严重问题",
                        "deduction": 80,
                        "tags": ["模型美感"],
                    },
                ]
        legacy_config = CategoryEvaluationV3Config(
            category_key=MODEL_3D_SU_CATEGORY_KEY,
            display_name="3D & SU 模型",
            revision=1,
            status="active",
            contract_json=canonical_json(legacy_contract),
            classification_map_json=canonical_json(
                build_model_3d_su_classification_map()
            ),
            subcategory_dimensions_json=canonical_json(legacy_dimensions),
            dimension_deduction_rules_json=canonical_json(
                extract_dimension_deduction_rules(legacy_dimensions)
            ),
            media_penalty_enabled=False,
            contract_hash=canonical_contract_hash(legacy_contract),
            created_by="system:model-3d-su-v1",
        )
        db.add_all([profile, legacy_config])
        db.flush()
        legacy_revision = ensure_projected_revision(db, legacy_config)
        legacy_revision_contract = legacy_revision.contract_json
        db.commit()

        settings = SimpleNamespace(project_root=PROJECT_ROOT)
        seed_model_3d_su(db, settings)
        db.commit()
        first_v2_revision_id = legacy_config.projected_revision_id
        first_v2_revision_number = legacy_config.revision
        seed_model_3d_su(db, settings)
        db.commit()

        prompts = db.scalars(
            select(PromptVersion).where(
                PromptVersion.category_key == MODEL_3D_SU_CATEGORY_KEY
            )
        ).all()
        assert MODEL_3D_SU_CALL_A_VERSION == "model-3d-su-a-v4-20260821"
        assert MODEL_3D_SU_CALL_B_VERSION == "model-3d-su-b-v4-20260821"
        assert {prompt.version for prompt in prompts} == {
            prompt_a_v1.version,
            prompt_b_v1.version,
            MODEL_3D_SU_CALL_A_VERSION,
            MODEL_3D_SU_CALL_B_VERSION,
        }
        assert profile.prompt_a_id != prompt_a_v1.id
        assert profile.prompt_b_id != prompt_b_v1.id
        assert profile.rubric_version == MODEL_3D_SU_RUBRIC_VERSION
        assert profile.description == "运营已编辑描述"
        assert legacy_config.revision == first_v2_revision_number == 2
        assert legacy_config.projected_revision_id == first_v2_revision_id
        assert legacy_revision.status == "retired"
        assert legacy_revision.contract_json == legacy_revision_contract
        active_revision = db.get(
            CategoryEvaluationV3Revision, legacy_config.projected_revision_id
        )
        assert active_revision.status == "active"
        assert active_revision.parent_revision_id == legacy_revision.id


def test_model_3d_su_semantic_contract_seed_is_draft_and_platform_wide() -> None:
    contract = build_model_3d_su_semantic_contract()
    assert set(contract["semantic_schema"]["fields"]) >= {
        "space", "object", "style", "material", "structural_features",
        "architectural_element", "soft_decoration", "hard_decoration", "color", "title",
    }
    assert contract["category_applicability"]["model_3d_su"]["space"] == "required"
    assert {item["asset_scope"] for item in contract["execution_variants"]} == {"whole", "single"}

    engine = _engine()
    with Session(engine) as db:
        db.add(ModelConfig(active=True))
        db.commit()
        seed_model_3d_su(db, SimpleNamespace(project_root=PROJECT_ROOT))
        db.commit()
        rows = db.scalars(select(TagDemandContract).where(TagDemandContract.contract_key == "semantic-platform")).all()
        assert len(rows) == 1
        assert rows[0].status == "draft"
    engine.dispose()


def test_seed_appends_v2_draft_without_activating_or_overwriting_v1() -> None:
    engine = _engine()
    with Session(engine) as db:
        db.add(ModelConfig(active=True))
        db.commit()
        settings = SimpleNamespace(project_root=PROJECT_ROOT)
        seed_model_3d_su(db, settings)
        db.commit()
        first_rows = db.scalars(
            select(TagDemandContract)
            .where(
                TagDemandContract.contract_key == MODEL_3D_SU_SEMANTIC_CONTRACT_KEY
            )
            .order_by(TagDemandContract.version)
        ).all()
        assert len(first_rows) == 1
        assert first_rows[0].status == "draft"
        assert json.loads(first_rows[0].definition_json)["schema_version"] == (
            "tag-demand-contract-v2"
        )
        first_hash = first_rows[0].contract_hash

        seed_model_3d_su(db, settings)
        db.commit()
        second_rows = db.scalars(
            select(TagDemandContract)
            .where(
                TagDemandContract.contract_key == MODEL_3D_SU_SEMANTIC_CONTRACT_KEY
            )
            .order_by(TagDemandContract.version)
        ).all()
        assert len(second_rows) == 1
        assert second_rows[0].contract_hash == first_hash
        assert second_rows[0].approved_by is None
    engine.dispose()


def test_seed_appends_v2_after_existing_v1_without_retiring_operator_contract() -> None:
    from tests.test_semantic_tag_contracts import valid_contract

    engine = _engine()
    with Session(engine) as db:
        db.add_all(
            [
                ModelConfig(active=True),
                TagDemandContract(
                    contract_key=MODEL_3D_SU_SEMANTIC_CONTRACT_KEY,
                    version=1,
                    status="active",
                    definition_json=json.dumps(
                        valid_contract(), ensure_ascii=False, sort_keys=True
                    ),
                    contract_hash="f" * 64,
                    created_by="operator:semantic-owner",
                ),
            ]
        )
        db.commit()

        seed_model_3d_su(db, SimpleNamespace(project_root=PROJECT_ROOT))
        db.commit()

        rows = db.scalars(
            select(TagDemandContract)
            .where(
                TagDemandContract.contract_key == MODEL_3D_SU_SEMANTIC_CONTRACT_KEY
            )
            .order_by(TagDemandContract.version)
        ).all()
        assert [(row.version, row.status) for row in rows] == [
            (1, "active"),
            (2, "draft"),
        ]
        assert rows[0].created_by == "operator:semantic-owner"
        assert json.loads(rows[0].definition_json)["schema_version"] == (
            "tag-demand-contract-v1"
        )
        assert json.loads(rows[1].definition_json)["schema_version"] == (
            "tag-demand-contract-v2"
        )
    engine.dispose()
