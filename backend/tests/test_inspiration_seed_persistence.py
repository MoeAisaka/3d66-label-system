from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.category_evaluation_contract import validate_category_evaluation_contract
from app.database import Base
from app.dimension_composition import validate_subcategory_dimensions
from app.inspiration_category_seed import (
    INSPIRATION_CALL_A_VERSION,
    INSPIRATION_CALL_B_VERSION,
    INSPIRATION_REV3_CALL_A_VERSION,
    INSPIRATION_SPEC_VERSION,
)
from app.proposal_text_contract import validate_proposal_text_contract
from app.models import CategoryEvaluationV3Config, EvaluationCategoryProfile, PromptVersion
from app.seed import (
    _seed_inspiration_image_prompts,
    _seed_inspiration_image_v3_config,
    seed_defaults,
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


def test_new_b_prompt_is_appended_without_overwriting_old_version() -> None:
    engine = _engine()
    with Session(engine) as db:
        db.add(
            PromptVersion(
                stage="B",
                category_key="inspiration_image",
                pipeline_scope="full_pipeline",
                name="旧灵感图 B",
                version="inspiration-b-v1",
                system_prompt="旧提示词不可覆盖",
                user_prompt="",
                rubric_version="inspiration-rubric-v1",
                status="published",
            )
        )
        db.commit()
        settings = SimpleNamespace(project_root=PROJECT_ROOT)
        _seed_inspiration_image_prompts(db, settings)
        _seed_inspiration_image_prompts(db, settings)
        db.commit()
        prompts = db.scalars(
            select(PromptVersion)
            .where(PromptVersion.category_key == "inspiration_image")
            .order_by(PromptVersion.id)
        ).all()
        assert [prompt.version for prompt in prompts].count(INSPIRATION_CALL_B_VERSION) == 1
        assert [prompt.version for prompt in prompts].count(INSPIRATION_CALL_A_VERSION) == 1
        legacy_a = db.scalar(
            select(PromptVersion).where(
                PromptVersion.version == INSPIRATION_REV3_CALL_A_VERSION
            )
        )
        active_a = db.scalar(
            select(PromptVersion).where(
                PromptVersion.version == INSPIRATION_CALL_A_VERSION
            )
        )
        assert legacy_a is not None and active_a is not None
        assert legacy_a.system_prompt == (
            PROJECT_ROOT / "prompts" / "inspiration_image_call_a_rev3.txt"
        ).read_text(encoding="utf-8").strip()
        assert active_a.system_prompt != legacy_a.system_prompt
        assert db.scalar(
            select(PromptVersion.system_prompt).where(
                PromptVersion.version == "inspiration-b-v1"
            )
        ) == "旧提示词不可覆盖"

        active_b = db.scalar(
            select(PromptVersion).where(
                PromptVersion.version == INSPIRATION_CALL_B_VERSION
            )
        )
        assert active_b is not None
        assert '"contract_version":"inspiration-aesthetic-foundation-v1"' in (
            active_b.system_prompt
        )
        assert active_b.version == "inspiration-b-v5-anchor-calibration-evidence-20260807"
        assert active_b.pipeline_scope == "shared"
        assert db.scalar(select(PromptVersion).where(
            PromptVersion.version == "inspiration-b-v3-anchor-aesthetic-20260806"
        )) is not None


def test_existing_profile_is_bound_to_rev4_a_and_new_b_atomically() -> None:
    engine = _engine()
    with Session(engine) as db:
        settings = SimpleNamespace(project_root=PROJECT_ROOT)
        _seed_inspiration_image_prompts(db, settings)
        legacy_a = db.scalar(
            select(PromptVersion).where(
                PromptVersion.version == INSPIRATION_REV3_CALL_A_VERSION
            )
        )
        legacy_b = db.scalar(
            select(PromptVersion).where(
                PromptVersion.version == "inspiration-b-v2-human-calibrated-20260805"
            )
        )
        assert legacy_a is not None and legacy_b is not None
        profile = EvaluationCategoryProfile(
            category_key="inspiration_image",
            display_name="灵感图",
            prompt_a_id=legacy_a.id,
            prompt_b_id=legacy_b.id,
            pipeline_revision=2,
        )
        db.add(profile)
        db.commit()

        _seed_inspiration_image_prompts(db, settings)
        db.commit()
        db.refresh(profile)
        assert (
            db.get(PromptVersion, profile.prompt_a_id).version == INSPIRATION_CALL_A_VERSION
        )
        assert (
            db.get(PromptVersion, profile.prompt_b_id).version == INSPIRATION_CALL_B_VERSION
        )
        assert profile.pipeline_revision == 3

        _seed_inspiration_image_prompts(db, settings)
        db.commit()
        db.refresh(profile)
        assert profile.pipeline_revision == 3


def test_existing_inspiration_config_is_replaced_once_and_stays_active() -> None:
    engine = _engine()
    with Session(engine) as db:
        db.add(
            CategoryEvaluationV3Config(
                category_key="inspiration_image",
                display_name="灵感图",
                status="active",
                contract_json=json.dumps({"spec_version": "legacy"}),
                classification_map_json="{}",
                subcategory_dimensions_json="{}",
                dimension_deduction_rules_json="{}",
                media_penalty_enabled=True,
                revision=7,
                contract_hash="legacy",
                created_by="test",
            )
        )
        db.commit()
        _seed_inspiration_image_v3_config(db)
        db.commit()
        row = db.scalar(
            select(CategoryEvaluationV3Config).where(
                CategoryEvaluationV3Config.category_key == "inspiration_image"
            )
        )
        assert row is not None
        assert row.status == "active"
        assert row.revision == 8
        assert row.media_penalty_enabled is False
        assert json.loads(row.contract_json)["spec_version"] == INSPIRATION_SPEC_VERSION

        frozen = row.contract_json
        _seed_inspiration_image_v3_config(db)
        db.commit()
        assert row.revision == 8
        assert row.contract_json == frozen

def test_seed_defaults_clones_active_v3_contract_and_prompts_for_all_categories() -> None:
    engine = _engine()
    expected_categories = {
        "space_image",
        "inspiration_image",
        "material_image",
        "pdf_text",
        "proposal_text_pdf",
    }
    with Session(engine) as db:
        seed_defaults(db)
        rows = db.scalars(
            select(CategoryEvaluationV3Config).order_by(
                CategoryEvaluationV3Config.category_key
            )
        ).all()
        assert {row.category_key for row in rows} == expected_categories
        assert {row.status for row in rows} == {"active"}

        revisions_before = {row.category_key: row.revision for row in rows}
        prompt_count_before = len(db.scalars(select(PromptVersion)).all())
        for row in rows:
            contract = json.loads(row.contract_json)
            classification_map = json.loads(row.classification_map_json)
            dimensions_by_track = json.loads(row.subcategory_dimensions_json)

            assert contract["category_key"] == row.category_key
            if row.category_key == "proposal_text_pdf":
                validate_proposal_text_contract(contract)
                assert contract["profile_type"] == "text-proposal-additive-v1"
                assert dimensions_by_track["profile_type"] == "text-proposal-additive-v1"
                for stage, version_key in (("A", "call_a_version"), ("B", "call_b_version")):
                    prompt = db.scalar(select(PromptVersion).where(
                        PromptVersion.version == contract[version_key]
                    ))
                    assert prompt is not None
                    assert prompt.stage == stage
                    assert prompt.category_key == row.category_key
                    assert prompt.status == "published"
                    assert prompt.source == "imported"
                continue
            if row.category_key == "inspiration_image":
                assert contract["spec_version"] == INSPIRATION_SPEC_VERSION
            else:
                assert row.category_key.replace("_", "-") in contract["spec_version"]
                assert contract["common_modifiers"]["format_version"] == "common-modifiers-v1"
                assert "authoritative_precheck_contract" not in contract
                assert row.revision == 1
            assert dimensions_by_track
            assert {
                len(config["common_group"]["schema_definition"]["dimensions"])
                for config in dimensions_by_track.values()
            } == {5, 6}

            validate_category_evaluation_contract(contract)
            track_keys = {
                track["key"]
                for track in contract["track_classification"]["tracks"]
            }
            validate_classification_map(
                classification_map, valid_track_keys=track_keys
            )
            for config in dimensions_by_track.values():
                validate_subcategory_dimensions(config)

            for stage, binding_key in (
                ("A", "call_a_version"),
                ("B", "call_b_version"),
            ):
                prompt = db.scalar(
                    select(PromptVersion).where(
                        PromptVersion.version
                        == contract["prompt_bindings"][binding_key]
                    )
                )
                assert prompt is not None
                assert prompt.stage == stage
                assert prompt.category_key == row.category_key
                assert prompt.status == "published"
                assert prompt.source == (
                    "imported"
                    if row.category_key == "inspiration_image"
                    else "v3_seed_clone"
                )

        seed_defaults(db)
        rows_after = db.scalars(select(CategoryEvaluationV3Config)).all()
        assert len(rows_after) == 5
        assert {row.category_key: row.revision for row in rows_after} == revisions_before
        assert len(db.scalars(select(PromptVersion)).all()) == prompt_count_before
